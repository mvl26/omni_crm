"""API giám sát và quản trị: snapshot, xử lý lại, xóa dữ liệu khách."""

from __future__ import annotations

import frappe
from frappe import _

from omni_crm.ingest.gateway import enqueue_identity

MAX_REPROCESS = 200


def require_omni_admin() -> None:
	"""Chỉ cho Omni Admin (và Administrator). Không dùng frappe.only_for vì nó bỏ qua kiểm tra khi chạy test."""
	if "Omni Admin" not in frappe.get_roles():
		frappe.throw(_("Chỉ Omni Admin được thực hiện thao tác này"), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def reprocess(inbound_names: list | str) -> dict:
	"""Đưa tin Manual/Retry về New và enqueue lại (chỉ Omni Admin)."""
	require_omni_admin()
	names = frappe.parse_json(inbound_names) if isinstance(inbound_names, str) else inbound_names
	if not isinstance(names, list) or len(names) > MAX_REPROCESS:
		frappe.throw(_("Danh sách tin không hợp lệ hoặc quá {0} tin").format(MAX_REPROCESS))
	rows = frappe.get_all(
		"Omni Inbound Message",
		filters={"name": ["in", [str(n) for n in names]], "status": ["in", ["Manual", "Retry"]]},
		fields=["name", "identity_key"],
	)
	if rows:
		frappe.db.set_value(
			"Omni Inbound Message",
			[r.name for r in rows],
			{"status": "New", "attempts": 0, "next_attempt_at": None, "processing_started_at": None},
			update_modified=False,
		)
	for key in {r.identity_key for r in rows if r.identity_key}:
		enqueue_identity(key)  # trong request: hoãn tới sau commit của framework
	return {"reset": len(rows), "skipped": len(names) - len(rows)}
