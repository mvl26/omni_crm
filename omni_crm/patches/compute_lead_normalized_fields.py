"""Tính omni_phone_e164 / omni_email_norm cho Lead cũ (theo lô 500, chạy lại an toàn)."""

import frappe

from omni_crm.crm_bridge.lead import normalized_fields

BATCH = 500


def execute() -> None:
	"""Duyệt CRM Lead theo tên tăng dần, chỉ ghi khi giá trị tính lại khác giá trị đang lưu."""
	if not frappe.db.has_column("CRM Lead", "omni_phone_e164"):
		from frappe.utils.fixtures import sync_fixtures

		sync_fixtures("omni_crm")  # Custom Field của app chưa được đồng bộ (patch chạy trước fixtures)
		frappe.clear_cache(doctype="CRM Lead")
	last = ""
	while True:
		rows = frappe.get_all(
			"CRM Lead",
			filters={"name": [">", last]},
			fields=["name", "mobile_no", "phone", "email", "omni_phone_e164", "omni_email_norm"],
			order_by="name asc",
			limit_page_length=BATCH,
		)
		if not rows:
			break
		for r in rows:
			phone, email = normalized_fields(r.mobile_no, r.phone, r.email)
			if (r.omni_phone_e164 or "") != phone or (r.omni_email_norm or "") != email:
				frappe.db.set_value(
					"CRM Lead",
					r.name,
					{"omni_phone_e164": phone, "omni_email_norm": email},
					update_modified=False,
				)
		last = rows[-1].name
		frappe.db.commit()
