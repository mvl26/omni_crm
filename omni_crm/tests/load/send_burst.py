"""Kiểm thử tải webhook Zalo (docs/10 mục 4). CHẠY NGOÀI BENCH, chỉ cần `httpx`.

Ví dụ (2 pha của D1-11):
	export OMNI_LOAD_OA_SECRET=...   # oa_secret_key của KÊNH TEST, không phải kênh thật
	export OMNI_LOAD_APP_ID=...      # zalo_app_id của kênh test
	python send_burst.py --site-url http://127.0.0.1:8099 --host omni.test --channel "LOAD kenh" \
		--rate 1.67 --duration 3600 --customers 300 --dup-ratio 0.1 --out /tmp/pha1.json
	python send_burst.py ... --rate 50 --duration 120 --out /tmp/pha2.json

Gửi theo lịch cố định (open-loop): phản hồi chậm không làm lịch gửi chậm theo, nên p95 không bị che.
Tin gửi lại (dup-ratio) dùng đúng payload và chữ ký của tin gốc, gửi sau `--resend-delay` giây (nhỏ hơn
nhiều so với giới hạn 10 phút của dấu thời gian, để không đếm nhầm 401 là mất tin).

Chữ ký theo công thức trong code (Z1, docs/12 mục B2): mac = sha256(app_id + raw_body + timestamp + oa_secret).
Công thức này CHƯA đối chiếu với tài liệu Zalo thật; script này không chứng minh gì về Zalo thật.
In ra JSON gồm: số liệu độ trễ, mã trạng thái, và danh sách msg_id duy nhất để đối chiếu với DB bằng
`bench --site <site> execute omni_crm.tests.load.verify_burst.run --kwargs '{...}'`.
Secret không bao giờ được in ra.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time

import httpx

WEBHOOK_PATH = "/api/method/omni_crm.api.webhooks.zalo"
TEXTS = (
	"Chào shop, cho mình hỏi giá máy A",
	"Bên mình có kệ pallet loại 3 tầng không ạ?",
	"Mình cần báo giá xe nâng 2 tấn",
	"Lịch giao hàng tuần sau thế nào?",
	"Cho mình xin catalog với",
)


def build_event(customer: str, msg_id: str, text: str, ts_ms: int, app_id: str) -> dict:
	"""Payload user_send_text theo mẫu tests/fixtures/zalo/user_send_text.json."""
	return {
		"app_id": app_id,
		"event_name": "user_send_text",
		"timestamp": str(ts_ms),
		"sender": {"id": customer},
		"recipient": {"id": "LOADOA"},
		"message": {"msg_id": msg_id, "text": text},
	}


def sign(raw: bytes, timestamp: str, app_id: str, secret: str) -> str:
	"""Giá trị header X-ZEvent-Signature."""
	return "mac=" + hashlib.sha256(app_id.encode() + raw + timestamp.encode() + secret.encode()).hexdigest()


def percentile(sorted_ms: list[float], p: float) -> float:
	"""Phân vị p (0..1) của danh sách đã sắp xếp (phương pháp hạng gần nhất)."""
	if not sorted_ms:
		return float("nan")
	k = max(0, min(len(sorted_ms) - 1, math.ceil(p * len(sorted_ms)) - 1))
	return sorted_ms[k]


async def run(args: argparse.Namespace) -> dict:
	secret = os.environ.get(args.secret_env)
	app_id = os.environ.get(args.app_id_env)
	if not secret or not app_id:
		sys.exit(f"Thiếu biến môi trường {args.secret_env} / {args.app_id_env}")
	rng = random.Random(args.seed)
	total = int(round(args.rate * args.duration))
	customers = [f"{args.sender_prefix}{i:04d}" for i in range(args.customers)]
	url = args.site_url.rstrip("/") + WEBHOOK_PATH
	headers_base = {"Content-Type": "application/json"}
	if args.host:
		headers_base["Host"] = args.host
	results: list[tuple[float, int, bool]] = []  # (ms, status, is_resend)
	errors: dict[str, int] = {}
	sent_ids: list[dict] = []
	limits = httpx.Limits(
		max_connections=args.max_connections, max_keepalive_connections=args.max_connections
	)
	client = httpx.AsyncClient(timeout=args.timeout, limits=limits)

	async def fire(raw: bytes, ts: str, resend: bool) -> None:
		hdr = {**headers_base, "X-ZEvent-Signature": sign(raw, ts, app_id, secret)}
		t = time.perf_counter()
		try:
			r = await client.post(url, params={"ch": args.channel}, content=raw, headers=hdr)
			results.append(((time.perf_counter() - t) * 1000, r.status_code, resend))
		except Exception as e:
			errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
			results.append(((time.perf_counter() - t) * 1000, 0, resend))

	async def message(i: int, start: float) -> None:
		await asyncio.sleep(max(0.0, start + i / args.rate - time.perf_counter()))
		customer = customers[i % len(customers)] if i < len(customers) else rng.choice(customers)
		msg_id = f"{args.sender_prefix}m{args.run_id}-{i}"
		ts_ms = int(time.time() * 1000)
		raw = json.dumps(
			build_event(customer, msg_id, TEXTS[i % len(TEXTS)], ts_ms, app_id), ensure_ascii=False
		).encode()
		sent_ids.append({"id": msg_id, "customer": customer})
		await fire(raw, str(ts_ms), False)
		if rng.random() < args.dup_ratio:
			await asyncio.sleep(args.resend_delay)
			await fire(raw, str(ts_ms), True)

	start = time.perf_counter()
	wall0 = time.time()
	await asyncio.gather(*(message(i, start) for i in range(total)))
	elapsed = time.perf_counter() - start
	await client.aclose()

	ok = sorted(ms for ms, st, _ in results if st == 200)
	allms = sorted(ms for ms, st, _ in results if st)
	codes: dict[str, int] = {}
	for _ms, st, _r in results:
		codes[str(st)] = codes.get(str(st), 0) + 1
	return {
		"channel": args.channel,
		"rate_per_s": args.rate,
		"duration_s": args.duration,
		"elapsed_s": round(elapsed, 1),
		"started_at_epoch": round(wall0, 1),
		"unique_messages_sent": total,
		"requests_total": len(results),
		"resends": sum(1 for _m, _s, r in results if r),
		"customers": args.customers,
		"status_codes": codes,
		"client_errors": errors,
		"latency_ms_200_only": {
			"n": len(ok),
			"p50": round(percentile(ok, 0.50), 1),
			"p95": round(percentile(ok, 0.95), 1),
			"p99": round(percentile(ok, 0.99), 1),
			"max": round(ok[-1], 1) if ok else None,
			"mean": round(statistics.fmean(ok), 1) if ok else None,
		},
		"latency_ms_all_responses": {
			"p95": round(percentile(allms, 0.95), 1),
			"max": round(allms[-1], 1) if allms else None,
		},
		"unique_ids": sent_ids if args.dump_ids else len(sent_ids),
		"sender_prefix": args.sender_prefix,
		"run_id": args.run_id,
	}


def main() -> None:
	"""Đọc tham số, chạy, in và (tùy chọn) ghi kết quả JSON."""
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--site-url", required=True, help="vd http://127.0.0.1:8099")
	p.add_argument("--host", default="", help="giá trị header Host (tên site) nếu nhiều site")
	p.add_argument("--channel", required=True, help="tên Omni Channel (tham số ?ch=)")
	p.add_argument("--rate", type=float, required=True, help="tin duy nhất mỗi giây")
	p.add_argument("--duration", type=float, required=True, help="giây")
	p.add_argument("--customers", type=int, default=300)
	p.add_argument("--dup-ratio", type=float, default=0.1)
	p.add_argument("--resend-delay", type=float, default=1.0)
	p.add_argument("--timeout", type=float, default=30.0)
	p.add_argument("--max-connections", type=int, default=200)
	p.add_argument("--seed", type=int, default=1)
	p.add_argument("--run-id", default=str(int(time.time())))
	p.add_argument("--sender-prefix", default="LOADC")
	p.add_argument("--secret-env", default="OMNI_LOAD_OA_SECRET")
	p.add_argument("--app-id-env", default="OMNI_LOAD_APP_ID")
	p.add_argument("--dump-ids", action="store_true", help="ghi cả danh sách msg_id vào kết quả")
	p.add_argument("--out", default="", help="ghi kết quả JSON ra tệp")
	args = p.parse_args()
	result = asyncio.run(run(args))
	text = json.dumps(result, ensure_ascii=False, indent=1)
	print(text)
	if args.out:
		with open(args.out, "w", encoding="utf-8") as f:
			f.write(text)


if __name__ == "__main__":
	main()
