"""Đọc các công tắc từ Omni Settings và Omni Channel (Đợt 1)."""

from collections.abc import Iterator
from contextlib import contextmanager

import frappe
from frappe.utils import cint


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


def lead_scoring_enabled() -> bool:
	"""Công tắc chấm điểm Lead (Đợt 2); lỗi hoặc chưa cấu hình thì coi là tắt (an toàn)."""
	return _settings_flag("lead_scoring_enabled")


def prospect_import_enabled() -> bool:
	"""Công tắc nhập danh sách nhà thầu (V2-02/V2-03); chưa từng lưu (site cũ) coi là bật, đọc lỗi coi là tắt."""
	try:
		rows = dict(
			frappe.db.sql(
				"select field, value from `tabSingles` where doctype = 'Omni Settings' and field = 'prospect_import_enabled'"
			)
		)
	except Exception:
		return False
	value = rows.get("prospect_import_enabled")
	return True if value is None else bool(cint(value))


def _settings_flag(fieldname: str) -> bool:
	try:
		return bool(frappe.get_cached_doc("Omni Settings").get(fieldname))
	except Exception:
		return False


AUTO_SEND_CONF = (
	"omni_auto_send_allowed"  # site_config: khóa hạ tầng, vắng = chặn; chỉ người vận hành đặt sau khi Q1 chốt
)


def auto_send_infra_allowed() -> bool:
	"""Khóa hạ tầng NGOÀI DB cho bot tự gửi (site_config `omni_auto_send_allowed`); vắng, sai kiểu hay lỗi thì chặn."""
	try:
		return bool(cint(frappe.conf.get(AUTO_SEND_CONF)))
	except Exception:
		return False


def group_auto_send(group: str | None) -> bool:
	"""Nhóm câu hỏi đang được tự gửi: khóa hạ tầng mở VÀ `auto_send_enabled` VÀ không phải nhóm luôn chuyển người.

	Đọc thẳng DB (không cache) vì đây là công tắc an toàn; lỗi thì coi là tắt.
	"""
	if not group or not auto_send_infra_allowed():
		return False
	try:
		row = frappe.db.get_value(
			"Omni Question Group", group, ["auto_send_enabled", "always_handoff"], as_dict=True
		)
	except Exception:
		return False
	return bool(row and cint(row.auto_send_enabled) and not cint(row.always_handoff))
