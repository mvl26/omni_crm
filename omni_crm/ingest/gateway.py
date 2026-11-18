"""Lưu Omni Inbound Message và đưa job định danh vào hàng đợi."""

from __future__ import annotations

import secrets
import time

import frappe

from omni_crm import flags
from omni_crm.identity.normalize import normalize_sender
from omni_crm.ingest.events import InboundEvent
from omni_crm.queue import DEFAULT_QUEUE, queue_for_identity

WORKER_METHOD = "omni_crm.pipeline.worker.process_identity"
QUEUE = DEFAULT_QUEUE  # tên mặc định; định tuyến thật qua queue_for_identity (V21-01)


def compute_identity_key(channel_type: str, external_sender_id: str) -> str:
	"""identity_key = "{channel_type}:{người gửi đã chuẩn hóa}"."""
	return f"{channel_type}:{normalize_sender(channel_type, external_sender_id)}"


def new_inbound_name() -> str:
	"""Tên bản ghi tin vào theo thời gian (OIN-<năm>-<micro giây><3 số ngẫu nhiên>), KHÔNG dùng bộ đếm tabSeries.

	Bộ đếm `OIN-.YYYY.-.########` giữ khóa ghi dòng `tabSeries` tới khi commit, nên MỌI webhook xếp hàng sau một giao dịch
	đang giữ dòng đó (nguyên nhân chờ khóa, V21-04). Tên mới dài cố định 19 chữ số nên vẫn sắp xếp theo thời gian
	(`scoring` dùng `name desc` để phân định khi cùng giây) và đứng sau mọi tên cũ 8 chữ số.
	"""
	return f"OIN-{time.strftime('%Y')}-{int(time.time() * 1_000_000)}{secrets.randbelow(1000):03d}"


def save_inbound(event: InboundEvent) -> str | None:
	"""Lưu một sự kiện vào; trả tên bản ghi, hoặc None nếu đã nhận trước đó (trùng)."""
	unparsed = event.event_type == "unparsed"
	ignored = event.event_type == "ignored"  # thư tự động/hệ thống: lưu để tra cứu, không xử lý
	identity_key = compute_identity_key(event.channel_type, event.external_sender_id)
	doc = frappe.get_doc(
		{
			"doctype": "Omni Inbound Message",
			"dedupe_key": f"{event.channel}:{event.external_message_id}",
			"channel": event.channel,
			"channel_type": event.channel_type,
			"event_type": event.event_type,
			"external_message_id": event.external_message_id,
			"external_sender_id": event.external_sender_id,
			"identity_key": identity_key,
			"communication": event.communication,
			"received_at": event.received_at,
			"text": event.text,
			"attachments": frappe.as_json(event.attachments or []),
			"sender_profile": frappe.as_json(event.sender_profile or {}),
			"raw_payload": frappe.as_json(event.raw or {}),
			"status": "Manual" if unparsed else "Ignored" if ignored else "New",
		}
	)
	name = new_inbound_name()
	for attempt in (1, 2):
		try:
			doc.flags.name_set = False
			doc.insert(ignore_permissions=True, set_name=name)
			break
		except frappe.UniqueValidationError:
			return None  # đã nhận rồi (dedupe_key): bỏ qua, không enqueue
		except frappe.DuplicateEntryError:  # trùng TÊN (cực hiếm): đổi tên rồi thử lại một lần
			if attempt == 2:
				raise
			name = new_inbound_name()

	if unparsed or ignored or not flags.channel_enabled(event.channel):
		return doc.name  # chỉ lưu, không xử lý
	enqueue_identity(identity_key)
	return doc.name


def enqueue_identity(identity_key: str, *, after_commit: bool = True, job_suffix: str = "") -> None:
	"""Đẩy job process_identity cho một định danh; không bao giờ ném lỗi ra ngoài.

	after_commit=True (mặc định, dùng trong request webhook): hoãn tới sau commit để worker thấy bản
	ghi. Callback chỉ chạy khi có commit; nếu không commit hoặc rollback thì job MẤT ÂM THẦM.
	Vì vậy worker và tác vụ quét (chạy trong job nền, không có commit tiếp theo) PHẢI gọi với
	after_commit=False. job_suffix tạo job_id khác để đẩy tiếp từ chính job đang chạy (job cùng id
	đang STARTED sẽ bị deduplicate bỏ qua). Lỗi enqueue (Redis chết) được bắt và ghi log; bản ghi
	New vẫn còn để tác vụ quét đẩy lại.
	"""
	job_id = f"omni-identity::{identity_key}" + (f"::{job_suffix}" if job_suffix else "")

	def _enqueue() -> None:
		try:
			from omni_crm.script.switch import fast_debounce

			frappe.enqueue(
				WORKER_METHOD,
				queue=queue_for_identity(identity_key),
				job_id=job_id,
				deduplicate=True,
				identity_key=identity_key,
				debounce_seconds=fast_debounce(identity_key),  # bot kịch bản: không chờ gom tin (None = mặc định)
			)
		except Exception:
			# Không ghi identity_key (có thể chứa SĐT/email) và không ghi nội dung lỗi.
			frappe.log_error(
				title="Omni: enqueue định danh thất bại",
				message="Không đẩy được job vào hàng đợi nền; tin giữ trạng thái New, tác vụ quét sẽ đẩy lại.",
			)
			if after_commit:
				frappe.db.commit()

	if after_commit:
		frappe.db.after_commit.add(_enqueue)
	else:
		_enqueue()
