"""Điểm nối của kênh tổng đài vào worker (docs/07 mục 2.2 bước 1-4).

Định danh qua SĐT (tín hiệu mạnh) do resolver đảm nhiệm; ở đây chỉ ghi Omni Call, CRM Call Log và ánh xạ nhân
viên. Gọi nhỡ -> việc gọi lại làm ở D2-03, ghi âm ở Đợt 3.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from omni_crm.channels.telephony import calls
from omni_crm.crm_bridge import call_log


def _call_event(msg: Any):
	raw = frappe.db.get_value("Omni Inbound Message", msg.name, "raw_payload")
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
		return calls.deserialize_event(data["call_event"])
	except (TypeError, ValueError, KeyError):
		return None  # payload đã bị dọn theo hạn hoặc hỏng: không dựng lại được cuộc gọi


def after_resolve(res: Any, msgs: list[Any], channel_doc: Any) -> None:
	"""Với mỗi tin `call_ended`: upsert Omni Call rồi CRM Call Log gắn Lead/Deal. Idempotent; không commit."""
	for msg in msgs:
		if msg.event_type != "call_ended":
			continue
		ev = _call_event(msg)
		if ev is None:
			raise ValueError("Không đọc được sự kiện cuộc gọi từ payload gốc")
		name = calls.apply_final(channel_doc, ev, res.lead)
		call = frappe.get_doc(calls.CALL, name)
		log = call_log.upsert_call_log(call, res.lead, res.deal)
		if call.crm_call_log != log:
			frappe.db.set_value(calls.CALL, name, "crm_call_log", log, update_modified=False)
