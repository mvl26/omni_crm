"""Kiểm tra cấu hình Email Account của hộp thư bán hàng (ADR 0005: tắt cơ chế tự tạo Lead của CRM)."""

from __future__ import annotations

import frappe
from frappe import _

CRM_AUTO_LEAD_FIELD = "create_lead_from_incoming_email"  # Custom Field do app crm thêm vào Email Account


def crm_auto_lead_issues(email_account: str | None) -> list[str]:
	"""Các cấu hình của Email Account khiến CRM/Frappe tự tạo Lead từ thư đến (phải tắt); rỗng nếu ổn."""
	if not email_account or not frappe.db.exists("Email Account", email_account):
		return []
	meta = frappe.get_meta("Email Account")
	issues = []
	if meta.has_field(CRM_AUTO_LEAD_FIELD) and frappe.db.get_value(
		"Email Account", email_account, CRM_AUTO_LEAD_FIELD
	):
		issues.append(
			_("Bỏ chọn 'Create Lead from Incoming Emails' trên Email Account {0}").format(email_account)
		)
	if frappe.db.get_value("Email Account", email_account, "append_to") == "CRM Lead":
		issues.append(
			_("Để trống 'Append To' trên Email Account {0} (không được là CRM Lead)").format(email_account)
		)
	return issues
