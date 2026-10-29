"""Báo cáo "Bot ngầm": số liệu nháp Shadow theo nhóm câu hỏi và danh sách nháp khác câu sale nhất (D2-07).

Chỉ Omni Admin, Omni Knowledge Editor xem được: nháp ngầm không hiện cho sale.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

ROLES = ("Omni Admin", "Omni Knowledge Editor")
BUCKETS = ((0.0, 0.2, "0-0.2"), (0.2, 0.5, "0.2-0.5"), (0.5, 1.0001, "0.5-1"))
DEFAULT_LIMIT = 10
MAX_LIMIT = 200
VIEW_FAR = "Nháp khác câu sale nhất"


def _filters(filters: dict) -> dict[str, Any]:
	cond: dict[str, Any] = {"mode": "Shadow"}
	lo, hi = filters.get("from_date"), filters.get("to_date")
	if lo and hi:
		cond["creation"] = ["between", [lo, f"{hi} 23:59:59"]]
	elif lo:
		cond["creation"] = [">=", lo]
	elif hi:
		cond["creation"] = ["<=", f"{hi} 23:59:59"]
	return cond


def summarize(rows: list[Any]) -> list[dict]:
	"""Gom nháp theo nhóm: số nháp, tỉ lệ chuyển người, số đã so với sale, phân bố edit_distance_ratio."""
	groups: dict[str, list[Any]] = {}
	for r in rows:
		groups.setdefault(r.question_group or _("(chưa phân nhóm)"), []).append(r)
	out = []
	for name, items in sorted(groups.items()):
		compared = [r for r in items if r.decision == "Shadow Compared"]
		ratios = [
			r.edit_distance_ratio for r in compared if r.action == "Reply"
		]  # Handoff không có nháp để so
		row = {
			"question_group": name,
			"drafts": len(items),
			"handoff": sum(1 for r in items if r.action == "Handoff"),
			"handoff_rate": round(100 * sum(1 for r in items if r.action == "Handoff") / len(items), 1),
			"compared": len(compared),
			"sale_handoff": sum(1 for r in compared if r.sale_handoff),
			"avg_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
		}
		for lo, hi, label in BUCKETS:
			row[f"b_{label}"] = sum(1 for x in ratios if lo <= x < hi)
		out.append(row)
	return out


def require_role() -> None:
	"""Chỉ Omni Admin, Omni Knowledge Editor (và Administrator); không dùng `frappe.only_for` vì nó bỏ qua kiểm tra khi chạy test."""
	if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & set(ROLES):
		frappe.throw(_("Bạn không có quyền xem báo cáo này"), frappe.PermissionError)


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	require_role()
	filters = frappe._dict(filters or {})
	fields = [
		"name",
		"lead",
		"question_group",
		"action",
		"decision",
		"edit_distance_ratio",
		"sale_handoff",
		"draft_text",
		"sale_reply_text",
		"guard_flags",
	]
	if filters.get("view") == VIEW_FAR:
		limit = min(max(cint(filters.get("limit")) or DEFAULT_LIMIT, 1), MAX_LIMIT)
		cond = _filters(filters)
		cond.update(decision="Shadow Compared", action="Reply")
		rows = frappe.get_all(
			"Omni Bot Draft",
			filters=cond,
			fields=fields,
			order_by="edit_distance_ratio desc",
			limit_page_length=limit,
		)
		columns = [
			{
				"fieldname": "name",
				"label": _("Nháp"),
				"fieldtype": "Link",
				"options": "Omni Bot Draft",
				"width": 150,
			},
			{"fieldname": "question_group", "label": _("Nhóm"), "fieldtype": "Data", "width": 140},
			{
				"fieldname": "edit_distance_ratio",
				"label": _("Độ khác biệt"),
				"fieldtype": "Float",
				"precision": 3,
				"width": 100,
			},
			{"fieldname": "draft_text", "label": _("Nháp của bot"), "fieldtype": "Text", "width": 320},
			{"fieldname": "sale_reply_text", "label": _("Câu sale"), "fieldtype": "Text", "width": 320},
		]
		return columns, [dict(r) for r in rows]
	rows = frappe.get_all("Omni Bot Draft", filters=_filters(filters), fields=fields)
	columns = [
		{"fieldname": "question_group", "label": _("Nhóm câu hỏi"), "fieldtype": "Data", "width": 180},
		{"fieldname": "drafts", "label": _("Số nháp"), "fieldtype": "Int", "width": 90},
		{"fieldname": "handoff", "label": _("Chuyển người"), "fieldtype": "Int", "width": 110},
		{
			"fieldname": "handoff_rate",
			"label": _("Tỉ lệ chuyển người (%)"),
			"fieldtype": "Float",
			"width": 150,
		},
		{"fieldname": "compared", "label": _("Đã so với sale"), "fieldtype": "Int", "width": 120},
		{"fieldname": "sale_handoff", "label": _("Sale trả lời ngoài kho"), "fieldtype": "Int", "width": 150},
		{
			"fieldname": "avg_ratio",
			"label": _("Khác biệt trung bình"),
			"fieldtype": "Float",
			"precision": 3,
			"width": 150,
		},
		*(
			{
				"fieldname": f"b_{label}",
				"label": _("Khác biệt {0}").format(label),
				"fieldtype": "Int",
				"width": 120,
			}
			for _lo, _hi, label in BUCKETS
		),
	]
	return columns, summarize(rows)
