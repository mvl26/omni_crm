"""API mỏng cho tab "Zalo" trong trang Lead/Deal của CRM (V3-01, ADR 0016).

Giao diện (patch frontend của crm) chỉ gọi 3 hàm này. Toàn bộ dữ liệu và guard gửi nằm ở `api/inbox.py`
(`thread_data`, `send_zalo`, `compose_state`): file này chỉ đổi Lead/Deal thành Lead, kiểm quyền theo CẢ trang đang xem
lẫn Lead gốc, lọc riêng kênh Zalo OA và rút gọn trường trả về. Không bao giờ gửi qua kênh khác (email, tổng đài).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm import flags
from omni_crm.api import inbox
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.pipeline import outbound as outbound_pipeline

LEAD = "CRM Lead"
DEAL = "CRM Deal"
CHANNEL = "Zalo OA"
MESSAGE_DOCTYPES = ("Omni Inbound Message", "Omni Outbound Message")


@frappe.whitelist()
def tab_state() -> dict:
	"""Có hiện tab Zalo cho người dùng này không (omni_crm đã cài vì hàm này tồn tại; công tắc `zalo_tab_disabled`)."""
	return {"enabled": bool(flags.zalo_tab_enabled() and frappe.session.user != "Guest")}


def resolve_lead(doctype: str, name: str, ptype: str = "read") -> str | None:
	"""Tên Lead chứa hội thoại của Lead/Deal `name`; ném lỗi nếu không tồn tại hoặc không đủ quyền `ptype` trên chính tài liệu.

	Deal không có Lead gốc (tạo tay) thì trả None. Quyền trên Lead gốc do hàm gọi kiểm tiếp (xem `inbox`).
	"""
	if doctype not in (LEAD, DEAL):
		frappe.throw(_("Chỉ hỗ trợ Lead và Deal"))
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("Không tìm thấy tài liệu"), frappe.DoesNotExistError)
	if not frappe.has_permission(doctype, ptype, doc=name):
		frappe.throw(_("Bạn không có quyền với tài liệu này"), frappe.PermissionError)
	lead = name if doctype == LEAD else frappe.db.get_value(DEAL, name, "lead")
	if not lead or not frappe.db.exists(LEAD, lead):
		return None
	return lead_bridge.follow_merged(lead) or lead


def _http_only(attachments: list[dict]) -> list[dict]:
	return [a for a in attachments if str(a.get("url") or "").startswith(("http://", "https://"))]


def _message(item: dict) -> dict:
	return {
		"name": item["name"],
		"kind": item.get("kind") or ("customer" if item["direction"] == "in" else "sale"),
		"direction": item["direction"],
		"sender": item.get("sender") or "",
		"text": item.get("text") or "",
		"attachments": _http_only(item.get("attachments") or []),
		"at": item["at"],
		"status": item.get("status"),
		"queued": bool(item.get("queued")),
		"sandbox": bool(item.get("is_sandbox")),
		"error": item.get("error"),
		"bot_sent": bool(item.get("bot_sent")),
	}


def _contact(lead: str, data: dict) -> dict | None:
	"""Thông tin để nhân viên tự tìm khách trong trang chat Zalo OA chính thức (Zalo không mở thẳng hội thoại theo tham số).

	Chỉ gồm tên khách (Lead, định danh) và mã người dùng Zalo; None khi Lead không có định danh Zalo hoặc khách đã từ chối liên hệ.
	"""
	if (data.get("profile") or {}).get("person_refused"):
		return None
	rows = frappe.get_all(
		"Omni Identity",
		filters={"lead": lead, "channel_type": CHANNEL},
		fields=["external_id", "display_name"],
		order_by="last_inbound_at desc, creation desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	return {
		"lead_name": data["lead"].get("lead_name") or "",
		"display_name": rows[0].display_name or "",
		"zalo_user_id": rows[0].external_id or "",
	}


def _compose(data: dict, can_write: bool) -> dict:
	"""Trạng thái khung soạn: luôn có `locked` + `reason` khi không gửi được (không có định danh Zalo, từ chối liên hệ...)."""
	zalo = (data.get("compose") or {}).get("zalo")
	state: dict[str, Any] = {
		"can_send": False,
		"reason": "",
		"paid": False,
		"message": "",
		"sandbox": bool(flags.sandbox()),
		"max_chars": outbound_pipeline.MAX_TEXT_CHARS,
		"quota": None,
		"linked": zalo is not None,
	}
	if zalo is None:
		state["reason"] = _(
			"Khách này chưa có tài khoản Zalo liên kết với Lead nên không thể nhắn qua Zalo từ đây."
		)
	elif not can_write:
		state["reason"] = _("Bạn chỉ được xem hội thoại này, không có quyền trả lời.")
	elif (data.get("profile") or {}).get("person_refused"):
		state["reason"] = _(
			"Người này đã từ chối nhận liên hệ (hoặc đã bị xóa dữ liệu): không gửi tin nhắn từ hệ thống."
		)
	elif not zalo["can_send"]:
		state["reason"] = zalo.get("message") or _("Hiện chưa gửi được tin Zalo cho khách này.")
	else:
		state["can_send"] = True
		state["paid"] = bool(zalo["paid"])
		state["message"] = zalo.get("message") or ""
	if zalo is not None:
		state["quota"] = zalo.get("quota")
	return state


@frappe.whitelist()
def get_conversation(doctype: str, name: str) -> dict:
	"""Hội thoại Zalo OA của Lead/Deal đang xem (chỉ kênh Zalo OA) và trạng thái khung soạn."""
	lead = resolve_lead(doctype, name, "read")
	if not lead:
		return {
			"lead": None,
			"customer_name": "",
			"messages": [],
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
	zalo_names = [
		i["display_name"] for i in data["identities"] if i["channel_type"] == CHANNEL and i["display_name"]
	]
	return {
		"lead": lead,
		"customer_name": (zalo_names[0] if zalo_names else None) or data["lead"].get("lead_name") or "",
		"messages": rows,
		"contact": _contact(lead, data),
		"truncated": bool(data.get("truncated")),
		"compose": _compose(data, can_write),
	}


@frappe.whitelist(methods=["POST"])
def send(doctype: str, name: str, text: str, confirm_paid: int | str = 0) -> dict:
	"""Sale trả lời khách qua Zalo OA từ tab Zalo; luôn kênh Zalo OA, mọi guard của `inbox.send_zalo` giữ nguyên."""
	lead = resolve_lead(doctype, name, "write")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Zalo"))
	lead = inbox.check_lead_write(lead)
	return inbox.send_zalo(lead, text, confirm_paid)
