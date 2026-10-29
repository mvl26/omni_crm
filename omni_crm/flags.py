"""Đọc các công tắc từ Omni Settings và Omni Channel (Đợt 1)."""

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
