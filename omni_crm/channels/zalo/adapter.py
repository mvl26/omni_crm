"""Adapter Zalo OA (docs/06): xác thực webhook, ánh xạ sự kiện, gửi tin tư vấn, khung thời gian gửi.

Tên sự kiện/cấu trúc payload theo docs/06 mục 4, CHƯA đối chiếu tài liệu chính thức (docs/12 G7).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, convert_utc_to_system_timezone, now_datetime

from omni_crm.channels.registry import register_adapter
from omni_crm.channels.zalo import signature, worker_hooks
from omni_crm.channels.zalo.client import ZaloClient
from omni_crm.ingest.events import InboundEvent, OutboundRequest, stable_message_id

CHANNEL_TYPE = "Zalo OA"
FREE, PAID, BLOCKED = "free", "paid", "blocked"
REJECT_TTL = 86400
USER_EVENT_PREFIX = "user_send_"
OA_EVENT_PREFIX = "oa_send_"
# loại đính kèm -> nhãn mô tả ngắn đặt vào `text`
LABELS = {"image": "Ảnh", "file": "Tệp", "sticker": "Sticker", "location": "Vị trí", "audio": "Tin thoại"}


def _env_allows_skip() -> bool:
	"""Chỉ site dev (developer_mode) mới được bỏ qua chữ ký."""
	return bool(cint(frappe.conf.get("developer_mode")))


def _record_rejection(channel: str, reason: str) -> None:
	"""Đếm số lần từ chối theo lý do (Redis, 24 giờ) để giám sát; không ném lỗi."""
	try:
		cache = frappe.cache()
		key = cache.make_key(f"omni:zalo:reject:{channel}:{reason}")
		cache.incr(key)
		cache.expire(key, REJECT_TTL)
	except Exception:
		pass


def _ts(data: dict) -> datetime:
	try:
		seconds = float(data.get("timestamp")) / 1000
		return convert_utc_to_system_timezone(datetime.utcfromtimestamp(seconds)).replace(tzinfo=None)
	except (TypeError, ValueError, OverflowError):
		return now_datetime()


def _attachments(message: dict) -> list[dict]:
	out: list[dict] = []
	for att in message.get("attachments") or []:
		kind = att.get("type")
		payload = att.get("payload") or {}
		if kind == "location":
			coords = payload.get("coordinates") or {}
			lat, lon = coords.get("latitude"), coords.get("longitude")
			name = _("Vị trí: {0}, {1}").format(lat, lon)
			item = {"type": kind, "name": name}
			if lat is not None and lon is not None:
				item["url"] = f"https://www.google.com/maps?q={lat},{lon}"
			out.append(item)
			continue
		url = payload.get("url") or payload.get("thumbnail")
		out.append(
			{
				"type": kind,
				"url": url,
				"name": payload.get("name") or url,
				"mime": payload.get("mime") or None,
			}
		)
	return out


def _describe(message: dict, attachments: list[dict]) -> str:
	"""`text` của tin: văn bản thuần, hoặc nhãn ngắn của tệp đính kèm (kèm chú thích nếu có)."""
	text = (message.get("text") or "").strip()
	if not attachments:
		return text
	labels = []
	for att in attachments:
		label = _(LABELS.get(att.get("type"), "Tệp đính kèm"))
		name = att.get("name")
		labels.append(f"[{label}: {name}]" if att.get("type") == "file" and name else f"[{label}]")
	return " ".join(labels) + (f" {text}" if text else "")


def _event(channel_doc, data: dict, *, event_type: str, user_id: str, text: str | None, **kw) -> InboundEvent:
	name = data.get("event_name") or ""
	message = data.get("message") or {}
	ext_id = message.get("msg_id") or stable_message_id(name, user_id, data.get("timestamp"))
	return InboundEvent(
		channel=channel_doc.name,
		channel_type=CHANNEL_TYPE,
		event_type=event_type,
		external_message_id=str(ext_id),
		external_sender_id=str(user_id),
		received_at=_ts(data),
		text=text,
		raw=data,
		**kw,
	)


def parse_event(channel_doc, data: dict) -> InboundEvent:
	"""Ánh xạ một payload Zalo thành InboundEvent theo docs/06 mục 4."""
	name = data.get("event_name") or ""
	sender = (data.get("sender") or {}).get("id")
	recipient = (data.get("recipient") or {}).get("id")
	if name.startswith(USER_EVENT_PREFIX) and sender:
		message = data.get("message") or {}
		atts = _attachments(message)
		return _event(
			channel_doc,
			data,
			event_type="message",
			user_id=sender,
			text=_describe(message, atts),
			attachments=atts,
		)
	if name.startswith(OA_EVENT_PREFIX) and recipient:  # nhân viên trả lời ngoài hệ thống: khách là recipient
		message = data.get("message") or {}
		atts = _attachments(message)
		return _event(
			channel_doc,
			data,
			event_type="oa_echo",
			user_id=recipient,
			text=_describe(message, atts),
			attachments=atts,
		)
	if name == "user_submit_info" and sender:
		info = data.get("info") or {}
		profile = {
			"display_name": info.get("name"),
			"phone": info.get("phone"),
			"email": info.get("email"),
			"address": ", ".join(
				filter(None, (info.get(k) for k in ("address", "ward", "district", "city")))
			),
		}
		return _event(
			channel_doc,
			data,
			event_type="user_info",
			user_id=sender,
			text=_("[Khách gửi thông tin liên hệ]"),
			sender_profile={k: v for k, v in profile.items() if v},
		)
	if name in ("follow", "unfollow"):
		user = (data.get("follower") or {}).get("id") or sender
		if user:
			return _event(channel_doc, data, event_type=name, user_id=user, text=None)
	# Sự kiện khác: lưu để tra cứu, không xử lý
	user = sender or recipient or "unknown"
	return _event(channel_doc, data, event_type="ignored", user_id=user, text=None)


class ZaloAdapter:
	"""Adapter kênh Zalo OA."""

	channel_type = CHANNEL_TYPE

	def verify_request(self, raw_body: bytes, headers: dict, channel_doc) -> bool:
		"""Kiểm chữ ký webhook; cờ zalo_skip_signature chỉ có tác dụng ở site dev, production thì từ chối và cảnh báo."""
		if channel_doc.zalo_skip_signature:
			if _env_allows_skip():
				return True
			from omni_crm.channels.zalo.token import _alert

			_record_rejection(channel_doc.name, "skip_signature_tren_production")
			_alert(
				channel_doc.name,
				"zalo_skip_signature đang bật trên production",
				"Cổng từ chối mọi webhook của kênh cho tới khi tắt cờ này.",
			)
			return False
		ok, reason = signature.check(
			raw_body,
			headers,
			channel_doc.zalo_app_id or "",
			channel_doc.get_password("zalo_oa_secret_key", raise_exception=False) or "",
		)
		if not ok:
			_record_rejection(channel_doc.name, reason)
		return ok

	def parse(self, raw_body: bytes, headers: dict, channel_doc) -> list[InboundEvent]:
		"""Tách body thành sự kiện (Zalo gửi một đối tượng JSON mỗi lần; chấp nhận cả danh sách)."""
		data = json.loads(raw_body)
		items = data if isinstance(data, list) else [data]
		return [parse_event(channel_doc, d) for d in items if isinstance(d, dict)]

	def send_window(self, identity_doc, channel_doc) -> str:
		"""Khung gửi hiện tại: "free" (<= zalo_cs_free_hours), "paid" (đến zalo_cs_max_days), "blocked"."""
		last = identity_doc.get("last_inbound_at")
		if not last:
			return BLOCKED
		age = now_datetime() - last
		if age <= timedelta(hours=channel_doc.zalo_cs_free_hours or 0):
			return FREE
		if age <= timedelta(days=channel_doc.zalo_cs_max_days or 0):
			return PAID
		return BLOCKED

	def can_send_now(self, identity_doc, channel_doc, source: str = "Sale") -> tuple[bool, str]:
		"""(được gửi, lý do/cảnh báo). Khung "paid": chỉ Sale gửi được, lý do khác rỗng là cảnh báo phí cần xác nhận."""
		if channel_doc is not None and not channel_doc.get("outbound_enabled"):
			return False, _("Kênh Zalo đã tắt chiều ra")
		window = self.send_window(identity_doc, channel_doc)
		if window == FREE:
			return True, ""
		if window == PAID:
			if source != "Sale":
				return False, _(
					"Đã quá {0} giờ kể từ tin cuối của khách: tin này có thể bị tính phí nên bot không gửi."
				).format(channel_doc.zalo_cs_free_hours)
			return True, _("Tin này có thể bị tính phí (đã quá {0} giờ kể từ tin cuối của khách).").format(
				channel_doc.zalo_cs_free_hours
			)
		if not identity_doc.get("last_inbound_at"):
			return False, _(
				"Khách chưa từng nhắn cho OA nên chưa gửi được tin tư vấn; hãy liên hệ qua kênh khác."
			)
		return False, _(
			"Đã quá {0} ngày kể từ tin cuối của khách, Zalo không cho gửi tin tư vấn; hãy liên hệ qua kênh khác."
		).format(channel_doc.zalo_cs_max_days)

	def send(self, req: OutboundRequest, channel_doc) -> str:
		"""Gửi tin tư vấn tới khách của định danh; trả mã tin (đầu tiên nếu bị tách). Sandbox: không gọi HTTP."""
		user_id = frappe.db.get_value("Omni Identity", req.identity, "external_id")
		if not user_id:
			raise ValueError(_("Định danh chưa có mã người dùng Zalo"))
		ids = ZaloClient(channel_doc.name).send_text(user_id, req.text)
		return ids[0]

	def request_user_info(self, identity_doc, channel_doc) -> tuple[str, str]:
		"""Gửi tin yêu cầu thông tin người dùng (xin SĐT); trả (mã tin, nội dung hiển thị). Sandbox: không gọi HTTP."""
		title = _("Miyano Việt Nam")
		subtitle = _("Vui lòng chia sẻ số điện thoại để nhân viên tư vấn hỗ trợ bạn nhanh hơn.")
		user_id = identity_doc.get("external_id")
		if not user_id:
			raise ValueError(_("Định danh chưa có mã người dùng Zalo"))
		message_id = ZaloClient(channel_doc.name).send_request_user_info(user_id, title, subtitle)
		return message_id, f"{title}: {subtitle}"

	# Điểm nối với worker (không thuộc Protocol bắt buộc)
	def prepare_batch(self, msgs: list[Any], channel_doc) -> list[Any]:
		"""Xem worker_hooks.prepare_batch."""
		return worker_hooks.prepare_batch(msgs, channel_doc)

	def after_resolve(self, res: Any, msgs: list[Any], channel_doc) -> None:
		"""Xem worker_hooks.after_resolve."""
		worker_hooks.after_resolve(res, msgs, channel_doc)


ADAPTER_CLASS = ZaloAdapter
register_adapter(CHANNEL_TYPE, ZaloAdapter())
