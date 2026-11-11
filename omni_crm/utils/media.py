"""Chuẩn hóa và kiểm an toàn URL đính kèm để hiển thị (V3-05, ADR 0020).

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


def _has_bad_chars(url: str, *, allow_space: bool = False) -> bool:
	"""URL có ký tự điều khiển, khoảng trắng (trừ khi cho phép) hoặc `\\` (trình duyệt coi `\\` như `/`, parser Python thì không)."""
	return any(
		c == "\\" or ord(c) < 32 or ord(c) == 127 or (c.isspace() and not (allow_space and c == " "))
		for c in url
	)


def _bad_local_path(url: str) -> bool:
	"""Đường dẫn /files có đoạn `..` (kể cả mã hóa %2e) hoặc `//`."""
	path = url.split("?", 1)[0].split("#", 1)[0].lower()
	return any(seg in ("..", "%2e%2e", ".%2e", "%2e.") for seg in path.split("/")) or "//" in path


def is_safe_link(url: str) -> bool:
	"""URL có thể làm liên kết bấm được: http(s) hoặc /files, /private/files (không javascript:, data:, //host, `\\`, `..`).

	http(s) không được có khoảng trắng; đường /files cho phép dấu cách thường (tên tệp Frappe có thể chứa), còn ảnh nội tuyến
	(`is_inline_image_url`) luôn đòi nghiêm ngặt hơn.
	"""
	if not isinstance(url, str) or not url.startswith(SAFE_PREFIXES):
		return False
	local = url.startswith(LOCAL_PREFIXES)
	if _has_bad_chars(url, allow_space=local):
		return False
	return not (local and _bad_local_path(url))


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
	"""Được dùng làm src của ảnh nội tuyến: /files nội bộ hoặc https tới CDN Zalo cho phép.

	Nghiêm ngặt: không khoảng trắng/ký tự điều khiển/`\\`, không userinfo, không cổng khác 443, không `..` trong /files.
	Máy khách (`zaloThread.js::isInlineImageUrl`) áp đúng các luật này.
	"""
	if not is_safe_link(url) or _has_bad_chars(url):
		return False
	if url.startswith(LOCAL_PREFIXES):
		return True
	if not url.startswith("https://"):
		return False
	try:
		parsed = urlparse(url)
		if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
			return False
	except ValueError:
		return False
	return host_allowed(url_host(url))


def filename_from_url(url: str) -> str:
	"""Tên tệp cuối đường dẫn (bỏ query); rỗng nếu không có."""
	try:
		path = urlparse(url).path
	except ValueError:
		return ""
	return path.rstrip("/").rsplit("/", 1)[-1][:120]
