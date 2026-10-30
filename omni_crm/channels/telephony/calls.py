"""Ghi Omni Call từ sự kiện cuộc gọi: sự kiện trung gian nối vào raw_events, sự kiện cuối ghi trạng thái cuối.

Mọi hàm idempotent: gọi lại cùng sự kiện không tạo bản ghi thứ hai và sự kiện trung gian đến muộn không ghi
đè trường của sự kiện kết thúc.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe

from omni_crm.channels.telephony.base import CallEvent
from omni_crm.identity.normalize import normalize_phone

CALL = "Omni Call"
MAX_RAW_EVENTS = 50  # giới hạn kích thước raw_events của một cuộc gọi


def serialize_event(ev: CallEvent) -> dict:
	"""CallEvent thành dict JSON được (thời gian dạng chuỗi), dùng cho raw_events và payload của tin vào."""
	out: dict[str, Any] = {}
	for key in (
		"call_id",
		"direction",
		"from_number",
		"to_number",
		"status",
		"started_at",
		"answered_at",
		"ended_at",
		"duration_seconds",
		"agent_ref",
		"recording_ref",
	):
		value = getattr(ev, key)
		out[key] = str(value) if isinstance(value, datetime) else value
	out["raw"] = ev.raw
	return out


def deserialize_event(data: dict) -> CallEvent:
	"""Ngược lại của serialize_event."""
	from frappe.utils import get_datetime

	def dt(key: str) -> datetime | None:
		return get_datetime(data[key]) if data.get(key) else None

	return CallEvent(
		call_id=str(data["call_id"]),
		direction=data.get("direction") or "Inbound",
		from_number=data.get("from_number") or "",
		to_number=data.get("to_number") or "",
		status=data.get("status") or "Ended",
		started_at=dt("started_at"),
		answered_at=dt("answered_at"),
		ended_at=dt("ended_at"),
		duration_seconds=data.get("duration_seconds"),
		agent_ref=data.get("agent_ref"),
		recording_ref=data.get("recording_ref"),
		raw=data.get("raw") or {},
	)


def _locked_name(call_id: str) -> str | None:
	return frappe.db.get_value(CALL, {"call_id": call_id}, "name", for_update=True)


def _merge_raw(current: Any, entry: dict) -> str:
	"""Nối `entry` vào danh sách raw_events (bỏ qua bản trùng nguyên văn, giữ MAX_RAW_EVENTS bản mới nhất)."""
	try:
		items = json.loads(current) if isinstance(current, str) and current else (current or [])
	except ValueError:
		items = []
	if not isinstance(items, list):
		items = []
	if entry not in items:
		items.append(entry)
	return json.dumps(items[-MAX_RAW_EVENTS:], ensure_ascii=False)


def get_or_create(channel_doc, ev: CallEvent) -> str:
	"""Tên Omni Call của `ev.call_id`, khóa dòng để hai yêu cầu song song không ghi đè nhau; tạo bản tạm nếu chưa có.

	Không `SELECT ... FOR UPDATE` trên call_id CHƯA có: khóa khoảng của InnoDB tương thích nhau nên hai worker tạo hai
	cuộc gọi khác nhau cùng giữ khóa rồi chờ nhau khi INSERT (deadlock 1213, đo được ở tải 10 cuộc/giây). Chỉ khóa khi
	hàng đã có (khóa bản ghi); chưa có thì INSERT thẳng, đụng khóa duy nhất mới quay lại khóa hàng của bên kia.
	"""
	if frappe.db.get_value(CALL, {"call_id": ev.call_id}, "name"):
		name = _locked_name(ev.call_id)
		if name:
			return name
	doc = frappe.get_doc(
		{
			"doctype": CALL,
			"call_id": ev.call_id,
			"provider": channel_doc.get("telephony_provider"),
			"direction": ev.direction if ev.direction in ("Inbound", "Outbound") else None,
			"from_number": ev.from_number,
			"to_number": ev.to_number,
			"started_at": ev.started_at,
			"raw_events": "[]",
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		return _locked_name(ev.call_id)  # yêu cầu song song vừa tạo xong
	return doc.name


def record_event(channel_doc, ev: CallEvent) -> str:
	"""Nối sự kiện vào `Omni Call.raw_events` (tạo bản tạm nếu chưa có); không đụng trường trạng thái."""
	name = get_or_create(channel_doc, ev)
	current = frappe.db.get_value(CALL, name, "raw_events")
	merged = _merge_raw(current, serialize_event(ev))
	if merged != (current or ""):
		frappe.db.set_value(CALL, name, "raw_events", merged, update_modified=False)
	return name


def map_agent(channel_doc, agent_ref: str | None) -> str | None:
	"""User CRM ứng với mã nhân viên/máy nhánh theo bảng Omni Agent Map của kênh; None nếu không có."""
	ref = (agent_ref or "").strip().lower()
	if not ref:
		return None
	rows = channel_doc.get("agent_map") or []
	for key in ("agent_ref", "extension"):
		for row in rows:
			if (row.get(key) or "").strip().lower() == ref and row.user:
				return row.user
	return None


def apply_final(channel_doc, ev: CallEvent, lead: str | None) -> str:
	"""Upsert Omni Call theo `call_id` với dữ liệu sự kiện kết thúc; trả tên Omni Call. Không commit."""
	name = get_or_create(channel_doc, ev)
	doc = frappe.get_doc(CALL, name)
	doc.flags.ignore_permissions = True
	doc.provider = channel_doc.get("telephony_provider") or doc.provider
	doc.direction = ev.direction if ev.direction in ("Inbound", "Outbound") else doc.direction
	doc.from_number = normalize_phone(ev.from_number) or ev.from_number or doc.from_number
	doc.to_number = normalize_phone(ev.to_number) or ev.to_number or doc.to_number
	doc.status = ev.final_status
	doc.started_at = ev.started_at or doc.started_at
	doc.answered_at = ev.answered_at or doc.answered_at
	doc.ended_at = ev.ended_at or doc.ended_at
	duration = ev.duration_seconds
	if duration is None and doc.answered_at and doc.ended_at:
		duration = max(0, int((doc.ended_at - doc.answered_at).total_seconds()))
	if duration is not None:
		doc.duration_seconds = duration
	doc.recording_ref = ev.recording_ref or doc.recording_ref
	doc.agent = map_agent(channel_doc, ev.agent_ref) or doc.agent
	if lead:
		doc.lead = lead
	doc.raw_events = _merge_raw(doc.raw_events, serialize_event(ev))
	doc.save()
	return doc.name
