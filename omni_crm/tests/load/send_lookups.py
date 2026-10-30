"""Đo tra cứu khách khi chuông reo (telephony_lookup): thời gian phản hồi và nội dung. CHẠY NGOÀI BENCH, chỉ cần `httpx`.

	export OMNI_LOAD_TEL_TOKEN=...
	python send_lookups.py --site-url http://127.0.0.1:8099 --host omni.test --channel "LOAD tong dai" \
		--numbers 84907700001,84907700002,84900000000 --requests 300 --concurrency 20

Mỗi yêu cầu GET `from` luân phiên trong danh sách số. Đếm thêm số yêu cầu không phải HTTP 200 hoặc chậm hơn
`--budget-ms` (mặc định 500 ms: ngân sách 0,3 giây của code cộng độ trễ HTTP). In JSON; token không bao giờ được in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time

import httpx

PATH = "/api/method/omni_crm.api.webhooks.telephony_lookup"


def pct(vals: list[float], p: float) -> float | None:
	"""Phân vị p (0..1), hạng gần nhất."""
	if not vals:
		return None
	vals = sorted(vals)
	return round(vals[max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))], 1)


async def main_async(a: argparse.Namespace) -> dict:
	token = os.environ.get(a.token_env)
	if not token:
		sys.exit(f"Thiếu biến môi trường {a.token_env}")
	numbers = a.numbers.split(",")
	sem = asyncio.Semaphore(a.concurrency)
	out: list[tuple[float, int, bool]] = []
	headers = {"Host": a.host} if a.host else {}
	async with httpx.AsyncClient(timeout=a.timeout, headers=headers) as client:

		async def one(i: int) -> None:
			async with sem:
				t = time.perf_counter()
				try:
					r = await client.get(
						a.site_url.rstrip("/") + PATH,
						params={
							"ch": a.channel,
							"t": token,
							"from": numbers[i % len(numbers)],
							"to": a.hotline,
						},
					)
					found = False
					try:
						found = bool(json.loads(r.content).get("found"))
					except ValueError:
						pass
					out.append(((time.perf_counter() - t) * 1000, r.status_code, found))
				except Exception:
					out.append(((time.perf_counter() - t) * 1000, 0, False))

		await asyncio.gather(*(one(i) for i in range(a.requests)))
	ms = [x[0] for x in out]
	return {
		"requests": len(out),
		"status": {str(s): sum(1 for x in out if x[1] == s) for s in {x[1] for x in out}},
		"found": sum(1 for x in out if x[2]),
		"ms": {"p50": pct(ms, 0.5), "p95": pct(ms, 0.95), "p99": pct(ms, 0.99), "max": round(max(ms), 1)},
		"over_budget": sum(1 for v in ms if v > a.budget_ms),
		"budget_ms": a.budget_ms,
	}


def main() -> None:
	"""Đọc tham số, chạy và in JSON."""
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--site-url", required=True)
	p.add_argument("--host", default="")
	p.add_argument("--channel", required=True)
	p.add_argument("--numbers", required=True)
	p.add_argument("--hotline", default="02873009999")
	p.add_argument("--requests", type=int, default=300)
	p.add_argument("--concurrency", type=int, default=20)
	p.add_argument("--budget-ms", type=float, default=500.0)
	p.add_argument("--timeout", type=float, default=10.0)
	p.add_argument("--token-env", default="OMNI_LOAD_TEL_TOKEN")
	a = p.parse_args()
	print(json.dumps(asyncio.run(main_async(a)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
	main()
