"""Phạm vi xem của Dashboard CSKH (docs/13 mục 1.3, 4): Sales User chỉ thấy Lead mình xem được; trưởng nhóm thấy toàn đội.

Mọi số liệu tính bằng SQL gộp có điều kiện quyền của CRM Lead (cùng `permissions._lead_condition`), không kiểm quyền
từng dòng. `scope` do client gửi KHÔNG bao giờ mở rộng quyền: "team" chỉ có nghĩa với người được xem nhiều Lead.
"""

from __future__ import annotations

import frappe

from omni_crm import flags, permissions

LEAD = "CRM Lead"
DEAL = "CRM Deal"
SCOPES = ("mine", "team")


def is_team_lead(user: str | None = None) -> bool:
	"""Người dùng có vai trò trưởng nhóm (`team_lead_role`), Omni Admin, System Manager hoặc Administrator."""
	user = user or frappe.session.user
	if permissions._full_access(user):
		return True
	return flags.care_setting("team_lead_role") in set(frappe.get_roles(user))


def lead_condition(user: str | None = None) -> str:
	"""SQL giới hạn CRM Lead (viết trên bảng `tabCRM Lead` không bí danh); "1=1" nếu thấy hết."""
	user = user or frappe.session.user
	if permissions._full_access(user):
		return "1=1"
	return permissions._lead_condition(user) or "1=1"


def deal_condition(user: str | None = None) -> str:
	"""SQL giới hạn CRM Deal theo hook quyền của CRM (bảng `tabCRM Deal` không bí danh)."""
	user = user or frappe.session.user
	if permissions._full_access(user):
		return "1=1"
	for path in frappe.get_hooks("permission_query_conditions").get(DEAL, []):
		cond = frappe.get_attr(path)(user)
		if cond:
			return cond
	return "1=1"


def normalize_scope(scope: str | None, user: str | None = None) -> str:
	""" "mine" | "team". Mặc định "team" cho trưởng nhóm, "mine" cho còn lại; giá trị lạ -> mặc định."""
	default = "team" if is_team_lead(user) else "mine"
	return scope if scope in SCOPES else default


def mine_lead_sql() -> str:
	"""Điều kiện "của tôi" trên `tabCRM Lead`: người phụ trách hoặc được giao (ToDo) là người dùng hiện tại."""
	return (
		"(`tabCRM Lead`.`lead_owner` = %(me)s or `tabCRM Lead`.`name` in (select `reference_name` from `tabToDo` "
		"where `reference_type` = 'CRM Lead' and `allocated_to` = %(me)s and `status` != 'Cancelled'))"
	)


def lead_where(scope: str, user: str | None = None) -> tuple[str, dict]:
	"""(mệnh đề WHERE, tham số) cho truy vấn trên `tabCRM Lead`: chưa gộp + quyền xem + (nếu "mine") của tôi."""
	user = user or frappe.session.user
	# V2-05: Lead thử nghiệm (Meta Testing Tool / is_test của Google) không tính vào báo cáo nào
	clause = (
		"`tabCRM Lead`.`omni_merged_into` is null and ifnull(`tabCRM Lead`.`omni_is_test`, 0) = 0 "
		f"and ({lead_condition(user)})"
	)
	params: dict = {"me": user}
	if normalize_scope(scope, user) == "mine":
		clause += f" and {mine_lead_sql()}"
	return clause, params
