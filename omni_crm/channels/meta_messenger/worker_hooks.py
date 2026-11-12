"""Phần xử lý riêng của Messenger trong worker (ADR 0022), cùng khuôn với channels/zalo/worker_hooks.py.

- `prepare_batch` (trước resolver): bỏ echo của tin chính hệ thống đã gửi; lấy tên người gửi qua Graph cho khách mới.
- `after_resolve` (sau resolver): ghi echo (nhân viên trả lời trên Meta Business Suite) thành Omni Outbound Message `Sent`;
  Lead còn tên tạm ("Khách Meta Messenger …") được đổi sang tên Meta trả (trừ khi sale đã sửa tay tên).
- `refresh_names`: lệnh lấy lại tên cho khách đã có (ví dụ trước khi kênh có token Page).
Chạy trong job nền: worker đã commit trước khi vào; lỗi lấy tên không làm hỏng lượt.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from omni_crm import flags
from omni_crm.channels.meta_messenger.client import MessengerClient
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity.normalize import is_placeholder_name
from omni_crm.pipeline.retry import redact

IDENTITY = "Omni Identity"
LEAD = "CRM Lead"
CHANNEL_TYPE = "Meta Messenger"
NAME_FIELDS = {"first_name", "last_name", "lead_name"}
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


def rename_placeholder_lead(lead: str | None, name: str | None) -> bool:
	"""Lead còn tên tạm và sale chưa sửa tay tên: đổi sang `name` (cùng quy tắc với bot.analyze). True nếu đã đổi."""
	name = (name or "").strip()[:140]
	if not lead or not name or is_placeholder_name(name):
		return False
	row = frappe.db.get_value(LEAD, lead, ["first_name", "last_name", "omni_manual_fields"], as_dict=True)
	if not row or not is_placeholder_name(" ".join(filter(None, [row.first_name, row.last_name]))):
		return False
	if NAME_FIELDS & set(lead_bridge._parse_fields(row.omni_manual_fields)):
		return False
	with flags.omni_bot():  # hệ thống ghi: không tính là sale sửa tay
		frappe.db.set_value(LEAD, lead, {"first_name": name, "last_name": None, "lead_name": name})
	return True


def after_resolve(res: Any, msgs: list[Any], channel_doc: Any) -> None:
	"""Sau resolver: ghi tin nhân viên trả lời ngoài hệ thống thành Omni Outbound Message `Sent`; đổi tên tạm của Lead."""
	for m in msgs:
		if m.event_type == ECHO:
			_create_echo_outbound(m, res)
	names = [_profile_of(m).get("display_name") for m in msgs if m.event_type != ECHO]
	name = next((n for n in names if n), None)
	if name:
		rename_placeholder_lead(res.lead, name)


def refresh_names(channel: str | None = None, apply: int | str = 0, limit: int = 500) -> dict:
	"""Lấy lại tên Meta cho khách Messenger chưa có tên hoặc Lead còn tên tạm. `apply=0` chỉ đếm; `apply=1` mới gọi Meta và ghi.

	Chạy: `bench --site <site> execute omni_crm.channels.meta_messenger.worker_hooks.refresh_names --kwargs '{"apply": 1}'`.
	"""
	idents = frappe.get_all(
		IDENTITY,
		filters={"channel_type": CHANNEL_TYPE},
		fields=["name", "identity_key", "external_id", "display_name", "lead"],
		order_by="creation desc",
		limit_page_length=int(limit),
	)

	def lead_placeholder(lead: str | None) -> bool:
		row = frappe.db.get_value(LEAD, lead, ["first_name", "last_name"], as_dict=True) if lead else None
		return bool(row) and is_placeholder_name(" ".join(filter(None, [row.first_name, row.last_name])))

	todo = [i for i in idents if not i.display_name or lead_placeholder(i.lead)]
	stats: dict[str, Any] = {
		"can_lay": len(todo),
		"da_lay_ten": 0,
		"da_doi_ten_lead": 0,
		"loi": 0,
		"apply": bool(int(apply)),
	}
	if not int(apply) or not todo:
		return stats
	name = channel or frappe.db.get_value(
		"Omni Channel", {"channel_type": CHANNEL_TYPE, "enabled": 1}, "name"
	)
	if not name:
		stats["loi_dau"] = "Chưa có kênh Meta Messenger đang bật"
		return stats
	client = MessengerClient(frappe.get_doc("Omni Channel", name))
	for i in todo:
		display = i.display_name if i.display_name and not is_placeholder_name(i.display_name) else ""
		if not display:
			try:
				display = client.get_name(str(i.external_id or ""))
			except Exception as e:
				stats["loi"] += 1
				stats.setdefault("loi_dau", redact(getattr(e, "message_vi", None) or type(e).__name__)[:200])
				continue
			if not display:
				stats["loi"] += 1
				continue
			frappe.db.set_value(IDENTITY, i.name, "display_name", display[:140], update_modified=False)
			stats["da_lay_ten"] += 1
		if rename_placeholder_lead(i.lead, display):
			stats["da_doi_ten_lead"] += 1
		frappe.db.commit()
	return stats
