"""API whitelisted của Marketing Hub (V3-10). Mỗi lời gọi xử lý MỘT bài / MỘT kênh."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm.marketing import approval, channels, roles
from omni_crm.marketing import settings as mk_settings


def _require_hub() -> None:
	if not mk_settings.hub_enabled():
		frappe.throw(_("Marketing Hub đang tắt"), frappe.PermissionError)


@frappe.whitelist()
def hub_state() -> dict[str, Any]:
	"""Giao diện CRM hỏi: hub bật không, người dùng được xem / sửa Thiết lập / duyệt bài không."""
	enabled = mk_settings.hub_enabled()
	return {
		"enabled": enabled,
		"can_view": enabled and roles.can_edit(),
		"can_edit_settings": enabled and roles.is_settings_admin(),
		"is_manager": enabled and roles.is_manager(),
		"dry_run": mk_settings.dry_run(),
	}


@frappe.whitelist()
def get_social_channels() -> list[dict[str, Any]]:
	"""Thiết lập > Mạng xã hội: một dòng mỗi Page Facebook / OA Zalo, kèm TikTok. Không trả token (chỉ 4 ký tự cuối)."""
	_require_hub()
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền xem kênh mạng xã hội"), frappe.PermissionError)
	return channels.accounts()


@frappe.whitelist(methods=["POST"])
def check_channel(name: str) -> dict[str, Any]:
	"""Nút "Kiểm tra quyền": gọi đọc Facebook (debug_token, thông tin Trang) / Zalo (thông tin OA), lưu kết quả."""
	_require_hub()
	if not roles.is_settings_admin():
		frappe.throw(_("Chỉ Omni Admin được kiểm tra quyền kênh"), frappe.PermissionError)
	if not name or not frappe.db.exists("Omni Channel", name):
		frappe.throw(_("Không tìm thấy kênh"), frappe.DoesNotExistError)
	return channels.check(name)


@frappe.whitelist()
def list_approvers() -> list[dict[str, str]]:
	"""Người có vai trò Marketing Manager (để chọn người duyệt mặc định)."""
	_require_hub()
	users = frappe.get_all(
		"Has Role",
		filters={"role": roles.MANAGER, "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", users or [""]], "enabled": 1, "user_type": "System User"},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)
	return [{"value": r.name, "label": r.full_name or r.name} for r in rows]


@frappe.whitelist(methods=["POST"])
def submit_item(name: str) -> str:
	"""Gửi duyệt một bài (Nháp -> Chờ duyệt)."""
	_require_hub()
	return approval.submit(name)


@frappe.whitelist(methods=["POST"])
def approve_item(name: str) -> str:
	"""Duyệt và lên lịch một bài (chỉ Marketing Manager, điểm >= ngưỡng, có nền tảng)."""
	_require_hub()
	return approval.approve(name)


@frappe.whitelist(methods=["POST"])
def cancel_item(name: str) -> str:
	"""Hủy lịch một bài: nền tảng chưa đăng -> Đã hủy."""
	_require_hub()
	return approval.cancel(name)
