"""Nút "Tạo liên hệ" trên trang Lead (V3-06): tạo hoặc cập nhật Liên hệ (Contact) từ dữ liệu Lead, không cần chuyển Deal.

- Tìm Liên hệ có sẵn theo thứ tự: Liên hệ đã gắn với Lead này (bảng `links` của Contact, link_doctype = CRM Lead), rồi
  email, rồi số điện thoại (so 9 số cuối, nên 0988806848 và +84988806848 là một).
- Có sẵn: "fetch" dữ liệu Lead sang, CHỈ điền ô còn trống và thêm email/SĐT còn thiếu; không ghi đè dữ liệu đã có
  của Liên hệ, không sửa Lead (khác `CRMLead.update_lead_contact` của CRM, hàm đó ghi ngược Liên hệ vào Lead).
- Chưa có: tạo Liên hệ như khi CRM chuyển Lead thành Deal (tên, họ, chức danh, đơn vị, email, SĐT).
- Quyền của chính người dùng: cần quyền SỬA Lead và quyền tạo/sửa Contact; không `ignore_permissions`.
- Công tắc `lead_contact_button_disabled` (Omni Settings, cực tính ngược: chưa lưu = hiện nút).
"""

from __future__ import annotations

import re
from typing import Any

import frappe
from frappe import _

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge

LEAD = "CRM Lead"
CONTACT = "Contact"
TAIL = 9


def _digits(value: Any) -> str:
	return re.sub(r"\D", "", str(value or ""))


def _lead_phones(lead: Any) -> list[str]:
	out: list[str] = []
	for v in (lead.mobile_no, lead.phone):
		v = (v or "").strip()
		if v and len(_digits(v)) >= TAIL and v not in out:
			out.append(v)
	return out


def _contact_by_link(lead: str) -> str | None:
	return frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": CONTACT, "link_doctype": LEAD, "link_name": lead},
		"parent",
		order_by="creation asc",
	)


def _contact_by_email(email: str | None) -> str | None:
	if not email:
		return None
	return frappe.db.get_value("Contact Email", {"email_id": email.strip()}, "parent")


def _contact_by_phone(phones: list[str]) -> str | None:
	for phone in phones:
		tail = _digits(phone)[-TAIL:]
		rows = frappe.db.sql(
			"select parent, phone from `tabContact Phone` where parenttype = 'Contact' and phone like %s",
			(f"%{tail[-4:]}",),
			as_dict=True,
		)
		for r in rows:
			if _digits(r.phone).endswith(tail):
				return r.parent
	return None


def find_contact(lead: Any) -> tuple[str | None, str]:
	"""Liên hệ có sẵn của Lead và lý do khớp ("link" / "email" / "phone" / "")."""
	for reason, name in (
		("link", _contact_by_link(lead.name)),
		("email", _contact_by_email(lead.email)),
		("phone", _contact_by_phone(_lead_phones(lead))),
	):
		if name and frappe.db.exists(CONTACT, name):
			return name, reason
	return None, ""


def _fill(contact: Any, lead: Any) -> list[str]:
	"""Điền ô trống của Liên hệ từ Lead, thêm email/SĐT còn thiếu, gắn link tới Lead; trả nhãn các mục đã thêm."""
	added: list[str] = []
	for field, value, label in (
		("first_name", lead.first_name or lead.lead_name, _("Tên")),
		("last_name", lead.last_name, _("Họ")),
		("company_name", lead.organization, _("Đơn vị")),
		("designation", lead.job_title, _("Chức danh")),
		("salutation", lead.salutation, _("Danh xưng")),
		("gender", lead.gender, _("Giới tính")),
	):
		if value and not contact.get(field):
			contact.set(field, value)
			added.append(label)
	email = (lead.email or "").strip()
	if email and email.lower() not in {(e.email_id or "").lower() for e in contact.email_ids}:
		contact.append("email_ids", {"email_id": email, "is_primary": 0 if contact.email_ids else 1})
		added.append(_("Email"))
	have = {_digits(p.phone)[-TAIL:] for p in contact.phone_nos}
	for phone in _lead_phones(lead):
		if _digits(phone)[-TAIL:] in have:
			continue
		first = not contact.phone_nos
		contact.append(
			"phone_nos",
			{"phone": phone, "is_primary_mobile_no": 1 if first else 0, "is_primary_phone": 0},
		)
		have.add(_digits(phone)[-TAIL:])
		added.append(_("Số điện thoại"))
	if not any(link.link_doctype == LEAD and link.link_name == lead.name for link in contact.links):
		contact.append("links", {"link_doctype": LEAD, "link_name": lead.name, "link_title": lead.lead_name})
	return added


@frappe.whitelist()
def state() -> dict:
	"""Có hiện nút "Tạo liên hệ" không (công tắc `lead_contact_button_disabled`, người dùng đã đăng nhập)."""
	return {"enabled": bool(flags.lead_contact_button_enabled() and frappe.session.user != "Guest")}


@frappe.whitelist(methods=["POST"])
def sync_from_lead(lead: str) -> dict:
	"""Tạo Liên hệ từ Lead, hoặc điền dữ liệu Lead sang Liên hệ có sẵn; trả {contact, created, matched_by, added}."""
	if not flags.lead_contact_button_enabled():
		frappe.throw(_("Chức năng tạo liên hệ từ Lead đang tắt"))
	if not lead or not frappe.db.exists(LEAD, lead):
		frappe.throw(_("Không tìm thấy Lead"), frappe.DoesNotExistError)
	lead = lead_bridge.follow_merged(lead) or lead
	if not frappe.has_permission(LEAD, "write", doc=lead):
		frappe.throw(_("Bạn không có quyền sửa Lead này"), frappe.PermissionError)
	doc = frappe.get_doc(LEAD, lead)
	if not (doc.first_name or doc.lead_name):
		frappe.throw(_("Lead chưa có tên, điền tên trước khi tạo liên hệ"))
	name, matched_by = find_contact(doc)
	if name:
		if not frappe.has_permission(CONTACT, "write", doc=name):
			frappe.throw(
				_("Đã có liên hệ {0} nhưng bạn không có quyền sửa").format(name), frappe.PermissionError
			)
		contact = frappe.get_doc(CONTACT, name)
		added = _fill(contact, doc)
		contact.save()
		created = False
	else:
		if not frappe.has_permission(CONTACT, "create"):
			frappe.throw(_("Bạn không có quyền tạo liên hệ"), frappe.PermissionError)
		contact = frappe.new_doc(CONTACT)
		added = _fill(contact, doc)
		contact.insert()
		created = True
	doc.add_comment(
		"Comment",
		(_("Đã tạo liên hệ {0} từ Lead") if created else _("Đã cập nhật liên hệ {0} từ Lead")).format(
			frappe.utils.escape_html(contact.name)
		),
	)
	return {"contact": contact.name, "created": created, "matched_by": matched_by, "added": added}
