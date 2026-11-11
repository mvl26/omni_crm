"""Ảnh / tệp sale gửi kèm qua Zalo OA (V3-09, ADR 0021).

Luật lấy từ tài liệu chính thức Zalo (stc-developers.zdn.vn, đọc 2026-11-11):

| loại  | API tải lên              | định dạng          | tối đa | gửi bằng `message/cs`                                  |
|-------|--------------------------|--------------------|--------|--------------------------------------------------------|
| image | /v2.0/oa/upload/image    | jpg, png           | 1 MB   | template `media`, media_type image, kèm chữ <= 2000 ký tự |
| gif   | /v2.0/oa/upload/gif      | gif                | 5 MB   | template `media`, media_type gif, BẮT BUỘC width/height |
| file  | /v2.0/oa/upload/file     | pdf, doc, docx, csv| 5 MB   | attachment type `file` (payload.token), KHÔNG kèm chữ  |

Mỗi API tải lên có hạn mức 5.000 lượt/tháng; Zalo giữ tệp tối đa 7 ngày (khách xem lại sau đó thì báo tệp không còn).
Ảnh JPG/PNG lớn hơn 1 MB được nén lại (thu nhỏ dần, xuất JPEG) trước khi tải; GIF và tệp quá cỡ thì từ chối.

Công tắc `Omni Settings.zalo_media_send_enabled` tắt (mặc định): chỉ tài khoản Zalo thử nghiệm nhận được, chặn ngay
trong ZaloClient trước cả nhánh sandbox (cùng danh sách trắng với luồng xin số, V3-07).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import cint, nowdate

IMAGE, GIF, FILE = "image", "gif", "file"
KINDS = (IMAGE, GIF, FILE)
MB = 1024 * 1024
LIMITS = {IMAGE: 1 * MB, GIF: 5 * MB, FILE: 5 * MB}
EXTENSIONS = {
	".jpg": IMAGE,
	".jpeg": IMAGE,
	".png": IMAGE,
	".gif": GIF,
	".pdf": FILE,
	".doc": FILE,
	".docx": FILE,
	".csv": FILE,
}
MAX_SOURCE_IMAGE = 15 * MB  # ảnh gốc lớn hơn thế này thì không nén, báo chọn ảnh khác
CAPTION_MAX = 2000
UPLOAD_MONTHLY_QUOTA = 5000
UPLOAD_KEY = "omni:zalo:upload_count"
LEAD = "CRM Lead"
DEAL = "CRM Deal"


class MediaError(frappe.ValidationError):
	"""Tệp không gửi được qua Zalo (sai định dạng, quá cỡ, không thuộc Lead...); thông điệp tiếng Việt cho sale."""


@dataclass(frozen=True)
class Attachment:
	file_url: str
	kind: str
	name: str


def enabled() -> bool:
	"""Công tắc gửi ảnh/tệp cho mọi khách."""
	return bool(cint(frappe.db.get_single_value("Omni Settings", "zalo_media_send_enabled")))


def recipient_allowed(user_id: str | None) -> bool:
	"""Được gửi ảnh/tệp tới mã Zalo này không: công tắc bật, hoặc là tài khoản thử nghiệm (danh sách của V3-07)."""
	from omni_crm.channels.zalo.phone_request import WHITELIST_USER_IDS

	return enabled() or str(user_id or "") in WHITELIST_USER_IDS


def kind_of(filename: str) -> str | None:
	"""Loại Zalo theo đuôi tệp (image/gif/file); None nếu Zalo không nhận."""
	return EXTENSIONS.get(os.path.splitext(str(filename or ""))[1].lower())


def allowed_types_text() -> str:
	return _("ảnh JPG/PNG/GIF hoặc tệp PDF/DOC/DOCX/CSV")


def attachment_for_lead(file_url: str, lead: str) -> Attachment:
	"""Kiểm tệp `file_url` là tệp đính kèm của Lead (hoặc Deal của Lead) và đúng loại Zalo nhận; trả Attachment.

	Người gọi đã kiểm quyền ghi Lead. Tệp phải nằm trong CRM (File) để không ai gửi được tệp của Lead khác hay URL ngoài.
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
		raise MediaError(_("Zalo chỉ nhận {0}").format(allowed_types_text()))
	size = cint(row.file_size)
	limit = MAX_SOURCE_IMAGE if kind == IMAGE else LIMITS[kind]
	if size > limit:
		raise MediaError(too_big_text(kind, size))
	return Attachment(file_url=file_url, kind=kind, name=name)


