"""TelephonyAdapter: bọc một TelephonyProvider thành ChannelAdapter cho cổng tiếp nhận (docs/07 mục 1, 2.2, 4).

Chỉ sự kiện KẾT THÚC mới tạo `InboundEvent(event_type="call_ended", external_message_id=call_id)`, nên một cuộc
gọi đúng một tin vào (kể cả khi đối soát đêm bù lại). Sự kiện trung gian nối vào `Omni Call.raw_events`.
"""

from __future__ import annotations

import hmac
from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime

from omni_crm.channels.registry import register_adapter
from omni_crm.channels.telephony import calls, worker_hooks
from omni_crm.channels.telephony.base import CallEvent
from omni_crm.channels.telephony.registry import ProviderNotFoundError, provider_for_channel
from omni_crm.ingest.events import InboundEvent, OutboundRequest

CHANNEL_TYPE = "Telephony"
MIN_TOKEN_LENGTH = 32  # docs/07 mục 4


def request_query() -> dict:
	"""Tham số query của yêu cầu hiện tại. Frappe bỏ query khi thân là JSON nên đọc `request.args` trước."""
	query: dict = {}
	args = getattr(getattr(frappe.local, "request", None), "args", None)
	if args is not None:
		query.update({k: args.get(k) for k in args})
	for key, value in (frappe.form_dict or {}).items():
		query.setdefault(key, value)
	return query


def verify_token(channel_doc, supplied: str | None) -> bool:
	"""So `t` với webhook_token của kênh bằng compare_digest; token chưa đặt hoặc ngắn hơn 32 ký tự thì từ chối."""
	stored = channel_doc.get_password("webhook_token", raise_exception=False) or ""
	if len(stored) < MIN_TOKEN_LENGTH:
		return False
	return hmac.compare_digest(stored.encode(), str(supplied or "").encode())


def to_inbound(channel_doc, ev: CallEvent) -> InboundEvent:
	"""InboundEvent `call_ended` của một cuộc gọi đã kết thúc."""
	return InboundEvent(
		channel=channel_doc.name,
		channel_type=CHANNEL_TYPE,
		event_type="call_ended",
		external_message_id=ev.call_id,
		external_sender_id=ev.customer_number or "unknown",
		received_at=ev.ended_at or ev.started_at or now_datetime(),
		raw={"call_event": calls.serialize_event(ev)},
	)


class TelephonyAdapter:
	"""Adapter kênh tổng đài, dùng provider theo `Omni Channel.telephony_provider`."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Token `t` (compare_digest) rồi xác thực riêng của provider; lỗi bất kỳ coi là không hợp lệ."""
		query = request_query()
		if not verify_token(channel_doc, query.get("t")):
			return False
		try:
			return bool(
				provider_for_channel(channel_doc).verify_request(raw_body, headers, query, channel_doc)
			)
		except ProviderNotFoundError:
			return False

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Sự kiện kết thúc -> tin vào; sự kiện trung gian -> raw_events của Omni Call (không tạo tin vào)."""
		events = provider_for_channel(channel_doc).parse_event(raw_body, headers, channel_doc)
		out: list[InboundEvent] = []
		for ev in events:
			if ev.is_final:
				out.append(to_inbound(channel_doc, ev))
			else:
				calls.record_event(channel_doc, ev)
		return out

	def send(self, req: OutboundRequest, channel_doc) -> str:
		"""Kênh tổng đài không gửi tin."""
		raise NotImplementedError(_("Kênh tổng đài không gửi tin nhắn"))

	def can_send_now(self, identity_doc, channel_doc, source: str = "Sale") -> tuple[bool, str]:
		"""Không bao giờ gửi được."""
		return False, _("Kênh tổng đài không gửi tin nhắn")

	# Điểm nối với worker (không thuộc Protocol bắt buộc)
	def after_resolve(self, res: Any, msgs: list[Any], channel_doc) -> None:
		"""Xem worker_hooks.after_resolve."""
		worker_hooks.after_resolve(res, msgs, channel_doc)


ADAPTER_CLASS = TelephonyAdapter
register_adapter(CHANNEL_TYPE, TelephonyAdapter())
