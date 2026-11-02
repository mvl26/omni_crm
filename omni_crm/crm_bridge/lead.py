"""Tạo, cập nhật CRM Lead và theo dõi sửa tay."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html

from omni_crm import flags
from omni_crm.identity.normalize import is_mobile_phone, is_system_email, normalize_email, normalize_phone

LEAD = "CRM Lead"
MAX_MERGE_HOPS = 10
LEAD_SOURCE = "CRM Lead Source"
DEFAULT_SOURCE_MAP = {
	"Email": "Email",
	"Zalo OA": "Zalo OA",
	"Telephony": "Điện thoại",
	"Meta Lead Ads": "Quảng cáo Meta",
	"Google Lead Form": "Quảng cáo Google",
	"Web Form": "Website",
}
INBOX_ROUTE = "/app/omni-inbox"

# Trường mà sale sửa tay thì bot không được ghi đè (docs/02 mục 15, docs/08). Trường `omni_*` do bot ghi từ
# Đợt 2; trường chưa tồn tại trên site thì has_value_changed luôn False nên không ảnh hưởng.
TRACKED_FIELDS = [
	"first_name",
	"middle_name",
	"last_name",
	"mobile_no",
	"phone",
	"email",
	"organization",
	"omni_need",
	"omni_products",
	"omni_quantity",
	"omni_timeframe",
	"omni_urgency",
	"omni_intent",
]


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
			"source": lead_source_for_channel(ident.channel_type),
			"omni_first_channel": ident.channel_type,
			"omni_last_inbound_at": signals.get("last_inbound_at"),
		}
	)
	doc.flags.ignore_permissions = True
	# Không truyền email vào insert: CRM Lead.validate gọi has_gravatar (mạng, timeout 5 giây) cho Lead mới
	# có email, mà lúc đó `set_new_name` đã giữ khóa dòng của bộ đếm tên (tabSeries), làm nghẽn mọi Lead
	# đang được tạo. Email (đã qua normalize_email) được ghi sau bằng db.set_value, không qua validate.
	with flags.omni_bot():
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


def lead_source_for_channel(channel_type: str | None) -> str | None:
	"""Tên CRM Lead Source cho kênh theo `Omni Settings.default_lead_source_map`; tạo nếu chưa có.

	JSON hỏng hoặc kênh không có trong bản đồ thì dùng bản đồ mặc định; kênh lạ thì trả None.
	"""
	if not channel_type:
		return None
	mapping = dict(DEFAULT_SOURCE_MAP)
	raw = frappe.db.get_single_value("Omni Settings", "default_lead_source_map")
	if raw:
		try:
			loaded = json.loads(raw)
			if isinstance(loaded, dict):
				mapping.update({str(k): str(v) for k, v in loaded.items() if v})
		except ValueError:
			pass
	source = mapping.get(channel_type)
	if source and not frappe.db.exists(LEAD_SOURCE, source):
		frappe.get_doc({"doctype": LEAD_SOURCE, "source_name": source}).insert(ignore_permissions=True)
	return source


def inbox_url(lead: str) -> str:
	"""Đường dẫn mở Hộp thư Omni tại hội thoại của Lead."""
	return f"{INBOX_ROUTE}?lead={lead}"


def ensure_inbox_link(lead: str) -> bool:
	"""Như `add_inbox_link` nhưng lỗi không lan ra: liên kết chỉ là tiện ích, không được làm hỏng lô tin.

	Gọi từ worker SAU khi resolver đã commit (không giữ khóa tên Lead/khóa tín hiệu): CRM
	`on_comment_insert` gọi `frappe.enqueue`, một lượt Redis có thể chậm hoặc lỗi.
	"""
	try:
		return add_inbox_link(lead)
	except Exception:
		frappe.log_error(title="Omni: không thêm được liên kết Hộp thư", message=f"lead={lead}")
		return False


def add_inbox_link(lead: str) -> bool:
	"""Thêm (một lần) Comment trên Lead có liên kết mở Hộp thư Omni; trả True nếu vừa tạo.

	CRM hiện Comment trong tab hoạt động của Lead; đây là cách mở rộng không cần sửa app `crm` (ADR 0004).
	"""
	if frappe.db.exists(
		"Comment",
		{
			"reference_doctype": LEAD,
			"reference_name": lead,
			"comment_type": "Comment",
			"content": ["like", f"%{INBOX_ROUTE}%"],
		},
	):
		return False
	link = f'<a href="{escape_html(inbox_url(lead))}">{escape_html(_("Mở Hộp thư Omni"))}</a>'
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": LEAD,
			"reference_name": lead,
			"comment_by": "Omni CRM",
			"content": f"<p>{escape_html(_('Hội thoại đa kênh của khách này'))}: {link}</p>",
		}
	).insert(ignore_permissions=True)
	return True


def _parse_fields(raw: Any) -> list[str]:
	if not raw:
		return []
	try:
		value = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return []
	return [str(v) for v in value] if isinstance(value, list) else []


def track_manual_edits(doc: Any, method: str | None = None) -> None:
	"""Hook `CRM Lead.on_update`: người dùng sửa trường theo dõi thì ghi tên trường vào `omni_manual_fields`.

	Bỏ qua khi tạo mới, khi code hệ thống ghi (cờ in_omni_bot, job nền, migrate/patch).
	"""
	if doc.flags.in_insert or flags.is_system_write():
		return
	changed = [f for f in TRACKED_FIELDS if doc.has_value_changed(f)]
	if not changed:
		return
	current = _parse_fields(doc.get("omni_manual_fields"))
	updated = current + [f for f in changed if f not in current]
	if updated != current:
		doc.db_set("omni_manual_fields", json.dumps(updated), update_modified=False)
