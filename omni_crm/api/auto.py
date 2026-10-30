"""API bật/tắt bot tự gửi theo nhóm câu hỏi (D3-05). Chỉ Omni Admin; logic ở `bot/auto_send.py`.

Mọi việc đi qua hộp xác nhận hai bước: `get_auto_status` (số liệu) -> `enable_auto(confirm=1)`. Khóa hạ tầng
(site_config `omni_auto_send_allowed`) và nhóm `always_handoff` chặn cả khi `force`.
"""

from __future__ import annotations

import frappe

from omni_crm import flags
from omni_crm.bot import auto_send


@frappe.whitelist()
def get_auto_status(group: str) -> dict:
	"""Trạng thái tự gửi của một nhóm: bật/tắt, khóa hạ tầng, eligibility (số liệu và lý do). Chỉ Omni Admin."""
	auto_send._require_admin()
	row = frappe.db.get_value(
		auto_send.GROUP,
		group,
		["always_handoff", "auto_send_enabled", "auto_send_enabled_at", "auto_send_enabled_by"],
		as_dict=True,
	)
	if not row:
		frappe.throw(frappe._("Không tìm thấy nhóm câu hỏi"), frappe.DoesNotExistError)
	return {
		"group": group,
		"infra_allowed": flags.auto_send_infra_allowed(),
		"always_handoff": bool(row.always_handoff),
		"enabled": bool(row.auto_send_enabled),
		"enabled_at": row.auto_send_enabled_at,
		"enabled_by": row.auto_send_enabled_by,
		"eligibility": auto_send.eligibility(group),
	}


@frappe.whitelist(methods=["POST"])
def enable_auto(group: str, confirm: int | str = 0, force: int | str = 0, reason: str | None = None) -> dict:
	"""Bật tự động gửi cho nhóm (Omni Admin); `confirm=1` mới thực hiện, không thì trả số liệu để hiện hộp xác nhận."""
	return auto_send.enable_group(
		group, bool(frappe.utils.cint(confirm)), bool(frappe.utils.cint(force)), reason
	)


@frappe.whitelist(methods=["POST"])
def disable_auto(group: str, reason: str | None = None) -> dict:
	"""Tắt tự động gửi của nhóm (Omni Admin, nút "Tắt tự động nhóm này")."""
	return auto_send.disable_group(group, reason)
