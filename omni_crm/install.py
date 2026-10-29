"""Thiết lập khi cài app."""

import frappe


def after_install() -> None:
	"""Lưu Omni Settings với mặc định an toàn (sandbox_mode = 1)."""
	settings = frappe.get_doc("Omni Settings")
	settings.sandbox_mode = 1
	settings.flags.ignore_permissions = True
	settings.save()
