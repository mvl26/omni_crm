"""Điền `CRM Lead.omni_first_response_at` cho Lead cũ (V2-01). Chạy lại an toàn; dry run: `first_response.backfill(True)`."""

import frappe

from omni_crm.crm_bridge import first_response


def execute() -> None:
	"""Đồng bộ Custom Field nếu cột chưa có, rồi điền ngược theo `first_response.backfill`."""
	if not frappe.db.has_column("CRM Lead", first_response.FIELD):
		from frappe.utils.fixtures import sync_fixtures

		sync_fixtures("omni_crm")
		frappe.clear_cache(doctype="CRM Lead")
	first_response.backfill(dry_run=False)
