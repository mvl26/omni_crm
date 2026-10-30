"""Báo cáo "Bot nháp": nháp Draft theo nhóm câu hỏi hoặc theo tuần, tỉ lệ gửi nguyên văn / sửa / bỏ, chấm Đúng/Sai/Thiếu (D3-01).

Chỉ trả số đếm và tỉ lệ, KHÔNG có nội dung hội thoại hay nháp, nên Omni Knowledge Editor xem được mà không cần quyền
Lead. Tỉ lệ tính trên nháp `Reply` đã có quyết định (Sent As Is + Sent Edited + Discarded); nháp Handoff đếm riêng.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import get_datetime

ROLES = ("Omni Admin", "Omni Knowledge Editor")
VIEW_GROUP, VIEW_WEEK = "Theo nhóm", "Theo tuần"
DECIDED = ("Sent As Is", "Sent Edited", "Discarded")


def week_start(day: date) -> date:
	"""Thứ Hai của tuần chứa `day`."""
	return day - timedelta(days=day.weekday())


def _filters(filters: dict) -> dict[str, Any]:
	cond: dict[str, Any] = {"mode": "Draft"}
	lo, hi = filters.get("from_date"), filters.get("to_date")
	if lo and hi:
		cond["creation"] = ["between", [lo, f"{hi} 23:59:59"]]
	elif lo:
		cond["creation"] = [">=", lo]
	elif hi:
		cond["creation"] = ["<=", f"{hi} 23:59:59"]
	return cond


def _pct(part: int, whole: int) -> float | None:
	return round(100 * part / whole, 1) if whole else None


def _row(items: list[Any]) -> dict:
	replies = [r for r in items if r.action == "Reply"]
	count = lambda decision: sum(1 for r in replies if r.decision == decision)  # noqa: E731
	decided = sum(count(d) for d in DECIDED)
	return {
		"drafts": len(items),
		"handoff": sum(1 for r in items if r.action == "Handoff"),
		"pending": count("Pending"),
		"expired": count("Expired"),
		"decided": decided,
		"sent_as_is": count("Sent As Is"),
		"sent_edited": count("Sent Edited"),
		"discarded": count("Discarded"),
		"rate_as_is": _pct(count("Sent As Is"), decided),
		"rate_edited": _pct(count("Sent Edited"), decided),
		"rate_discarded": _pct(count("Discarded"), decided),
		"rating_ok": sum(1 for r in items if r.rating == "Đúng"),
		"rating_wrong": sum(1 for r in items if r.rating == "Sai"),
		"rating_missing": sum(1 for r in items if r.rating == "Thiếu"),
		"critical": sum(1 for r in items if r.critical),
	}


def summarize(rows: list[Any], by_week: bool = False) -> list[dict]:
	"""Gom nháp theo nhóm câu hỏi (hoặc theo tuần rồi nhóm), mỗi dòng gồm số đếm, tỉ lệ và điểm chấm."""
	buckets: dict[tuple[str, str], list[Any]] = {}
	for r in rows:
		group = r.question_group or _("(chưa phân nhóm)")
		week = week_start(get_datetime(r.creation).date()).isoformat() if by_week else ""
		buckets.setdefault((week, group), []).append(r)
	out = []
	for (week, group), items in sorted(buckets.items(), key=lambda kv: ((kv[0][0] or ""), kv[0][1])):
		line = {"question_group": group, **_row(items)}
		if by_week:
			line["week"] = week
		out.append(line)
	return out


def require_role() -> None:
	"""Chỉ Omni Admin, Omni Knowledge Editor (và Administrator); không dùng `frappe.only_for` vì nó bỏ qua kiểm tra khi chạy test."""
	if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & set(ROLES):
		frappe.throw(_("Bạn không có quyền xem báo cáo này"), frappe.PermissionError)


def _columns(by_week: bool) -> list[dict]:
	def col(field: str, label: str, kind: str = "Int", width: int = 100) -> dict:
		return {"fieldname": field, "label": label, "fieldtype": kind, "width": width}

	head = [col("week", _("Tuần (thứ Hai)"), "Data", 120)] if by_week else []
	return [
		*head,
		col("question_group", _("Nhóm câu hỏi"), "Data", 180),
		col("drafts", _("Số nháp")),
		col("handoff", _("Chuyển người")),
		col("pending", _("Đang chờ")),
		col("expired", _("Hết hạn")),
		col("decided", _("Đã quyết định")),
		col("sent_as_is", _("Gửi nguyên văn")),
		col("sent_edited", _("Sửa rồi gửi")),
		col("discarded", _("Bỏ")),
		col("rate_as_is", _("Tỉ lệ nguyên văn (%)"), "Float", 140),
		col("rate_edited", _("Tỉ lệ sửa (%)"), "Float", 120),
		col("rate_discarded", _("Tỉ lệ bỏ (%)"), "Float", 120),
		col("rating_ok", _("Chấm Đúng")),
		col("rating_wrong", _("Chấm Sai")),
		col("rating_missing", _("Chấm Thiếu")),
		col("critical", _("Nghiêm trọng")),
	]


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	require_role()
	filters = frappe._dict(filters or {})
	by_week = filters.get("view") == VIEW_WEEK
	rows = frappe.get_all(
		"Omni Bot Draft",
		filters=_filters(filters),
		fields=["question_group", "action", "decision", "rating", "critical", "creation"],
	)
	return _columns(by_week), summarize(rows, by_week)
