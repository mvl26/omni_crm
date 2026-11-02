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
