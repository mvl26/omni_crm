"""Tạo, cập nhật CRM Lead và theo dõi sửa tay."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm.identity.normalize import is_mobile_phone, is_system_email, normalize_email, normalize_phone

LEAD = "CRM Lead"
MAX_MERGE_HOPS = 10


def normalized_fields(mobile_no: str | None, phone: str | None, email: str | None) -> tuple[str, str]:
	"""Tính (omni_phone_e164, omni_email_norm); chuỗi rỗng khi không có/không hợp lệ.

	Ưu tiên số di động: nếu `mobile_no` không phải di động mà `phone` là di động thì lấy `phone`.
	"""
	candidates = [p for p in (normalize_phone(mobile_no), normalize_phone(phone)) if p]
	chosen = next((p for p in candidates if is_mobile_phone(p)), candidates[0] if candidates else "")
	mail = normalize_email(email)
	if mail and is_system_email(mail):
		mail = None  # địa chỉ hệ thống không dùng để khớp khách
	return chosen, mail or ""


def compute_normalized_fields(doc: Any, method: str | None = None) -> None:
	"""Hook `CRM Lead.validate`: tính omni_phone_e164 và omni_email_norm từ SĐT/email thô."""
	doc.omni_phone_e164, doc.omni_email_norm = normalized_fields(
		doc.get("mobile_no"), doc.get("phone"), doc.get("email")
	)


def follow_merged(lead: str | None) -> str | None:
	"""Đi theo `omni_merged_into` tới Lead chính; None nếu Lead không tồn tại hoặc vòng lặp quá sâu."""
	current = lead
	for _hop in range(MAX_MERGE_HOPS):
		if not current:
			return None
		row = frappe.db.get_value(LEAD, current, ["name", "omni_merged_into"], as_dict=True)
		if not row:
			return None
		if not row.omni_merged_into:
			return row.name
		current = row.omni_merged_into
	return None


def get_deal_for_lead(lead: str) -> str | None:
	"""Tên CRM Deal nếu Lead đã chuyển (converted=1; Deal giữ `lead` trỏ về), ngược lại None.

	Lead chính chưa chuyển nhưng đã gộp Lead phụ đã chuyển thì trả Deal của Lead phụ (ADR 0002:
	không sửa `CRM Deal.lead`, chỉ tra theo `omni_merged_into`).
	"""
	if frappe.db.get_value(LEAD, lead, "converted"):
		deal = frappe.db.get_value("CRM Deal", {"lead": lead}, "name")
		if deal:
			return deal
	for merged in frappe.get_all(LEAD, filters={"omni_merged_into": lead, "converted": 1}, pluck="name"):
		deal = frappe.db.get_value("CRM Deal", {"lead": merged}, "name")
		if deal:
			return deal
	return None


def default_lead_name(channel_type: str, external_id: str | None) -> str:
	"""Tên tạm "Khách {kênh} {4 số cuối mã}" khi kênh không cho biết tên."""
	tail = (external_id or "")[-4:]
	return _("Khách {0} {1}").format(channel_type, tail).strip()


def create_lead_for_identity(identity: str, signals: dict) -> str:
	"""Tạo CRM Lead tối thiểu cho một Omni Identity; trả tên Lead.

	`signals`: {"phone": E.164 mạnh|None, "email": mạnh|None, "display_name": str|None,
	"last_inbound_at": datetime|None}. SĐT/email chỉ truyền khi là tín hiệu mạnh.
	"""
	ident = frappe.db.get_value(
		"Omni Identity", identity, ["channel_type", "external_id", "display_name"], as_dict=True
	)
	name = (signals.get("display_name") or ident.display_name or "").strip()
	if not name:
		name = default_lead_name(ident.channel_type, ident.external_id or identity)
	doc = frappe.get_doc(
		{
			"doctype": LEAD,
			"first_name": name[:140],
			"mobile_no": signals.get("phone") or None,
			"omni_first_channel": ident.channel_type,
			"omni_last_inbound_at": signals.get("last_inbound_at"),
		}
	)
	doc.flags.ignore_permissions = True
	# Không truyền email vào insert: CRM Lead.validate gọi has_gravatar (mạng, timeout 5 giây) cho Lead mới
	# có email, mà lúc đó `set_new_name` đã giữ khóa dòng của bộ đếm tên (tabSeries), làm nghẽn mọi Lead
	# đang được tạo. Email (đã qua normalize_email) được ghi sau bằng db.set_value, không qua validate.
	doc.insert()
	email = signals.get("email")
	if email:
		frappe.db.set_value(
			LEAD, doc.name, {"email": email, "omni_email_norm": normalized_fields(None, None, email)[1]}
		)
	return doc.name


def fill_blank_contact(lead: str, phone: str | None, email: str | None) -> bool:
	"""Điền SĐT/email mạnh vào Lead nếu Lead chưa có (không ghi đè); trả True nếu có thay đổi."""
	row = frappe.db.get_value(
		LEAD,
		lead,
		["mobile_no", "phone", "email", "omni_phone_e164", "omni_email_norm"],
		as_dict=True,
	)
	values: dict[str, str] = {}
	if phone and not row.omni_phone_e164:
		if not row.mobile_no:
			values["mobile_no"] = phone
		elif not row.phone:
			values["phone"] = phone
	if email and not row.omni_email_norm and not row.email:
		values["email"] = email
	if not values:
		return False
	new_phone, new_email = normalized_fields(
		values.get("mobile_no", row.mobile_no), values.get("phone", row.phone), values.get("email", row.email)
	)
	values.update(omni_phone_e164=new_phone, omni_email_norm=new_email)
	# db.set_value: không chạy validate của CRM (tránh has_gravatar qua mạng); tự tính trường chuẩn hóa.
	frappe.db.set_value(LEAD, lead, values)
	return True
