"""Phần xử lý riêng của Messenger trong worker (ADR 0022), cùng khuôn với channels/zalo/worker_hooks.py.

- `prepare_batch` (trước resolver): bỏ echo của tin chính hệ thống đã gửi; lấy tên người gửi qua Graph cho khách mới.
- `after_resolve` (sau resolver): ghi echo (nhân viên trả lời trên Meta Business Suite) thành Omni Outbound Message `Sent`.
Chạy trong job nền: worker đã commit trước khi vào; lỗi lấy tên không làm hỏng lượt.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from omni_crm.channels.meta_messenger.client import MessengerClient
from omni_crm.pipeline.retry import redact

IDENTITY = "Omni Identity"
OUTBOUND = "Omni Outbound Message"
ECHO = "oa_echo"  # dùng chung loại sự kiện "sale trả lời ngoài hệ thống" với Zalo (ADR 0022 mục 9)
PROFILE_EVENTS = {"message", "postback", "referral"}
PROFILE_COOLDOWN = 600  # giây: sau lỗi lấy tên, chưa hỏi lại cho cùng khách


def _profile_of(msg: Any) -> dict:
	raw = msg.get("sender_profile")
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			raw = None
	return raw if isinstance(raw, dict) else {}


def _is_own_send(msg: Any) -> bool:
	"""Echo của tin do chính hệ thống gửi (đã có Omni Outbound Message cùng mã tin)."""
	ext = msg.get("external_message_id")
	return bool(ext) and bool(
		frappe.db.exists(OUTBOUND, {"channel": msg.channel, "external_message_id": ext})
	)


def _needs_name(msg: Any) -> bool:
	if msg.event_type not in PROFILE_EVENTS or _profile_of(msg).get("display_name"):
		return False
	name = frappe.db.get_value(IDENTITY, {"identity_key": msg.identity_key}, "display_name")
	if name:
		return False
	return not frappe.cache().get_value(f"omni:messenger:profile_cooldown:{msg.identity_key}")


def fetch_name(msg: Any, channel_doc: Any) -> str:
	"""Tên người gửi từ Graph; lỗi thì đặt thời gian chờ và trả rỗng (không làm hỏng lượt)."""
	psid = str(msg.get("external_sender_id") or "")
	if not psid:
		return ""
	try:
		return MessengerClient(channel_doc).get_name(psid)
	except Exception as e:
		frappe.cache().set_value(
			f"omni:messenger:profile_cooldown:{msg.identity_key}", 1, expires_in_sec=PROFILE_COOLDOWN
		)
		frappe.logger("omni").warning(
			"omni messenger: không lấy được tên người gửi (kênh %s): %s",
			channel_doc.name,
			redact(getattr(e, "message_vi", None) or type(e).__name__)[:200],
		)
		return ""


def prepare_batch(msgs: list[Any], channel_doc: Any) -> list[Any]:
	"""Bỏ echo của tin ta gửi, lấy tên khách mới (mỗi khách một lần cho cả lô); trả các tin còn lại cho resolver."""
	remaining = [m for m in msgs if not (m.event_type == ECHO and _is_own_send(m))]
	seen: set[str] = set()
	for m in remaining:
		if m.identity_key in seen or not _needs_name(m):
			continue
		seen.add(m.identity_key)
		name = fetch_name(m, channel_doc)
		if name:
			m.sender_profile = json.dumps({**_profile_of(m), "display_name": name}, ensure_ascii=False)
			frappe.db.set_value(
				"Omni Inbound Message", m.name, "sender_profile", m.sender_profile, update_modified=False
			)
	return remaining


def _create_echo_outbound(msg: Any, res: Any) -> None:
	if _is_own_send(msg):
		return
	frappe.get_doc(
		{
			"doctype": OUTBOUND,
			"channel": msg.channel,
			"identity": res.identity,
			"lead": res.lead,
			"text": msg.text,
			"source": "Sale",
			"status": "Sent",
			"external_message_id": msg.external_message_id,
			"sent_at": msg.received_at,
		}
	).insert(ignore_permissions=True)


def after_resolve(res: Any, msgs: list[Any], channel_doc: Any) -> None:
	"""Sau resolver: ghi tin nhân viên trả lời ngoài hệ thống thành Omni Outbound Message `Sent`."""
	for m in msgs:
		if m.event_type == ECHO:
			_create_echo_outbound(m, res)
