"""Lưu Omni Inbound Message và đưa job định danh vào hàng đợi."""

from __future__ import annotations

import frappe

from omni_crm import flags
from omni_crm.identity.normalize import normalize_sender
from omni_crm.ingest.events import InboundEvent

WORKER_METHOD = "omni_crm.pipeline.worker.process_identity"
QUEUE = "omni"


def compute_identity_key(channel_type: str, external_sender_id: str) -> str:
	"""identity_key = "{channel_type}:{người gửi đã chuẩn hóa}"."""
	return f"{channel_type}:{normalize_sender(channel_type, external_sender_id)}"


def save_inbound(event: InboundEvent) -> str | None:
	"""Lưu một sự kiện vào; trả tên bản ghi, hoặc None nếu đã nhận trước đó (trùng)."""
	unparsed = event.event_type == "unparsed"
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
			"received_at": event.received_at,
			"text": event.text,
			"attachments": frappe.as_json(event.attachments or []),
			"sender_profile": frappe.as_json(event.sender_profile or {}),
			"raw_payload": frappe.as_json(event.raw or {}),
			"status": "Manual" if unparsed else "New",
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		return None  # đã nhận rồi: bỏ qua, không enqueue

	if unparsed or not flags.channel_enabled(event.channel):
		return doc.name  # chỉ lưu, không xử lý
	enqueue_identity(identity_key)
	return doc.name


def enqueue_identity(identity_key: str, *, after_commit: bool = True) -> None:
	"""Đẩy job process_identity cho một định danh; không bao giờ ném lỗi ra ngoài.

	Mặc định hoãn đến sau commit (để worker thấy bản ghi). Lỗi enqueue (Redis chết) được bắt và
	ghi log; bản ghi New vẫn còn để tác vụ quét đẩy lại.
	"""

	def _enqueue() -> None:
		try:
			frappe.enqueue(
				WORKER_METHOD,
				queue=QUEUE,
				job_id=f"omni-identity::{identity_key}",
				deduplicate=True,
				identity_key=identity_key,
			)
		except Exception:
			# Không ghi identity_key (có thể chứa SĐT/email) và không ghi nội dung lỗi.
			frappe.log_error(
				title="Omni: enqueue định danh thất bại",
				message="Không đẩy được job vào hàng đợi omni; tin giữ trạng thái New, tác vụ quét sẽ đẩy lại.",
			)
			if after_commit:
				frappe.db.commit()

	if after_commit:
		frappe.db.after_commit.add(_enqueue)
	else:
		_enqueue()
