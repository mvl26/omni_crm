"""Tác vụ định kỳ: đưa lại tin kẹt, dọn payload thô."""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from omni_crm import flags
from omni_crm.ingest.gateway import enqueue_identity
from omni_crm.pipeline.outbound import enqueue_outbound
from omni_crm.pipeline.retry import TransientError, mark_retry

INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
NEW_ORPHAN_MINUTES = 2
OUTBOUND_ORPHAN_MINUTES = 5
COUNTS_CACHE_KEY = "omni:sweeper:counts"
COUNTS_TTL = 3600


def requeue_stuck() -> dict[str, int]:
	"""Quét mỗi 5 phút: Processing kẹt -> Retry; Retry đến hạn và New mồ côi -> enqueue (docs/03 mục 6)."""
	now = now_datetime()
	counts = {
		"stuck": _requeue_stuck_processing(now),
		"retry_due": 0,
		"new_orphan": 0,
		"outbound": requeue_stuck_outbound(),
	}
	retry_keys = _keys({"status": "Retry", "next_attempt_at": ["<=", now]})
	counts["retry_due"] = _enqueue_all(retry_keys)
	orphan_keys = _keys({"status": "New", "creation": ["<", add_to_date(now, minutes=-NEW_ORPHAN_MINUTES)]})
	counts["new_orphan"] = _enqueue_all(orphan_keys)
	counts["checked_at"] = str(now)
	frappe.cache().set_value(COUNTS_CACHE_KEY, counts, expires_in_sec=COUNTS_TTL)
	return counts


def _requeue_stuck_processing(now) -> int:
	minutes = cint(frappe.db.get_single_value("Omni Settings", "stuck_after_minutes")) or 10
	rows = frappe.get_all(
		INBOUND,
		filters={"status": "Processing", "processing_started_at": ["<", add_to_date(now, minutes=-minutes)]},
		fields=["name"],
	)
	for row in rows:
		mark_retry([row], TransientError("Processing kẹt quá hạn"))
		frappe.db.commit()  # một đơn vị việc = một tin
	return len(rows)


def _keys(filters: dict) -> list[str]:
	"""identity_key khác nhau của các tin khớp bộ lọc, chỉ ở kênh đang bật."""
	rows = frappe.get_all(
		INBOUND, filters=filters, fields=["identity_key", "channel"], limit_page_length=1000
	)
	seen: dict[str, None] = {}
	for r in rows:
		if r.identity_key and flags.channel_enabled(r.channel):
			seen[r.identity_key] = None
	return list(seen)


def _enqueue_all(identity_keys: list[str]) -> int:
	for key in identity_keys:
		# after_commit=False: tác vụ nền không có commit tiếp theo cho callback.
		enqueue_identity(key, after_commit=False)
	return len(identity_keys)


def requeue_stuck_outbound() -> int:
	"""Mục 4 docs/03: tin ra Queued đến hạn thử lại (hoặc hết hạn thuê), hoặc cũ hơn 5 phút mà chưa ai nhận -> enqueue.

	send_outbound tự quyết (an toàn gửi trùng): tin có dấu "đang gửi" quá hạn bị chuyển Failed chứ không gửi lại.
	Kênh đã tắt chiều ra: send_outbound chuyển tin sang Blocked (kèm lý do) thay vì để Queued mãi.
	"""
	now = now_datetime()
	due = frappe.get_all(
		OUTBOUND,
		# frappe coi NULL là 0001-01-01 khi so sánh ngày giờ: phải thêm "is set" để loại tin chưa có hạn
		filters=[
			[OUTBOUND, "status", "=", "Queued"],
			[OUTBOUND, "next_attempt_at", "is", "set"],
			[OUTBOUND, "next_attempt_at", "<=", now],
		],
		pluck="name",
		limit_page_length=500,
	)
	orphans = frappe.get_all(
		OUTBOUND,
		filters={
			"status": "Queued",
			"next_attempt_at": ["is", "not set"],
			"creation": ["<", add_to_date(now, minutes=-OUTBOUND_ORPHAN_MINUTES)],
		},
		pluck="name",
		limit_page_length=500,
	)
	names = list(dict.fromkeys(due + orphans))
	for name in names:
		enqueue_outbound(name, after_commit=False)
	return len(names)
