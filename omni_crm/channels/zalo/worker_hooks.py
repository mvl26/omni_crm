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

from omni_crm import flags
from omni_crm.channels.zalo.client import ZaloClient
from omni_crm.pipeline.retry import redact

IDENTITY = "Omni Identity"
OUTBOUND = "Omni Outbound Message"
PROFILE_COOLDOWN = 600  # giây: sau lỗi lấy hồ sơ, chưa thử lại cho cùng khách
PROFILE_EVENTS = {"message", "follow", "user_info"}
PROFILE_NOTE_SECONDS = 30 * 24 * 3600  # giữ lý do không lấy được tên 30 ngày (bộ nhớ đệm, không đổi DocType)


def _note_key(identity_key: str) -> str:
	return f"omni:zalo:profile_note:{identity_key}"


def note_profile_reason(identity_key: str, reason: str | None) -> None:
	"""Ghi (hoặc xóa khi reason rỗng) lý do gần nhất không lấy được tên Zalo; chỉ thông điệp đã lọc, không bí mật."""
	cache = frappe.cache()
	if reason:
		cache.set_value(_note_key(identity_key), reason[:200], expires_in_sec=PROFILE_NOTE_SECONDS)
	else:
		cache.delete_value(_note_key(identity_key))


def profile_reason(identity_key: str) -> str | None:
	"""Lý do gần nhất không lấy được tên Zalo của khách (None nếu chưa có ghi nhận)."""
	return frappe.cache().get_value(_note_key(identity_key))


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
		note_profile_reason(
			msg.identity_key, _("Kênh Zalo chưa có access token nên chưa hỏi được hồ sơ khách")
		)
		return False
	name = _identity_name(msg.identity_key)
	if name and frappe.db.get_value(IDENTITY, name, "display_name"):
		return False
	return not frappe.cache().get_value(f"omni:zalo:profile_fail:{msg.identity_key}", expires=True)


def fetch_profile(
	msg: Any, channel_doc: Any, ignore_cooldown: bool = False, error_sink: list | None = None
) -> dict:
	"""Lấy hồ sơ Zalo của khách; lỗi thì ghi log đã lọc, lưu lý do, đặt thời gian chờ và trả {} (không ném).

	Sandbox: không gọi API (trả {}), lý do được ghi để tab Zalo hiển thị. `ignore_cooldown`: lệnh chạy tay bỏ qua thời gian chờ;
	`error_sink`: nếu là list thì lỗi gặp phải được thêm vào đó (để lệnh chạy tay biết khi nào nên dừng).
	"""
	if flags.sandbox():
		note_profile_reason(msg.identity_key, _("Đang ở chế độ thử (sandbox) nên không gọi Zalo để lấy tên"))
		return {}
	if not ignore_cooldown and frappe.cache().get_value(
		f"omni:zalo:profile_fail:{msg.identity_key}", expires=True
	):
		return {}
	try:
		profile = ZaloClient(channel_doc.name).get_user_profile(msg.external_sender_id)
	except Exception as e:
		if error_sink is not None:
			error_sink.append(e)
		frappe.cache().set_value(
			f"omni:zalo:profile_fail:{msg.identity_key}", 1, expires_in_sec=PROFILE_COOLDOWN
		)
		reason = getattr(e, "message_vi", None) or _("Lỗi gọi Zalo ({0})").format(type(e).__name__)
		note_profile_reason(msg.identity_key, redact(str(reason)))
		frappe.logger("omni").warning(
			"omni zalo: không lấy được hồ sơ khách (kênh %s): %s", channel_doc.name, redact(str(e))[:200]
		)
		return {}
	if not profile.get("display_name"):
		note_profile_reason(
			msg.identity_key,
			_("Zalo không trả tên cho khách này (khách chưa chia sẻ hoặc OA không có quyền)"),
		)
	else:
		note_profile_reason(msg.identity_key, None)
	return profile


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


def agent_user(channel_doc: Any, admin_id: str | None) -> str | None:
	"""User CRM của admin OA (`sender.admin_id` của oa_echo) theo bảng Omni Agent Map của kênh (cột agent_ref)."""
	ref = (admin_id or "").strip()
	if not ref:
		return None
	for row in channel_doc.get("agent_map") or []:
		if (row.get("agent_ref") or "").strip() == ref and row.get("user"):
			return row.user
	return None


def _echo_route(msg: Any, identity: str | None) -> dict:
	"""Loại tin của tin OA gõ ngoài hệ thống (V3-07): trong 48 giờ kể từ tin khách gần nhất trước nó thì miễn phí,
	ngoài khung thì Zalo trừ quỹ nên đánh dấu `quota_consumed` để bộ đếm khớp số Zalo tính."""
	from omni_crm.channels.zalo import routing

	last = None
	if identity:
		last = frappe.db.get_value(
			"Omni Inbound Message",
			{
				"identity": identity,
				"event_type": ["not in", ["oa_echo", "unfollow"]],  # như pipeline.worker.NON_INBOUND_EVENTS
				"received_at": ["<=", msg.received_at],
			},
			"received_at",
			order_by="received_at desc",
		)
	free_hours = 48
	inside = bool(last) and (msg.received_at - last).total_seconds() <= free_hours * 3600
	return {
		"message_kind": routing.CS_48H if inside else routing.CS_PAID,
		"purpose": "reply",
		"quota_consumed": 0 if inside else 1,
	}


def _create_echo_outbound(msg: Any, res: Any, channel_doc: Any = None) -> None:
	"""Ghi tin OA gửi ngoài hệ thống. Có `admin_id` = nhân viên gõ trên công cụ chat OA: nguồn Sale, `sent_by` theo
	Omni Agent Map, tạm dừng bot 24 giờ. Không có `admin_id` = kịch bản chatbot Zalo / ứng dụng khác: nguồn Bot, không
	tính là sale phản hồi (V3-07 mục C)."""
	if _is_own_send(msg):
		return
	admin_id = _profile_of(msg).get("admin_id")
	human = bool(admin_id)
	frappe.get_doc(
		{
			"doctype": OUTBOUND,
			"channel": msg.channel,
			"identity": res.identity,
			"lead": res.lead,
			"text": msg.text,
			"source": "Sale" if human else "Bot",
			"sent_by": agent_user(channel_doc, admin_id) if (human and channel_doc is not None) else None,
			"status": "Sent",
			"external_message_id": msg.external_message_id,
			"sent_at": msg.received_at,
			**_echo_route(msg, res.identity),
		}
	).insert(ignore_permissions=True)
	if human and res.identity:
		from omni_crm.bot import handoff

		handoff.pause_identity_for_sale(res.identity)


def after_resolve(res: Any, msgs: list[Any], channel_doc: Any) -> None:
	"""Sau resolver: cờ is_follower và ghi tin Sale trả lời ngoài hệ thống thành Omni Outbound Message."""
	for m in msgs:
		if m.event_type == "follow":
			_set_follower(m.identity_key, True)
		elif m.event_type == "oa_echo":
			_create_echo_outbound(m, res, channel_doc)
