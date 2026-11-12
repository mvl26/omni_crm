"""Gửi ảnh/tệp của Lead qua Messenger (ADR 0023): kiểm tệp thuộc Lead, loại và cỡ; đọc nội dung lúc gửi.

Tệp phải nằm trong CRM (File gắn vào Lead hoặc Deal của Lead), như Zalo (ADR 0021): không ai gửi được tệp của Lead khác
hay URL ngoài. Messenger nhận ảnh (jpg, png, gif), video (mp4), âm thanh (mp3, m4a) và tệp thường, tối đa 25 MB
**[Nguồn phụ, kiểm bằng thử thật trước khi bật]**. Tệp riêng tư không có URL công khai nên client tải lên kiểu multipart.
"""

from __future__ import annotations

import mimetypes
import os

import frappe
from frappe import _
from frappe.utils import cint

IMAGE, VIDEO, AUDIO, FILE = "image", "video", "audio", "file"
MB = 1024 * 1024
MAX_BYTES = 25 * MB
EXTENSIONS = {
	IMAGE: (".jpg", ".jpeg", ".png", ".gif"),
	VIDEO: (".mp4",),
	AUDIO: (".mp3", ".m4a"),
	FILE: (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".zip"),
}
ACCEPT = ",".join(ext for exts in EXTENSIONS.values() for ext in exts)
LEAD = "CRM Lead"
DEAL = "CRM Deal"


class MediaError(frappe.ValidationError):
	"""Tệp không gửi được qua Messenger (không thuộc Lead, sai loại, quá cỡ, không đọc được)."""


def kind_of(filename: str) -> str | None:
	"""Loại đính kèm Messenger theo đuôi tệp; None nếu không nhận."""
	name = (filename or "").lower()
	return next((k for k, exts in EXTENSIONS.items() if name.endswith(exts)), None)


def mime_of(filename: str) -> str:
	return mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def allowed_types_text() -> str:
	return "JPG, PNG, GIF, MP4, MP3, M4A, PDF, Word, Excel, CSV, TXT, ZIP"


def attachment_for_lead(file_url: str, lead: str) -> dict:
	"""{"file_url", "kind", "name"} nếu `file_url` là File gắn vào Lead (hoặc Deal của Lead), đúng loại và cỡ; ném MediaError.

	Người gọi đã kiểm quyền ghi Lead.
	"""
	file_url = str(file_url or "").strip()
	if not file_url.startswith(("/files/", "/private/files/")):
		raise MediaError(_("Tệp đính kèm không hợp lệ"))
	rows = frappe.get_all(
		"File",
		filters={"file_url": file_url, "is_folder": 0},
		fields=["name", "file_name", "file_size", "attached_to_doctype", "attached_to_name"],
		limit=20,
	)
	deals = set(frappe.get_all(DEAL, filters={"lead": lead}, pluck="name")) if rows else set()
	row = next(
		(
			r
			for r in rows
			if (r.attached_to_doctype == LEAD and r.attached_to_name == lead)
			or (r.attached_to_doctype == DEAL and r.attached_to_name in deals)
		),
		None,
	)
	if not row:
		raise MediaError(_("Tệp này không thuộc Lead đang mở"))
	name = (row.file_name or os.path.basename(file_url))[:140]
	kind = kind_of(name) or kind_of(file_url)
	if not kind:
		raise MediaError(_("Messenger chỉ nhận {0}").format(allowed_types_text()))
	if cint(row.file_size) > MAX_BYTES:
		raise MediaError(_("Tệp lớn hơn {0} MB, Messenger không nhận").format(MAX_BYTES // MB))
	return {"file_url": file_url, "kind": kind, "name": name}


def read_content(file_url: str) -> bytes:
	"""Nội dung tệp trong CRM; tệp mất/hỏng thì ném MediaError (lỗi vĩnh viễn, không thử lại)."""
	try:
		content = frappe.get_doc("File", {"file_url": file_url}).get_content()
	except Exception as e:
		raise MediaError(_("Không đọc được tệp đính kèm ({0})").format(type(e).__name__)) from e
	if isinstance(content, str):
		content = content.encode()
	if not content:
		raise MediaError(_("Tệp đính kèm rỗng"))
	if len(content) > MAX_BYTES:
		raise MediaError(_("Tệp lớn hơn {0} MB, Messenger không nhận").format(MAX_BYTES // MB))
	return content
