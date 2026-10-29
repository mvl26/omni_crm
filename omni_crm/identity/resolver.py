"""Khớp định danh với Lead (tạo, khớp, nghi trùng)."""

from __future__ import annotations

from typing import Any

import frappe


def resolve(msgs: list[Any]) -> tuple[str, str | None]:
	"""Trả (tên Omni Identity, tên Lead hoặc None) cho lô tin của một khách.

	# Thay thế ở D1-05: bản tạm chỉ get_or_create Omni Identity theo identity_key, chưa gắn Lead.
	"""
	first = msgs[0]
	identity_key = first.identity_key
	name = frappe.db.get_value("Omni Identity", {"identity_key": identity_key}, "name")
	if name:
		return name, frappe.db.get_value("Omni Identity", name, "lead")
	doc = frappe.get_doc(
		{
			"doctype": "Omni Identity",
			"identity_key": identity_key,
			"channel_type": first.channel_type,
			"external_id": first.external_sender_id,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name, None
