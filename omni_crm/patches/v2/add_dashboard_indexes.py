"""V2-04: thêm chỉ mục cho Tổng quan CSKH (chỉ THÊM, chạy lại được): tin khách cuối của mỗi Lead quét trong chỉ mục."""

import frappe


def execute() -> None:
	"""Thêm chỉ mục (lead, event_type, status, received_at) cho Omni Inbound Message nếu chưa có."""
	frappe.db.add_index(
		"Omni Inbound Message",
		["lead", "event_type", "status", "received_at"],
		"lead_event_status_received_index",
	)
