"""Đọc ảnh của bài đăng để gửi lên Meta (ADR 0029); ảnh đã chuyển sang kho S3 thì đọc từ S3 (ADR 0030)."""

from __future__ import annotations

import mimetypes
import os

import frappe
from frappe import _

from omni_crm.marketing.constants import POST

MAX_BYTES = 10 * 1024 * 1024  # Facebook nhận ảnh tối đa 10 MB
ALLOWED = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}


class MediaError(Exception):
	"""Ảnh không dùng được; thông điệp an toàn để hiện cho người dùng."""


def is_stored_file_url(file_url: str | None) -> bool:
	"""Ảnh là tệp đã tải lên CRM (trên đĩa, hoặc đã chuyển sang kho S3), không phải link ngoài."""
	from omni_crm.marketing import s3_media

	url = str(file_url or "")
	return url.startswith(("/files/", "/private/files/")) or s3_media.is_download_url(url)


def image_file(post: str, file_url: str):
	"""Dòng File của ảnh `file_url` gắn vào bài `post`; ném MediaError nếu không có."""
	# Chỉ tệp gắn vào CHÍNH bài này: không bao giờ đăng một tệp riêng tư của bản ghi khác lên Page.
	name = frappe.db.get_value(
		"File",
		{"file_url": file_url, "attached_to_doctype": POST, "attached_to_name": post, "is_folder": 0},
		"name",
	)
	if not name:
		raise MediaError(_("Không tìm thấy tệp ảnh của bài"))
	return frappe.get_doc("File", name)


def read_bytes(file_doc) -> bytes:
	"""Nội dung tệp: tệp đã chuyển sang kho S3 (ADR 0030) thì đọc từ S3, còn lại đọc trên đĩa."""
	from omni_crm.marketing import s3_media

	key = file_doc.get(s3_media.KEY_FIELD)
	if key:
		try:
			return s3_media.get_object(key)[0]
		except s3_media.S3Error as e:
			raise MediaError(str(e)) from None
	content = file_doc.get_content()
	return content.encode() if isinstance(content, str) else content


def read_image(post: str, file_url: str) -> tuple[str, bytes, str]:
	"""(tên tệp, bytes, MIME) của ảnh bài đăng; ném MediaError khi sai loại, quá cỡ hoặc không đọc được."""
	file_doc = image_file(post, file_url)
	name = file_doc.file_name or os.path.basename(file_url)
	ext = os.path.splitext(name)[1].lower()
	if ext not in ALLOWED:
		raise MediaError(_("Ảnh phải là JPG, PNG hoặc GIF: {0}").format(name))
	try:
		content = read_bytes(file_doc)
	except MediaError:
		raise
	except Exception:
		raise MediaError(_("Không đọc được tệp ảnh {0}").format(name)) from None
	if len(content) > MAX_BYTES:
		raise MediaError(_("Ảnh lớn hơn 10 MB: {0}").format(name))
	return name, content, ALLOWED.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
