"""Adapter giả dùng trong test cổng tiếp nhận (không gọi mạng)."""

import json

import frappe
from frappe.utils import now_datetime

from omni_crm.ingest.events import InboundEvent


class FakeAdapter:
	channel_type = "Zalo OA"
	GOOD_SIGNATURE = "chu-ky-mau"

	def verify_request(self, raw_body, headers, channel_doc):
		return headers.get("X-Fake-Signature") == self.GOOD_SIGNATURE

	def parse(self, raw_body, headers, channel_doc):
		data = json.loads(raw_body)
		return [
			InboundEvent(
				channel=channel_doc.name,
				channel_type="Zalo OA",
				event_type="message",
				external_message_id=e["id"],
				external_sender_id=e["from"],
				received_at=now_datetime(),
				text=e.get("text"),
				raw=e,
			)
			for e in data["events"]
		]

	def send(self, req, channel_doc):
		raise NotImplementedError

	def can_send_now(self, identity_doc, channel_doc):
		return True, ""


def make_channel(name: str, **kw):
	"""Tạo Omni Channel Zalo OA cho test."""
	return frappe.get_doc(
		{"doctype": "Omni Channel", "channel_name": name, "channel_type": "Zalo OA", "zalo_app_id": "a", **kw}
	).insert()


def make_event(channel: str, ext_id: str, sender: str = " U1 ", **kw) -> InboundEvent:
	"""Tạo InboundEvent mẫu."""
	return InboundEvent(
		channel=channel,
		channel_type="Zalo OA",
		event_type="message",
		external_message_id=ext_id,
		external_sender_id=sender,
		received_at=now_datetime(),
		text="xin chào",
		**kw,
	)
