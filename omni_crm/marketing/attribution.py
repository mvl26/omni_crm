"""Gắn Lead với bài đăng qua `utm_content` = mã bài, và đếm Lead, Deal theo bài (ADR 0029)."""

from __future__ import annotations

import frappe

from omni_crm.marketing.constants import DEAL, LEAD, POST
from omni_crm.marketing.utm import is_valid_post_code, normalize_post_code

LEAD_FIELD = "omni_content_post"


def lead_field_ready() -> bool:
	"""Custom Field `CRM Lead.omni_content_post` đã có (sau migrate) chưa."""
	return frappe.get_meta(LEAD).has_field(LEAD_FIELD)


def match_post(utm_content: str | None) -> str | None:
	"""Tên Omni Content Post có mã bài trùng `utm_content` (không phân biệt hoa thường); None nếu không có."""
	code = normalize_post_code(utm_content)
	if not is_valid_post_code(code) or not frappe.db.exists("DocType", POST):
		return None
	return frappe.db.get_value(POST, code, "name")


def relink_post(post: str) -> int:
	"""Gán bù bài cho các Lead có `omni_utm_content` trùng mã bài mà chưa gắn bài nào; trả số Lead vừa gán."""
	if not lead_field_ready():
		return 0
	code = normalize_post_code(post)
	leads = frappe.db.sql(
		"""select name from `tabCRM Lead`
		where lower(trim(ifnull(omni_utm_content, ''))) = %s and ifnull(omni_content_post, '') = ''""",
		(code,),
		pluck="name",
	)
	for lead in leads:
		frappe.db.set_value(LEAD, lead, LEAD_FIELD, post, update_modified=False)
	return len(leads)


def counts(post: str) -> dict:
	"""{"leads": số Lead không phải Lead thử gắn bài, "deals": số Deal sinh từ các Lead đó}."""
	if not lead_field_ready():
		return {"leads": 0, "deals": 0}
	leads = frappe.get_all(LEAD, filters={LEAD_FIELD: post, "omni_is_test": 0}, pluck="name")
	deals = frappe.db.count(DEAL, {"lead": ["in", leads]}) if leads else 0
	return {"leads": len(leads), "deals": deals}


def refresh_counts(post: str) -> dict:
	"""Ghi lại `lead_count`, `deal_count` của một bài (không đổi `modified`)."""
	c = counts(post)
	frappe.db.set_value(
		POST, post, {"lead_count": c["leads"], "deal_count": c["deals"]}, update_modified=False
	)
	return c


def refresh_all_counts() -> None:
	"""Job đêm: tính lại số Lead, Deal của mọi bài."""
	for post in frappe.get_all(POST, pluck="name"):
		refresh_counts(post)
		frappe.db.commit()
