"""Tải webhook lead quảng cáo (V2-08): Meta + Google + form website trộn, gửi theo lịch cố định (open-loop). CHẠY NGOÀI BENCH.

	export OMNI_LOAD_META_SECRET=... OMNI_LOAD_GOOGLE_KEY=...   # của kênh/biểu mẫu LOADADS (dữ liệu tổng hợp)
	python send_ads_burst.py --site-url http://127.0.0.1:8099 --host omni.test --leads 200 --duration 600 \
		--resend-ratio 0.1 --out /tmp/ads.json

- `--leads` người duy nhất (mỗi người đúng một lần gửi gốc); `--resend-ratio` tỉ lệ gửi lại NGUYÊN payload sau `--resend-delay` giây
  (Meta/Google gửi lại; form website gửi lại cùng `submission_id`).
- `--duration` có thể NÉN so với 10 phút thật; kết quả ghi `duration_s` thật để người đọc biết đã nén hay chưa.
- Chữ ký Meta: HMAC-SHA256(raw, app_secret) theo công thức trong code (CHƯA đối chiếu Meta thật). Google: `google_key` trong body.
Không in bí mật. Dữ liệu sinh từ `ads_load_common` (tổng hợp).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
import random
import sys
import time

import httpx
from ads_load_common import CHANNELS, GOOGLE_FORMS, META_FORMS, META_PAGE, WEB_FORMS, person, platform_of

BASE = "/api/method/omni_crm.api.webhooks."


def percentile(vals: list[float], p: float) -> float:
	"""Phân vị p (0..1), phương pháp hạng gần nhất."""
	if not vals:
		return float("nan")
	vals = sorted(vals)
	return vals[max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))]


def build(i: int, platform: str, google_key: str) -> tuple[str, bytes, dict]:
	"""(đường dẫn, raw body, header bổ sung chưa có chữ ký) cho người thứ i."""
	p = person(i)
	if platform == "meta":
		body = {
			"object": "page",
			"entry": [
				{
					"id": META_PAGE,
					"time": int(time.time()),
					"changes": [
						{
							"field": "leadgen",
							"value": {
								"adgroup_id": f"LOADADSAD{i % 4}",
								"ad_id": f"LOADADSAD{i % 4}",
								"created_time": int(time.time()),
								"leadgen_id": f"LOADADS-M-{i}",
								"page_id": META_PAGE,
								"form_id": META_FORMS[i % 2],
							},
						}
					],
				}
			],
		}
		return "meta_leads", json.dumps(body).encode(), {}
	if platform == "google":
		body = {
			"lead_id": f"LOADADS-G-{i}",
			"api_version": "1.0",
			"form_id": GOOGLE_FORMS[i % 2],
			"campaign_id": "LOADADSGCAMP",
			"gcl_id": f"LOADADSGCLID{i}",
			"adgroup_id": "LOADADSGADG",
			"creative_id": "LOADADSGCRE",
			"is_test": False,
			"google_key": google_key,
			"lead_submit_time": "2026-11-02T03:00:00.000Z",
			"user_column_data": [
				{"column_id": "FULL_NAME", "string_value": p["name"]},
				{"column_id": "EMAIL", "string_value": p["email"]},
				{"column_id": "PHONE_NUMBER", "string_value": p["phone"]},
				{"column_id": "COMPANY_NAME", "string_value": p["company"]},
			],
		}
		return "google_leads", json.dumps(body, ensure_ascii=False).encode(), {}
	body = {
		"form": WEB_FORMS[0],
		"submission_id": f"loadads-w-{i:06d}",
		"full_name": p["name"],
		"phone": p["phone"],
		"email": p["email"],
		"company": p["company"],
		"utm_source": "google",
		"utm_medium": "cpc",
		"utm_campaign": "loadads-chien-dich",
		"landing_page": "https://example.invalid/landing",
	}
	return "web_form", json.dumps(body, ensure_ascii=False).encode(), {}


async def run(a: argparse.Namespace) -> dict:
	meta_secret = os.environ.get("OMNI_LOAD_META_SECRET")
	gkey = os.environ.get("OMNI_LOAD_GOOGLE_KEY")
	if not meta_secret or not gkey:
		sys.exit("Thiếu OMNI_LOAD_META_SECRET / OMNI_LOAD_GOOGLE_KEY")
	rng = random.Random(a.seed)
	client = httpx.AsyncClient(timeout=a.timeout, limits=httpx.Limits(max_connections=a.max_connections))
	results: list[tuple[str, float, int, bool]] = []
	sent: list[dict] = []
	headers_base = {"Content-Type": "application/json"}
	if a.host:
		headers_base["Host"] = a.host

	async def fire(i: int, platform: str, endpoint: str, raw: bytes, resend: bool) -> None:
		h = dict(headers_base)
		if platform == "meta":
			h["X-Hub-Signature-256"] = (
				"sha256=" + hmac.new(meta_secret.encode(), raw, hashlib.sha256).hexdigest()
			)
		t = time.perf_counter()
		try:
			r = await client.post(
				a.site_url.rstrip("/") + BASE + endpoint,
				params={"ch": CHANNELS["web" if platform == "web" else platform]},
				content=raw,
				headers=h,
			)
			code = r.status_code
		except Exception:
			code = 0
		results.append((platform, (time.perf_counter() - t) * 1000, code, resend))

	async def one(n: int, start: float) -> None:
		await asyncio.sleep(max(0.0, start + n * a.duration / a.leads - time.perf_counter()))
		i = a.first + n
		platform = a.only or platform_of(i)
		endpoint, raw, _ = build(i, platform, gkey)
		sent.append({"i": i, "platform": platform})
		await fire(i, platform, endpoint, raw, False)
		if rng.random() < a.resend_ratio:
			await asyncio.sleep(a.resend_delay)
			await fire(i, platform, endpoint, raw, True)

	start = time.perf_counter()
	t_wall = time.time()
	await asyncio.gather(*(one(n, start) for n in range(a.leads)))
	elapsed = time.perf_counter() - start
	await client.aclose()
	ok = [ms for _p, ms, c, _r in results if c == 200]
	codes: dict[str, int] = {}
	for _p, _m, c, _r in results:
		codes[str(c)] = codes.get(str(c), 0) + 1
	per = {}
	for pl in ("meta", "google", "web"):
		v = [ms for p, ms, c, _r in results if p == pl and c == 200]
		per[pl] = {"n": len(v), "p95": round(percentile(v, 0.95), 1) if v else None}
	return {
		"leads_unique": a.leads,
		"duration_s_planned": a.duration,
		"elapsed_s": round(elapsed, 1),
		"started_at_epoch": round(t_wall, 1),
		"requests_total": len(results),
		"resends": sum(1 for r in results if r[3]),
		"status_codes": codes,
		"mix": {pl: sum(1 for s in sent if s["platform"] == pl) for pl in ("meta", "google", "web")},
		"latency_ms_200_only": {
			"p50": round(percentile(ok, 0.5), 1),
			"p95": round(percentile(ok, 0.95), 1),
			"p99": round(percentile(ok, 0.99), 1),
			"max": round(max(ok), 1) if ok else None,
		},
		"per_platform": per,
	}


def main() -> None:
	"""Đọc tham số, chạy, in kết quả JSON."""
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--site-url", required=True)
	p.add_argument("--host", default="")
	p.add_argument("--leads", type=int, default=200)
	p.add_argument(
		"--first", type=int, default=1, help="chỉ số người đầu tiên (tránh trùng giữa các lần chạy)"
	)
	p.add_argument("--duration", type=float, default=600)
	p.add_argument("--resend-ratio", type=float, default=0.1)
	p.add_argument("--resend-delay", type=float, default=1.0)
	p.add_argument("--timeout", type=float, default=30.0)
	p.add_argument("--max-connections", type=int, default=100)
	p.add_argument(
		"--only", choices=("meta", "google", "web"), default="", help="chỉ gửi một nền tảng (kịch bản sự cố)"
	)
	p.add_argument("--seed", type=int, default=1)
	p.add_argument("--out", default="")
	a = p.parse_args()
	result = asyncio.run(run(a))
	text = json.dumps(result, ensure_ascii=False, indent=1)
	print(text)
	if a.out:
		open(a.out, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
	main()
