"""API trang Lead cho bot kịch bản v3 (ADR 0032; kịch bản mục 1.4, 3.5, 4.4, C.4): "Trả lại cho bot", trạng thái bot,
"Đã gửi tài liệu", "Đã gửi báo giá", kết quả demo, "không cho bot nhắn lại".

Mọi thao tác ghi kiểm quyền GHI theo Lead (không theo DocType), giống `api/drafts.py` / `api/bot_control.py`.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.api import inbox
from omni_crm.bot import handoff as bot_handoff
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.script import lead_ui, switch, tasks

LEAD = "CRM Lead"
IDENTITY = "Omni Identity"


def _check_read(lead: str) -> str:
	"""Ném PermissionError nếu người dùng không được ĐỌC Lead (đã theo Lead gộp); trả tên Lead đích."""
	if not lead or not frappe.db.exists(LEAD, lead):
		frappe.throw(_("Không tìm thấy Lead"), frappe.DoesNotExistError)
	lead = lead_bridge.follow_merged(lead) or lead
	if not frappe.has_permission(LEAD, "read", doc=lead):
		frappe.throw(_("Bạn không có quyền xem Lead này"), frappe.PermissionError)
	return lead


def _status(lead: str) -> dict:
	row = (
		frappe.db.get_value(
			LEAD,
			lead,
			[
				"omni_do_not_contact",
				"omni_no_bot_followup",
				"omni_journey_status",
				"omni_docs_sent_at",
				"omni_quote_sent_at",
				"omni_demo_result",
			],
			as_dict=True,
		)
		or {}
	)
	identities = frappe.get_all(
		IDENTITY, filters={"lead": lead}, pluck="name", order_by="last_inbound_at desc"
	)
	until = bot_handoff.paused_until(lead)
	enabled = any(switch.enabled_for(i) for i in identities)
	if cint(row.get("omni_do_not_contact")):
		bot = "stopped"
	elif until:
		bot = "paused"
	else:
		bot = "on" if enabled else "off"
	from omni_crm.script.flows import handoff as flow

	return {
		"bot": bot,
		"script_enabled": enabled,
		"paused_until": until,
		"do_not_contact": bool(cint(row.get("omni_do_not_contact"))),
		"no_bot_followup": bool(cint(row.get("omni_no_bot_followup"))),
		"is_customer": flow.is_customer(lead),
		"journey_status": row.get("omni_journey_status"),
		"docs_sent_at": row.get("omni_docs_sent_at"),
		"quote_sent_at": row.get("omni_quote_sent_at"),
		"demo_result": row.get("omni_demo_result"),
		"chat_windows": [w for w in (tasks.chat_deadline(i) for i in identities) if w],
		"can_control": bool(frappe.has_permission(LEAD, "write", doc=lead)),
	}


@frappe.whitelist()
def bot_status(lead: str) -> dict:
	"""Trạng thái bot với khách của Lead: on | paused (đến khi nào) | stopped | off, và hạn nhắn miễn phí từng kênh."""
	return _status(_check_read(lead))


@frappe.whitelist(methods=["POST"])
def return_to_bot(lead: str) -> dict:
	"""Nhân viên bấm "Trả lại cho bot": mở lại bot, xóa trạng thái chuyển người, ghi người bấm vào dòng thời gian."""
	lead = inbox.check_lead_write(lead)
	from omni_crm.script.flows import handoff as flow

	resumed = bot_handoff.manual_resume(lead, frappe.session.user)
	cleared = flow.clear(lead)
	return {"status": "Resumed" if resumed or cleared else "NotPaused", **_status(lead)}


@frappe.whitelist(methods=["POST"])
def mark_docs_sent(lead: str) -> dict:
	"""Nhân viên bấm "Đã gửi tài liệu": ghi mốc giờ + hành trình; là mốc để bot bắt đầu Bước 4."""
	lead = inbox.check_lead_write(lead)
	return lead_ui.mark_docs_sent(lead)


@frappe.whitelist(methods=["POST"])
def mark_quote_sent(lead: str) -> dict:
	"""Nhân viên bấm "Đã gửi báo giá": ghi mốc giờ + hành trình; là mốc để bot nhắn lại sau báo giá."""
	lead = inbox.check_lead_write(lead)
	return lead_ui.mark_quote_sent(lead)


@frappe.whitelist(methods=["POST"])
def set_demo_result(lead: str, result: str, note: str | None = None) -> dict:
	"""Ghi kết quả demo (Đã demo | Khách vắng | Hoãn) kèm ghi chú; báo luồng sau demo."""
	lead = inbox.check_lead_write(lead)
	return lead_ui.set_demo_result(lead, result, note)


@frappe.whitelist(methods=["POST"])
def set_no_bot_followup(lead: str, value: int | str | bool = 1) -> dict:
	"""Bật/tắt "không cho bot nhắn lại" cho Lead (bật thì hủy tin hẹn giờ đang chờ)."""
	lead = inbox.check_lead_write(lead)
	return lead_ui.set_no_bot_followup(lead, value)
