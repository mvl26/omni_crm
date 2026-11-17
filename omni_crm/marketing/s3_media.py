"""Ảnh bài đăng trên kho S3 nội bộ (MinIO AIStor Free, ADR 0030).

Sau khi lưu bài (`on_change`, đã qua `attach_files_to_document`), ảnh trên đĩa được đẩy lên S3, `File.file_url` và
`Omni Content Post.image` đổi sang đường tải qua CRM (`api/media.py::post_image`), bản trên đĩa bị xóa. MinIO lỗi thì ảnh
ở lại trên đĩa, người soạn được báo, job mỗi giờ đẩy lại. Secret key chỉ đọc bằng `get_password`, không vào log.
"""

from __future__ import annotations

import os
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.marketing.constants import POST

KEY_FIELD = "omni_s3_key"
DOWNLOAD_PREFIX = "/api/method/omni_crm.api.media.post_image?file="
DEFAULT_REGION = "us-east-1"
DEFAULT_PREFIX = "omni-content-post/"
LOCAL_PREFIXES = ("/files/", "/private/files/")


class S3Error(Exception):
	"""Lỗi kho S3, thông điệp an toàn để hiện cho người dùng (không có secret)."""


def enabled() -> bool:
	"""Công tắc `s3_media_enabled` và đủ cấu hình tối thiểu."""
	s = frappe.get_cached_doc("Omni Settings")
	return bool(cint(s.get("s3_media_enabled")) and s.get("s3_endpoint_url") and s.get("s3_bucket"))


def download_url(file_name: str) -> str:
	"""Đường tải ảnh qua CRM cho dòng File `file_name`."""
	return f"{DOWNLOAD_PREFIX}{file_name}"


def is_download_url(url: str | None) -> bool:
	"""`url` là đường tải ảnh S3 qua CRM."""
	return str(url or "").startswith(DOWNLOAD_PREFIX)


def _settings():
	return frappe.get_single("Omni Settings")


def _client():
	"""Client boto3 tới MinIO: chữ ký s3v4, địa chỉ kiểu path, timeout ngắn, thử lại 2 lần."""
	import boto3
	from botocore.config import Config

	s = _settings()
	return boto3.client(
		"s3",
		endpoint_url=(s.s3_endpoint_url or "").strip(),
		aws_access_key_id=(s.s3_access_key or "").strip(),
		aws_secret_access_key=s.get_password("s3_secret_key", raise_exception=False) or "",
		region_name=(s.s3_region or DEFAULT_REGION).strip(),
		verify=bool(cint(s.s3_verify_tls)),
		config=Config(
			signature_version="s3v4",
			s3={"addressing_style": "path"},
			connect_timeout=5,
			read_timeout=30,
			retries={"max_attempts": 2},
		),
	)


def _bucket() -> str:
	return (_settings().s3_bucket or "").strip()


def _wrap(action: str, exc: Exception) -> S3Error:
	"""Lỗi boto3/botocore -> S3Error gọn: mã lỗi S3 hoặc tên lớp lỗi (không kèm secret)."""
	code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
	return S3Error(_("Kho ảnh S3: {0} không được ({1})").format(action, code or type(exc).__name__))


def put_object(key: str, body: bytes, content_type: str) -> None:
	"""Ghi object; ném S3Error."""
	try:
		_client().put_object(Bucket=_bucket(), Key=key, Body=body, ContentType=content_type)
	except Exception as e:
		raise _wrap(_("ghi ảnh"), e) from None


def get_object(key: str) -> tuple[bytes, str]:
	"""(bytes, Content-Type) của object; ném S3Error."""
	try:
		res = _client().get_object(Bucket=_bucket(), Key=key)
		return res["Body"].read(), res.get("ContentType") or "application/octet-stream"
	except Exception as e:
		raise _wrap(_("đọc ảnh"), e) from None


def delete_object(key: str) -> None:
	"""Xóa object; ném S3Error."""
	try:
		_client().delete_object(Bucket=_bucket(), Key=key)
	except Exception as e:
		raise _wrap(_("xóa ảnh"), e) from None


def object_key(post: str, file_doc: Any) -> str:
	"""Khóa object: <prefix><mã bài>/<tên File><đuôi tệp>."""
	prefix = (_settings().s3_prefix or DEFAULT_PREFIX).strip().lstrip("/")
	if prefix and not prefix.endswith("/"):
		prefix += "/"
	ext = os.path.splitext(file_doc.file_name or file_doc.file_url or "")[1].lower()
	return f"{prefix}{post}/{file_doc.name}{ext}"


