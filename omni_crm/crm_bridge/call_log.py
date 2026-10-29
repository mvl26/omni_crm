"""Ghi CRM Call Log từ Omni Call (tên trường đã xác nhận ở docs/12 mục C, G1).

CRM Call Log đặt tên theo trường `id` (autoname `field:id`); ta đặt `id` ổn định từ nhà cung cấp và mã cuộc
gọi nên chạy lại không tạo bản thứ hai. Tab Calls của CRM lọc theo `reference_docname` (và bảng `links`), vì
vậy tham chiếu trỏ tới Deal nếu Lead đã chuyển (như dòng thời gian, Q10), ngược lại trỏ tới Lead.
"""

from __future__ import annotations

import hashlib
from typing import Any

import frappe
from frappe.utils import cint

CALL_LOG = "CRM Call Log"
# Trạng thái Omni Call -> CRM Call Log.status. Voicemail: khách để lại lời nhắn, không ai nghe máy -> No Answer.
STATUS_MAP = {
	"Answered": "Completed",
	"Missed": "No Answer",
	"Busy": "Busy",
	"Failed": "Failed",
	"Voicemail": "No Answer",
}
TYPE_MAP = {"Inbound": "Incoming", "Outbound": "Outgoing"}
MAX_ID_LEN = 140


def call_log_id(provider: str | None, call_id: str) -> str:
	"""`id` ổn định của CRM Call Log: "{provider}-{call_id}", hoặc băm nếu quá dài."""
	raw = f"{(provider or 'tel').lower()}-{call_id}"
	return raw if len(raw) <= MAX_ID_LEN else "omni-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _reference(lead: str | None, deal: str | None) -> tuple[str | None, str | None]:
	if deal:
		return "CRM Deal", deal
	if lead:
		return "CRM Lead", lead
	return None, None


def upsert_call_log(call: Any, lead: str | None, deal: str | None = None) -> str:
	"""Tạo hoặc cập nhật CRM Call Log cho một Omni Call đã có trạng thái cuối; trả tên CRM Call Log.

	Không commit. `telephony_medium` giữ mặc định của CRM (Manual) vì Select cố định chưa có nhà cung cấp của
	ta; tên nhà cung cấp ghi ở `medium`. `recording_url` chưa đặt: ghi âm là tệp private ở Đợt 3.
	"""
	name = call_log_id(call.provider, call.call_id)
	incoming = call.direction == "Inbound"
	ref_dt, ref_name = _reference(lead, deal)
	values = {
		"from": call.from_number or "unknown",
		"to": call.to_number or "unknown",
		"type": TYPE_MAP.get(call.direction, "Incoming"),
		"status": STATUS_MAP.get(call.status, "Completed"),
		"start_time": call.started_at or call.answered_at,
		"end_time": call.ended_at,
		"duration": cint(call.duration_seconds),
		"medium": call.provider or None,
		"receiver": call.agent if incoming else None,
		"caller": None if incoming else call.agent,
		"reference_doctype": ref_dt,
		"reference_docname": ref_name,
	}
	if frappe.db.exists(CALL_LOG, name):
		doc = frappe.get_doc(CALL_LOG, name)
		doc.update(values)
	else:
		doc = frappe.get_doc({"doctype": CALL_LOG, "id": name, **values})
	doc.flags.ignore_permissions = True
	# Không thêm hàng vào bảng `links`: get_linked_calls của CRM gộp cả hai nguồn nên cuộc gọi sẽ hiện hai lần.
	if doc.name:
		doc.save()
	else:
		doc.insert()
	return doc.name
