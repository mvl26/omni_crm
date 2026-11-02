"""API đọc dữ liệu nhà thầu theo Lead (docs/14 mục 6, D1-13 mục 10).

Người xem được Lead của Organization mới xem được Contact và gói thầu của Organization đó; mọi hàm kiểm quyền
bằng `frappe.has_permission("CRM Lead", "read", lead)`, không chỉ dựa vào quyền DocType.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm.api.admin import require_omni_admin
from omni_crm.prospect import hospital

LEAD = "CRM Lead"
ORG_FIELDS = [
	"name",
	"organization_name",
	"website",
	"omni_tax_id",
	"omni_short_name",
	"omni_address",
	"omni_province",
	"omni_region",
	"omni_company_phone",
	"omni_company_email",
	"omni_verified",
	"omni_interested_package",
	"omni_awards_in_period",
	"omni_hospital_count",
	"omni_max_items",
	"omni_items_for_scoring",
	"omni_award_value_bn",
	"omni_active_awards",
	"omni_meets_criteria",
	"omni_fit_score",
	"omni_fit_tier",
	"omni_fit_computed_at",
]
CONTACT_FIELDS = [
	"name",
	"full_name",
	"designation",
	"email_id",
	"mobile_no",
	"omni_contact_role",
	"omni_info_source",
	"omni_has_zalo",
	"omni_consent",
]
AWARD_FIELDS = [
	"name",
	"hospital",
	"notice_no",
	"package_name",
	"category",
	"award_date",
	"end_date",
	"award_value",
	"item_count",
	"role",
	"source_url",
]
REFUSED = "Từ chối"


def _lead_organization(lead: str) -> str | None:
	"""Kiểm quyền đọc Lead rồi trả tên Organization của Lead (None nếu Lead chưa gắn nhà thầu)."""
	if not frappe.db.exists(LEAD, lead):
		frappe.throw(_("Không tìm thấy Lead {0}").format(lead), frappe.DoesNotExistError)
	frappe.has_permission(LEAD, "read", doc=lead, throw=True)
	return frappe.db.get_value(LEAD, lead, "organization") or None


@frappe.whitelist()
def get_organization(lead: str) -> dict[str, Any]:
	"""Thông tin nhà thầu của Lead (kèm điểm phù hợp); {} nếu Lead chưa gắn nhà thầu."""
	org = _lead_organization(lead)
	if not org or not frappe.db.exists("CRM Organization", org):
		return {}
	return frappe.db.get_value("CRM Organization", org, ORG_FIELDS, as_dict=True)


@frappe.whitelist()
def get_contacts(lead: str) -> list[dict[str, Any]]:
	"""Người liên hệ của nhà thầu của Lead; `do_not_contact` = 1 khi người đó từ chối nhận liên hệ."""
	org = _lead_organization(lead)
	if not org:
		return []
	rows = frappe.get_all(
		"Contact",
		filters={"company_name": org},
		fields=CONTACT_FIELDS,
		order_by="creation",
		limit_page_length=200,
	)
	for r in rows:
		r["do_not_contact"] = 1 if r.get("omni_consent") == REFUSED else 0
	return rows


@frappe.whitelist()
def get_awards(lead: str) -> list[dict[str, Any]]:
	"""Gói thầu đã trúng của nhà thầu của Lead, mới nhất trước."""
	org = _lead_organization(lead)
	if not org:
		return []
	return frappe.get_all(
		"Omni Bid Award",
		filters={"organization": org},
		fields=AWARD_FIELDS,
		order_by="award_date desc",
		limit_page_length=500,
	)


@frappe.whitelist()
def list_similar_hospitals(threshold: float | str = hospital.SIMILAR_FROM) -> list[dict]:
	"""Cặp tên bệnh viện giống nhau >= ngưỡng để gộp bằng Omni Hospital Alias (chỉ Omni Admin)."""
	require_omni_admin()
	return hospital.similar_hospital_names(float(threshold))
