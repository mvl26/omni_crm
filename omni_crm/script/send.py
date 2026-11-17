"""Gửi tin kịch bản qua đường gửi chung (`pipeline.outbound.queue_outbound`, nguồn `Bot`).

Quy tắc an toàn số 1: `switch.enabled_for(identity)` sai thì KHÔNG tạo tin nào (trả None). Công tắc được kiểm lại ngay
trước khi gửi (`send_time_check`, gọi từ `bot.auto_send.send_time_check`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm.bot import handoff
from omni_crm.ingest.events import OutboundRequest
from omni_crm.omni_crm.doctype.omni_script_node.omni_script_node import validate_buttons
from omni_crm.pipeline.retry import redact
from omni_crm.script import store, switch

OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
INBOUND = "Omni Inbound Message"
SENDING_FLAG = "omni_script_sending"  # frappe.flags: định danh đang được send.py xếp tin (auto_send.assert_can_queue đọc)
TEXT_CODE = "_TEXT"  # script_node của tin gửi bằng send_text không kèm mã (câu Kho tri thức)
JUNK_STATUS = "Junk"  # CRM Lead Status coi là spam (không có trường spam riêng)


@contextmanager
def _sending(identity: str) -> Iterator[None]:
	frappe.flags[SENDING_FLAG] = identity
	try:
		yield
	finally:
		frappe.flags.pop(SENDING_FLAG, None)


def is_script_sending(identity: str | None) -> bool:
	"""Lời gọi `queue_outbound` hiện tại đến từ send.py cho đúng định danh này."""
	return bool(identity) and frappe.flags.get(SENDING_FLAG) == identity


def channel_of(identity: str) -> str | None:
	"""Kênh (Omni Channel) của định danh: kênh của tin vào gần nhất."""
	return frappe.db.get_value(INBOUND, {"identity": identity}, "channel", order_by="received_at desc")


def blocked_reason(identity: str | None) -> str | None:
	"""Lý do không được gửi tin kịch bản cho định danh lúc này (công tắc, tạm dừng, không liên hệ, spam); None nếu được."""
	if not switch.enabled_for(identity):
		return _("Bot kịch bản đang tắt với khách này")
	if handoff.is_bot_paused(identity):
		return _("Bot đang tạm dừng với khách này")
	lead = frappe.db.get_value(IDENTITY, identity, "lead")
	if lead:
		row = frappe.db.get_value("CRM Lead", lead, ["status", "omni_do_not_contact"], as_dict=True) or {}
		if cint(row.get("omni_do_not_contact")):
			return _("Lead đánh dấu không liên hệ")
		if row.get("status") == JUNK_STATUS:
			return _("Lead bị đánh dấu rác")
	return None


def send_node(identity: str, code: str, ctx: dict | None = None, *, purpose: str = "reply") -> str | None:
	"""Xếp tin của ô kịch bản `code` cho định danh; trả tên Omni Outbound Message hoặc None nếu không gửi."""
	if not switch.enabled_for(identity):
		return None
	node = store.get_node(code)
	rendered = store.render(node, {**(ctx or {}), "identity": identity})
	if not rendered:
		return None
	text, buttons = rendered
	return _queue(identity, text, buttons, node["code"], purpose, node_channel=node.get("channel"))


def send_text(
	identity: str, text: str, buttons: list[dict] | None = None, code: str | None = None
) -> str | None:
	"""Xếp một câu có sẵn (vd câu Kho tri thức) kèm nút tùy chọn; trả tên Omni Outbound Message hoặc None."""
	if not switch.enabled_for(identity) or not (text or "").strip():
		return None
	btns = [
		{"label": (b.get("label") or "").strip(), "target": store.normalize_code(b.get("target"))}
		for b in buttons or []
	]
	return _queue(identity, text.strip(), btns, store.normalize_code(code) or TEXT_CODE, "reply")


def _queue(
	identity: str,
	text: str,
	buttons: list[dict],
	code: str,
	purpose: str,
	node_channel: str | None = None,
) -> str | None:
	from omni_crm.pipeline import outbound  # import trễ: outbound -> auto_send -> (lazy) script.send

	try:
		validate_buttons([(b["label"], b["target"]) for b in buttons])
	except frappe.ValidationError:
		_log("nut_khong_hop_le", identity, code)
		return None
	if blocked_reason(identity):
		return None
	channel = channel_of(identity)
	if not channel:
		_log("khong_co_kenh", identity, code)
		return None
	channel_type = frappe.db.get_value("Omni Channel", channel, "channel_type")
	if node_channel and node_channel not in ("Tất cả", channel_type):
		return None
	req = OutboundRequest(channel, identity, text, None, "Bot", purpose=purpose)
	try:
		with _sending(identity):
			name = outbound.queue_outbound(req)
	except frappe.ValidationError as e:  # gồm RoutingError (ngoài khung gửi miễn phí...)
		frappe.db.rollback()
		_log(f"khong_xep_duoc:{type(e).__name__}", identity, code)
		return None
	frappe.db.set_value(
		OUTBOUND,
		name,
		{"script_node": code, "buttons": json.dumps(buttons, ensure_ascii=False) if buttons else None},
		update_modified=False,
	)
	frappe.db.set_value(IDENTITY, identity, "script_last_bot_at", now_datetime(), update_modified=False)
	frappe.db.commit()  # một tin = một đơn vị việc; job gửi chạy sau commit
	return name


def send_time_check(row: Any) -> tuple[bool, str]:
	"""Ngay trước khi gửi tin kịch bản đang chờ (từ `auto_send.send_time_check`): kiểm lại công tắc và điều kiện dừng."""
	why = blocked_reason(row.get("identity"))
	return (False, why) if why else (True, "")


def buttons_of(outbound_name: str | None) -> list[dict]:
	"""Danh sách nút đã ghi trên Omni Outbound Message (trống nếu không có)."""
	if not outbound_name:
		return []
	raw = frappe.db.get_value(OUTBOUND, outbound_name, "buttons")
	try:
		value = json.loads(raw) if raw else []
	except (TypeError, ValueError):
		return []
	return [b for b in value if isinstance(b, dict) and b.get("label") and b.get("target")]


def _log(kind: str, identity: str | None, code: str | None) -> None:
	"""Ghi log nội bộ (không có nội dung tin, không bí mật)."""
	frappe.logger("omni").info(
		"omni script: bỏ qua gửi (%s) identity=%s node=%s", kind, identity, redact(code or "")
	)
