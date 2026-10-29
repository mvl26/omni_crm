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


UNVERIFIED_COUNTER_KEY = "omni:telephony:unverified:{channel}"


def verify_missed_call(call_name: str, channel_doc: Any) -> bool | None:
	"""Xác minh `call_id` của cuộc gọi nhỡ qua API nhật ký của nhà cung cấp (docs/07 mục 3, đoạn cuối).

	GỌI NGOÀI khóa DB (có gọi HTTP): D2-03 phải gọi hàm này trước khi tạo việc gọi lại. Trả True (có thật),
	False (API trả lời nhưng không có call_id) hoặc None (không xác minh được / nhà cung cấp không hỗ trợ).
	False/None -> đặt `Omni Call.unverified = 1` và tăng bộ đếm cảnh báo trong cache; vẫn xử lý bình thường.
	"""
	from omni_crm.channels.telephony.registry import provider_for_channel

	verify = getattr(provider_for_channel(channel_doc), "call_exists", None)
	if verify is None:
		return None
	call = frappe.db.get_value(calls.CALL, call_name, ["call_id", "ended_at", "started_at"], as_dict=True)
	if not call:
		return None
	around = call.ended_at or call.started_at
	try:
		if around is None:
			raise ValueError("thiếu thời điểm cuộc gọi")
		result: bool | None = bool(verify(call.call_id, around, channel_doc))
	except Exception as e:
		frappe.logger("omni").warning("omni: khong xac minh duoc cuoc goi (%s)", type(e).__name__)
		result = None
	if result is not True:
		frappe.db.set_value(calls.CALL, call_name, "unverified", 1, update_modified=False)
		try:
			cache = frappe.cache()
			key = cache.make_key(UNVERIFIED_COUNTER_KEY.format(channel=channel_doc.name))
			cache.incr(key)
			cache.expire(key, 7 * 86400)
		except Exception:
			pass
	return result
