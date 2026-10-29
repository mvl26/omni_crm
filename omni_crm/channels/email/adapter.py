"""Adapter email dựa trên Communication của Frappe (docs/05)."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html, now_datetime

from omni_crm import flags
from omni_crm.channels.email.auto import ignore_reason
from omni_crm.channels.email.clean import clean_email
from omni_crm.channels.registry import register_adapter
from omni_crm.identity.extract import extract_contacts
from omni_crm.identity.normalize import normalize_email
from omni_crm.ingest import gateway
from omni_crm.ingest.events import InboundEvent, OutboundRequest, stable_message_id
from omni_crm.pipeline.retry import redact

CHANNEL_TYPE = "Email"
DATA_LEN = 140  # độ dài tối đa của trường Data; dedupe_key = "{kênh}:{mã thư}" phải nằm trong đó
ID_BUDGET = 130


def external_message_id(channel: str, message_id: str | None, communication: str) -> str:
	"""Message-ID của thư; băm nếu thiếu hoặc quá dài (để dedupe_key vừa trường Data)."""
	mid = (message_id or "").strip()
	if not mid:
		return stable_message_id("email", communication)
	if len(channel) + 1 + len(mid) > ID_BUDGET:
		return "sha:" + stable_message_id("email", mid)
	return mid


def find_channel(email_account: str | None) -> str | None:
	"""Tên Omni Channel Email đang bật trỏ tới Email Account; None nếu không có."""
	if not email_account:
		return None
	return frappe.db.get_value(
		"Omni Channel",
		{"channel_type": CHANNEL_TYPE, "email_account": email_account, "enabled": 1},
		"name",
	)


def _attachments(communication: str) -> list[dict]:
	"""Tham chiếu File đang gắn với Communication (có thể rỗng lúc hook chạy: Frappe lưu tệp sau insert)."""
	rows = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Communication", "attached_to_name": communication},
		fields=["name", "file_name", "file_url"],
		limit_page_length=50,
	)
	return [{"file": r.name, "name": r.file_name, "url": r.file_url} for r in rows]


def _display_name(doc: Any, sender: str) -> str | None:
	"""Tên người gửi từ header From nếu thật sự là tên (không phải địa chỉ email)."""
	name = (doc.get("sender_full_name") or "").strip()
	if not name or "@" in name or name.lower() == sender.split("@", 1)[0].lower():
		return None
	return name


def build_event(doc: Any, channel: str) -> InboundEvent:
	"""Dựng InboundEvent từ một Communication thư đến (không chạm DB ngoài danh sách tệp đính kèm)."""
	sender = normalize_email(doc.sender) or (doc.sender or "").strip().lower()
	reason = ignore_reason(sender, doc.subject)
	body, signature = clean_email(doc.text_content or doc.content)
	profile: dict = {}
	name = _display_name(doc, sender)
	if name:
		profile["display_name"] = name
	found = extract_contacts(signature)  # chữ ký: tín hiệu TRUNG BÌNH, tách riêng khỏi phone/email (mạnh)
	if found["phones"]:
		profile["signature_phones"] = found["phones"]
	if found["emails"]:
		profile["signature_emails"] = found["emails"]
	return InboundEvent(
		channel=channel,
		channel_type=CHANNEL_TYPE,
		event_type="ignored" if reason else "message",
		external_message_id=external_message_id(channel, doc.message_id, doc.name),
		external_sender_id=sender[:DATA_LEN],
		received_at=doc.communication_date or now_datetime(),
		text=body,
		attachments=_attachments(doc.name),
		sender_profile=profile,
		raw={"subject": doc.subject, "in_reply_to": doc.in_reply_to, "ignored": reason},
		communication=doc.name,
	)


def on_communication_insert(doc: Any, method: str | None = None) -> None:
	"""Hook Communication.after_insert: đưa thư đến của hộp thư bán hàng vào cổng tiếp nhận; không bao giờ ném lỗi."""
	try:
		if doc.communication_medium != "Email" or doc.sent_or_received != "Received":
			return
		channel = find_channel(doc.email_account)
		if not channel:
			return
		gateway.save_inbound(build_event(doc, channel))
	except Exception:
		# Hook chạy trong vòng nhận thư của Frappe: lỗi ở đây không được làm hỏng việc nhận thư.
		frappe.log_error(
			title="Omni: lỗi đưa email vào cổng tiếp nhận",
			message=redact(f"communication={doc.name}\n{frappe.get_traceback(with_context=False)[-1500:]}"),
		)


class EmailAdapter:
	"""Adapter kênh Email: nhận qua hook Communication, gửi qua frappe.sendmail."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Email không có webhook nên không xác thực gì; luôn True."""
		return True

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Email không nhận qua webhook: không có sự kiện nào."""
		return []

	def can_send_now(self, identity_doc, channel_doc, source: str = "Sale") -> tuple[bool, str]:
		"""Gửi được trừ khi kênh tắt chiều ra."""
		if channel_doc is not None and not channel_doc.get("outbound_enabled"):
			return False, _("Kênh email đã tắt chiều ra")
		return True, ""

	def send(self, req: OutboundRequest, channel_doc) -> str:
		"""Gửi thư trả lời qua frappe.sendmail; trả Message-ID. Sandbox chỉ ghi log, không gửi."""
		message_id = f"<{frappe.generate_hash(length=24)}@{frappe.local.site}>"
		if flags.sandbox():
			frappe.logger("omni").info("omni sandbox: bỏ qua gửi email (kênh %s)", channel_doc.name)
			return message_id
		ident = frappe.db.get_value("Omni Identity", req.identity, ["email_norm", "lead"], as_dict=True)
		if not ident or not ident.email_norm:
			raise ValueError(_("Định danh chưa có email để trả lời"))
		account = frappe.db.get_value(
			"Email Account", channel_doc.email_account, ["email_id", "name"], as_dict=True
		)
		if not account:
			raise ValueError(_("Kênh email chưa có tài khoản email hợp lệ"))
		in_reply_to, subject = self._thread(channel_doc.name, req.reply_to_external_id)
		frappe.sendmail(
			recipients=[ident.email_norm],
			sender=account.email_id,
			subject=subject,
			message="<br>".join(escape_html(req.text).splitlines()),
			reference_doctype="CRM Lead" if ident.lead else None,
			reference_name=ident.lead,
			message_id=message_id,
			in_reply_to=in_reply_to,
			now=True,
		)
		return message_id

	@staticmethod
	def _thread(channel: str, reply_to_external_id: str | None) -> tuple[str | None, str]:
		"""(Message-ID thật của thư khách gần nhất, tiêu đề "Re: ...") để thư liền mạch với chuỗi."""
		subject = _("Phản hồi từ Miyano")
		if not reply_to_external_id:
			return None, subject
		comm = frappe.db.get_value(
			"Omni Inbound Message",
			{"channel": channel, "external_message_id": reply_to_external_id},
			"communication",
		)
		row = (
			frappe.db.get_value("Communication", comm, ["message_id", "subject"], as_dict=True)
			if comm
			else None
		)
		if not row:
			return reply_to_external_id, subject
		title = (row.subject or "").strip()
		if title:
			subject = title if title.lower().startswith("re:") else f"Re: {title}"
		return row.message_id or reply_to_external_id, subject


ADAPTER_CLASS = EmailAdapter
register_adapter(CHANNEL_TYPE, EmailAdapter())
