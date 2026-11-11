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
from omni_crm.channels.zalo import worker_hooks
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.pipeline import outbound as outbound_pipeline
from omni_crm.utils import media

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


def _visible_attachments(item: dict) -> list[dict]:
	"""Đính kèm hiện trên tab Zalo: tin từ Zalo chỉ http(s) (V3-01); tin ra của hệ thống (V3-09, ảnh/tệp sale gửi kèm,
	dòng Omni Outbound Message) được thêm đường dẫn /files, /private/files của CRM."""
	attachments = item.get("attachments") or []
	if item.get("doctype") == inbox.OUTBOUND:
		return [a for a in attachments if media.is_safe_link(str(a.get("url") or ""))]
	return _http_only(attachments)


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
	"""Thông tin để mở đúng hội thoại trên trang chat Zalo OA chính thức và để nhân viên nhận ra khách.

	Gồm tên khách (Lead, định danh), mã người dùng Zalo, mã OA của kênh, và lý do chưa có tên Zalo (nếu thiếu). None khi Lead
	không có định danh Zalo hoặc khách đã từ chối liên hệ.
	"""
	if (data.get("profile") or {}).get("person_refused"):
		return None
	rows = frappe.get_all(
		"Omni Identity",
		filters={"lead": lead, "channel_type": CHANNEL},
		fields=["name", "identity_key", "external_id", "display_name"],
		order_by="last_inbound_at desc, creation desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	ident = rows[0]
	channel = frappe.db.get_value(
		"Omni Inbound Message", {"identity": ident.name}, "channel", order_by="received_at desc"
	) or frappe.db.get_value("Omni Channel", {"channel_type": CHANNEL, "enabled": 1}, "name")
	oa_id = (frappe.db.get_value("Omni Channel", channel, "zalo_oa_id") if channel else None) or ""
	return {
		"lead_name": data["lead"].get("lead_name") or "",
		"display_name": ident.display_name or "",
		"zalo_user_id": ident.external_id or "",
		"oa_id": str(oa_id).strip(),
		"name_note": "" if ident.display_name else _name_note(ident.identity_key),
	}


def _name_note(identity_key: str) -> str:
	""" "Chưa lấy được tên Zalo: <lý do>" cho khách chưa có tên định danh (lý do chỉ là thông điệp đã lọc, không bí mật)."""
	reason = worker_hooks.profile_reason(identity_key)
	if not reason:
		reason = (
			_("Đang ở chế độ thử (sandbox) nên không gọi Zalo để lấy tên")
			if flags.sandbox()
			else _("chưa có lần lấy hồ sơ nào hoặc Zalo không trả tên")
		)
	return _("Chưa lấy được tên Zalo: {0}").format(reason)


def _bot(lead: str, info: dict) -> dict | None:
	"""Thẻ tóm tắt của bot cho đầu tab Zalo: tóm tắt, ý định, mức gấp, điểm, giờ phân tích gần nhất; None khi bot chưa ghi gì.

	`info` là khối `lead` của `inbox.thread_data` (đã kiểm quyền đọc Lead, điểm đã qua `scoring.score_view`).
	"""
	summary = (info.get("omni_bot_summary") or "").strip()
	intent = info.get("omni_intent") or ""
	if not summary and not intent:
		return None
	analyzed_at = frappe.db.get_value(
		"Omni Bot Run",
		{"lead": lead, "kind": "Analyze", "status": "Success"},
		"creation",
		order_by="creation desc",
	)
	return {
		"summary": summary,
		"intent": intent,
		"urgency": frappe.db.get_value(LEAD, lead, "omni_urgency") or "",
		"score": info.get("omni_score"),
		"analyzed_at": analyzed_at,
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
		"can_request_phone": False,
		"phone_request_reason": "",
		"can_send_media": False,
		"media_accept": ".jpg,.jpeg,.png,.pdf,.doc,.docx,.csv",
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
		state["quota"] = zalo.get("quota")  # V3-07: chỉ có khi khách đã ngoài 48 giờ
		state["can_request_phone"] = bool(can_write and zalo.get("can_request_phone"))
		state["phone_request_reason"] = zalo.get("phone_request_reason") or ""
		state["can_send_media"] = bool(state["can_send"] and zalo.get("can_send_media"))
	return state


@frappe.whitelist()
def get_conversation(doctype: str, name: str) -> dict:
	"""Hội thoại Zalo OA của Lead/Deal đang xem (chỉ kênh Zalo OA) và trạng thái khung soạn."""
	if not flags.zalo_tab_enabled():  # công tắc tắt thật ở máy chủ, không chỉ ẩn tab ở giao diện
		return {
			"lead": None,
			"customer_name": "",
			"contact": None,
			"messages": [],
			"truncated": False,
			"disabled": True,
			"compose": {**_compose({}, False), "reason": _("Tab Zalo đang bị tắt")},
		}
	lead = resolve_lead(doctype, name, "read")
	if not lead:
		return {
			"lead": None,
			"customer_name": "",
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
	zalo_names = [
		i["display_name"] for i in data["identities"] if i["channel_type"] == CHANNEL and i["display_name"]
	]
	return {
		"lead": lead,
		"customer_name": (zalo_names[0] if zalo_names else None) or data["lead"].get("lead_name") or "",
		"messages": rows,
		"contact": _contact(lead, data),
		"bot": _bot(lead, data["lead"]),
		"truncated": bool(data.get("truncated")),
		"compose": _compose(data, can_write),
	}


@frappe.whitelist(methods=["POST"])
def send(doctype: str, name: str, text: str, confirm_paid: int | str = 0) -> dict:
	"""Sale trả lời khách qua Zalo OA từ tab Zalo; luôn kênh Zalo OA, mọi guard của `inbox.send_zalo` giữ nguyên."""
	if not flags.zalo_tab_enabled():
		frappe.throw(_("Tab Zalo đang bị tắt"), frappe.PermissionError)
	lead = resolve_lead(doctype, name, "write")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Zalo"))
	lead = inbox.check_lead_write(lead)
	return inbox.send_zalo(lead, text, confirm_paid)


@frappe.whitelist(methods=["POST"])
def send_attachment(
	doctype: str, name: str, file_url: str, text: str = "", confirm_paid: int | str = 0
) -> dict:
	"""Sale gửi ảnh (JPG/PNG; GIF Zalo chưa nhận) hoặc tệp (PDF/DOC/DOCX/CSV) của Lead qua Zalo OA từ tab Zalo (V3-09).

	Tệp phải đã đính kèm vào Lead (hoặc Deal của Lead) trong CRM. Ảnh: chữ đi cùng tin làm chú thích. Tệp: Zalo không cho
	kèm chữ nên chữ được xếp hàng thành một tin riêng TRƯỚC tệp. Mọi guard của `inbox.send_zalo` giữ nguyên.
	"""
	from omni_crm.channels.zalo import media_out

	if not flags.zalo_tab_enabled():
		frappe.throw(_("Tab Zalo đang bị tắt"), frappe.PermissionError)
	lead = resolve_lead(doctype, name, "write")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Zalo"))
	lead = inbox.check_lead_write(lead)
	att = media_out.attachment_for_lead(file_url, lead)
	attachment = {"file_url": att.file_url, "kind": att.kind, "name": att.name}
	text = (text or "").strip()
	if att.kind == media_out.FILE and text:
		first = inbox.send_zalo(lead, text, confirm_paid)
		if first.get("status") != "Queued":
			return first
		result = inbox.send_zalo(lead, "", confirm_paid, attachment=attachment)
		result["text_outbound"] = first.get("outbound")
		return result
	if len(text) > media_out.CAPTION_MAX:
		frappe.throw(_("Chú thích ảnh dài quá {0} ký tự").format(media_out.CAPTION_MAX))
	return inbox.send_zalo(lead, text, confirm_paid, attachment=attachment)


@frappe.whitelist(methods=["POST"])
def request_phone(doctype: str, name: str) -> dict:
	"""Sale bấm "Xin số điện thoại" trên tab Zalo (V3-07): gửi thẻ request_user_info, mọi điều kiện kiểm ở máy chủ."""
	lead = resolve_lead(doctype, name, "write")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Zalo"))
	return inbox.request_phone(lead)
