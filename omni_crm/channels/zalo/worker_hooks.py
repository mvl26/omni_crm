"""Phần xử lý riêng của Zalo trong worker (docs/06 mục 4, 5; prompt D1-08 mục 6, 7).

Worker gọi qua adapter (`prepare_batch` trước resolver, `after_resolve` sau resolver) nên pipeline không chứa
logic riêng của kênh. Mọi hàm ở đây chạy trong job nền: không gọi API khi đang giữ khóa DB (worker đã commit
trước khi vào), lỗi lấy hồ sơ không làm hỏng lượt.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

from omni_crm.channels.zalo.client import ZaloClient
from omni_crm.pipeline.retry import redact

IDENTITY = "Omni Identity"
OUTBOUND = "Omni Outbound Message"
PROFILE_COOLDOWN = 600  # giây: sau lỗi lấy hồ sơ, chưa thử lại cho cùng khách
PROFILE_EVENTS = {"message", "follow", "user_info"}


def _profile_of(msg: Any) -> dict:
	raw = msg.get("sender_profile")
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			raw = None
	return raw if isinstance(raw, dict) else {}


def _identity_name(identity_key: str) -> str | None:
	return frappe.db.get_value(IDENTITY, {"identity_key": identity_key}, "name")


def _set_follower(identity_key: str, value: bool) -> None:
	name = _identity_name(identity_key)
	if name:
		frappe.db.set_value(IDENTITY, name, "is_follower", 1 if value else 0, update_modified=False)


def _upsert_identity(msg: Any, display_name: str | None = None) -> str:
	"""Tạo Omni Identity (chưa gắn Lead) cho khách chỉ quan tâm OA; trả tên."""
	name = _identity_name(msg.identity_key)
	if name:
		if display_name and not frappe.db.get_value(IDENTITY, name, "display_name"):
			frappe.db.set_value(IDENTITY, name, "display_name", display_name, update_modified=False)
		return name
	doc = frappe.get_doc(
		{
			"doctype": IDENTITY,
			"identity_key": msg.identity_key,
			"channel_type": msg.channel_type,
			"external_id": msg.identity_key.split(":", 1)[-1],
			"display_name": display_name,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def _needs_profile(msg: Any, channel_doc: Any) -> bool:
	if msg.event_type not in PROFILE_EVENTS or _profile_of(msg).get("display_name"):
		return False
	if not channel_doc.get_password("zalo_access_token", raise_exception=False):
		return False
	name = _identity_name(msg.identity_key)
	if name and frappe.db.get_value(IDENTITY, name, "display_name"):
		return False
	return not frappe.cache().get_value(f"omni:zalo:profile_fail:{msg.identity_key}", expires=True)


def fetch_profile(msg: Any, channel_doc: Any) -> dict:
	"""Lấy hồ sơ Zalo của khách; lỗi thì ghi log đã lọc, đặt thời gian chờ và trả {} (không ném)."""
	try:
		return ZaloClient(channel_doc.name).get_user_profile(msg.external_sender_id)
	except Exception as e:
		frappe.cache().set_value(
			f"omni:zalo:profile_fail:{msg.identity_key}", 1, expires_in_sec=PROFILE_COOLDOWN
		)
		frappe.logger("omni").warning(
			"omni zalo: không lấy được hồ sơ khách (kênh %s): %s", channel_doc.name, redact(str(e))[:200]
		)
		return {}


def _is_own_send(msg: Any) -> bool:
	"""Echo của tin do chính hệ thống gửi (đã có Omni Outbound Message cùng mã tin)."""
	ext = msg.get("external_message_id")
	return bool(ext) and bool(
		frappe.db.exists(OUTBOUND, {"channel": msg.channel, "external_message_id": ext})
	)


def prepare_batch(msgs: list[Any], channel_doc: Any) -> list[Any]:
	"""Xử lý follow/unfollow/echo của chính ta và lấy hồ sơ khách mới; trả các tin còn lại cho resolver."""
	remaining: list[Any] = []
	for m in msgs:
		if m.event_type == "unfollow":
			_set_follower(m.identity_key, False)
		elif m.event_type == "follow" and not channel_doc.zalo_create_lead_on_follow:
			profile = fetch_profile(m, channel_doc) if _needs_profile(m, channel_doc) else {}
			_upsert_identity(m, profile.get("display_name"))
			_set_follower(m.identity_key, True)
		elif m.event_type == "oa_echo" and _is_own_send(m):
			continue  # tin ta gửi: đã có Omni Outbound Message và dòng thời gian, không ghi lần hai
		else:
			remaining.append(m)
	seen: set[str] = set()
	for m in remaining:  # mỗi khách một lần gọi hồ sơ cho cả lô
		if m.identity_key in seen or not _needs_profile(m, channel_doc):
			continue
		seen.add(m.identity_key)
		profile = fetch_profile(m, channel_doc)
		if profile:
			merged = {**profile, **{k: v for k, v in _profile_of(m).items() if v}}
			m.sender_profile = json.dumps(merged, ensure_ascii=False)
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
	"""Sau resolver: cờ is_follower và ghi tin Sale trả lời ngoài hệ thống thành Omni Outbound Message."""
	for m in msgs:
		if m.event_type == "follow":
			_set_follower(m.identity_key, True)
		elif m.event_type == "oa_echo":
			_create_echo_outbound(m, res)
