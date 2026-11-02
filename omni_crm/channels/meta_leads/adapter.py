"""Adapter Meta Lead Ads (docs/15 mục 2): webhook `leadgen` chỉ LƯU mã lead; worker gọi Graph API lấy nội dung.

Xác thực: chữ ký `X-Hub-Signature-256` (HMAC-SHA256 raw body với App Secret, so hằng thời gian) trên mọi POST; handshake GET
so `hub.verify_token`. Chống phát lại bằng idempotent theo `leadgen_id` (không dùng cửa sổ thời gian vì Meta gửi lại tới 36 giờ).
CHƯA THỬ VỚI NỀN TẢNG THẬT.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from frappe import _

from omni_crm.channels import form_common
from omni_crm.channels.registry import register_adapter
from omni_crm.ingest.events import InboundEvent, OutboundRequest

CHANNEL_TYPE = "Meta Lead Ads"
SIGNATURE_HEADER = "x-hub-signature-256"


def _header(headers: dict, name: str) -> str | None:
	for key, value in (headers or {}).items():
		if str(key).lower() == name:
			return value
	return None


def signature_of(raw_body: bytes, app_secret: str) -> str:
	"""Giá trị header chuẩn "sha256=<hex>"."""
	return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify_token(channel_doc, supplied: str | None) -> bool:
	"""So `hub.verify_token` với token đã lưu bằng compare_digest; chưa đặt hoặc ngắn dưới 16 ký tự thì từ chối."""
	stored = channel_doc.get_password("meta_verify_token", raise_exception=False) or ""
	if len(stored) < 16:
		return False
	return hmac.compare_digest(stored.encode(), str(supplied or "").encode())


class MetaLeadsAdapter:
	"""Kênh nhận lead Meta; không có chiều ra."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Chữ ký X-Hub-Signature-256 đúng với App Secret của kênh (thiếu secret hoặc header thì sai)."""
		secret = channel_doc.get_password("meta_app_secret", raise_exception=False) or ""
		header = _header(headers, SIGNATURE_HEADER)
		if not secret or not header:
			return False
		return hmac.compare_digest(signature_of(raw_body, secret).encode(), header.strip().lower().encode())

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Mỗi thay đổi `leadgen` -> một sự kiện `form_lead` chỉ có mã (chưa có dữ liệu cá nhân)."""
		data: Any = json.loads(raw_body)
		if not isinstance(data, dict) or data.get("object") != "page":
			return []
		page_id = (channel_doc.get("meta_page_id") or "").strip()
		events: list[InboundEvent] = []
		for entry in data.get("entry") or []:
			for change in (entry or {}).get("changes") or []:
				value = (change or {}).get("value") or {}
				if (change or {}).get("field") != "leadgen" or not value.get("leadgen_id"):
					continue
				if page_id and str(value.get("page_id") or "") != page_id:
					continue  # lead của Page khác: không thuộc kênh này
				form = {
					"platform": "Meta",
					"lead_id": form_common.clean(value["leadgen_id"], 140),
					"form_id": form_common.clean(value.get("form_id"), 140),
					"page_id": form_common.clean(value.get("page_id"), 140),
					"ad_id": form_common.clean(value.get("ad_id"), 140),
					"adset_id": form_common.clean(value.get("adgroup_id"), 140),
					"created_time": value.get("created_time"),
					"fetched": False,
				}
				events.append(
					InboundEvent(
						channel=channel_doc.name,
						channel_type=CHANNEL_TYPE,
						event_type=form_common.EVENT_TYPE,
						external_message_id=form["lead_id"],
						external_sender_id=form["lead_id"],
						received_at=_now(),
						text=None,
						sender_profile={"form_lead": True},
						raw={"form_lead": form},
					)
				)
		return events

	def send(self, req: OutboundRequest, channel_doc) -> str:
		"""Kênh lead quảng cáo không gửi tin."""
		raise NotImplementedError(_("Kênh lead quảng cáo không gửi tin nhắn"))

	def can_send_now(self, identity_doc, channel_doc, source: str = "Sale") -> tuple[bool, str]:
		"""Không bao giờ gửi được."""
		return False, _("Kênh lead quảng cáo không gửi tin nhắn")

	def prepare_batch(self, msgs: list[Any], channel_doc) -> list[Any]:
		"""Xem crm_bridge.form_lead.prepare_batch."""
		from omni_crm.crm_bridge import form_lead

		return form_lead.prepare_batch(msgs, channel_doc)

	def after_resolve(self, res: Any, msgs: list[Any], channel_doc) -> None:
		"""Xem crm_bridge.form_lead.after_resolve."""
		from omni_crm.crm_bridge import form_lead

		form_lead.after_resolve(res, msgs, channel_doc)


def _now():
	from frappe.utils import now_datetime

	return now_datetime()


ADAPTER_CLASS = MetaLeadsAdapter
register_adapter(CHANNEL_TYPE, MetaLeadsAdapter())
