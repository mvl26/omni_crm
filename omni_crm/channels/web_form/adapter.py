"""Adapter form website/landing page (docs/15 mục 4): POST JSON hoặc form-data, người gửi ẩn danh (Guest).

Bảo vệ: giới hạn kích thước, giới hạn tần suất theo IP (api/webhooks), honeypot (trả thành công giả, không lưu),
kiểm tra chống bot tùy chọn (Turnstile/reCAPTCHA, tắt mặc định, thất bại thì từ chối), giới hạn độ dài trường.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs

from frappe import _

from omni_crm import flags
from omni_crm.channels import form_common
from omni_crm.channels.registry import register_adapter
from omni_crm.ingest.events import InboundEvent, OutboundRequest

CHANNEL_TYPE = "Web Form"
MAX_BODY_BYTES = 64 * 1024
HONEYPOT_FIELDS = ("website_url", "hp_field", "_hp")
META_KEYS = {
	"form",
	"form_id",
	"submission_id",
	"ch",
	"cmd",
	"utm_source",
	"utm_medium",
	"utm_campaign",
	"utm_content",
	"utm_term",
	"gclid",
	"fbclid",
	"landing_page",
	"referrer",
	"captcha_token",
	"cf-turnstile-response",
	"g-recaptcha-response",
	*HONEYPOT_FIELDS,
}
_SUBMISSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def load_body(raw_body: bytes, content_type: str = "") -> dict[str, Any]:
	"""Body JSON hoặc x-www-form-urlencoded thành dict phẳng; ném ValueError nếu không đọc được."""
	if len(raw_body) > MAX_BODY_BYTES:
		raise ValueError("too_large")
	text = raw_body.decode("utf-8", errors="strict")
	if "json" in content_type or text.lstrip().startswith("{"):
		data = json.loads(text)
		if not isinstance(data, dict):
			raise ValueError("body")
		return data
	return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(text, keep_blank_values=True).items()}


def is_honeypot(data: dict) -> bool:
	"""Ô bẫy bot bị điền."""
	return any(str(data.get(f) or "").strip() for f in HONEYPOT_FIELDS)


class WebFormAdapter:
	"""Kênh nhận form website; không có chiều ra."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Chống bot (nếu cấu hình): token không hợp lệ thì từ chối; không cấu hình thì luôn đúng (lớp bảo vệ khác ở endpoint)."""
		try:
			data = load_body(raw_body, "json")
		except ValueError:  # JSON hỏng/không phải UTF-8: không lưu rác từ người lạ
			return False
		provider = channel_doc.get("webform_captcha_provider")
		if not provider:
			return True
		from omni_crm.channels.web_form import captcha

		token = (
			data.get("captcha_token") or data.get("cf-turnstile-response") or data.get("g-recaptcha-response")
		)
		return captcha.verify(channel_doc, provider, str(token or ""))

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Honeypot hoặc thiếu mọi dữ liệu thì 0 sự kiện; ngược lại một sự kiện `form_lead`."""
		import uuid

		import frappe

		from omni_crm.crm_bridge.form_lead import get_form

		data = load_body(raw_body, "json")
		submission = str(data.get("submission_id") or "")
		if not _SUBMISSION_RE.match(submission):
			submission = uuid.uuid4().hex
		frappe.flags.omni_web_submission = submission  # endpoint trả lại để trang hiện lời cảm ơn
		if is_honeypot(data):
			return []
		form_id = str(data.get("form") or data.get("form_id") or "")
		form_doc = get_form("Website", form_id)
		if form_doc is None and not flags.webform_accept_unknown_form():
			return []  # mã biểu mẫu chưa cấu hình: bỏ qua, trả thành công giả như honeypot (không lộ cấu hình)
		answers = {k: v for k, v in data.items() if k not in META_KEYS}
		utm = {
			k: form_common.clean(data.get(k), 200)
			for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
		}
		form = form_common.build_form(
			"Website",
			lead_id=submission,
			answers=answers,
			form_doc=form_doc,
			form_id=form_id,
			extra={
				"utm": {k: v for k, v in utm.items() if v},
				"click_id": form_common.clean(data.get("gclid") or data.get("fbclid"), 200),
				"landing_page": form_common.clean(data.get("landing_page"), 500),
				"referrer": form_common.clean(data.get("referrer"), 500),
			},
		)
		if not (form["mapped"] or form["custom"] or form["unmapped"]):
			return []
		return [form_common.to_event(channel_doc, form)]

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


ADAPTER_CLASS = WebFormAdapter
register_adapter(CHANNEL_TYPE, WebFormAdapter())
