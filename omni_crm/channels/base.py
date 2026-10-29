"""Interface ChannelAdapter chung cho mọi kênh."""

from __future__ import annotations

from typing import Protocol

from omni_crm.ingest.events import InboundEvent, OutboundRequest


class ChannelAdapter(Protocol):
	"""Hợp đồng mọi adapter kênh phải thực hiện (docs/01 mục 4)."""

	channel_type: str

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Xác thực yêu cầu webhook (chữ ký hoặc token) trên raw body."""
		...

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Tách raw body thành 0, 1 hoặc nhiều sự kiện."""
		...

	def send(self, req: OutboundRequest, channel_doc) -> str:
		"""Gửi tin ra; trả external_message_id."""
		...

	def can_send_now(self, identity_doc, channel_doc, source: str = "Sale") -> tuple[bool, str]:
		"""Có được gửi cho định danh này lúc này không, kèm lý do."""
		...
