"""Ảnh bài marketing trên kho S3 nội bộ (MinIO AIStor Free, ADR 0033).

Ảnh của `Omni Content Item` (ảnh bìa `media`, ảnh thêm `media_items[].image`) là tệp công khai `/files/...` trên đĩa
máy chủ CRM, vì Facebook/Zalo tải ảnh theo địa chỉ (`publisher/content.py::public_image_url`). Khi bật kho S3:

- lưu bài (`on_change`) hoặc job mỗi giờ: ảnh `/files/...` được ghi lên MinIO, ô ảnh và `File.file_url` đổi sang
  `/omni-media/<tên File>-<chữ ký>.<đuôi>`, bản trên đĩa bị xóa khi không còn ai dùng;
- CRM phát lại ảnh ở địa chỉ đó (`MediaRenderer`, không cần đăng nhập, giống `/files/`), đọc từ MinIO. MinIO không mở
  ra Internet; chữ ký HMAC theo khóa mã hóa của site nên không đoán được địa chỉ ảnh khác;
- MinIO lỗi thì ảnh ở lại đĩa, người soạn được báo, job đẩy lại sau.

Ảnh riêng tư (`/private/files/`) không bao giờ được chuyển (chuyển sẽ biến chúng thành công khai). Secret key chỉ đọc
bằng `get_password`, không vào log.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_url, now_datetime
from frappe.website.page_renderers.base_renderer import BaseRenderer
from werkzeug.wrappers import Response

SETTINGS = "Omni Media Storage"
ITEM = "Omni Content Item"
MEDIA = "Omni Content Media"
TARGET = "Omni Content Target"
KEY_FIELD = "omni_s3_key"
ROUTE = "omni-media"
PUBLIC_PREFIX = f"/{ROUTE}/"
LOCAL_PREFIX = "/files/"
DEFAULT_REGION = "us-east-1"
DEFAULT_PREFIX = "omni-content-item/"
BUSY = "Đang xử lý"
SCHEDULED = "Đã lên lịch"
SOON_MINUTES = 10  # job mỗi giờ không chuyển ảnh của bài sắp đăng trong 10 phút tới
# Chỉ ảnh raster mà Facebook/Zalo nhận. KHÔNG có SVG: SVG chứa được script, phát inline trên tên miền CRM là XSS
# (nginx của Frappe ép tải về cho .svg/.html/.xml trong /files/; /omni-media/ không có luật đó nên tự chặn ở đây).
IMAGE_TYPES = {
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
	".gif": "image/gif",
	".webp": "image/webp",
}
_PATH_RE = re.compile(rf"^{ROUTE}/([A-Za-z0-9]+)-([0-9a-f]{{32}})(\.[A-Za-z0-9]{{1,5}})?$")


class S3Error(Exception):
	"""Lỗi kho S3, thông điệp an toàn để hiện cho người dùng (không có secret)."""


# ---------------------------------------------------------------- cấu hình


def _settings():
	return frappe.get_cached_doc(SETTINGS)


def configured() -> bool:
	"""Đã điền endpoint và bucket (chưa cần bật công tắc): đủ để bấm "Kiểm kho ảnh S3"."""
	s = _settings()
	return bool((s.get("endpoint_url") or "").strip() and (s.get("bucket") or "").strip())


def enabled() -> bool:
	"""Công tắc `enabled` đang bật và đã điền endpoint, bucket."""
	return bool(cint(_settings().get("enabled"))) and configured()


def verify_tls(value) -> bool:
	"""Kiểm chứng chỉ TLS: mặc định BẬT khi ô chưa từng được lưu (None/""), chỉ tắt khi đã lưu 0."""
	return True if value in (None, "") else bool(cint(value))


def tls_verify_param(verify_value, ca_bundle) -> bool | str:
	"""Tham số `verify` của boto3: False khi tắt kiểm TLS; đường dẫn tệp CA khi MinIO dùng chứng chỉ của CA nội bộ
	(boto3 dùng bộ CA riêng, không đọc kho chứng chỉ của hệ điều hành); True (bộ CA công khai) khi không điền."""
	if not verify_tls(verify_value):
		return False
	path = str(ca_bundle or "").strip()
	if not path:
		return True
	if not os.path.isfile(path):
		raise S3Error(_("Không thấy tệp CA {0} trên máy chủ CRM").format(path))
	return path


def _prefix() -> str:
	prefix = (_settings().get("prefix") or DEFAULT_PREFIX).strip().lstrip("/")
	return prefix if not prefix or prefix.endswith("/") else prefix + "/"


# ---------------------------------------------------------------- S3


def _client():
	"""Client boto3 tới MinIO: chữ ký s3v4, địa chỉ kiểu path, timeout ngắn, thử lại 2 lần."""
	import boto3
	from botocore.config import Config

	s = frappe.get_single(SETTINGS)
	return boto3.client(
		"s3",
		endpoint_url=(s.endpoint_url or "").strip(),
		aws_access_key_id=(s.access_key or "").strip(),
		aws_secret_access_key=s.get_password("secret_key", raise_exception=False) or "",
		region_name=(s.region or DEFAULT_REGION).strip(),
		verify=tls_verify_param(s.get("verify_tls"), s.get("ca_bundle")),
		config=Config(
			signature_version="s3v4",
			s3={"addressing_style": "path"},
			connect_timeout=5,
			read_timeout=30,
			retries={"max_attempts": 2},
		),
	)


def _bucket() -> str:
	return (_settings().get("bucket") or "").strip()


def _wrap(action: str, exc: Exception) -> S3Error:
	"""Lỗi boto3/botocore -> S3Error gọn: mã lỗi S3 hoặc tên lớp lỗi (không kèm secret)."""
	if isinstance(exc, S3Error):
		return exc  # lỗi cấu hình đã có thông điệp rõ (ví dụ thiếu tệp CA)
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


def list_objects(prefix: str | None = None, limit: int = 10000) -> list[dict]:
	"""Object trong bucket dưới `prefix` (mặc định thư mục ảnh bài): [{key, size, modified}], `modified` là datetime
	UTC có múi giờ. Dừng ở `limit` object. Ném S3Error. Dùng cho báo cáo "Kho ảnh MinIO" (chỉ đọc)."""
	out: list[dict] = []
	try:
		pages = _client().get_paginator("list_objects_v2").paginate(
			Bucket=_bucket(), Prefix=_prefix() if prefix is None else prefix
		)
		for page in pages:
			for obj in page.get("Contents") or []:
				out.append({"key": obj["Key"], "size": obj.get("Size") or 0, "modified": obj.get("LastModified")})
				if len(out) >= limit:
					return out
	except Exception as e:
		raise _wrap(_("liệt kê ảnh"), e) from None
	return out


# ---------------------------------------------------------------- địa chỉ công khai có chữ ký


def _signature(file_name: str, key: str) -> str:
	from frappe.utils.password import get_encryption_key

	secret = f"omni-media:{get_encryption_key()}".encode()
	return hmac.new(secret, f"{file_name}:{key}".encode(), hashlib.sha256).hexdigest()[:32]


def public_path(file_name: str, key: str) -> str:
	"""Địa chỉ tương đối CRM phát lại ảnh: /omni-media/<tên File>-<chữ ký>.<đuôi>."""
	ext = os.path.splitext(key)[1].lower()
	return f"{PUBLIC_PREFIX}{file_name}-{_signature(file_name, key)}{ext}"


def is_public_url(url: str | None) -> bool:
	"""`url` là địa chỉ ảnh đã chuyển sang S3 (tương đối, như lưu trong ô ảnh)."""
	return str(url or "").startswith(PUBLIC_PREFIX)


def resolve(path: str) -> tuple[str, str] | None:
	"""(tên File, khóa S3) khi `path` (có hoặc không có dấu / đầu) là địa chỉ hợp lệ, chữ ký đúng; ngược lại None."""
	m = _PATH_RE.match(str(path or "").strip().lstrip("/"))
	if not m:
		return None
	file_name, sig = m.group(1), m.group(2)
	key = frappe.db.get_value("File", file_name, KEY_FIELD)
	if not key or not hmac.compare_digest(sig, _signature(file_name, key)):
		return None
	return file_name, key


def absolute_public_url(url: str) -> str:
	"""Địa chỉ tuyệt đối để Facebook/Zalo tải ảnh; ném S3Error khi địa chỉ không còn hợp lệ."""
	if not resolve(url):
		raise S3Error(_("Không tìm thấy ảnh {0} trên kho S3").format(os.path.basename(str(url))))
	return get_url(url)


class MediaRenderer(BaseRenderer):
	"""Hook `page_renderer`: phát ảnh /omni-media/... từ MinIO (khách không đăng nhập, như /files/)."""

	def can_render(self) -> bool:
		return self.path.startswith(f"{ROUTE}/")

	def render(self):
		found = resolve(self.path)
		if not found:
			return Response(_("Không có ảnh này"), status=404, mimetype="text/plain")
		try:
			body, content_type = get_object(found[1])
		except S3Error:
			frappe.log_error(title="Omni: không đọc được ảnh trên S3", message=f"file={found[0]}")
			return Response(_("Kho ảnh tạm thời không đọc được"), status=503, mimetype="text/plain")
		if content_type not in IMAGE_TYPES.values():
			return Response(_("Không có ảnh này"), status=404, mimetype="text/plain")
		return Response(
			body,
			status=200,
			mimetype=content_type,
			headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"},
		)


# ---------------------------------------------------------------- chuyển ảnh lên S3


def item_urls(item: Any) -> list[str]:
	"""Ảnh của bài theo thứ tự: ảnh bìa rồi ảnh thêm (chuỗi như lưu trong ô)."""
	urls = [item.get("media")] if item.get("media") else []
	urls += [row.get("image") for row in item.get("media_items") or [] if row.get("image")]
	return urls


def _busy(item_name: str, soon: bool = False) -> bool:
	"""Bài có nền tảng đang đăng (hoặc, với `soon`, sắp đến giờ đăng): không đổi ảnh lúc đó."""
	if frappe.db.exists(TARGET, {"content_item": item_name, "status": BUSY}):
		return True
	if soon:
		return bool(
			frappe.db.exists(
				TARGET,
				{
					"content_item": item_name,
					"status": SCHEDULED,
					"scheduled_at": ["<=", add_to_date(now_datetime(), minutes=SOON_MINUTES)],
				},
			)
		)
	return False


def _file_for(item_name: str, url: str):
	"""Dòng File của ảnh `url` thuộc bài: gắn vào bài, hoặc chưa gắn vào đâu (ảnh thêm tải từ /crm). None nếu ảnh
	thuộc tài liệu khác (không chuyển tệp của người khác)."""
	rows = frappe.get_all(
		"File",
		filters={"file_url": url, "is_folder": 0, "is_private": 0},
		fields=["name", "attached_to_doctype", "attached_to_name"],
		order_by="creation asc",
	)
	for row in rows:
		if row.attached_to_doctype == ITEM and row.attached_to_name == item_name:
			return frappe.get_doc("File", row.name)
	for row in rows:
		if not row.attached_to_doctype:
			return frappe.get_doc("File", row.name)
	return None


def _replace_refs(item_name: str | None, old: str, new: str) -> None:
	"""Đổi ô ảnh `old` -> `new` (của một bài, hoặc mọi bài khi `item_name` None)."""
	cond = {"media": old}
	if item_name:
		cond["name"] = item_name
	for name in frappe.get_all(ITEM, filters=cond, pluck="name"):
		frappe.db.set_value(ITEM, name, "media", new, update_modified=False)
	cond = {"image": old, "parenttype": ITEM}
	if item_name:
		cond["parent"] = item_name
	for name in frappe.get_all(MEDIA, filters=cond, pluck="name"):
		frappe.db.set_value(MEDIA, name, "image", new, update_modified=False)


def _still_used(url: str) -> bool:
	"""Còn dòng File hoặc ô ảnh nào dùng `url` (sau khi bài này đã đổi sang địa chỉ mới)."""
	return bool(
		frappe.db.exists("File", {"file_url": url})
		or frappe.db.exists(ITEM, {"media": url})
		or frappe.db.exists(MEDIA, {"image": url})
	)


def offload(item_name: str, url: str) -> str | None:
	"""Đẩy ảnh công khai `url` của bài lên S3; trả địa chỉ mới, None nếu không có gì để đẩy. Ném S3Error."""
	if not str(url or "").startswith(LOCAL_PREFIX):
		return None
	ext = os.path.splitext(url)[1].lower()
	if ext not in IMAGE_TYPES:
		return (
			None  # loại tệp khác (svg, pdf, mp4...): ở lại /files/ như trước, nginx phục vụ theo luật sẵn có
		)
	file_doc = _file_for(item_name, url)
	if not file_doc:
		return None
	path = file_doc.get_full_path()
	if not os.path.exists(path):
		return None  # tệp đã mất trên đĩa: để publisher báo lỗi như hiện nay
	with open(path, "rb") as fh:
		content = fh.read()
	key = f"{_prefix()}{item_name}/{file_doc.name}{ext}"
	put_object(key, content, IMAGE_TYPES[ext])
	new = public_path(file_doc.name, key)
	# Bỏ content_hash: Frappe gộp tệp tải lên trùng nội dung bằng hash rồi tìm tệp cũ trên đĩa; ảnh đã rời đĩa thì việc
	# đó báo lỗi "Cannot access file path" và chặn tải lên. Không hash = tệp mới trùng nội dung được lưu như tệp riêng.
	frappe.db.set_value(
		"File", file_doc.name, {"file_url": new, KEY_FIELD: key, "content_hash": None}, update_modified=False
	)
	_replace_refs(item_name, url, new)
	if not _still_used(url):
		frappe.db.after_commit.add(lambda: _remove_quietly(path))  # chỉ xóa đĩa khi DB đã ghi đường mới
	return new


def _remove_quietly(path: str) -> None:
	try:
		os.remove(path)
	except OSError:
		pass


def offload_item(item_name: str) -> int:
	"""Đẩy mọi ảnh còn trên đĩa của một bài; trả số ảnh đã đẩy. Ném S3Error."""
	item = frappe.get_doc(ITEM, item_name)
	done = 0
	for url in dict.fromkeys(item_urls(item)):  # giữ thứ tự, bỏ trùng
		if offload(item_name, url):
			done += 1
	return done


def on_item_change(doc, method=None) -> None:
	"""Hook `on_change` của Omni Content Item: chuyển ảnh mới tải lên sang S3; lỗi thì báo, giữ ảnh trên đĩa."""
	if not enabled() or not any(movable(u) for u in item_urls(doc)):
		return
	if _busy(doc.name):
		return  # đang đăng: job mỗi giờ chuyển sau
	try:
		offload_item(doc.name)
	except S3Error as e:
		frappe.msgprint(
			_("{0}. Ảnh vẫn lưu trên máy chủ CRM, sẽ tự đẩy lại trong vòng 1 giờ.").format(e),
			indicator="orange",
			alert=True,
		)
		return
	# Cho bản trả về giao diện đúng địa chỉ mới (DB đã ghi trong offload)
	doc.media = frappe.db.get_value(ITEM, doc.name, "media")
	for row in doc.get("media_items") or []:
		row.image = frappe.db.get_value(MEDIA, row.name, "image")


def movable(url: str | None) -> bool:
	"""Ảnh còn trên đĩa và thuộc loại chuyển được lên S3 (IMAGE_TYPES)."""
	url = str(url or "")
	return url.startswith(LOCAL_PREFIX) and os.path.splitext(url)[1].lower() in IMAGE_TYPES


def _local_refs() -> list[tuple[str, str]]:
	"""(bài, ô ảnh) của mọi ảnh còn trên đĩa, chỉ loại chuyển được."""
	rows = frappe.db.sql(
		f"""select name, media from `tab{ITEM}` where media like '/files/%%'
		union all select parent, image from `tab{MEDIA}` where parenttype = %s and image like '/files/%%'""",
		(ITEM,),
	)
	return [(name, url) for name, url in rows if movable(url)]


def push_pending(limit: int = 200) -> None:
	"""Job mỗi giờ: đẩy lên S3 ảnh của các bài còn trên đĩa (MinIO từng lỗi, hoặc ảnh có trước khi bật)."""
	if not enabled():
		return
	names = list(dict.fromkeys(name for name, _url in _local_refs()))
	for name in names[: cint(limit) or None]:
		if _busy(name, soon=True):
			continue
		try:
			offload_item(name)
			frappe.db.commit()
		except S3Error:
			frappe.db.rollback()
			frappe.log_error(title="Omni: chưa đẩy được ảnh bài marketing lên S3", message=f"item={name}")
			return  # kho đang lỗi: dừng lượt này


def pending_count() -> dict:
	"""Số ảnh còn trên đĩa sẽ được chuyển, số ảnh đã ở S3, số ảnh ở lại đĩa vì loại tệp (để IT kiểm, `bench execute`)."""
	rows = frappe.db.sql(
		f"""select media from `tab{ITEM}` where media like '/files/%%'
		union all select image from `tab{MEDIA}` where parenttype = %s and image like '/files/%%'""",
		(ITEM,),
		pluck=True,
	)
	on_disk = sum(1 for u in rows if movable(u))
	return {
		"on_disk": on_disk,
		"on_s3": frappe.db.count("File", {KEY_FIELD: ["is", "set"]}),
		"kept_other_type": len(rows) - on_disk,
	}


# ---------------------------------------------------------------- quay lui, xóa


def pull_back(dry_run: int | bool = 1, limit: int = 0, item: str | None = None) -> dict:
	"""Quay lui (ADR 0033): kéo ảnh từ kho S3 về đĩa máy chủ CRM thành tệp công khai /files/ như trước khi bật.

	Dùng khi thôi dùng MinIO hoặc MinIO sắp ngừng: TẮT "Lưu ảnh bài marketing trên kho S3" trước, rồi chạy
	`bench --site <site> execute omni_crm.marketing.s3_media.pull_back --kwargs "{'dry_run': 0}"`.
	Mặc định chỉ đếm (dry_run). `item`: chỉ một bài (thử trước một bài). Object trên S3 được GIỮ lại (xóa tay sau khi
	kiểm). Lỗi một tệp thì ghi lại, làm tệp khác.
	"""
	from frappe.core.doctype.file.utils import get_content_hash, get_file_name

	filters = {KEY_FIELD: ["is", "set"]}
	if item:
		filters[KEY_FIELD] = ["like", f"%/{item}/%"]
	files = frappe.get_all(
		"File",
		filters=filters,
		fields=["name", "file_name", "file_url", KEY_FIELD],
		order_by="creation asc",
		limit_page_length=cint(limit) or 0,
	)
	result = {"files": len(files), "pulled": 0, "errors": []}
	if cint(dry_run):
		return result
	if enabled():
		raise S3Error(_('Tắt "Lưu ảnh bài marketing trên kho S3" trước khi kéo ảnh về đĩa'))
	for row in files:
		try:
			body, _ctype = get_object(row.get(KEY_FIELD))
			base = row.file_name or f"{row.name}.bin"
			name = get_file_name(base, frappe.generate_hash(length=6))
			path = frappe.get_site_path("public", "files", name)
			while os.path.exists(path):  # không ghi đè tệp đang có trên đĩa
				name = get_file_name(base, frappe.generate_hash(length=6))
				path = frappe.get_site_path("public", "files", name)
			with open(path, "wb") as fh:
				fh.write(body)
			local = f"{LOCAL_PREFIX}{name}"
			frappe.db.set_value(
				"File",
				row.name,
				{"file_url": local, KEY_FIELD: None, "content_hash": get_content_hash(body), "is_private": 0},
				update_modified=False,
			)
			_replace_refs(None, row.file_url, local)
			frappe.db.commit()
			result["pulled"] += 1
		except Exception as e:
			frappe.db.rollback()
			result["errors"].append(f"{row.name}: {e if isinstance(e, S3Error) else type(e).__name__}")
	return result


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
	"""Ghi, đọc, xóa một object thử; trả {"ok": True, ...} hoặc ném S3Error. Dùng cho nút Kiểm kho ảnh S3.

	Chỉ cần đã điền endpoint và bucket (đã Lưu), KHÔNG cần bật công tắc: kiểm trước rồi mới bật.
	"""
	if not configured():
		raise S3Error(_("Chưa điền Endpoint S3 hoặc Bucket (điền rồi bấm Lưu trước khi kiểm)"))
	key = f"{_prefix()}_kiem-tra/{frappe.generate_hash(length=8)}.txt"
	body = b"omni_crm s3 check"
	put_object(key, body, "text/plain")
	got, _ctype = get_object(key)
	delete_object(key)
	if got != body:
		raise S3Error(_("Kho ảnh S3 trả nội dung khác nội dung vừa ghi"))
	return {"ok": True, "bucket": _bucket(), "key": key}
