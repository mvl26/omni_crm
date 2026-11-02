"""Contact của nhà thầu: khóa tìm theo SĐT/email, liên kết Organization, khóa băm khi xóa dữ liệu (docs/14 mục 1.3, 6)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import frappe

from omni_crm.identity.normalize import normalize_email, normalize_phone

CONTACT = "Contact"
SEP = "|"


def _wrap(values: list[str]) -> str:
	return f"{SEP}{SEP.join(values)}{SEP}" if values else ""


def contact_keys(doc: Any) -> tuple[list[str], list[str]]:
	"""(SĐT E.164, email chuẩn hóa) của các dòng SĐT/email trên Contact, bỏ trùng, giữ thứ tự."""
	phones: list[str] = []
	for row in doc.get("phone_nos") or []:
		p = normalize_phone(row.phone)
		if p and p not in phones:
			phones.append(p)
	emails: list[str] = []
	for row in doc.get("email_ids") or []:
		e = normalize_email(row.email_id)
		if e and e not in emails:
			emails.append(e)
	return phones, emails


def validate(doc: Any, method: str | None = None) -> None:
	"""Hook `Contact.validate`: tính `omni_phones_e164`, `omni_emails_norm` (trường kỹ thuật dùng để khớp định danh)."""
	phones, emails = contact_keys(doc)
	doc.omni_phones_e164 = _wrap(phones)
	doc.omni_emails_norm = _wrap(emails)


def erase_key(value: str) -> str:
	"""Khóa băm (HMAC-SHA256 với khóa của site) của SĐT E.164 / email chuẩn hóa — để nhớ người đã xóa mà không giữ dữ liệu."""
	secret = (frappe.local.conf.get("encryption_key") or frappe.local.site or "omni").encode()
	return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:32]


def erased_keys_for(phone_or_email: str | None) -> str | None:
	"""Khóa băm của một SĐT/email thô (None nếu không chuẩn hóa được)."""
	value = normalize_phone(phone_or_email) or normalize_email(phone_or_email)
	return erase_key(value) if value else None


def is_erased_person(phone: str | None = None, email: str | None = None) -> bool:
	"""SĐT/email này thuộc một Contact đã bị xóa dữ liệu theo yêu cầu chưa (importer không được tạo lại người này)."""
	for raw in (phone, email):
		key = erased_keys_for(raw)
		if key and frappe.db.exists(CONTACT, {"omni_erased_keys": ["like", f"%{SEP}{key}{SEP}%"]}):
			return True
	return False


def contacts_matching(phones: list[str], emails: list[str]) -> list[Any]:
	"""Contact có SĐT/email (đã chuẩn hóa) khớp, kèm tổ chức (`company_name`); chỉ Contact còn dữ liệu."""
	or_filters: list[list] = []
	or_filters += [["omni_phones_e164", "like", f"%{SEP}{p}{SEP}%"] for p in phones]
	or_filters += [["omni_emails_norm", "like", f"%{SEP}{e}{SEP}%"] for e in emails]
	if not or_filters:
		return []
	return frappe.get_all(
		CONTACT,
		or_filters=or_filters,
		filters={"company_name": ["is", "set"]},
		fields=["name", "company_name", "first_name", "last_name", "full_name"],
		limit_page_length=200,
	)


ERASED = "[đã xóa]"
REFUSED = "Từ chối"
ERASED_NAME_PREFIX = "Đã xóa"


def contacts_of_lead(lead: str) -> list[str]:
	"""Contact là chính người của Lead: khớp email/SĐT của Lead (thô hoặc chuẩn hóa) hoặc là Contact CHÍNH của Deal của Lead.

	KHÔNG lấy các Contact khác của cùng Organization, cũng không lấy Contact phụ mà sale gắn thêm vào Deal (đồng nghiệp
	của người đó): xóa dữ liệu một người không được xóa đồng nghiệp của họ. CRM đặt người của Lead là Contact chính
	khi chuyển Lead thành Deal.
	"""
	row = frappe.db.get_value(
		"CRM Lead", lead, ["email", "mobile_no", "phone", "omni_phone_e164", "omni_email_norm"], as_dict=True
	)
	if not row:
		return []
	found: set[str] = set()
	emails = [e for e in {row.email, row.omni_email_norm, normalize_email(row.email)} if e]
	phones_raw = [p for p in {row.mobile_no, row.phone} if p]
	phones_norm = [p for p in {row.omni_phone_e164, *(normalize_phone(x) for x in phones_raw)} if p]
	if emails:
		found.update(frappe.get_all("Contact Email", filters={"email_id": ["in", emails]}, pluck="parent"))
	if phones_raw:
		found.update(frappe.get_all("Contact Phone", filters={"phone": ["in", phones_raw]}, pluck="parent"))
	for c in contacts_matching_any(phones_norm, emails):
		found.add(c)
	deals = frappe.get_all("CRM Deal", filters={"lead": lead}, pluck="name")
	if deals:
		found.update(
			frappe.get_all(
				"CRM Contacts",
				filters={"parenttype": "CRM Deal", "parent": ["in", deals], "is_primary": 1},
				pluck="contact",
			)
		)
	return sorted(x for x in found if x and frappe.db.exists(CONTACT, x))


def contacts_matching_any(phones: list[str], emails: list[str]) -> list[str]:
	"""Như `contacts_matching` nhưng không đòi Contact có tổ chức; chỉ trả tên Contact."""
	or_filters: list[list] = []
	or_filters += [["omni_phones_e164", "like", f"%{SEP}{p}{SEP}%"] for p in phones]
	or_filters += [["omni_emails_norm", "like", f"%{SEP}{e}{SEP}%"] for e in emails if "@" in e]
	if not or_filters:
		return []
	return frappe.get_all(CONTACT, or_filters=or_filters, pluck="name", limit_page_length=200)


def erase_contact(contact: str, reason: str = "") -> bool:
	"""Xóa dữ liệu cá nhân của một Contact theo docs/14 mục 6; giữ bản ghi ẩn danh. Trả False nếu đã xóa trước đó.

	Xóa họ tên, SĐT, email, chức danh, ảnh, ghi chú (Comment); đặt `omni_consent = Từ chối`; lưu khóa băm (HMAC) của SĐT/email
	để lần nhập sau không tạo lại người này; đổi tên bản ghi (tên bản ghi Contact chứa họ tên). Giữ liên kết tới Organization
	(`company_name`) và `omni_info_source`. Chạy lại không lỗi. Không commit.
	"""
	doc = frappe.get_doc(CONTACT, contact)
	phones, emails = contact_keys(doc)
	keys = [erase_key(v) for v in (*phones, *emails)]
	already = ERASED_NAME_PREFIX in (doc.name or "") and not keys and (doc.first_name == ERASED)
	if already:
		return False
	old = [k for k in (doc.get("omni_erased_keys") or "").split(SEP) if k]
	merged_keys = list(dict.fromkeys([*old, *keys]))
	for child in ("Contact Email", "Contact Phone"):
		frappe.db.delete(child, {"parent": contact, "parenttype": CONTACT})
	frappe.db.set_value(
		CONTACT,
		contact,
		{
			"first_name": ERASED,
			"middle_name": None,
			"last_name": None,
			"full_name": ERASED,
			"salutation": None,
			"gender": None,
			"designation": None,
			"image": None,
			"email_id": None,
			"mobile_no": None,
			"phone": None,
			"address": None,
			"_comments": None,  # cột phi chuẩn hóa chứa nguyên văn các Comment (ghi chú)
			"omni_phones_e164": "",
			"omni_emails_norm": "",
			"omni_erased_keys": _wrap(merged_keys),
			"omni_consent": REFUSED,
			"omni_consent_method": ERASED_NAME_PREFIX + (f": {reason}"[:120] if reason else ""),
		},
		update_modified=False,
	)
	frappe.db.delete("Comment", {"reference_doctype": CONTACT, "reference_name": contact})
	new_name = f"{ERASED_NAME_PREFIX}-{(merged_keys[0] if merged_keys else erase_key(contact))[:10]}"
	if new_name != contact:
		if frappe.db.exists(CONTACT, new_name):
			new_name = f"{new_name}-{frappe.generate_hash(length=4)}"
		frappe.rename_doc(CONTACT, contact, new_name, force=True, show_alert=False)
		contact = new_name
	# Lần đổi tên của Frappe ghi Comment "renamed from <họ tên cũ> to ..." và Version chứa giá trị cũ: xóa cả hai.
	frappe.db.delete("Comment", {"reference_doctype": CONTACT, "reference_name": contact})
	frappe.db.delete("Version", {"ref_doctype": CONTACT, "docname": ["in", [contact, doc.name]]})
	return True
