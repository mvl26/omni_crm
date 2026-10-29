"""Đọc các công tắc từ Omni Settings và Omni Channel (Đợt 1)."""

from collections.abc import Iterator
from contextlib import contextmanager

import frappe


def sandbox() -> bool:
	"""Chế độ sandbox của Omni Settings; lỗi hoặc chưa cấu hình thì coi là bật (an toàn)."""
	try:
		return bool(frappe.get_cached_doc("Omni Settings").sandbox_mode)
	except Exception:
		return True


def channel_enabled(channel: str) -> bool:
	"""Kênh có đang bật không; kênh không tồn tại thì False."""
	return _channel_flag(channel, "enabled")


def outbound_enabled(channel: str) -> bool:
	"""Chiều ra của kênh có đang bật không; kênh không tồn tại thì False."""
	return _channel_flag(channel, "outbound_enabled")


def _channel_flag(channel: str, fieldname: str) -> bool:
	if not channel:
		return False
	try:
		return bool(frappe.get_cached_doc("Omni Channel", channel).get(fieldname))
	except frappe.DoesNotExistError:
		return False


@contextmanager
def omni_bot() -> Iterator[None]:
	"""Đánh dấu code hệ thống đang ghi (frappe.flags.in_omni_bot) để không tính là sale sửa tay."""
	previous = frappe.flags.get("in_omni_bot")
	frappe.flags.in_omni_bot = True
	try:
		yield
	finally:
		frappe.flags.in_omni_bot = previous


def is_system_write() -> bool:
	"""True nếu lần ghi hiện tại do hệ thống thực hiện (cờ in_omni_bot, job nền, migrate, patch, cài đặt)."""
	f = frappe.flags
	if f.get("in_omni_bot") or f.get("in_migrate") or f.get("in_patch") or f.get("in_install"):
		return True
	return bool(getattr(frappe.local, "job", None))


def bot_analyze_enabled() -> bool:
	"""Công tắc bot phân tích; lỗi hoặc chưa cấu hình thì coi là tắt (an toàn)."""
	return _settings_flag("bot_analyze_enabled")


def bot_reply_mode() -> str:
	"""Chế độ bot trả lời toàn cục: "Off" | "Shadow" | "Draft"; lỗi thì "Off"."""
	try:
		mode = frappe.get_cached_doc("Omni Settings").get("bot_reply_mode") or "Off"
	except Exception:
		return "Off"
	return mode if mode in ("Off", "Shadow", "Draft") else "Off"


def recording_enabled() -> bool:
	"""Công tắc ghi âm và tóm tắt cuộc gọi (Đợt 3); trường chưa có hoặc lỗi thì tắt."""
	return _settings_flag("recording_enabled")


def _settings_flag(fieldname: str) -> bool:
	try:
		return bool(frappe.get_cached_doc("Omni Settings").get(fieldname))
	except Exception:
		return False
