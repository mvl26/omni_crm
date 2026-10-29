"""Việc gọi lại cho cuộc gọi nhỡ: xác minh `call_id` NGOÀI khóa DB rồi mới tạo CRM Task (docs/07 mục 2.2 bước 5).

Worker chỉ đẩy job sau khi đã commit lô tin (`after_batch`); job `process_missed_call` làm phần có gọi HTTP
(`verify_missed_call`) khi không giữ khóa/transaction mở, rồi tạo việc trong transaction riêng và commit. Nếu đẩy job
thất bại hoặc job mất, `sweep_pending` (5 phút/lần) xử lý các cuộc gọi nhỡ chưa có việc.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm import flags
from omni_crm.channels.telephony import worker_hooks
from omni_crm.crm_bridge import task as task_bridge

CALL = "Omni Call"
INBOUND = "Omni Inbound Message"
JOB = "omni_crm.channels.telephony.callbacks.process_missed_call"
QUEUE = "omni"
SWEEP_AGE_MINUTES = 5  # chỉ quét cuộc gọi đã nhỡ hơn 5 phút (job thường đã chạy)
SWEEP_WINDOW_DAYS = 2
SWEEP_LIMIT = 100


def _pending_filters() -> dict:
	return {
		"direction": "Inbound",
		"status": "Missed",
		"lead": ["is", "set"],
		"callback_task": ["is", "not set"],
	}


def after_batch(batch_id: str) -> None:
	"""Sau commit của lô tin: đẩy job tạo việc gọi lại cho từng cuộc gọi nhỡ trong lô. Lỗi đẩy job không ném ra."""
	call_ids = frappe.get_all(
		INBOUND,
		filters={"batch_id": batch_id, "channel_type": "Telephony", "event_type": "call_ended"},
		fields=["external_message_id", "channel"],
	)
	for row in call_ids:
		name = frappe.db.get_value(CALL, {"call_id": row.external_message_id, **_pending_filters()}, "name")
		if name:
			_enqueue(name, row.channel)


def _enqueue(call_name: str, channel: str) -> None:
	try:
		frappe.enqueue(
			JOB,
			queue=QUEUE,
			job_id=f"omni-callback::{call_name}",
			deduplicate=True,
			call_name=call_name,
			channel=channel,
		)
	except Exception:
		# Không mất việc gọi lại: sweep_pending sẽ xử lý. Không ghi số điện thoại hay nội dung vào log.
		frappe.log_error(title="Omni: không đẩy được job việc gọi lại", message="Tác vụ quét sẽ xử lý bù.")


def _channel_of(call_name: str, channel: str | None) -> str | None:
	if channel:
		return channel
	call_id = frappe.db.get_value(CALL, call_name, "call_id")
	return frappe.db.get_value(
		INBOUND, {"external_message_id": call_id, "channel_type": "Telephony"}, "channel"
	)


def process_missed_call(call_name: str, channel: str | None = None) -> str | None:
	"""Job nền: xác minh cuộc gọi nhỡ (HTTP, ngoài khóa DB) rồi tạo/gộp việc gọi lại; trả tên CRM Task hoặc None."""
	channel = _channel_of(call_name, channel)
	if not channel or not flags.channel_enabled(channel):
		return None  # kênh đã tắt: bật lại thì sweep_pending xử lý bù
	frappe.db.rollback()  # không giữ transaction/khóa nào mở khi gọi ra ngoài
	if not frappe.db.exists(CALL, call_name):
		return None
	channel_doc = frappe.get_cached_doc("Omni Channel", channel)
	try:
		worker_hooks.verify_missed_call(call_name, channel_doc)  # có HTTP; lỗi đã được bắt bên trong
		frappe.db.commit()  # lưu cờ unverified
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: lỗi xác minh cuộc gọi nhỡ", message="Vẫn tạo việc gọi lại.")
		frappe.db.commit()
	try:
		task = task_bridge.create_callback_task(call_name)
		frappe.db.commit()
		return task
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: lỗi tạo việc gọi lại", message=f"call={call_name}")
		frappe.db.commit()
		return None


def sweep_pending() -> int:
	"""Tác vụ định kỳ: xử lý cuộc gọi nhỡ (có Lead) chưa có việc gọi lại, tối đa SWEEP_LIMIT mỗi lượt. Trả số đã xử lý."""
	now = now_datetime()
	names = frappe.get_all(
		CALL,
		filters={
			**_pending_filters(),
			"creation": [
				"between",
				[add_to_date(now, days=-SWEEP_WINDOW_DAYS), add_to_date(now, minutes=-SWEEP_AGE_MINUTES)],
			],
		},
		pluck="name",
		order_by="creation asc",
		limit_page_length=SWEEP_LIMIT,
	)
	done = 0
	for name in names:
		if process_missed_call(name):
			done += 1
	return done
