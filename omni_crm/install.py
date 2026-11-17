"""Thiết lập khi cài app."""

import frappe


def after_install() -> None:
	"""Lưu Omni Settings với mặc định an toàn (sandbox_mode = 1)."""
	settings = frappe.get_doc("Omni Settings")
	settings.sandbox_mode = 1
	settings.flags.ignore_permissions = True
	settings.save()


def after_migrate() -> None:
	"""Ghi phiên bản câu lệnh bot mới nhất vào Omni Settings (trường chỉ đọc)."""
	from omni_crm.bot.prompts.loader import sync_settings

	sync_settings()
	try:  # ADR 0032: nạp ô kịch bản còn thiếu (không ghi đè ô đã sửa trên site); lỗi không làm hỏng migrate
		from omni_crm.script.seed.load import load

		load()
	except Exception:
		frappe.log_error(title="Omni: nạp kho kịch bản lỗi")
