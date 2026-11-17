"""Tải ảnh bài đăng đã chuyển sang kho S3 và nút kiểm kho trên Omni Settings (ADR 0030).

Ảnh chỉ trả cho người đã đăng nhập và đọc được bài mà ảnh gắn vào; MinIO không cần mở cho trình duyệt.
"""

from __future__ import annotations

import frappe
from frappe import _

from omni_crm.marketing import s3_media
from omni_crm.marketing.constants import POST


@frappe.whitelist(methods=["GET"])
def post_image(file: str) -> None:
	"""Trả ảnh (inline) của dòng File `file` gắn vào một Omni Content Post mà người xem đọc được."""
	row = frappe.db.get_value(
		"File",
		file,
		["attached_to_doctype", "attached_to_name", "file_name", s3_media.KEY_FIELD],
		as_dict=True,
	)
	if not row or not row.get(s3_media.KEY_FIELD) or row.attached_to_doctype != POST:
		raise frappe.DoesNotExistError(_("Không có ảnh này"))
	frappe.has_permission(POST, "read", doc=row.attached_to_name, throw=True)
	try:
		body, content_type = s3_media.get_object(row.get(s3_media.KEY_FIELD))
	except s3_media.S3Error as e:
		frappe.throw(str(e))
	frappe.local.response.update(
		{
			"type": "download",
			"filename": row.file_name or file,
			"filecontent": body,
			"content_type": content_type,
			"display_content_as": "inline",
		}
	)


@frappe.whitelist(methods=["POST"])
def check_storage() -> dict:
	"""Nút "Kiểm kho ảnh S3" (Omni Admin): ghi, đọc, xóa một object thử."""
	if not {"Omni Admin", "System Manager"} & set(frappe.get_roles()):
		raise frappe.PermissionError(_("Chỉ Omni Admin kiểm được kho ảnh S3"))
	try:
		return s3_media.check_storage()
	except s3_media.S3Error as e:
		frappe.throw(str(e))
