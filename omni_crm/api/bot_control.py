"""Nút "Tạm dừng bot" / "Cho bot tiếp tục" trong Hộp thư (D3-02, docs/08 mục 6).

Cả hai kiểm quyền GHI Lead (theo Lead, không theo DocType) và ghi người bấm vào dòng thời gian Lead.
"""

from __future__ import annotations

import frappe

from omni_crm import flags
from omni_crm.api import inbox
from omni_crm.bot import handoff


def state(lead: str, can_write: bool) -> dict:
	"""Trạng thái bot với khách của Lead cho Hộp thư: đang tạm dừng đến khi nào, có được bấm nút không."""
	until = handoff.paused_until(lead)
	return {
		"paused_until": until,
		"can_control": bool(can_write),
		"mode": flags.bot_reply_mode(),
	}


@frappe.whitelist(methods=["POST"])
def pause_bot(lead: str) -> dict:
	"""Sale tạm dừng bot với khách của Lead (theo `handoff_pause_hours`); ghi người bấm vào dòng thời gian."""
	lead = inbox.check_lead_write(lead)
	until = handoff.manual_pause(lead, frappe.session.user)
	return {"status": "Paused", "paused_until": until}


@frappe.whitelist(methods=["POST"])
def resume_bot(lead: str) -> dict:
	"""Sale cho bot tiếp tục với khách của Lead (xóa `bot_paused_until`); ghi người bấm vào dòng thời gian."""
	lead = inbox.check_lead_write(lead)
	done = handoff.manual_resume(lead, frappe.session.user)
	return {"status": "Resumed" if done else "NotPaused", "paused_until": None}
