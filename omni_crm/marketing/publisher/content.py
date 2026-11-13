"""Dữ liệu một lần đăng (thuần + đọc DB), dùng chung cho mọi adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import get_url

from omni_crm.marketing.publisher.errors import PublishValidationError

ZALO_TITLE_MAX = 150
ZALO_DESC_MAX = 300


@dataclass
class Payload:
	target: str
	platform: str
	post_format: str
	channel: str | None
	title: str
	caption: str
	tracking_url: str
	images: list[str] = field(default_factory=list)  # URL công khai tuyệt đối, ảnh chính trước

	@property
	def marker(self) -> str:
		"""Chuỗi duy nhất của lần đăng nằm trong nội dung bài (để đối chiếu sau khi hết giờ chờ)."""
		return urlparse(self.tracking_url).query

	def message(self) -> str:
		"""Caption kèm link theo dõi ở cuối (mọi định dạng Facebook, kể cả ảnh, để đối chiếu được)."""
		caption = (self.caption or "").strip()
		return f"{caption}\n\n{self.tracking_url}" if self.tracking_url else caption


def public_image_url(file_url: str | None) -> str:
	"""URL tuyệt đối của ảnh trong CRM; ném PublishValidationError nếu ảnh riêng tư / không có trên đĩa / không phải File."""
	file_url = str(file_url or "").strip()
	if not file_url:
		raise PublishValidationError(_("Thiếu ảnh"))
	if file_url.startswith(("http://", "https://")):
		if not file_url.startswith("https://"):
			raise PublishValidationError(_("Ảnh phải dùng https"))
		return file_url
	if file_url.startswith("/private/files/"):
		raise PublishValidationError(
			_("Ảnh {0} là tệp riêng tư: nền tảng không tải được. Tải lại ảnh ở chế độ công khai").format(
				os.path.basename(file_url)
			)
		)
	if not file_url.startswith("/files/"):
		raise PublishValidationError(_("Đường dẫn ảnh không hợp lệ"))
	row = frappe.db.get_value("File", {"file_url": file_url}, ["name", "is_private"], as_dict=True)
	if not row or row.is_private:
		raise PublishValidationError(_("Không tìm thấy ảnh công khai {0}").format(os.path.basename(file_url)))
	path = frappe.get_site_path("public", file_url.lstrip("/"))
	if not os.path.exists(path):
		raise PublishValidationError(
			_("Tệp ảnh {0} không còn trên máy chủ").format(os.path.basename(file_url))
		)
	return get_url(file_url)


def build(target_name: str) -> Payload:
	"""Payload của một nền tảng đăng (đọc bài, ảnh); kiểm dữ liệu chung."""
	t = frappe.get_doc("Omni Content Target", target_name)
	item = frappe.get_doc("Omni Content Item", t.content_item)
	raw_images = [item.media] if item.media else []
	raw_images += [m.image for m in item.get("media_items") or [] if m.image]
	needs_images = t.post_format in ("fb_photo", "fb_multi_photo", "zalo_article")
	images = [public_image_url(u) for u in raw_images] if needs_images else []
	payload = Payload(
		target=t.name,
		platform=t.platform,
		post_format=t.post_format,
		channel=t.channel_account,
		title=(item.title or "").strip(),
		caption=(item.caption or "").strip(),
		tracking_url=t.tracking_url or "",
		images=images,
	)
	validate(payload)
	return payload


def validate(p: Payload) -> None:
	"""Kiểm trước khi đăng (Prompt 2 mục 5): caption rỗng, ảnh thiếu, tiêu đề Zalo quá dài."""
	if not p.caption:
		raise PublishValidationError(_("Caption rỗng"))
	if "[cần bổ sung]" in f"{p.title or ''} {p.caption}":
		raise PublishValidationError(_("Bài còn chỗ [cần bổ sung]"))
	if p.post_format == "fb_photo" and not p.images:
		raise PublishValidationError(_("Bài ảnh Facebook chưa có ảnh"))
	if p.post_format == "fb_multi_photo" and len(p.images) < 2:
		raise PublishValidationError(_("Bài nhiều ảnh cần ít nhất 2 ảnh"))
	if p.post_format == "zalo_article":
		if not p.images:
			raise PublishValidationError(_("Bài viết Zalo OA thiếu ảnh bìa"))
		if not p.title:
			raise PublishValidationError(_("Bài viết Zalo OA thiếu tiêu đề"))
		if len(p.title) > ZALO_TITLE_MAX:
			raise PublishValidationError(
				_("Tiêu đề Zalo dài {0} ký tự, tối đa {1}").format(len(p.title), ZALO_TITLE_MAX)
			)
