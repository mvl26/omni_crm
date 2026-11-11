"""Thư sale gửi từ CRM (Lead/Deal): nối vào luồng thư của khách và gửi từ đúng hộp thư.

Giao diện CRM gửi thư bằng `frappe.core.doctype.communication.email.make` (CommunicationArea.vue) nhưng:
- không truyền `in_reply_to`, nên thư "Re: ..." đi ra không có header In-Reply-To: hộp thư của khách không xếp nó vào
  cuộc hội thoại cũ và bộ lọc thư rác thấy một thư "Re:" không trả lời thư nào (dấu hiệu thư rác quen thuộc);
- khi bấm Trả lời một thư khách gửi, ô FROM được gán bằng địa chỉ người gửi thư đó, tức chính khách
  (components/Activities/EmailArea.vue); khi người dùng chưa liên kết hộp thư (User Emails), FROM là email đăng nhập.
  Địa chỉ gửi không phải hộp thư thật thì Gmail viết lại hoặc người nhận coi là giả mạo.

Hook before_insert của Communication sửa hai điểm trên trước khi Frappe dựng thư; không sửa mã app `crm`, không bao giờ
chặn việc gửi (lỗi chỉ ghi log).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import parse_addr, split_emails

from omni_crm.crm_bridge import lead as lead_bridge

LEAD = "CRM Lead"
DEAL = "CRM Deal"
COMMUNICATION = "Communication"


def on_communication_before_insert(doc: Any, method: str | None = None) -> None:
	try:
		if (
			doc.communication_medium != "Email"
			or doc.sent_or_received != "Sent"
			or (doc.communication_type or "Communication") != "Communication"
			or doc.reference_doctype not in (LEAD, DEAL)
			or not doc.reference_name
		):
			return
		if not doc.in_reply_to:
			doc.in_reply_to = last_received(doc.reference_doctype, doc.reference_name, doc.recipients)
		fix_sender(doc)
	except Exception:
		frappe.log_error(title="Omni: không chỉnh được thư gửi từ CRM (thư vẫn gửi)")


def _refs(doctype: str, name: str) -> list[tuple[str, str]]:
	"""Tài liệu chứa thư của cùng khách: chính nó và Lead/Deal đi cặp (thư khách có thể gắn ở Lead hay Deal)."""
	refs = [(doctype, name)]
	other = (
		(DEAL, lead_bridge.get_deal_for_lead(name))
		if doctype == LEAD
		else (LEAD, frappe.db.get_value(DEAL, name, "lead"))
	)
	if other[1]:
		refs.append(other)
	return refs


def last_received(doctype: str, name: str, recipients: str | None) -> str | None:
	"""Thư khách gửi đến gần nhất (có Message-ID thật) trên Lead/Deal, ưu tiên thư của chính người đang được trả lời."""
	rows = []
	for dt, dn in _refs(doctype, name):
		rows += frappe.get_all(
			COMMUNICATION,
			filters={
				"reference_doctype": dt,
				"reference_name": dn,
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"message_id": ["like", "%@%"],  # khóa thay thế "id:..." của intake không phải Message-ID
			},
			fields=["name", "sender", "communication_date", "creation"],
			order_by="communication_date desc, creation desc",
			limit=20,
		)
	if not rows:
		return None
	rows.sort(key=lambda r: (r.communication_date or r.creation, r.creation), reverse=True)
	wanted = {parse_addr(r)[1].lower() for r in split_emails(recipients or "")}
	for r in rows:
		if (parse_addr(r.sender or "")[1] or "").lower() in wanted:
			return r.name
	return rows[0].name


def fix_sender(doc: Any) -> None:
	"""FROM không phải hộp thư gửi được -> dùng hộp thư khách đã viết tới, nếu không thì hộp thư liên kết với người dùng."""
	outgoing = {
		(a.email_id or "").lower(): a.name
		for a in frappe.get_all("Email Account", filters={"enable_outgoing": 1}, fields=["name", "email_id"])
	}
	if (parse_addr(doc.sender or "")[1] or "").lower() in outgoing:
		return
	candidates = []
	if doc.in_reply_to:
		candidates.append(frappe.db.get_value(COMMUNICATION, doc.in_reply_to, "email_account"))
	candidates += frappe.get_all(
		"User Email", filters={"parent": frappe.session.user}, pluck="email_account", order_by="idx asc"
	)
	account = next((a for a in candidates if a and a in outgoing.values()), None)
	if not account:
		return  # để Frappe tự chọn như cũ
	doc.sender = frappe.db.get_value("Email Account", account, "email_id")
	doc.email_account = account
