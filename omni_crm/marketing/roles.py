"""Vai trò Marketing Hub. Omni Admin (có sẵn) sửa Thiết lập; Marketing Manager duyệt; Marketing User soạn bài."""

from __future__ import annotations

import frappe

MANAGER = "Marketing Manager"
USER = "Marketing User"
ADMIN = "Omni Admin"
ROLES = (MANAGER, USER)


def _roles(user: str | None = None) -> set[str]:
	return set(frappe.get_roles(user or frappe.session.user))


def is_manager(user: str | None = None) -> bool:
	"""Được duyệt bài: có vai trò Marketing Manager (Administrator có mọi vai trò)."""
	return MANAGER in _roles(user)


def can_edit(user: str | None = None) -> bool:
	"""Được soạn / gửi duyệt / hủy bài."""
	return bool(_roles(user) & {MANAGER, USER, ADMIN, "System Manager"})


def is_settings_admin(user: str | None = None) -> bool:
	"""Được sửa Thiết lập Marketing và bấm Kiểm tra quyền kênh."""
	return bool(_roles(user) & {ADMIN, "System Manager"})


def ensure_roles() -> None:
	"""Tạo hai vai trò nếu chưa có (patch cài đặt gọi; idempotent)."""
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			doc = frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1})
			doc.flags.ignore_permissions = True
			doc.insert()
