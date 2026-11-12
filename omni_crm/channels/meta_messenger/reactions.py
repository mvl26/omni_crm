"""Cảm xúc (react) trên tin Messenger (ADR 0027).

- Khách thả/bỏ cảm xúc: webhook `message_reactions` -> miyano-intake ghi file `kind = "reaction"` -> runner gọi
  `apply_customer`: ghi `reaction_customer` lên tin có cùng mã tin Meta (`external_message_id`) của đúng khách đó.
- Sale thả/bỏ cảm xúc: `omni_crm.api.messenger_tab.react` gọi Send API (`sender_action = react/unreact`) rồi `set_page`.

Không tạo tin mới, không tạo Communication; chỉ báo realtime để tab tải lại.
"""

from __future__ import annotations

from typing import Any

import frappe

from omni_crm.crm_bridge import timeline

INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
MESSENGER = "Meta Messenger"


def find_message(channel: str, mid: str) -> dict | None:
	"""Tin (vào hoặc ra) của kênh có mã tin Meta `mid`: {message_doctype, name, lead, psid, at}."""
	if not mid:
		return None
	row = frappe.db.get_value(
		INBOUND,
		{"channel": channel, "external_message_id": mid},
		["name", "lead", "external_sender_id", "reaction_customer_at"],
		as_dict=True,
	)
	if row:
		return {
			"message_doctype": INBOUND,
			"name": row.name,
			"lead": row.lead,
			"psid": row.external_sender_id,
			"at": row.reaction_customer_at,
		}
	row = frappe.db.get_value(
		OUTBOUND,
		{"channel": channel, "external_message_id": mid},
		["name", "lead", "identity", "reaction_customer_at"],
		as_dict=True,
	)
	if row:
		psid = frappe.db.get_value(IDENTITY, row.identity, "external_id") if row.identity else None
		return {
			"message_doctype": OUTBOUND,
			"name": row.name,
			"lead": row.lead,
			"psid": psid,
			"at": row.reaction_customer_at,
		}
	return None


def apply_customer(channel: str, data: dict[str, Any]) -> str:
	"""Áp cảm xúc của khách (`mapper.reaction_of`); trả kết quả runner: "moi" | "trung" | "bo_qua".

	Bỏ qua khi không tìm thấy tin (tin cũ trước khi có kênh, đoạn sau của tin bị tách) hoặc tin không thuộc khách đã thả
	(PSID khác). Sự kiện cũ hơn lần đã áp (file nạp lệch thứ tự) không ghi đè.
	"""
	target = find_message(channel, data.get("mid") or "")
	if not target or (target["psid"] and target["psid"] != data.get("psid")):
		return "bo_qua"
	at = data.get("at")
	if target["at"] and at and target["at"] > at:
		return "trung"
	frappe.db.set_value(
		target["message_doctype"],
		target["name"],
		{"reaction_customer": data.get("emoji") or "", "reaction_customer_at": at},
		update_modified=False,
	)
	timeline.notify(target["lead"], [MESSENGER])
	return "moi"


def set_page(doctype: str, name: str, emoji: str, user: str) -> None:
	"""Ghi cảm xúc của Page (sale thả qua CRM) lên tin; emoji rỗng là bỏ."""
	frappe.db.set_value(
		doctype,
		name,
		{"reaction_page": emoji or "", "reaction_page_by": user if emoji else None},
		update_modified=False,
	)
