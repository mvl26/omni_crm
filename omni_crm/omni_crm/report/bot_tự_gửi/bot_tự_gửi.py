"""Báo cáo "Bot tự gửi": theo nhóm câu hỏi, trạng thái Auto, eligibility hiện tại, số tin bot đã gửi, số lần tự tắt (D3-05).

Chỉ trả số đếm và tỉ lệ, KHÔNG có nội dung hội thoại hay nháp. Quyền như báo cáo "Bot nháp" (Omni Admin, Omni Knowledge Editor).
"""

from __future__ import annotations

import frappe
from frappe import _

from omni_crm.bot import auto_send

ROLES = ("Omni Admin", "Omni Knowledge Editor")


def require_role() -> None:
	"""Chỉ Omni Admin, Omni Knowledge Editor (và Administrator); không dùng `frappe.only_for` vì nó bỏ qua kiểm tra khi chạy test."""
	if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & set(ROLES):
		frappe.throw(_("Bạn không có quyền xem báo cáo này"), frappe.PermissionError)


def _columns() -> list[dict]:
	def col(field: str, label: str, kind: str = "Int", width: int = 110) -> dict:
		return {"fieldname": field, "label": label, "fieldtype": kind, "width": width}

	return [
		col("question_group", _("Nhóm câu hỏi"), "Data", 180),
		col("auto_enabled", _("Đang tự gửi"), "Check"),
		col("always_handoff", _("Luôn chuyển người"), "Check"),
		col("eligible", _("Đủ điều kiện mở"), "Check"),
		col("decided", _("Nháp đã quyết định (14 ngày)")),
		col("rate", _("Tỉ lệ đạt (%)"), "Float"),
		col("critical", _("Nghiêm trọng")),
		col("bot_sent", _("Tin bot đã gửi")),
		col("rating_ok", _("Chấm Đúng")),
		col("rating_wrong", _("Chấm Sai")),
		col("rating_missing", _("Chấm Thiếu")),
		col("auto_off", _("Số lần tự tắt")),
	]


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	require_role()
	filters = frappe._dict(filters or {})
	return _columns(), auto_send.group_overview(filters.get("from_date"), filters.get("to_date"))
