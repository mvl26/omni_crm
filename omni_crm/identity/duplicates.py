"""Tạo, bỏ qua và gộp cặp nghi trùng."""

from __future__ import annotations

import frappe
from frappe import _

CANDIDATE = "Omni Duplicate Candidate"


def pair_exists(lead_a: str, lead_b: str) -> bool:
	"""Cặp (không phân biệt thứ tự) đã có Candidate ở bất kỳ trạng thái nào chưa."""
	a, b = sorted((lead_a, lead_b))
	return bool(frappe.db.exists(CANDIDATE, {"lead_a": a, "lead_b": b}))


def create_candidate(lead_a: str, lead_b: str, reason: str, score: int) -> str | None:
	"""Tạo Candidate cho cặp Lead; bỏ qua (None) nếu hai Lead trùng nhau hoặc cặp đã có."""
	if not lead_a or not lead_b or lead_a == lead_b or pair_exists(lead_a, lead_b):
		return None
	doc = frappe.get_doc(
		{
			"doctype": CANDIDATE,
			"lead_a": lead_a,
			"lead_b": lead_b,
			"reason": reason[:500],
			"score": max(0, min(100, int(score))),
			"status": "Open",
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		return None  # lượt khác vừa tạo cùng cặp
	return doc.name


def _check_can_write_both(doc) -> None:
	frappe.has_permission(CANDIDATE, "read", doc.name, throw=True)
	for lead in (doc.lead_a, doc.lead_b):
		if not frappe.has_permission("CRM Lead", "write", lead):
			frappe.throw(_("Bạn không có quyền sửa Lead {0}").format(lead), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def dismiss(candidate: str) -> str:
	"""Đánh dấu cặp nghi trùng là không trùng (Dismissed); cần quyền ghi cả hai Lead."""
	doc = frappe.get_doc(CANDIDATE, candidate)
	_check_can_write_both(doc)
	if doc.status != "Open":
		return doc.status
	doc.status = "Dismissed"
	doc.decided_by = frappe.session.user
	doc.save(ignore_permissions=True)
	return doc.status


@frappe.whitelist(methods=["POST"])
def merge(candidate: str, primary: str) -> dict:
	"""Gộp cặp nghi trùng: `primary` (một trong hai Lead) giữ lại, Lead còn lại bị gộp vào."""
	from omni_crm.identity.merge import merge_leads

	doc = frappe.get_doc(CANDIDATE, candidate)
	if primary not in (doc.lead_a, doc.lead_b):
		frappe.throw(_("Lead chính phải là một trong hai Lead của cặp nghi trùng"))
	_check_can_write_both(doc)
	secondary = doc.lead_b if primary == doc.lead_a else doc.lead_a
	if doc.status == "Dismissed":
		frappe.throw(_("Cặp này đã được bỏ qua, không thể gộp"))
	return merge_leads(primary, secondary, frappe.session.user, candidate=candidate)
