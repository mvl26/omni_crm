"""Điểm nối của kênh tổng đài vào worker (docs/07 mục 2.2 bước 1-4).

Định danh qua SĐT (tín hiệu mạnh) do resolver đảm nhiệm; ở đây chỉ ghi Omni Call, CRM Call Log và ánh xạ nhân
viên. Gọi nhỡ -> việc gọi lại làm ở D2-03, ghi âm ở Đợt 3.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from omni_crm import flags
from omni_crm.channels.telephony import calls
from omni_crm.crm_bridge import call_log, first_response
from omni_crm.crm_bridge import task as task_bridge
from omni_crm.identity.normalize import normalize_phone


def _call_event(msg: Any):
	raw = frappe.db.get_value("Omni Inbound Message", msg.name, "raw_payload")
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
		return calls.deserialize_event(data["call_event"])
	except (TypeError, ValueError, KeyError):
		return None  # payload đã bị dọn theo hạn hoặc hỏng: không dựng lại được cuộc gọi


def _record(channel_doc: Any, ev: Any, lead: str | None, deal: str | None) -> Any:
	"""Upsert Omni Call + CRM Call Log của một sự kiện kết thúc; trả Omni Call."""
	name = calls.apply_final(channel_doc, ev, lead)
	call = frappe.get_doc(calls.CALL, name)
	log = call_log.upsert_call_log(call, lead, deal)
	if call.crm_call_log != log:
		frappe.db.set_value(calls.CALL, name, "crm_call_log", log, update_modified=False)
		call.crm_call_log = log
	first_response.on_call_recorded(call, lead)  # V2-01: cuộc gọi đi được nghe máy = phản hồi đầu tiên
	return call


def prepare_batch(msgs: list[Any], channel_doc: Any) -> list[Any]:
	"""Cuộc gọi ẩn số/số không chuẩn hóa được: chỉ ghi Omni Call + CRM Call Log, KHÔNG định danh, KHÔNG tạo/gắn Lead.

	Gộp mọi khách ẩn số vào một Lead chung là sai (khác khách, lẫn lịch sử, lẫn việc gọi lại); trả về các tin còn lại
	cho resolver. Idempotent; không commit.
	"""
	kept: list[Any] = []
	for msg in msgs:
		if msg.event_type != "call_ended":
			kept.append(msg)
			continue
		ev = _call_event(msg)
		if ev is None:
			raise ValueError("Không đọc được sự kiện cuộc gọi từ payload gốc")
		if normalize_phone(ev.customer_number):
			kept.append(msg)
			continue
		_record(channel_doc, ev, None, None)
	return kept


def after_resolve(res: Any, msgs: list[Any], channel_doc: Any) -> None:
	"""Với mỗi tin `call_ended`: upsert Omni Call rồi CRM Call Log gắn Lead/Deal; cuộc gọi nghe máy đóng việc gọi lại.

	Idempotent; không commit. Việc gọi lại cho cuộc gọi nhỡ KHÔNG tạo ở đây (xem callbacks.after_batch).
	"""
	for msg in msgs:
		if msg.event_type != "call_ended":
			continue
		ev = _call_event(msg)
		if ev is None:
			raise ValueError("Không đọc được sự kiện cuộc gọi từ payload gốc")
		call = _record(channel_doc, ev, res.lead, res.deal)
		if call.status == "Answered":
			task_bridge.close_callback_tasks(res.lead, call)


UNVERIFIED_COUNTER_KEY = "omni:telephony:unverified:{channel}"


def verify_missed_call(call_name: str, channel_doc: Any) -> bool | None:
	"""Xác minh `call_id` của cuộc gọi nhỡ qua API nhật ký của nhà cung cấp (docs/07 mục 3, đoạn cuối).

	GỌI NGOÀI khóa DB (có gọi HTTP): `callbacks.process_missed_call` gọi hàm này trước khi tạo việc gọi lại. Trả True
	(có thật), False (API trả lời nhưng không có call_id) hoặc None (không xác minh được / nhà cung cấp không hỗ
	trợ). False/None -> đặt `Omni Call.unverified = 1` và tăng bộ đếm cảnh báo trong cache; vẫn xử lý bình thường.
	Sandbox bật: không gọi nhà cung cấp, trả None và KHÔNG đánh dấu (không phải bất thường). Không commit.
	"""
	if flags.sandbox():
		return None
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