def _local_file(post: str, file_url: str):
	name = frappe.db.get_value(
		"File",
		{"file_url": file_url, "attached_to_doctype": POST, "attached_to_name": post, "is_folder": 0},
		"name",
	)
	return frappe.get_doc("File", name) if name else None


def offload(post: str, file_url: str) -> str | None:
	"""Đẩy ảnh `file_url` (trên đĩa) của bài lên S3; trả đường tải mới, None nếu không có gì để đẩy. Ném S3Error."""
	from omni_crm.marketing.media import ALLOWED

	if not str(file_url or "").startswith(LOCAL_PREFIXES):
		return None
	file_doc = _local_file(post, file_url)
	if not file_doc:
		return None
	content = file_doc.get_content()
	content = content.encode() if isinstance(content, str) else content
	key = object_key(post, file_doc)
	ext = os.path.splitext(key)[1]
	put_object(key, content, ALLOWED.get(ext, "application/octet-stream"))
	shared = frappe.get_all(
		"File", filters={"file_url": file_url, "name": ["!=", file_doc.name]}, limit=1, pluck="name"
	)
	url = download_url(file_doc.name)
	# Bỏ content_hash: Frappe gộp tệp tải lên trùng nội dung bằng hash rồi tìm tệp cũ trên đĩa; ảnh đã rời đĩa thì việc
	# đó báo lỗi "Cannot access file path" và chặn tải lên. Không hash = tệp mới trùng nội dung được lưu như tệp riêng.
	frappe.db.set_value(
		"File", file_doc.name, {"file_url": url, KEY_FIELD: key, "content_hash": None}, update_modified=False
	)
	frappe.db.set_value(POST, post, "image", url, update_modified=False)
	if not shared:
		path = file_doc.get_full_path()
		frappe.db.after_commit.add(lambda: _remove_quietly(path))  # chỉ xóa đĩa khi DB đã ghi đường mới
	return url


def _remove_quietly(path: str) -> None:
	try:
		os.remove(path)
	except OSError:
		pass


def on_post_change(doc, method=None) -> None:
	"""Hook `on_change` của Omni Content Post: đẩy ảnh mới tải lên sang S3; lỗi thì báo, giữ ảnh trên đĩa."""
	if not doc.image or not enabled() or not str(doc.image).startswith(LOCAL_PREFIXES):
		return
	try:
		url = offload(doc.name, doc.image)
	except S3Error as e:
		frappe.msgprint(
			_("{0}. Ảnh vẫn lưu trên máy chủ CRM, sẽ tự đẩy lại trong vòng 1 giờ.").format(e),
			indicator="orange",
			alert=True,
		)
		return
	if url:
		doc.image = url


def push_pending() -> None:
	"""Job mỗi giờ: đẩy lên S3 ảnh của các bài còn trên đĩa (MinIO từng lỗi, hoặc ảnh có trước khi bật)."""
	if not enabled():
		return
	posts = frappe.db.sql(
		f"""select name, image from `tab{POST}`
		where image like '/files/%%' or image like '/private/files/%%' order by modified desc""",
		as_dict=True,
	)
	for row in posts:
		try:
			offload(row.name, row.image)
			frappe.db.commit()
		except S3Error:
			frappe.db.rollback()
			frappe.log_error(title="Omni: chưa đẩy được ảnh bài đăng lên S3", message=f"post={row.name}")
			return  # kho đang lỗi: dừng lượt này


def on_file_delete(doc, method=None) -> None:
	"""Hook `after_delete` của File: xóa object S3 của File đó (nếu không File nào khác dùng chung khóa)."""
	key = doc.get(KEY_FIELD)
	if not key or frappe.db.exists("File", {KEY_FIELD: key}):
		return
	try:
		delete_object(key)
	except S3Error:
		frappe.log_error(title="Omni: chưa xóa được ảnh trên S3", message=f"file={doc.name} key={key}")


def check_storage() -> dict:
	"""Ghi, đọc, xóa một object thử; trả {"ok": True} hoặc ném S3Error. Dùng cho nút trên Omni Settings."""
	if not enabled():
		raise S3Error(_("Chưa bật hoặc chưa điền endpoint, bucket của kho ảnh S3"))
	key = f"{(_settings().s3_prefix or DEFAULT_PREFIX).strip().lstrip('/')}_kiem-tra/{frappe.generate_hash(length=8)}.txt"
	body = b"omni_crm s3 check"
	put_object(key, body, "text/plain")
	got, _ctype = get_object(key)
	delete_object(key)
	if got != body:
		raise S3Error(_("Kho ảnh S3 trả nội dung khác nội dung vừa ghi"))
	return {"ok": True, "bucket": _bucket(), "key": key}
