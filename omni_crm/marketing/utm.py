"""Mã bài và link có UTM của bài đăng (ADR 0029). Hàm thuần, không đọc DB."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

POST_CODE_MAX = 40
POST_CODE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UTM_MEDIUM = "social"
SOURCE_OF_PLATFORM = {"Facebook": "facebook", "Zalo OA": "zalo", "Khác": "other"}
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
LINK_PLACEHOLDER = "{link}"


def normalize_post_code(value: str | None) -> str:
	"""Mã bài chuẩn: bỏ khoảng trắng hai đầu, chữ thường."""
	return str(value or "").strip().lower()


def is_valid_post_code(code: str) -> bool:
	"""Mã bài hợp lệ: chữ thường không dấu, số, gạch ngang giữa các cụm, tối đa 40 ký tự."""
	return bool(code) and len(code) <= POST_CODE_MAX and bool(POST_CODE_RE.match(code))


def utm_source(platform: str | None) -> str:
	"""utm_source theo kênh đăng; kênh lạ là "other"."""
	return SOURCE_OF_PLATFORM.get(platform or "", "other")


def build_utm_link(
	base_url: str | None, platform: str | None, utm_campaign: str | None, post_code: str | None
) -> str:
	"""Link đích kèm utm_source, utm_medium, utm_campaign, utm_content (đúng thứ tự này); "" khi thiếu link, chiến dịch hay mã bài.

	Giữ các tham số khác của link đích, bỏ mọi tham số `utm_*` cũ để không có hai giá trị cho cùng một khóa.
	"""
	base = str(base_url or "").strip()
	campaign = str(utm_campaign or "").strip()
	code = normalize_post_code(post_code)
	if not base or not campaign or not code:
		return ""
	parts = urlsplit(base)
	if parts.scheme not in ("http", "https") or not parts.netloc:
		return ""
	kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM_KEYS]
	utm = [
		("utm_source", utm_source(platform)),
		("utm_medium", UTM_MEDIUM),
		("utm_campaign", campaign),
		("utm_content", code),
	]
	path = parts.path or "/"
	return urlunsplit((parts.scheme, parts.netloc, path, urlencode(kept + utm), parts.fragment))


def render_message(content: str | None, link: str | None) -> tuple[str, bool]:
	"""(nội dung gửi Meta, đã chèn link chưa): thay `{link}` bằng link có UTM nếu nội dung có chỗ đặt link."""
	text = str(content or "").strip()
	if link and LINK_PLACEHOLDER in text:
		return text.replace(LINK_PLACEHOLDER, link), True
	return text.replace(LINK_PLACEHOLDER, ""), False
