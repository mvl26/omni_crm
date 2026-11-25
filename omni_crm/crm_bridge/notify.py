"""Thông báo cho sale."""

from __future__ import annotations

import frappe
from frappe.utils import escape_html

CRM_NOTIFICATION = "CRM Notification"
LEAD = "CRM Lead"


def crm_lead_link(lead: str) -> str:
	"""Liên kết mở tab Zalo của Lead trong app CRM (sale làm việc ở /crm, không ở Desk)."""
	return f"/crm/leads/{lead}#zalo"


def crm_notify(lead: str, users: list[str], text: str) -> int:
	"""Tạo CRM Notification (chuông của app CRM) cho từng người; bấm vào mở trang Lead. Trả số đã tạo; lỗi chỉ ghi log.

	`text` là văn bản thuần do hệ thống dựng (không chứa lời khách); được thoát HTML ở đây. Không commit.
	"""
	if not frappe.db.exists("DocType", CRM_NOTIFICATION):
		return 0
	done = 0
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": CRM_NOTIFICATION,
					"from_user": "Administrator",
					"to_user": user,
					"type": "Assignment",
					"notification_text": f'<div class="omni-bot mb-2 leading-5 text-ink-gray-9">{escape_html(text)}</div>',
					"notification_type_doctype": LEAD,
					"notification_type_doc": lead,
					"reference_doctype": LEAD,
					"reference_name": lead,
				}
			).insert(ignore_permissions=True)
			done += 1
		except Exception:
			frappe.log_error(title="Omni: không tạo được thông báo CRM", message=f"lead={lead}")
	return done
