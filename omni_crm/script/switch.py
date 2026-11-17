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
