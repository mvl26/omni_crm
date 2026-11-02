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


CARE_DEFAULTS = {
	"dashboard_enabled": 1,
	"care_log_enabled": 1,
	"response_sla_minutes": 15,
	"care_session_gap_hours": 4,
	"team_lead_role": "Sales Manager",
}


def care_setting(fieldname: str):
	"""Giá trị đã LƯU của cấu hình chăm sóc (V2-04); chưa từng lưu, trống hoặc <= 0 (với số) thì dùng mặc định.

	Đọc `tabSingles` vì site migrate từ bản cũ chưa lưu các trường này (`get_single_value` trả 0 cho trường chưa lưu,
	không phân biệt được với "đặt 0"). Công tắc (Check) là ngoại lệ: chưa từng lưu = bật, đã lưu 0 = tắt.
	"""
	default = CARE_DEFAULTS[fieldname]
	try:
		row = frappe.db.sql(
			"select value from `tabSingles` where doctype = 'Omni Settings' and field = %s", fieldname
		)
	except Exception:
		return default
	value = row[0][0] if row else None
	if value is None or value == "":
		return default
	if fieldname in ("dashboard_enabled", "care_log_enabled"):
		return cint(value)
	if isinstance(default, int):
		return cint(value) if cint(value) > 0 else default
	return value


ADS_DEFAULTS = {
	"ads_silence_hours": 6,
	"webform_rate_limit": 5,
	"webform_rate_window_minutes": 10,
	"webform_global_rate_per_hour": 300,
}


def webform_accept_unknown_form() -> bool:
	"""Có nhận form website có mã chưa cấu hình Omni Lead Form không; chưa từng lưu hoặc lỗi = không (an toàn)."""
	try:
		row = frappe.db.sql(
			"select value from `tabSingles` where doctype = 'Omni Settings' and field = 'webform_accept_unknown_form'"
		)
	except Exception:
		return False
	return bool(row and cint(row[0][0]))


def ads_setting(fieldname: str) -> int:
	"""Cấu hình lead quảng cáo (V2-05): chưa từng lưu, trống hoặc <= 0 thì dùng mặc định (đọc tabSingles như `care_setting`)."""
	default = ADS_DEFAULTS[fieldname]
	try:
		row = frappe.db.sql(
			"select value from `tabSingles` where doctype = 'Omni Settings' and field = %s", fieldname
		)
	except Exception:
		return default
	value = cint(row[0][0]) if row and row[0][0] not in (None, "") else 0
	return value if value > 0 else default


def dashboard_enabled() -> bool:
	"""Công tắc Dashboard CSKH (V2-04); chưa từng lưu = bật."""
	return bool(care_setting("dashboard_enabled"))


def care_log_enabled() -> bool:
	"""Công tắc tự ghi nhật ký chăm sóc (V2-04); chưa từng lưu = bật."""
	return bool(care_setting("care_log_enabled"))


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
