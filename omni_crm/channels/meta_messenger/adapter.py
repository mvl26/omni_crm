"""Adapter Meta Messenger (ADR 0022).

Nhận: CHỈ qua file của miyano-intake (`intake_files`), không có webhook Messenger vào omni_crm, để mỗi tin chỉ có một
đường vào (Page chỉ có một callback URL, đang trỏ về miyano-intake). Vì vậy `verify_request` luôn từ chối và `parse`
không dùng; ánh xạ file -> InboundEvent nằm ở `intake_files/mapper.py`.

Gửi: tin chữ trả lời khách qua Send API, chỉ trong 24 giờ kể từ tin cuối của khách (`messaging_type = RESPONSE`).
Gửi ảnh/tệp (ADR 0023) qua `req.attachment` khi bật `messenger_media_send_enabled`. Gửi ngoài 24 giờ (tag
HUMAN_AGENT) chưa làm (ADR 0022, D3).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from omni_crm.channels.meta_messenger import worker_hooks
from omni_crm.channels.meta_messenger.client import PERMANENT, MessengerClient, MessengerError
from omni_crm.channels.registry import register_adapter
from omni_crm.ingest.events import InboundEvent, OutboundRequest

CHANNEL_TYPE = "Meta Messenger"
WINDOW_HOURS = 24


class MetaMessengerAdapter:
	"""Kênh Messenger của một Page."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Không nhận webhook Messenger trực tiếp (ADR 0022 mục A1): luôn từ chối."""
		return False

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Không dùng: tin Messenger vào qua intake_files.mapper."""
		return []

	def can_send_now(self, identity_doc, channel_doc, source: str = "Sale") -> tuple[bool, str]:
		"""Được gửi khi kênh bật chiều ra và tin cuối của khách trong 24 giờ; lý do rỗng nghĩa là gửi bình thường."""
		if channel_doc is not None and not channel_doc.get("outbound_enabled"):
			return False, _("Kênh Messenger đã tắt chiều ra")
		last = identity_doc.get("last_inbound_at")
		if not last:
			return False, _(
				"Khách chưa từng nhắn cho Page nên chưa gửi được tin Messenger; hãy liên hệ qua kênh khác."
			)
		if now_datetime() - get_datetime(last) > timedelta(hours=WINDOW_HOURS):
			return False, _(
				"Đã quá {0} giờ kể từ tin cuối của khách, Messenger không cho Page nhắn tiếp; hãy liên hệ qua kênh khác."
			).format(WINDOW_HOURS)
		return True, ""

	def send(self, req: OutboundRequest, channel_doc) -> str:
		"""Gửi tin chữ tới PSID của định danh; trả mã tin (đoạn đầu nếu bị tách). Sandbox: không gọi HTTP."""
		psid = frappe.db.get_value("Omni Identity", req.identity, "external_id")
		if not psid:
			raise ValueError(_("Định danh chưa có mã người dùng Messenger (PSID)"))
		attachment = getattr(req, "attachment", None)
		if attachment:
			return self._send_attachment(channel_doc, psid, attachment)
		return MessengerClient(channel_doc).send_text(psid, req.text)

	@staticmethod
	def _send_attachment(channel_doc, psid: str, attachment: dict) -> str:
		"""Tin ảnh/tệp (ADR 0023): chặn khi công tắc tắt, đọc tệp trong CRM lúc gửi; lỗi tệp là lỗi vĩnh viễn."""
		from omni_crm import flags
		from omni_crm.channels.meta_messenger import media_out

		if not flags.messenger_media_send_enabled():
			raise MessengerError(None, PERMANENT, _("Chưa bật gửi ảnh/tệp qua Messenger"))
		name = attachment.get("name") or "tep"
		try:
			content = media_out.read_content(attachment["file_url"])
		except media_out.MediaError as e:
			raise MessengerError(None, PERMANENT, str(e)) from e
		kind = attachment.get("kind") or media_out.kind_of(name) or media_out.FILE
		return MessengerClient(channel_doc).send_attachment(
			psid, kind, name, content, media_out.mime_of(name)
		)

	# Điểm nối với worker (không thuộc Protocol bắt buộc)
	def prepare_batch(self, msgs: list[Any], channel_doc) -> list[Any]:
		"""Xem worker_hooks.prepare_batch."""
		return worker_hooks.prepare_batch(msgs, channel_doc)

	def after_resolve(self, res: Any, msgs: list[Any], channel_doc) -> None:
		"""Xem worker_hooks.after_resolve."""
		worker_hooks.after_resolve(res, msgs, channel_doc)


ADAPTER_CLASS = MetaMessengerAdapter
register_adapter(CHANNEL_TYPE, MetaMessengerAdapter())
