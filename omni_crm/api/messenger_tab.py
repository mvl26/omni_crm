"""API mỏng cho tab "Messenger" trong trang Lead/Deal của CRM (ADR 0023), cùng khuôn với `api/zalo_tab.py` (ADR 0016).

Toàn bộ dữ liệu và guard gửi nằm ở `api/inbox.py` (`get_thread`, `send_messenger`, `compose_state`): file này đổi Lead/Deal
thành Lead, kiểm quyền theo CẢ trang đang xem lẫn Lead gốc, lọc riêng kênh Meta Messenger và rút gọn trường trả về.
Không gửi qua kênh khác. Khác Zalo: không có xác nhận phí, quỹ tin, thẻ xin số; đính kèm của khách là tệp đã lưu trong CRM.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.api import inbox
from omni_crm.api.zalo_tab import resolve_lead
from omni_crm.channels.meta_messenger import media_out
from omni_crm.crm_bridge import chat_templates
from omni_crm.pipeline import outbound as outbound_pipeline
from omni_crm.utils import media

LEAD = "CRM Lead"
CHANNEL = "Meta Messenger"
INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
MESSAGE_DOCTYPES = (INBOUND, OUTBOUND)
# ADR 0027
LIKE_STICKER_ID = "369239263222822"  # sticker 👍 của Messenger, luôn gửi được (không có trong kho)
STICKER_HOSTS = ("fbcdn.net", "fbsbx.com", "facebook.com")
STICKER_LOCALE = "vi_VN"
STICKER_CACHE_SECONDS = 6 * 3600
MAX_STICKER_ITEMS = 120
QUOTE_CHARS = 80
MAX_FORWARD_FILES = 5
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


def _message(item: dict, user: str, manager: bool) -> dict:
	own = item.get("doctype") == OUTBOUND and item.get("sent_by") == user
	sticker = item.get("sticker") or None
	if sticker:
		sticker = {"id": sticker["id"], "url": sticker["url"] if _sticker_url_ok(sticker.get("url")) else ""}
	return {
		"name": item["name"],
		"doctype": item.get("doctype"),
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
		# ADR 0027
		"mid": item.get("mid") or "",
		"reply_to_mid": item.get("reply_to_mid") or "",
		"reactions": item.get("reactions") or {"customer": "", "page": ""},
		"sticker": sticker,
		"hidden": bool(item.get("hidden")),
		"can_react": bool(item.get("mid"))
		and (not str(item.get("mid")).startswith("sandbox:") or flags.sandbox()),
		"can_hide": manager or own,
		# ADR 0027 đợt 2: chỉ chuyển tiếp tin Page đã gửi (không đưa tin của khách này cho khách khác)
		"forwarded": bool(item.get("forwarded")),
		"can_forward": _forwardable(item),
	}


def _forwardable(item: dict) -> bool:
	if item.get("direction") != "out" or item.get("hidden"):
		return False
	if item.get("doctype") == OUTBOUND and item.get("status") != "Sent":
		return False
	return bool(item.get("text") or item.get("attachments") or item.get("sticker"))


def _with_quotes(rows: list[dict]) -> list[dict]:
	"""Gắn `reply_to` (tin được trả lời, rút gọn) cho tin có `reply_to_mid`; tin gốc không còn trong hội thoại thì `missing`."""
	by_mid = {r["mid"]: r for r in rows if r.get("mid")}
	for r in rows:
		mid = r.get("reply_to_mid")
		if not mid:
			r["reply_to"] = None
			continue
		src = by_mid.get(mid)
		if not src or (src.get("hidden") and not r.get("hidden")):
			r["reply_to"] = {"missing": True}
			continue
		label = (
			src["text"]
			or (_("Sticker") if src.get("sticker") else "")
			or (_("Tệp đính kèm") if src.get("attachments") else "")
		)
		r["reply_to"] = {
			"name": src["name"],
			"doctype": src["doctype"],
			"sender": src["sender"],
			"direction": src["direction"],
			"text": label[:QUOTE_CHARS],
		}
	return rows


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
	# ADR 0027: sticker theo khung 24 giờ như tin chữ; cảm xúc chỉ cần quyền ghi và khách không từ chối liên hệ
	state["can_send_sticker"] = state["can_send"]
	state["like_sticker_id"] = LIKE_STICKER_ID
	state["can_react"] = bool(
		ms is not None and can_write and not (data.get("profile") or {}).get("person_refused")
	)
	# ADR 0027 đợt 2: ghi âm là tệp (cần công tắc gửi tệp) và cần ffmpeg trên máy chủ để đổi sang MP3
	has_ffmpeg = bool(media_out.ffmpeg_path())
	state["can_send_voice"] = bool(state["can_send_media"] and has_ffmpeg)
	state["voice_max_seconds"] = media_out.VOICE_MAX_SECONDS
	state["voice_reason"] = (
		""
		if state["can_send_voice"] or not state["can_send_media"]
		else _("Máy chủ chưa cài ffmpeg nên chưa gửi được ghi âm")
	)
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
	user, manager = frappe.session.user, inbox.can_see_hidden()
	rows = _with_quotes(
		[
			_message(i, user, manager)
			for i in data["messages"]
			if i.get("channel_type") == CHANNEL and i.get("doctype") in MESSAGE_DOCTYPES
		]
	)
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


@frappe.whitelist()
def get_templates(doctype: str, name: str) -> dict:
	"""Mẫu tin nhắn (ADR 0026) đã điền cho Lead gốc của trang đang xem và người đang soạn; chỉ để ĐIỀN ô soạn, không gửi.

	Quyền: đọc trang đang mở VÀ Lead gốc (như `get_conversation`). Tab tắt hoặc không có Lead gốc: danh sách rỗng.
	"""
	if not flags.messenger_tab_enabled():
		return {"templates": []}
	lead = resolve_lead(doctype, name, "read")
	if not lead:
		return {"templates": []}
	if not frappe.has_permission(LEAD, "read", doc=lead):
		frappe.throw(_("Bạn không có quyền xem Lead này"), frappe.PermissionError)
	return {"templates": chat_templates.for_lead(lead, CHANNEL)}


def _write_lead(doctype: str, name: str) -> str:
	if not flags.messenger_tab_enabled():
		frappe.throw(_("Tab Messenger đang bị tắt"), frappe.PermissionError)
	lead = resolve_lead(doctype, name, "write")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Messenger"))
	return inbox.check_lead_write(lead)


def _message_of_lead(lead: str, message_doctype: str, message: str) -> dict:
	"""Tin Messenger `message` thuộc Lead `lead` (ném lỗi nếu không): {doctype, name, mid, sent_by, channel, hidden_at}."""
	if message_doctype not in MESSAGE_DOCTYPES or not message:
		frappe.throw(_("Tin nhắn không hợp lệ"))
	fields = ["name", "lead", "external_message_id", "hidden_at", "hidden_by"]
	fields += ["channel_type", "channel"] if message_doctype == INBOUND else ["channel", "sent_by"]
	row = frappe.db.get_value(message_doctype, message, fields, as_dict=True)
	if not row or row.lead != lead:
		frappe.throw(_("Tin nhắn không thuộc Lead đang mở"), frappe.PermissionError)
	channel_type = row.get("channel_type") or frappe.db.get_value("Omni Channel", row.channel, "channel_type")
	if channel_type != CHANNEL:
		frappe.throw(_("Chỉ áp dụng cho tin Messenger"))
	return {
		"doctype": message_doctype,
		"name": row.name,
		"mid": row.external_message_id or "",
		"sent_by": row.get("sent_by"),
		"channel": row.channel,
		"hidden_at": row.hidden_at,
	}


def _quote_mid(lead: str, reply_to_doctype: str | None, reply_to: str | None) -> str | None:
	"""Mã tin Meta của tin được trả lời (ADR 0027); None nếu không trả lời tin nào."""
	if not reply_to:
		return None
	target = _message_of_lead(lead, reply_to_doctype or "", reply_to)
	mid = target["mid"]
	if not mid or (mid.startswith("sandbox:") and not flags.sandbox()):
		frappe.throw(_("Tin này chưa có mã tin của Meta (chưa gửi tới khách) nên không trả lời riêng được"))
	return mid


@frappe.whitelist(methods=["POST"])
def send(
	doctype: str, name: str, text: str, reply_to_doctype: str | None = None, reply_to: str | None = None
) -> dict:
	"""Sale trả lời khách bằng chữ qua Messenger từ tab Messenger; mọi guard của `inbox.send_messenger` giữ nguyên.

	`reply_to_doctype` + `reply_to` (ADR 0027): trả lời một tin cụ thể (Messenger hiện trích dẫn tin đó).
	"""
	lead = _write_lead(doctype, name)
	quote = _quote_mid(lead, reply_to_doctype, reply_to)
	return inbox.send_messenger(lead, text, extras={"quote_mid": quote})


@frappe.whitelist(methods=["POST"])
def send_attachment(
	doctype: str,
	name: str,
	file_url: str,
	text: str = "",
	reply_to_doctype: str | None = None,
	reply_to: str | None = None,
) -> dict:
	"""Sale gửi ảnh/video/âm thanh/tệp của Lead qua Messenger (ADR 0023). Tệp phải đã đính kèm vào Lead (hoặc Deal của Lead).

	Messenger không cho chữ đi cùng tệp: có chữ thì xếp tin chữ TRƯỚC, rồi tin tệp. Công tắc `messenger_media_send_enabled`.
	Trả lời tin cụ thể (ADR 0027): gắn vào tin đi đầu (chữ nếu có, không thì tệp).
	"""
	lead = _write_lead(doctype, name)
	if not flags.messenger_media_send_enabled():
		frappe.throw(_("Chưa bật gửi ảnh/tệp qua Messenger"), frappe.PermissionError)
	attachment = media_out.attachment_for_lead(file_url, lead)
	quote = _quote_mid(lead, reply_to_doctype, reply_to)
	text = (text or "").strip()
	if text:
		first = inbox.send_messenger(lead, text, extras={"quote_mid": quote})
		if first.get("status") != "Queued":
			return first
		result = inbox.send_messenger(lead, "", attachment=attachment)
		result["text_outbound"] = first.get("outbound")
		return result
	return inbox.send_messenger(lead, "", attachment=attachment, extras={"quote_mid": quote})


def _sticker_url_ok(url: Any) -> bool:
	"""Ảnh sticker chỉ hiện khi là https từ CDN của Meta (không lưu/hiện URL lạ do trình duyệt gửi lên)."""
	try:
		parts = urlsplit(str(url or ""))
	except ValueError:
		return False
	host = (parts.hostname or "").lower()
	return parts.scheme == "https" and any(host == d or host.endswith("." + d) for d in STICKER_HOSTS)


@frappe.whitelist(methods=["POST"])
def send_sticker(
	doctype: str,
	name: str,
	sticker_id: str,
	sticker_url: str = "",
	reply_to_doctype: str | None = None,
	reply_to: str | None = None,
) -> dict:
	"""Sale gửi một sticker miễn phí của Meta (hoặc 👍) qua Messenger (ADR 0027); trong khung 24 giờ như tin chữ."""
	lead = _write_lead(doctype, name)
	sticker_id = str(sticker_id or "").strip()
	if not sticker_id.isdigit() or len(sticker_id) > 30:
		frappe.throw(_("Mã sticker không hợp lệ"))
	quote = _quote_mid(lead, reply_to_doctype, reply_to)
	extras = {
		"sticker_id": sticker_id,
		"sticker_url": sticker_url if _sticker_url_ok(sticker_url) else "",
		"quote_mid": quote,
	}
	return inbox.send_messenger(lead, "", extras=extras)


def _valid_emoji(emoji: str) -> str:
	emoji = (emoji or "").strip()
	if len(emoji) > 16 or any(ch.isascii() and ch.isalnum() for ch in emoji):
		frappe.throw(_("Cảm xúc không hợp lệ"))
	return emoji


@frappe.whitelist(methods=["POST"])
def react(doctype: str, name: str, message_doctype: str, message: str, emoji: str = "") -> dict:
	"""Sale thả (emoji) hoặc bỏ (emoji rỗng) cảm xúc lên một tin Messenger của Lead (ADR 0027; Send API sender_action)."""
	from omni_crm.channels.meta_messenger import reactions
	from omni_crm.channels.meta_messenger.client import MessengerClient, MessengerError

	lead = _write_lead(doctype, name)
	emoji = _valid_emoji(emoji)
	target = _message_of_lead(lead, message_doctype, message)
	from omni_crm.api import customer as customer_api

	if customer_api._person_flags(lead)["refused"]:
		frappe.throw(_("Người này đã từ chối nhận liên hệ: không tương tác từ hệ thống."))
	conv = inbox._channel_target(lead, CHANNEL)
	if not conv:
		frappe.throw(_("Khách này chưa có tài khoản Messenger liên kết với Lead"))
	identity_doc, channel_doc = conv
	try:
		MessengerClient(channel_doc).react(identity_doc.external_id, target["mid"], emoji)
	except MessengerError as e:
		frappe.throw(e.message_vi)
	reactions.set_page(target["doctype"], target["name"], emoji, frappe.session.user)
	inbox.timeline.notify(lead, [CHANNEL])
	return {"ok": True, "reaction": emoji}


@frappe.whitelist(methods=["POST"])
def hide_message(doctype: str, name: str, message_doctype: str, message: str, hidden: int | str = 1) -> dict:
	"""Thu hồi 1 phía (ADR 0027): ẩn/bỏ ẩn một tin Messenger TRONG CRM; khách vẫn thấy (Meta không có API thu hồi).

	Người gửi ẩn/bỏ ẩn được tin do mình gửi từ CRM; Sales Manager/Omni Admin/System Manager được mọi tin. Bản ghi gốc giữ nguyên,
	ghi người ẩn và lúc ẩn; quản lý vẫn thấy tin đã ẩn (đánh dấu) trong tab.
	"""
	lead = _write_lead(doctype, name)
	target = _message_of_lead(lead, message_doctype, message)
	user = frappe.session.user
	own = target["doctype"] == OUTBOUND and target["sent_by"] == user
	if not (own or inbox.can_see_hidden(user)):
		frappe.throw(_("Chỉ người gửi tin hoặc quản lý mới được ẩn tin này"), frappe.PermissionError)
	hide = bool(cint(hidden))
	frappe.db.set_value(
		target["doctype"],
		target["name"],
		{"hidden_at": now_datetime() if hide else None, "hidden_by": user if hide else None},
		update_modified=False,
	)
	inbox.timeline.notify(lead, [CHANNEL])
	return {"ok": True, "hidden": hide}


# ---- Đợt 2 (ADR 0027): ghi âm (đổi sang MP3), chuyển tiếp ----


@frappe.whitelist(methods=["POST"])
def send_voice(
	doctype: str, name: str, file_url: str, reply_to_doctype: str | None = None, reply_to: str | None = None
) -> dict:
	"""Gửi bản ghi âm sale vừa ghi trong tab: tệp `ghi-am-…` đã tải lên Lead, máy chủ đổi sang MP3 rồi gửi kiểu `audio`.

	Cần công tắc `messenger_media_send_enabled` và ffmpeg. Mọi guard gửi tin (24 giờ, từ chối liên hệ, sandbox) giữ nguyên.
	"""
	lead = _write_lead(doctype, name)
	if not flags.messenger_media_send_enabled():
		frappe.throw(_("Chưa bật gửi ảnh/tệp qua Messenger"), frappe.PermissionError)
	quote = _quote_mid(lead, reply_to_doctype, reply_to)
	attachment = media_out.voice_to_mp3(file_url, lead)
	return inbox.send_messenger(lead, "", attachment=attachment, extras={"quote_mid": quote})


def _read_lead(doctype: str, name: str) -> str:
	if not flags.messenger_tab_enabled():
		frappe.throw(_("Tab Messenger đang bị tắt"), frappe.PermissionError)
	lead = resolve_lead(doctype, name, "read")
	if not lead:
		frappe.throw(_("Không có Lead gốc để gắn hội thoại Messenger"))
	inbox.check_lead_access(lead)
	return lead


@frappe.whitelist()
def forward_targets(doctype: str, name: str, q: str = "") -> dict:
	"""Khách Messenger khác (Lead người dùng được sửa) để chuyển tiếp tới, kèm trạng thái gửi được hay lý do (24 giờ…)."""
	lead = _read_lead(doctype, name)
	q = " ".join(str(q or "").split())[:80]
	leads = sorted(
		set(
			frappe.get_all(
				"Omni Identity", filters={"channel_type": CHANNEL, "lead": ["is", "set"]}, pluck="lead"
			)
		)
		- {lead}
	)
	if not leads:
		return {"items": []}
	filters: dict[str, Any] = {"name": ["in", leads]}
	if q:
		filters["lead_name"] = ["like", f"%{q}%"]
	rows = frappe.get_list(
		LEAD, filters=filters, fields=["name", "lead_name"], order_by="modified desc", limit_page_length=20
	)
	from omni_crm.channels.registry import get_adapter

	adapter = get_adapter(CHANNEL)
	out = []
	for r in rows:
		if not frappe.has_permission(LEAD, "write", doc=r.name):
			continue
		target = inbox._channel_target(r.name, CHANNEL)
		if not target:
			continue
		ok, reason = adapter.can_send_now(target[0], target[1], source="Sale")
		out.append(
			{"lead": r.name, "lead_name": r.lead_name or r.name, "can_send": bool(ok), "reason": reason or ""}
		)
	return {"items": out}


def _forward_content(message_doctype: str, message: str) -> dict:
	"""Nội dung tin gốc: {text, files: [(file_url, name)], sticker}; chỉ tin Page đã gửi."""
	if message_doctype == OUTBOUND:
		row = frappe.db.get_value(
			OUTBOUND,
			message,
			["text", "status", "attachment", "attachment_name", "sticker_id", "sticker_url", "hidden_at"],
			as_dict=True,
		)
		if not row or row.status != "Sent" or row.hidden_at:
			frappe.throw(_("Chỉ chuyển tiếp được tin đã gửi"))
		files = [(row.attachment, row.attachment_name)] if row.attachment else []
		sticker = (
			{"sticker_id": row.sticker_id, "sticker_url": row.sticker_url or ""} if row.sticker_id else None
		)
		return {"text": row.text or "", "files": files, "sticker": sticker}
	row = frappe.db.get_value(INBOUND, message, ["event_type", "text", "hidden_at"], as_dict=True)
	if not row or row.event_type != "oa_echo" or row.hidden_at:
		frappe.throw(
			_(
				"Chỉ chuyển tiếp được tin do Page gửi; tin của khách không chuyển cho khách khác (bảo vệ dữ liệu cá nhân)."
			),
			frappe.PermissionError,
		)
	files = [
		(f.file_url, f.file_name)
		for f in frappe.get_all(
			"File",
			filters={"attached_to_doctype": INBOUND, "attached_to_name": message, "is_folder": 0},
			fields=["file_url", "file_name"],
			order_by="creation asc",
			limit=MAX_FORWARD_FILES + 1,
		)
	]
	return {"text": row.text or "", "files": files, "sticker": None}


@frappe.whitelist(methods=["POST"])
def forward(doctype: str, name: str, message_doctype: str, message: str, target_lead: str) -> dict:
	"""Chuyển tiếp một tin Page đã gửi sang khách Messenger của Lead khác (ADR 0027 đợt 2): gửi lại chữ, tệp (chép sang Lead
	nhận), sticker như tin mới của sale, qua mọi guard của Lead nhận (24 giờ, từ chối liên hệ, quyền ghi). Một tin, một khách.
	"""
	source_lead = _read_lead(doctype, name)
	_message_of_lead(source_lead, message_doctype, message)
	target_lead = str(target_lead or "").strip()
	if not target_lead or target_lead == source_lead or not frappe.db.exists(LEAD, target_lead):
		frappe.throw(_("Chọn một khách khác để chuyển tiếp"))
	target_lead = inbox.check_lead_write(target_lead)
	content = _forward_content(message_doctype, message)
	if len(content["files"]) > MAX_FORWARD_FILES:
		frappe.throw(_("Tin có quá {0} tệp, không chuyển tiếp được").format(MAX_FORWARD_FILES))
	if content["files"] and not flags.messenger_media_send_enabled():
		frappe.throw(_("Chưa bật gửi ảnh/tệp qua Messenger"), frappe.PermissionError)
	attachments = [media_out.copy_to_lead(url, fname, target_lead) for url, fname in content["files"]]
	origin = {"forwarded_from": f"{message_doctype}:{message}"}
	results = []
	if content["text"]:
		results.append(inbox.send_messenger(target_lead, content["text"], extras=origin))
	for att in attachments:
		if results and results[-1].get("status") != "Queued":
			break
		results.append(inbox.send_messenger(target_lead, "", attachment=att, extras=origin))
	if content["sticker"] and (not results or results[-1].get("status") == "Queued"):
		results.append(inbox.send_messenger(target_lead, "", extras={**origin, **content["sticker"]}))
	if not results:
		frappe.throw(_("Tin này không có nội dung để chuyển tiếp"))
	last = results[-1]
	return {
		"status": last.get("status"),
		"reason": last.get("reason", ""),
		"target": target_lead,
		"outbounds": [r.get("outbound") for r in results],
	}


# ---- Kho sticker của Meta (ADR 0027): chỉ đọc, đệm 6 giờ, App Access Token của kênh ----


def _catalog_client():
	from omni_crm.channels.meta_messenger.client import MessengerClient

	name = frappe.db.get_value(
		"Omni Channel", {"channel_type": CHANNEL, "enabled": 1, "meta_app_id": ["is", "set"]}, "name"
	)
	if not name:
		frappe.throw(_("Chưa có kênh Messenger đang bật có App ID"))
	return MessengerClient(frappe.get_doc("Omni Channel", name))


def _catalog(kind: str, arg: str, fetch) -> dict:
	from omni_crm.channels.meta_messenger.client import MessengerError

	if not flags.messenger_tab_enabled() or frappe.session.user == "Guest":
		frappe.throw(_("Tab Messenger đang bị tắt"), frappe.PermissionError)
	if flags.sandbox():
		return {"items": [], "error": _("Chế độ thử (sandbox): không tải kho sticker của Meta")}
	key = f"omni:messenger:stickers:{kind}:{arg}:{STICKER_LOCALE}"
	cached = frappe.cache().get_value(key)
	if cached is not None:
		return {"items": cached, "error": ""}
	try:
		items = fetch()
	except MessengerError as e:
		return {"items": [], "error": e.message_vi}
	frappe.cache().set_value(key, items, expires_in_sec=STICKER_CACHE_SECONDS)
	return {"items": items, "error": ""}


def _clean_sticker(s: dict) -> dict | None:
	sid = str(s.get("id") or "")
	url = str(s.get("image_url") or "")
	if not sid.isdigit() or not _sticker_url_ok(url):
		return None
	return {
		"id": sid,
		"name": str(s.get("name") or "")[:80],
		"image_url": url,
		"animated": bool(s.get("is_animated")),
	}


@frappe.whitelist()
def sticker_packs() -> dict:
	"""Các bộ sticker miễn phí của Meta (tên, ảnh đại diện, số sticker)."""

	def fetch():
		out = []
		for p in _catalog_client().sticker_packs(STICKER_LOCALE)[:MAX_STICKER_ITEMS]:
			pid, preview = str(p.get("id") or ""), str(p.get("preview_image_url") or "")
			if pid.isdigit():
				out.append(
					{
						"id": pid,
						"name": str(p.get("name") or "")[:80],
						"preview_image_url": preview if _sticker_url_ok(preview) else "",
						"count": cint(p.get("sticker_count")),
					}
				)
		return out

	return _catalog("packs", "-", fetch)


@frappe.whitelist()
def sticker_pack(pack: str) -> dict:
	"""Sticker trong một bộ."""
	pack = str(pack or "").strip()
	if not pack.isdigit():
		frappe.throw(_("Mã bộ sticker không hợp lệ"))
	return _catalog(
		"pack",
		pack,
		lambda: [c for c in map(_clean_sticker, _catalog_client().pack_stickers(pack, STICKER_LOCALE)) if c][
			:MAX_STICKER_ITEMS
		],
	)


@frappe.whitelist()
def sticker_search(q: str) -> dict:
	"""Tìm sticker theo từ khóa (tối thiểu 2 ký tự; tiếng Việt, Meta tự lùi về tiếng Anh nếu không thấy)."""
	q = " ".join(str(q or "").split())[:50]
	if len(q) < 2:
		return {"items": [], "error": _("Nhập ít nhất 2 ký tự")}
	return _catalog(
		"search",
		q.lower(),
		lambda: [c for c in map(_clean_sticker, _catalog_client().search_stickers(q, STICKER_LOCALE)) if c][
			:MAX_STICKER_ITEMS
		],
	)
