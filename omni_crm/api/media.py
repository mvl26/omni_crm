"""Nút "Kiểm kho ảnh S3" trên Omni Media Storage (ADR 0033). Ảnh đã chuyển được phát ở /omni-media/ (s3_media)."""

from __future__ import annotations

import frappe
from frappe import _

from omni_crm.marketing import s3_media


@frappe.whitelist(methods=["POST"])
def check_storage() -> dict:
	"""Omni Admin / System Manager: ghi, đọc, xóa một object thử trên kho S3 (chưa cần bật công tắc)."""
	if not {"Omni Admin", "System Manager"} & set(frappe.get_roles()):
		raise frappe.PermissionError(_("Chỉ Omni Admin kiểm được kho ảnh S3"))
	try:
		return s3_media.check_storage()
	except s3_media.S3Error as e:
		frappe.throw(str(e))
