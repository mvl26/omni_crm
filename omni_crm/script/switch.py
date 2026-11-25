"""Công tắc bot kịch bản v3: bật toàn bộ (`script_bot_enabled`) hoặc chỉ danh sách thử (`script_test_identities`).

Đọc thẳng DB (không cache) vì đây là công tắc an toàn; lỗi thì coi là tắt.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

SETTINGS = "Omni Settings"


def setting(fieldname: str):
	"""Giá trị một trường của Omni Settings (đọc thẳng DB); lỗi trả None."""
	try:
		return frappe.db.get_single_value(SETTINGS, fieldname)
	except Exception:
		return None


def test_identities() -> set[str]:
	"""Tên các Omni Identity trong danh sách thử (mỗi dòng một tên)."""
	raw = setting("script_test_identities") or ""
	return {line.strip() for line in str(raw).replace(",", "\n").splitlines() if line.strip()}


def is_test(identity: str | None) -> bool:
	"""Định danh nằm trong danh sách thử của bot kịch bản."""
	return bool(identity) and identity in test_identities()


def enabled_for(identity: str | None) -> bool:
	"""Bot kịch bản được chạy với định danh này (bật toàn bộ, hoặc định danh trong danh sách thử)."""
	if not identity:
		return False
	return bool(cint(setting("script_bot_enabled"))) or is_test(identity)


FAST_CHANNEL = "Zalo OA"
GATHER = -1  # giá trị đặc biệt của `fast_debounce`: lượt có tin gõ, gom tin bằng `script.gather.run`


def fast_debounce(identity_key: str) -> int | None:
	"""Cách chờ gom tin cho định danh đang chạy bot kịch bản: 0 nếu mọi tin đang chờ là bấm nút (`#MÃ`); `GATHER` nếu
	có tin gõ (đợi khách im `debounce_seconds` kể từ tin cuối); None = dùng `debounce_seconds` chung (kênh khác, bot
	kịch bản không chạy, lỗi).
	"""
	try:
		rows = frappe.get_all(
			"Omni Inbound Message",
			filters={"identity_key": identity_key, "status": ["in", ["New", "Retry"]]},
			fields=["channel_type", "text"],
			limit_page_length=20,
		)
		if not rows or any(r.channel_type != FAST_CHANNEL for r in rows):
			return None
		if not cint(setting("script_bot_enabled")):
			name = frappe.db.get_value("Omni Identity", {"identity_key": identity_key}, "name")
			if not name or not is_test(name):
				return None
		from omni_crm.script import buttons

		if all(buttons.is_tap(r.text, identity_key) for r in rows):
			return 0
		return GATHER  # tin gõ: đợi khách nhắn xong (`script.gather`), không dùng thời gian chờ cố định
	except Exception:
		return None