def too_big_text(kind: str, size: int) -> str:
	mb = f"{size / MB:.1f}"
	if kind == IMAGE:
		return _("Ảnh {0} MB quá lớn (tối đa {1} MB trước khi nén)").format(mb, MAX_SOURCE_IMAGE // MB)
	if kind == GIF:
		return _("Ảnh GIF {0} MB quá lớn: Zalo nhận tối đa 5 MB").format(mb)
	return _("Tệp {0} MB quá lớn: Zalo nhận tối đa 5 MB").format(mb)


def read_content(file_url: str) -> bytes:
	"""Nội dung nhị phân của tệp trong CRM."""
	doc = frappe.get_doc("File", {"file_url": file_url})
	content = doc.get_content()
	return content.encode() if isinstance(content, str) else content


def shrink_image(content: bytes, limit: int = LIMITS[IMAGE]) -> tuple[bytes, str]:
	"""Ảnh JPG/PNG <= `limit` byte: giữ nguyên nếu đã vừa, không thì thu nhỏ và xuất JPEG; trả (nội dung, đuôi tệp)."""
	from PIL import Image, ImageOps

	if len(content) <= limit:
		img = Image.open(io.BytesIO(content))
		return content, ".png" if (img.format or "").upper() == "PNG" else ".jpg"
	img = ImageOps.exif_transpose(Image.open(io.BytesIO(content)))
	if img.mode not in ("RGB", "L"):
		background = Image.new("RGB", img.size, (255, 255, 255))
		rgba = img.convert("RGBA")
		background.paste(rgba, mask=rgba.split()[-1])
		img = background
	side = max(img.size)
	for max_side in (2560, 2048, 1600, 1280, 1024, 800):
		work = img.copy()
		if side > max_side:
			work.thumbnail((max_side, max_side))
		for quality in (85, 75, 65):
			buf = io.BytesIO()
			work.save(buf, format="JPEG", quality=quality, optimize=True)
			if buf.tell() <= limit:
				return buf.getvalue(), ".jpg"
	raise MediaError(_("Không nén được ảnh xuống dưới 1 MB để gửi Zalo"))


def gif_size(content: bytes) -> tuple[int, int]:
	"""(rộng, cao) của ảnh GIF (Zalo bắt buộc khi gửi GIF)."""
	from PIL import Image

	return Image.open(io.BytesIO(content)).size


def _upload_key(kind: str) -> str:
	return frappe.cache().make_key(f"{UPLOAD_KEY}:{nowdate()[:7]}:{kind}")


def uploads_this_month(kind: str) -> int:
	"""Số lần đã gọi API tải lên `kind` trong tháng (đếm ở Redis; mất cache thì đếm lại từ 0)."""
	return cint(frappe.cache().get(_upload_key(kind)))


def reserve_upload(kind: str) -> None:
	"""Giữ một lượt tải lên trong hạn mức tháng của Zalo (5.000/tháng/API); hết thì ném MediaError."""
	cache = frappe.cache()
	key = _upload_key(kind)
	used = cint(cache.incr(key))
	if used == 1:
		cache.expire(key, 40 * 86400)
	if used > UPLOAD_MONTHLY_QUOTA:
		raise MediaError(
			_("Đã hết hạn mức tải {0} lên Zalo trong tháng ({1} lượt)").format(kind, UPLOAD_MONTHLY_QUOTA)
		)
