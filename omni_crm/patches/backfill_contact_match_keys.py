"""V2-02: điền khóa khớp SĐT/email chuẩn hóa cho Contact có sẵn (trường kỹ thuật ẩn). Không đổi dữ liệu hiển thị. Chạy lại được."""

import frappe

from omni_crm.prospect import contacts


def execute() -> None:
	for name in frappe.get_all("Contact", pluck="name", limit_page_length=0):
		doc = frappe.get_doc("Contact", name)
		contacts.validate(doc)
		frappe.db.set_value(
			"Contact",
			name,
			{"omni_phones_e164": doc.omni_phones_e164, "omni_emails_norm": doc.omni_emails_norm},
			update_modified=False,
		)
