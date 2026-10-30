"""Kiểm thử tải webhook sự kiện cuộc gọi Stringee (docs/10 mục 4, Đợt 2). CHẠY NGOÀI BENCH, chỉ cần `httpx`.

Ví dụ (kênh `LOAD tong dai` dùng nhà cung cấp Stringee, sandbox bật, khóa giả):
	export OMNI_LOAD_TEL_TOKEN=...    # webhook_token của kênh test
	export OMNI_LOAD_TEL_SECRET=...   # telephony_api_key_secret của kênh test (ký HMAC-SHA1 như Stringee)
	python send_calls.py --site-url http://127.0.0.1:8099 --host omni.test --channel "LOAD tong dai" \
		--rate 10 --duration 120 --customers 150 --missed-ratio 0.35 --dup-ratio 0.1 --out /tmp/calls.json

Mỗi cuộc gọi gửi MỘT sự kiện `ended` (có `answerDuration` > 0 là nghe máy, bằng 0 là nhỡ), khách chọn xoay vòng
từ `--customers` số 8490xxxxxxx nên một khách có nhiều cuộc gọi (nhỡ lẫn nghe máy). Gửi theo lịch cố định
(open-loop). Sự kiện gửi lại dùng đúng payload và chữ ký gốc. `--bad-token-ratio` thêm các request sai token
(phải trả 401, không tạo bản ghi). Chữ ký là công thức đang dùng trong code (S1 chưa xác nhận với Stringee thật).
Secret không bao giờ được in ra. Kết quả JSON liệt kê số liệu độ trễ, mã trạng thái và các số khách/cuộc gọi.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import random
import statistics
import sys
import time

import httpx

WEBHOOK_PATH = "/api/method/omni_crm.api.webhooks.telephony_event"


def percentile(sorted_ms: list[float], p: float) -> float:
	"""Phân vị p (0..1) của danh sách đã sắp xếp (hạng gần nhất)."""
	if not sorted_ms:
		return float("nan")
	k = max(0, min(len(sorted_ms) - 1, math.ceil(p * len(sorted_ms)) - 1))
	return sorted_ms[k]


def build_event(call_id: str, number: str, missed: bool, ts_ms: int, agent: str) -> dict:
	"""Payload `ended` theo mẫu tests/fixtures/stringee/event_ended_*.json."""
	return {
		"call_status": "ended",
		"project_id": 9999,
		"account_sid": "AC-load",
		"timestamp_ms": ts_ms,
		"from": {"number": number, "alias": number, "is_online": False, "type": "external"},
		"to": {"number": agent, "alias": agent, "is_online": True, "type": "internal"},
		"type": "stringee_call",
		"call_id": call_id,
		"callCreatedReason": "EXTERNAL_CALL_IN",
		"endCallCause": "480 Temporarily Unavailable" if missed else "USER_END_CALL",
		"endedBy": "EXTERNAL",
		"callType": "CALL",
		"duration": 30 if missed else 130,
		"answerDuration": 0 if missed else 125,
	}


def sign(raw: bytes, secret: str) -> str:
	"""Giá trị header X-Stringee-Signature (HMAC-SHA1 base64 của thân)."""
	return base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha1).digest()).decode()


async def run(args: argparse.Namespace) -> dict:
	token = os.environ.get(args.token_env)
	secret = os.environ.get(args.secret_env)
	if not token or not secret:
		sys.exit(f"Thiếu biến môi trường {args.token_env} / {args.secret_env}")
	rng = random.Random(args.seed)
	total = int(round(args.rate * args.duration))
	numbers = [f"8490{args.number_block}{i:05d}"[:11] for i in range(args.customers)]
	url = args.site_url.rstrip("/") + WEBHOOK_PATH
	headers_base = {"Content-Type": "application/json"}
	if args.host:
		headers_base["Host"] = args.host
	results: list[tuple[float, int, str]] = []  # (ms, status, loại)
	errors: dict[str, int] = {}
	calls: list[dict] = []
	client = httpx.AsyncClient(
		timeout=args.timeout,
		limits=httpx.Limits(
			max_connections=args.max_connections, max_keepalive_connections=args.max_connections
		),
	)

	async def fire(raw: bytes, kind: str, bad_token: bool = False) -> None:
		hdr = {**headers_base, "X-Stringee-Signature": sign(raw, secret)}
		params = {"ch": args.channel, "t": "sai-token-" + "x" * 30 if bad_token else token}
		t = time.perf_counter()
		try:
			r = await client.post(url, params=params, content=raw, headers=hdr)
			results.append(((time.perf_counter() - t) * 1000, r.status_code, kind))
		except Exception as e:
			errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
			results.append(((time.perf_counter() - t) * 1000, 0, kind))

	async def one(i: int, start: float) -> None:
		await asyncio.sleep(max(0.0, start + i / args.rate - time.perf_counter()))
		number = numbers[i % len(numbers)] if i < len(numbers) else rng.choice(numbers)
		missed = rng.random() < args.missed_ratio
		call_id = f"{args.id_prefix}{args.run_id}-{i}"
		raw = json.dumps(
			build_event(call_id, number, missed, int(time.time() * 1000), rng.choice(args.agents.split(","))),
			ensure_ascii=False,
		).encode()
		calls.append({"id": call_id, "number": number, "missed": missed})
		await fire(raw, "missed" if missed else "answered")
		if rng.random() < args.dup_ratio:
			await asyncio.sleep(args.resend_delay)
			await fire(raw, "resend")
		if rng.random() < args.bad_token_ratio:
			await fire(raw, "bad_token", bad_token=True)

	start = time.perf_counter()
	wall0 = time.time()
	await asyncio.gather(*(one(i, start) for i in range(total)))
	elapsed = time.perf_counter() - start
	await client.aclose()
	good = sorted(ms for ms, st, k in results if st == 200 and k != "bad_token")
	codes: dict[str, int] = {}
	for _ms, st, k in results:
		key = f"{k}:{st}"
		codes[key] = codes.get(key, 0) + 1
	unique_customers = len({c["number"] for c in calls})
	return {
		"channel": args.channel,
		"rate_per_s": args.rate,
		"duration_s": args.duration,
		"elapsed_s": round(elapsed, 1),
		"started_at_epoch": round(wall0, 1),
		"unique_calls_sent": total,
		"unique_customers": unique_customers,
		"missed_sent": sum(1 for c in calls if c["missed"]),
		"requests_total": len(results),
		"status_by_kind": codes,
		"client_errors": errors,
		"latency_ms_200_only": {
			"n": len(good),
			"p50": round(percentile(good, 0.50), 1),
			"p95": round(percentile(good, 0.95), 1),
			"p99": round(percentile(good, 0.99), 1),
			"max": round(good[-1], 1) if good else None,
			"mean": round(statistics.fmean(good), 1) if good else None,
		},
		"id_prefix": args.id_prefix,
		"run_id": args.run_id,
		"numbers": numbers if args.dump_numbers else len(numbers),
		"customers_with_missed": len({c["number"] for c in calls if c["missed"]}),
	}


def main() -> None:
	"""Đọc tham số, chạy, in và (tùy chọn) ghi kết quả JSON."""
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--site-url", required=True)
	p.add_argument("--host", default="")
	p.add_argument("--channel", required=True)
	p.add_argument("--rate", type=float, required=True, help="cuộc gọi duy nhất mỗi giây")
	p.add_argument("--duration", type=float, required=True, help="giây")
	p.add_argument("--customers", type=int, default=150)
	p.add_argument("--missed-ratio", type=float, default=0.35)
	p.add_argument("--dup-ratio", type=float, default=0.1)
	p.add_argument("--bad-token-ratio", type=float, default=0.0)
	p.add_argument("--resend-delay", type=float, default=1.0)
	p.add_argument("--agents", default="agent101,agent102,agent103")
	p.add_argument("--timeout", type=float, default=30.0)
	p.add_argument("--max-connections", type=int, default=200)
	p.add_argument("--seed", type=int, default=1)
	p.add_argument("--run-id", default=str(int(time.time())))
	p.add_argument("--id-prefix", default="LOADCALL-")
	p.add_argument("--number-block", default="77", help="2 chữ số giữa số điện thoại giả 8490<block>xxxxx")
	p.add_argument("--token-env", default="OMNI_LOAD_TEL_TOKEN")
	p.add_argument("--secret-env", default="OMNI_LOAD_TEL_SECRET")
	p.add_argument("--dump-numbers", action="store_true")
	p.add_argument("--out", default="")
	args = p.parse_args()
	result = asyncio.run(run(args))
	text = json.dumps(result, ensure_ascii=False, indent=1)
	print(text)
	if args.out:
		with open(args.out, "w", encoding="utf-8") as f:
			f.write(text)


if __name__ == "__main__":
	main()
