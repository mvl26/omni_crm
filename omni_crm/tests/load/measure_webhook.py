"""Đo p50/p95 của webhook với DB thật, commit thật và Redis THẬT (không mock enqueue/commit). Không thuộc run-tests.

Cần một Redis tạm (KHÔNG dùng Redis queue của bench):
	redis-server --port 11099 --save "" --appendonly no --daemonize yes --dir <thư mục tạm>
	OMNI_LOAD_REDIS=redis://127.0.0.1:11099 bench --site omni.test execute omni_crm.tests.load.measure_webhook.run
Adapter giả (không tính chữ ký Zalo thật). Dữ liệu tạo có tiền tố PLTWH và được xóa ở cuối.
"""

from __future__ import annotations

import os
import time

import frappe

from omni_crm.channels.registry import register_adapter, unregister_adapter
from omni_crm.tests.fake_adapter import FakeAdapter, make_channel
from omni_crm.tests.test_webhooks import body_of, fake_request

PREFIX = "PLTWH"


def _cleanup() -> None:
	frappe.db.rollback()
	frappe.db.delete("Omni Inbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Identity", {"identity_key": ["like", f"%{PREFIX}%"]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.commit()


def run(n: int = 200) -> None:
	"""Gọi webhooks.zalo() n lần và in p50/p95/max (ms)."""
	from omni_crm.api import webhooks

	url = os.environ.get("OMNI_LOAD_REDIS")
	assert url and ":11000" not in url, "Đặt OMNI_LOAD_REDIS tới Redis tạm (không phải cổng 11000)"
	frappe.conf.redis_queue = url
	import frappe.utils.background_jobs as bj

	bj.redis_connection = None
	_cleanup()
	register_adapter("Zalo OA", FakeAdapter())
	try:
		make_channel(f"{PREFIX} kenh", enabled=1)
		frappe.db.commit()
		times = []
		for i in range(int(n)):
			frappe.local.request = fake_request(body_of(f"{PREFIX}m{i}"))
			frappe.local.form_dict = frappe._dict(ch=f"{PREFIX} kenh")
			frappe.local.response = frappe._dict()
			t = time.perf_counter()
			webhooks.zalo()
			frappe.db.commit()  # framework commit sau request
			times.append((time.perf_counter() - t) * 1000)
			assert frappe.local.response.get("http_status_code", 200) == 200
		times.sort()
		print(
			f"webhook (DB+Redis thật, {n} request): p50={times[len(times) // 2]:.1f} ms p95={times[int(len(times) * 0.95) - 1]:.1f} ms max={times[-1]:.1f} ms"
		)
	finally:
		unregister_adapter("Zalo OA")
		_cleanup()
