"""Chuyển hội thoại cho người (docs/08 mục 6).

D2-07 chỉ có chế độ Shadow nên mới làm phần kiểm tra: bot có đang bị tạm dừng với khách này không. Việc đặt
`bot_paused_until`, thông báo cho người phụ trách và câu giữ chân là của D3-02.
"""

from __future__ import annotations

import frappe
from frappe.utils import get_datetime, now_datetime


def is_bot_paused(identity: str | None) -> bool:
	"""Khách (Omni Identity) đang bị tạm dừng bot, tức `bot_paused_until` còn ở tương lai."""
	until = frappe.db.get_value("Omni Identity", identity, "bot_paused_until") if identity else None
	return bool(until and get_datetime(until) > now_datetime())
