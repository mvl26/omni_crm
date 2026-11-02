"""Adapter Google Ads lead form (docs/15 mục 3): webhook POST JSON, xác thực `google_key` theo từng biểu mẫu.

`lead_id` là khóa chống trùng (Google có thể gửi lại). `column_name` đã ngừng dùng và có thể thiếu: chỉ dựa vào `column_id`.
`google_key` KHÔNG được lưu vào raw_payload. CHƯA THỬ VỚI NỀN TẢNG THẬT (đã đối chiếu tài liệu Google ngày 2026-11-02).
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from frappe import _

from omni_crm.channels import form_common
from omni_crm.channels.registry import register_adapter
from omni_crm.ingest.events import InboundEvent, OutboundRequest

CHANNEL_TYPE = "Google Lead Form"


def _body(raw_body: bytes) -> dict:
	data = json.loads(raw_body)
	if not isinstance(data, dict):
		raise ValueError("body")
	return data


def answers_of(data: dict) -> dict[str, str]:
	"""{column_id: giá trị} từ `user_column_data` (bỏ phần tử không có column_id)."""
	out: dict[str, str] = {}
	for col in data.get("user_column_data") or []:
		if isinstance(col, dict) and col.get("column_id"):
			out[form_common.clean(col["column_id"], 120)] = form_common.clean(col.get("string_value"))
	return out


def build(channel_doc, data: dict, form_doc, *, received_at=None):
	"""InboundEvent từ payload kiểu webhook (dùng chung với nhập CSV để hai đường chống trùng nhau theo lead_id)."""
	lead_id = form_common.clean(data.get("lead_id"), 140)
	if not lead_id:
		raise ValueError("lead_id")
	form = form_common.build_form(
		"Google",
		lead_id=lead_id,
		answers=answers_of(data),
		form_doc=form_doc,
		form_id=str(data.get("form_id") or ""),
		extra={
			"is_test": bool(data.get("is_test")),
			"campaign_ext_id": form_common.clean(data.get("campaign_id"), 140),
			"adset_id": form_common.clean(data.get("adgroup_id"), 140),
			"ad_id": form_common.clean(data.get("creative_id"), 140),
			"click_id": form_common.clean(data.get("gcl_id"), 200),
			"created_time": data.get("lead_submit_time"),
		},
	)
	return form_common.to_event(
		channel_doc, form, received_at=received_at or form_common.parse_time(data.get("lead_submit_time"))
	)


class GoogleLeadsAdapter:
	"""Kênh nhận lead Google; không có chiều ra."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""`google_key` trong body khớp khóa đã lưu cho đúng `form_id` (compare_digest); biểu mẫu lạ hoặc chưa có khóa thì sai."""
		from omni_crm.crm_bridge.form_lead import get_form

		try:
			data = _body(raw_body)
		except ValueError:
			return False
		form = get_form("Google", str(data.get("form_id") or ""))
		stored = (form.get_password("secret", raise_exception=False) if form else "") or ""
		if len(stored) < 8:
			return False
		return hmac.compare_digest(stored.encode(), str(data.get("google_key") or "").encode())

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Một lead -> một sự kiện `form_lead` (không chứa google_key)."""
		from omni_crm.crm_bridge.form_lead import get_form

		data = _body(raw_body)
		data.pop("google_key", None)
		return [build(channel_doc, data, get_form("Google", str(data.get("form_id") or "")))]

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


ADAPTER_CLASS = GoogleLeadsAdapter
register_adapter(CHANNEL_TYPE, GoogleLeadsAdapter())
