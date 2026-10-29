"""Quyền đọc tin/định danh theo Lead xem được (docs/02 mục 16, phát hiện D1-02).

Sales User có quyền Đọc ở mức DocType trên Omni Inbound/Outbound Message và Omni Identity; hai hook dưới đây
thu hẹp về các bản ghi có `lead` mà người dùng được xem theo quyền của chính CRM Lead (kể cả phân cấp bán
hàng của CRM, vì gọi lại đúng hook của CRM). Omni Admin, System Manager và Administrator thấy tất cả.
"""

from __future__ import annotations

from typing import Any

import frappe

LEAD = "CRM Lead"
FULL_ACCESS_ROLES = {"Omni Admin", "System Manager"}


def _full_access(user: str) -> bool:
	return user == "Administrator" or bool(FULL_ACCESS_ROLES & set(frappe.get_roles(user)))


def _lead_condition(user: str) -> str:
	"""Điều kiện SQL CRM đặt cho CRM Lead (qua hooks `permission_query_conditions`); rỗng = thấy hết."""
	for path in frappe.get_hooks("permission_query_conditions").get(LEAD, []):
		cond = frappe.get_attr(path)(user)
		if cond:
			return cond
	return ""


def lead_scoped_condition(table: str, user: str | None = None) -> str:
	"""SQL giới hạn `table`.`lead` trong các Lead người dùng xem được."""
	user = user or frappe.session.user
	if _full_access(user):
		return ""
	cond = _lead_condition(user)
	if not cond:  # CRM cho thấy mọi Lead (vd Sales Manager ngoài phân cấp)
		return ""
	return f"`tab{table}`.`lead` in (select `name` from `tabCRM Lead` where {cond})"


def inbound_conditions(user: str | None = None) -> str:
	"""permission_query_conditions của Omni Inbound Message."""
	return lead_scoped_condition("Omni Inbound Message", user)


def outbound_conditions(user: str | None = None) -> str:
	"""permission_query_conditions của Omni Outbound Message."""
	return lead_scoped_condition("Omni Outbound Message", user)


def identity_conditions(user: str | None = None) -> str:
	"""permission_query_conditions của Omni Identity."""
	return lead_scoped_condition("Omni Identity", user)


def has_lead_scoped_permission(doc: Any, ptype: str | None = None, user: str | None = None) -> bool | None:
	"""has_permission cho ba DocType trên: người ngoài nhóm toàn quyền chỉ đọc bản ghi có Lead xem được."""
	user = user or frappe.session.user
	if _full_access(user):
		return None
	lead = doc.get("lead")
	if not lead:
		return False
	return bool(frappe.has_permission(LEAD, "read", doc=lead, user=user))


def candidate_conditions(user: str | None = None) -> str:
	"""permission_query_conditions của Omni Duplicate Candidate: chỉ cặp mà người dùng đọc được CẢ hai Lead.

	Nếu chỉ đọc được một Lead thì ẩn cặp, vì `reason` có thể lộ SĐT/tên của Lead kia.
	"""
	user = user or frappe.session.user
	if _full_access(user):
		return ""
	cond = _lead_condition(user)
	if not cond:
		return ""
	sub = f"select `name` from `tabCRM Lead` where {cond}"
	t = "`tabOmni Duplicate Candidate`"
	return f"({t}.`lead_a` in ({sub}) and {t}.`lead_b` in ({sub}))"


def has_candidate_permission(doc: Any, ptype: str | None = None, user: str | None = None) -> bool | None:
	"""has_permission của Omni Duplicate Candidate: phải đọc được cả lead_a và lead_b."""
	user = user or frappe.session.user
	if _full_access(user):
		return None
	leads = [doc.get("lead_a"), doc.get("lead_b")]
	if not all(leads):
		return False
	return all(frappe.has_permission(LEAD, "read", doc=x, user=user) for x in leads)
