"""API mỏng cho tab "Messenger" trong trang Lead/Deal của CRM (ADR 0023), cùng khuôn với `api/zalo_tab.py` (ADR 0016).

Toàn bộ dữ liệu và guard gửi nằm ở `api/inbox.py` (`get_thread`, `send_messenger`, `compose_state`): file này đổi Lead/Deal
thành Lead, kiểm quyền theo CẢ trang đang xem lẫn Lead gốc, lọc riêng kênh Meta Messenger và rút gọn trường trả về.
Không gửi qua kênh khác. Khác Zalo: không có xác nhận phí, quỹ tin, thẻ xin số; đính kèm của khách là tệp đã lưu trong CRM.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm import flags
from omni_crm.api import inbox
from omni_crm.api.zalo_tab import resolve_lead
from omni_crm.channels.meta_messenger import media_out
from omni_crm.pipeline import outbound as outbound_pipeline
from omni_crm.utils import media

LEAD = "CRM Lead"
CHANNEL = "Meta Messenger"
MESSAGE_DOCTYPES = ("Omni Inbound Message", "Omni Outbound Message")
INBOX_URL = "https://business.facebook.com/latest/inbox/all/?asset_id={0}"


@frappe.whitelist()
def tab_state() -> dict:
	"""Có hiện tab Messenger cho người dùng này không (công tắc `messenger_tab_disabled`)."""
	return {"enabled": bool(flags.messenger_tab_enabled() and frappe.session.user != "Guest")}


def _visible_attachments(item: dict) -> list[dict]:
	"""Đính kèm hiện trên tab: đường dẫn an toàn (tệp khách đã chép vào File, tệp sale gửi) và tệp không lưu được (chỉ tên)."""
	out = []
	for a in item.get("attachments") or []:
		url = str(a.get("url") or "")
		if media.is_safe_link(url) or (not url and a.get("note")):
			out.append(a)
	return out


def _message(item: dict) -> dict:
	return {
		"name": item["name"],
		"kind": item.get("kind") or ("customer" if item["direction"] == "in" else "sale"),
		"direction": item["direction"],
		"sender": item.get("sender") or "",
		"text": item.get("text") or "",
		"attachments": _visible_attachments(item),
		"at": item["at"],
		"status": item.get("status"),
		"queued": bool(item.get("queued")),
		"sandbox": bool(item.get("is_sandbox")),
		"error": item.get("error"),
		"bot_sent": bool(item.get("bot_sent")),
	}


def _contact(lead: str, data: dict) -> dict | None:
	"""Tên khách, PSID, Page và liên kết hộp thư Meta Business Suite; None khi không có định danh Messenger hoặc khách từ chối."""
	if (data.get("profile") or {}).get("person_refused"):
		return None
	rows = frappe.get_all(
		"Omni Identity",
		filters={"lead": lead, "channel_type": CHANNEL},
		fields=["name", "external_id", "display_name"],
		order_by="last_inbound_at desc, creation desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	ident = rows[0]
	channel = frappe.db.get_value(
		"Omni Inbound Message", {"identity": ident.name}, "channel", order_by="received_at desc"
	) or frappe.db.get_value("Omni Channel", {"channel_type": CHANNEL, "enabled": 1}, "name")
	page_id = str(
		(frappe.db.get_value("Omni Channel", channel, "meta_page_id") if channel else "") or ""
	).strip()
	return {
		"lead_name": data["lead"].get("lead_name") or "",
		"display_name": ident.display_name or "",
		"psid": ident.external_id or "",
		"page_id": page_id,
		"inbox_url": INBOX_URL.format(page_id) if page_id.isdigit() else "",
		"name_note": ""
		if ident.display_name
		else _("Chưa lấy được tên Messenger (kênh chưa có token Page, hoặc Meta không trả tên)"),
	}


def _compose(data: dict, can_write: bool) -> dict:
	"""Trạng thái khung soạn: luôn có `reason` khi không gửi được."""
	ms = (data.get("compose") or {}).get("messenger")
	state: dict[str, Any] = {
		"can_send": False,
		"reason": "",
		"sandbox": bool(flags.sandbox()),
		"max_chars": outbound_pipeline.MAX_TEXT_CHARS,
		"linked": ms is not None,
		"can_send_media": False,
		"media_accept": media_out.ACCEPT,
		"media_max_mb": media_out.MAX_BYTES // media_out.MB,
	}
	if ms is None:
		state["reason"] = _(
			"Khách này chưa có tài khoản Messenger liên kết với Lead nên không thể nhắn qua Messenger từ đây."
		)
	elif not can_write:
		state["reason"] = _("Bạn chỉ được xem hội thoại này, không có quyền trả lời.")
	elif (data.get("profile") or {}).get("person_refused"):
		state["reason"] = _(
			"Người này đã từ chối nhận liên hệ (hoặc đã bị xóa dữ liệu): không gửi tin nhắn từ hệ thống."
		)
	elif not ms["can_send"]:
		state["reason"] = ms.get("message") or _("Hiện chưa gửi được tin Messenger cho khách này.")
	else:
		state["can_send"] = True
		state["can_send_media"] = bool(flags.messenger_media_send_enabled())
	return state


@frappe.whitelist()
def get_conversation(doctype: str, name: str) -> dict:
	"""Hội thoại Messenger của Lead/Deal đang xem (chỉ kênh Meta Messenger) và trạng thái khung soạn."""
	if not flags.messenger_tab_enabled():
		return {
			"lead": None,
			"customer_name": "",
			"contact": None,
			"messages": [],
			"bot": None,
			"truncated": False,
			"disabled": True,
			"compose": {**_compose({}, False), "reason": _("Tab Messenger đang bị tắt")},
		}
	lead = resolve_lead(doctype, name, "read")
	if not lead:
		return {
			"lead": None,
			"customer_name": "",
			"contact": None,
			"messages": [],
			"bot": None,
			"truncated": False,
			"compose": _compose({}, False),
		}
	data = inbox.get_thread(lead)  # kiểm quyền đọc Lead gốc theo quy tắc của Hộp thư
	can_write = bool(
		frappe.has_permission(LEAD, "write", doc=lead) and frappe.has_permission(doctype, "write", doc=name)
	)
	rows = [
		_message(i)
		for i in data["messages"]
		if i.get("channel_type") == CHANNEL and i.get("doctype") in MESSAGE_DOCTYPES
	]
	names = [
		i["display_name"] for i in data["identities"] if i["channel_type"] == CHANNEL and i["display_name"]
	]
	from omni_crm.api.zalo_tab import _bot

	return {
		"lead": lead,
		"customer_name": (names[0] if names else None) or data["lead"].get("lead_name") or "",
		"messages": rows,
		"contact": _contact(lead, data),
		"bot": _bot(lead, data["lead"]),
		"truncated": bool(data.get("truncated")),
		"compose": _compose(data, can_write),
	}


def _write_lead(doctype: str, name: str) -> str:
	if not flags.messenger_tab_enabled():
		frappe.throw(_("Tab Messenger đang bị tắt"), frappe.PermissionError)
	lead = resolve_lead(doctype, name, "write")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Messenger"))
	return inbox.check_lead_write(lead)


@frappe.whitelist(methods=["POST"])
def send(doctype: str, name: str, text: str) -> dict:
	"""Sale trả lời khách bằng chữ qua Messenger từ tab Messenger; mọi guard của `inbox.send_messenger` giữ nguyên."""
	return inbox.send_messenger(_write_lead(doctype, name), text)


@frappe.whitelist(methods=["POST"])
def send_attachment(doctype: str, name: str, file_url: str, text: str = "") -> dict:
	"""Sale gửi ảnh/video/âm thanh/tệp của Lead qua Messenger (ADR 0023). Tệp phải đã đính kèm vào Lead (hoặc Deal của Lead).

	Messenger không cho chữ đi cùng tệp: có chữ thì xếp tin chữ TRƯỚC, rồi tin tệp. Công tắc `messenger_media_send_enabled`.
	"""
	lead = _write_lead(doctype, name)
	if not flags.messenger_media_send_enabled():
		frappe.throw(_("Chưa bật gửi ảnh/tệp qua Messenger"), frappe.PermissionError)
	attachment = media_out.attachment_for_lead(file_url, lead)
	text = (text or "").strip()
	if text:
		first = inbox.send_messenger(lead, text)
		if first.get("status") != "Queued":
			return first
		result = inbox.send_messenger(lead, "", attachment=attachment)
		result["text_outbound"] = first.get("outbound")
		return result
	return inbox.send_messenger(lead, "", attachment=attachment)
