"""`CRM Lead.omni_first_response_at`: thời điểm phản hồi đầu tiên (docs/15 mục 7 bước 4, docs/13 mục 1.2).

Tính là phản hồi: tin ra đầu tiên KHÔNG sandbox do Sale hoặc Bot gửi (đã `Sent`), thư gửi đi từ CRM tới Lead,
cuộc gọi đi được nghe máy, và nhật ký chăm sóc đầu tiên do sale GHI (V2-04: `care_log.save_outcome` gọi `mark`).
Phiên chăm sóc TỰ TẠO từ tin khách không tính (nếu tính thì phản hồi = lúc khách nhắn).
Tin `System` (câu giữ chân khi chuyển người) và tin sandbox KHÔNG tính. Luôn giữ giá trị SỚM NHẤT.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import frappe

from omni_crm.crm_bridge.lead import follow_merged

LEAD = "CRM Lead"
FIELD = "omni_first_response_at"
COUNTED_SOURCES = ("Sale", "Bot")
BATCH = 500


def mark(lead: str | None, at: datetime | str | None) -> bool:
	"""Ghi `omni_first_response_at` = `at` nếu trống hoặc `at` sớm hơn giá trị cũ; trả True nếu có đổi. Không commit."""
	if not lead or not at:
		return False
	lead = follow_merged(lead) or lead
	frappe.db.sql(
		f"update `tab{LEAD}` set `{FIELD}` = %s where `name` = %s and (`{FIELD}` is null or `{FIELD}` > %s)",
		(at, lead, at),
	)
	return bool(frappe.db.sql("select row_count()")[0][0])


def _safe(fn: Any, *args: Any) -> None:
	"""Gọi `fn`; lỗi chỉ ghi log, không bao giờ làm hỏng việc gửi/nhận chính."""
	try:
		fn(*args)
	except Exception:
		frappe.log_error(title="Omni: lỗi ghi omni_first_response_at", message=frappe.get_traceback()[-800:])


def counts_as_response(row: Any) -> bool:
	"""Tin ra (cần `status`, `source`, `is_sandbox`, `lead`) có tính là phản hồi đầu tiên không."""
	return bool(
		row.get("lead")
		and row.get("status") == "Sent"
		and not row.get("is_sandbox")
		and row.get("source") in COUNTED_SOURCES
		# V3-07: tin "Bot" không có nháp là kịch bản chatbot Zalo dội về (oa_echo), không phải phản hồi của mình
		and not (row.get("source") == "Bot" and not row.get("bot_draft"))
	)


def on_outbound_sent(row: Any, sent_at: datetime | str | None) -> None:
	"""Gọi sau khi tin ra chuyển `Sent` (pipeline/outbound._sent): ghi nếu thuộc loại được tính."""
	if counts_as_response({**row, "status": "Sent"}):
		_safe(mark, row.get("lead"), sent_at)


def on_outbound_insert(doc: Any, method: str | None = None) -> None:
	"""Hook Omni Outbound Message.after_insert: tin ra tạo sẵn ở trạng thái Sent (sale trả lời ngoài hệ thống, oa_echo)."""
	if counts_as_response(doc):
		_safe(mark, doc.lead, doc.sent_at or doc.creation)


def on_communication_insert(doc: Any, method: str | None = None) -> None:
	"""Hook Communication.after_insert: thư Email gửi đi từ CRM tới Lead/Deal (không tính bản sao dòng thời gian của Omni)."""
	if doc.get("sent_or_received") != "Sent" or doc.get("communication_medium") != "Email":
		return
	if doc.get("communication_type") not in (None, "Communication"):  # bỏ Automated Message, Feedback…
		return
	if doc.get("omni_outbound_message") or doc.get("reference_doctype") not in (LEAD, "CRM Deal"):
		return
	lead = doc.reference_name
	if doc.reference_doctype == "CRM Deal":
		lead = frappe.db.get_value("CRM Deal", doc.reference_name, "lead")
	_safe(mark, lead, doc.get("communication_date") or doc.creation)


def on_call_recorded(call: Any, lead: str | None) -> None:
	"""Cuộc gọi đi được nghe máy (telephony worker_hooks._record) là một phản hồi."""
	if lead and call.direction == "Outbound" and call.status == "Answered":
		_safe(mark, lead, call.answered_at or call.started_at)


# --- điền ngược cho Lead cũ (patch) ------------------------------------------------------------------------------


def earliest_responses() -> dict[str, datetime]:
	"""{lead: thời điểm phản hồi đầu tiên} tính từ dữ liệu hiện có, theo đúng định nghĩa ở đầu tệp."""
	found: dict[str, datetime] = {}

	def take(rows: list[tuple]) -> None:
		for lead, at in rows:
			if lead and at and (lead not in found or at < found[lead]):
				found[lead] = at

	take(
		frappe.db.sql(
			"""select lead, min(coalesce(sent_at, creation)) from `tabOmni Outbound Message`
			where status = 'Sent' and is_sandbox = 0 and source in ('Sale', 'Bot') and ifnull(lead, '') != ''
			group by lead"""
		)
	)
	take(
		frappe.db.sql(
			"""select reference_name, min(communication_date) from `tabCommunication`
			where reference_doctype = 'CRM Lead' and sent_or_received = 'Sent' and communication_medium = 'Email'
				and communication_type = 'Communication'
				and ifnull(omni_outbound_message, '') = '' group by reference_name"""
		)
	)
	take(
		frappe.db.sql(
			"""select d.lead, min(c.communication_date) from `tabCommunication` c
			join `tabCRM Deal` d on d.name = c.reference_name
			where c.reference_doctype = 'CRM Deal' and c.sent_or_received = 'Sent' and c.communication_medium = 'Email'
				and c.communication_type = 'Communication'
				and ifnull(c.omni_outbound_message, '') = '' and ifnull(d.lead, '') != '' group by d.lead"""
		)
	)
	take(
		frappe.db.sql(
			"""select lead, min(coalesce(answered_at, started_at)) from `tabOmni Call`
			where direction = 'Outbound' and status = 'Answered' and ifnull(lead, '') != '' group by lead"""
		)
	)
	take(
		frappe.db.sql(
			"""select lead, min(if(auto_created = 0, started_at, outcome_at)) from `tabOmni Care Log`
			where ifnull(outcome_by, '') != '' and ifnull(lead, '') != '' group by lead"""
		)
	)
	return found


def backfill(dry_run: bool = True) -> dict[str, int]:
	"""Điền `omni_first_response_at` cho Lead cũ; chạy lại được (chỉ ghi khi trống hoặc muộn hơn thật).

	`dry_run=True` (mặc định) chỉ đếm, không ghi. Trả {"candidates": số Lead có phản hồi, "to_set": số Lead sẽ
	đổi/đã đổi, "already_ok": số Lead đã đúng}. Commit theo lô khi chạy thật.
	"""
	found = earliest_responses()
	to_set = already = 0
	items = sorted(found.items())
	for i in range(0, len(items), BATCH):
		for lead, at in items[i : i + BATCH]:
			current = frappe.db.get_value(LEAD, lead, FIELD) if frappe.db.exists(LEAD, lead) else False
			if current is False:
				continue  # Lead đã bị xóa
			if current and current <= at:
				already += 1
			elif dry_run:
				to_set += 1
			elif mark(lead, at):
				to_set += 1
		if not dry_run:
			frappe.db.commit()
	return {"candidates": len(found), "to_set": to_set, "already_ok": already}
