"""Gửi ảnh/tệp của Lead qua Messenger (ADR 0023): kiểm tệp thuộc Lead, loại và cỡ; đọc nội dung lúc gửi.

Tệp phải nằm trong CRM (File gắn vào Lead hoặc Deal của Lead), như Zalo (ADR 0021): không ai gửi được tệp của Lead khác
hay URL ngoài. Messenger nhận ảnh (jpg, png, gif), video (mp4), âm thanh (mp3, m4a) và tệp thường, tối đa 25 MB
**[Nguồn phụ, kiểm bằng thử thật trước khi bật]**. Tệp riêng tư không có URL công khai nên client tải lên kiểu multipart.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import tempfile
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

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


def _lead_file(file_url: str, lead: str) -> Any:
	"""Dòng File có `file_url` gắn vào Lead (hoặc Deal của Lead); ném MediaError nếu không có."""
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
	return row


def attachment_for_lead(file_url: str, lead: str) -> dict:
	"""{"file_url", "kind", "name"} nếu `file_url` là File gắn vào Lead (hoặc Deal của Lead), đúng loại và cỡ; ném MediaError.

	Người gọi đã kiểm quyền ghi Lead.
	"""
	file_url = str(file_url or "").strip()
	row = _lead_file(file_url, lead)
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


# ---- Ghi âm (ADR 0027 đợt 2): trình duyệt ghi WebM/Ogg/MP4, máy chủ đổi sang MP3 bằng ffmpeg rồi gửi kiểu audio ----

VOICE_PREFIX = (
	"ghi-am-"  # tên tệp ghi âm do tab Messenger tải lên; chỉ tệp có tiền tố này mới bị xóa sau khi đổi
)
VOICE_INPUT = (".webm", ".ogg", ".oga", ".opus", ".mp4", ".m4a", ".aac", ".wav", ".mp3")
VOICE_MAX_INPUT = 15 * MB
VOICE_MAX_SECONDS = 300
FFMPEG_TIMEOUT = 90
FFMPEG_CONF = "omni_ffmpeg_path"  # site_config, tùy chọn; mặc định tìm `ffmpeg` trong PATH
# Chỉ đọc tệp cục bộ, chỉ các định dạng âm thanh/hộp chứa trình duyệt ghi ra: không theo danh sách phát, không mở URL.
FFMPEG_FORMATS = "matroska,webm,ogg,mov,mp4,m4a,3gp,3g2,mj2,wav,mp3,aac"


def ffmpeg_path() -> str | None:
	"""Đường dẫn ffmpeg dùng được, hoặc None (tab ẩn nút ghi âm, API từ chối)."""
	conf = str(frappe.conf.get(FFMPEG_CONF) or "").strip()
	if conf:
		return conf if os.path.isfile(conf) and os.access(conf, os.X_OK) else None
	return shutil.which("ffmpeg")


def voice_to_mp3(file_url: str, lead: str) -> dict:
	"""Đổi bản ghi âm (File của Lead) sang MP3 mono 64 kbps, lưu thành File riêng tư mới của Lead; trả attachment `audio`.

	Bản ghi gốc do tab tải lên (tên `ghi-am-…`) bị xóa sau khi đổi xong; tệp khác của Lead giữ nguyên. Ném MediaError.
	"""
	file_url = str(file_url or "").strip()
	row = _lead_file(file_url, lead)
	name = row.file_name or os.path.basename(file_url)
	if not name.lower().endswith(VOICE_INPUT):
		raise MediaError(_("Bản ghi âm không đúng định dạng"))
	if cint(row.file_size) > VOICE_MAX_INPUT:
		raise MediaError(_("Bản ghi âm lớn hơn {0} MB").format(VOICE_MAX_INPUT // MB))
	ffmpeg = ffmpeg_path()
	if not ffmpeg:
		raise MediaError(_("Máy chủ chưa cài ffmpeg nên chưa gửi được ghi âm"))
	src_doc = frappe.get_doc("File", row.name)
	src = src_doc.get_full_path()
	with tempfile.TemporaryDirectory(prefix="omni-voice-") as tmp:
		dst = os.path.join(tmp, "out.mp3")
		cmd = [
			ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
			"-protocol_whitelist", "file", "-format_whitelist", FFMPEG_FORMATS,
			"-i", src, "-t", str(VOICE_MAX_SECONDS), "-map", "0:a:0", "-vn",
			"-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "64k", dst,
		]  # fmt: skip
		try:
			res = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False)
		except subprocess.TimeoutExpired as e:
			raise MediaError(_("Đổi định dạng ghi âm quá lâu")) from e
		if res.returncode != 0 or not os.path.isfile(dst) or not os.path.getsize(dst):
			frappe.log_error(
				title="Omni: đổi ghi âm Messenger lỗi",
				message=(res.stderr or b"").decode("utf-8", "replace")[-2000:],
			)
			raise MediaError(_("Không đổi được bản ghi âm sang MP3"))
		with open(dst, "rb") as fh:
			content = fh.read()
	if len(content) > MAX_BYTES:
		raise MediaError(_("Tệp lớn hơn {0} MB, Messenger không nhận").format(MAX_BYTES // MB))
	mp3_name = f"{VOICE_PREFIX}{now_datetime().strftime('%Y%m%d-%H%M%S')}.mp3"
	mp3 = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": mp3_name,
			"is_private": 1,
			"content": content,
			"attached_to_doctype": row.attached_to_doctype,
			"attached_to_name": row.attached_to_name,
		}
	).insert(ignore_permissions=True)
	if name.startswith(VOICE_PREFIX) and not name.lower().endswith(".mp3"):
		frappe.delete_doc("File", src_doc.name, ignore_permissions=True, force=True)
	return {"file_url": mp3.file_url, "kind": AUDIO, "name": mp3_name}


def copy_to_lead(file_url: str, name: str, lead: str) -> dict:
	"""Chép một tệp trong CRM sang File riêng tư của Lead `lead` (chuyển tiếp, ADR 0027 đợt 2); trả attachment đã kiểm."""
	content = read_content(file_url)
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": (name or os.path.basename(file_url) or "tep")[:140],
			"is_private": 1,
			"content": content,
			"attached_to_doctype": LEAD,
			"attached_to_name": lead,
		}
	).insert(ignore_permissions=True)
	return attachment_for_lead(doc.file_url, lead)
