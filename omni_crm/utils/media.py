"""Chuẩn hóa và kiểm an toàn URL đính kèm để hiển thị (V3-05, ADR 0018).

Ảnh nội tuyến (thẻ <img> trong trình duyệt của nhân viên) chỉ cho phép: https tới host thuộc danh sách CDN Zalo, hoặc
đường dẫn nội bộ của Frappe (/files, /private/files). Mọi URL khác chỉ được hiện thành liên kết có nhãn.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Hậu tố host CDN Zalo. Xác nhận từ dữ liệu thật người dùng dán: zgif-v2.zdn.vn, photo-stal-27.zdn.vn.
# zadn.vn, zaloapp.com, zalo.me: theo hiểu biết sẵn có về CDN Zalo, CHƯA xác nhận từ tài liệu chính thức.
ZALO_MEDIA_HOST_SUFFIXES = ("zdn.vn", "zadn.vn", "zaloapp.com", "zalo.me")
INLINE_IMAGE_TYPES = ("image", "gif", "sticker")
LOCAL_PREFIXES = ("/files/", "/private/files/")
SAFE_PREFIXES = ("http://", "https://", *LOCAL_PREFIXES)


def is_safe_link(url: str) -> bool:
	"""URL có thể làm liên kết bấm được: http(s) hoặc /files, /private/files (không javascript:, data:, //host)."""
	return isinstance(url, str) and url.startswith(SAFE_PREFIXES) and not any(c in url for c in "\r\n\t\0")


def url_host(url: str) -> str:
	"""Tên host (chữ thường) của URL tuyệt đối; rỗng nếu không đọc được."""
	try:
		return (urlparse(url).hostname or "").lower()
	except ValueError:
		return ""


def host_allowed(host: str) -> bool:
	"""Host là một trong các CDN Zalo cho phép (khớp đúng hoặc là tên miền con, không khớp hậu tố chuỗi bừa)."""
	return any(host == s or host.endswith("." + s) for s in ZALO_MEDIA_HOST_SUFFIXES)


def is_inline_image_url(url: str) -> bool:
	"""Được dùng làm src của ảnh nội tuyến: /files nội bộ hoặc https tới CDN Zalo cho phép."""
	if not is_safe_link(url):
		return False
	if url.startswith(LOCAL_PREFIXES):
		return True
	return url.startswith("https://") and host_allowed(url_host(url))


def filename_from_url(url: str) -> str:
	"""Tên tệp cuối đường dẫn (bỏ query); rỗng nếu không có."""
	try:
		path = urlparse(url).path
	except ValueError:
		return ""
	return path.rstrip("/").rsplit("/", 1)[-1][:120]
