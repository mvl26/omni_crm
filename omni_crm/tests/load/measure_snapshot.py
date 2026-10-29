"""Đo thời gian monitor_snapshot với N tin vào giả (mặc định 100.000). KHÔNG nằm trong bộ run-tests.

Chạy trên SITE TEST:
	bench --site omni.test execute omni_crm.tests.load.measure_snapshot.run
Dữ liệu giả dùng kênh có tiền tố PLTLOAD và được xóa ở cuối (kể cả khi lỗi).
"""

from __future__ import annotations

import random
import statistics
import time

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.monitoring import alerts, snapshot

PREFIX = "PLTLOAD"
CHUNK = 5000


def _seed(n: int, channel: str) -> None:
	now = now_datetime()
	fields = [
		"name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
		"dedupe_key", "channel", "channel_type", "event_type", "external_message_id", "external_sender_id",
		"identity_key", "received_at", "text", "status", "attempts",
	]  # fmt: skip
	rng = random.Random(7)
	for start in range(0, n, CHUNK):
		rows = []
		for i in range(start, min(n, start + CHUNK)):
			age = rng.randint(0, 3 * 24 * 3600)  # rải trong 3 ngày
			created = add_to_date(now, seconds=-age)
			r = rng.random()
			status = (
				"Done"
				if r < 0.9
				else "Ignored"
				if r < 0.95
				else "New"
				if r < 0.97
				else "Retry"
				if r < 0.99
				else "Manual"
			)
			done_after = rng.choice([2, 3, 5, 8, 20, 60])
			modified = add_to_date(created, seconds=done_after) if status == "Done" else created
			rows.append(
				[
					f"{PREFIX}{i:07d}", created, modified, "Administrator", "Administrator", 0, 0,
					f"{PREFIX}-d-{i}", channel, "Zalo OA", "message", f"{PREFIX}m{i}", f"{PREFIX}U{i % 5000}",
					f"Zalo OA:{PREFIX}U{i % 5000}", created, "x", status, 0,
				]
			)  # fmt: skip
		frappe.db.bulk_insert("Omni Inbound Message", fields, rows, ignore_duplicates=True)
		frappe.db.commit()


def _cleanup() -> None:
	frappe.db.rollback()
	frappe.db.sql("delete from `tabOmni Inbound Message` where name like %s", f"{PREFIX}%")
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.commit()


def _time(fn, runs: int = 7) -> list[float]:
	out = []
	for _ in range(runs):
		t = time.perf_counter()
		fn()
		out.append((time.perf_counter() - t) * 1000)
	return out


def run(n: int = 100000) -> None:
	"""Gieo n tin, đo build_snapshot và evaluate, in số đo (ms), rồi dọn."""
	n = int(n)
	site = frappe.local.site
	assert "test" in site, f"Chỉ chạy trên site test, đang ở {site}"
	_cleanup()
	try:
		ch = frappe.get_doc(
			{
				"doctype": "Omni Channel",
				"channel_name": f"{PREFIX} kenh",
				"channel_type": "Zalo OA",
				"zalo_app_id": "a",
				"enabled": 0,
			}
		).insert()
		frappe.db.commit()
		t = time.perf_counter()
		_seed(n, ch.name)
		total = frappe.db.count("Omni Inbound Message")
		print(f"gieo {n} tin trong {time.perf_counter() - t:.1f}s; tổng bảng hiện {total}")
		snapshot.build_snapshot()  # làm nóng
		times = _time(snapshot.build_snapshot)
		print(
			"build_snapshot ms:",
			[round(x) for x in times],
			f"trung vị={statistics.median(times):.0f} max={max(times):.0f}",
		)
		snap = snapshot.build_snapshot()
		print("queue:", snap["queue"], "timing:", snap["timing"])
		for label, sql in (
			("count theo status", "select status, count(*) from `tabOmni Inbound Message` group by status"),
			("min creation New", "select min(creation) from `tabOmni Inbound Message` where status='New'"),
			(
				"count Done 24h",
				"select count(*) from `tabOmni Inbound Message` where status='Done' and modified >= now() - interval 1 day",
			),
			(
				"max received_at theo kênh",
				"select channel, max(received_at) from `tabOmni Inbound Message` group by channel",
			),
		):
			tm = _time(lambda s=sql: frappe.db.sql(s))
			print(f"  {label}: median {statistics.median(tm):.0f} ms")
		ev = _time(lambda: alerts.evaluate(snap))
		print(f"evaluate ms: median {statistics.median(ev):.1f}")
	finally:
		_cleanup()
		print(
			"đã dọn; còn lại tin PLTLOAD:",
			frappe.db.count("Omni Inbound Message", {"channel": ["like", f"{PREFIX}%"]}),
		)
