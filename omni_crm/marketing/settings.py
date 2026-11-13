"""Đọc Omni Marketing Settings an toàn: Single chưa lưu thì Frappe trả None cho mọi trường, nên mặc định nằm ở đây.

`dry_run` chưa cấu hình (None) hay lỗi đọc thì coi là BẬT: không bao giờ đăng thật do thiếu cấu hình.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

DOCTYPE = "Omni Marketing Settings"
DEFAULTS: dict[str, Any] = {
	"hub_disabled": 0,
	"dry_run": 1,
	"late_window_minutes": 30,
	"insights_sync_hours": 6,
	"landing_base_url": "https://yhct.miyano.com.vn",
	"default_landing_page": "/spd/",
	"require_approval": 1,
	"quality_threshold": 80,
	"graph_api_version": "",
	"brand_voice": "",
	"ai_model": "",
	"ai_fallback_model": "",
	"monthly_ai_budget": 0,
	"default_approver": None,
}
INT_FIELDS = {
	"hub_disabled",
	"dry_run",
	"late_window_minutes",
	"insights_sync_hours",
	"require_approval",
	"quality_threshold",
}


def get(field: str) -> Any:
	"""Giá trị một trường; chưa lưu (không có dòng trong tabSingles) thì lấy mặc định."""
	row = frappe.db.sql("select value from `tabSingles` where doctype=%s and field=%s", (DOCTYPE, field))
	if not row or row[0][0] in (None, ""):
		return DEFAULTS.get(field)
	value = row[0][0]
	return cint(value) if field in INT_FIELDS else value


def dry_run() -> bool:
	"""Chế độ thử của Marketing Hub; Omni Settings.sandbox_mode bật cũng tính là thử. Lỗi đọc -> thử (an toàn)."""
	from omni_crm import flags

	try:
		return bool(cint(get("dry_run"))) or flags.sandbox()
	except Exception:
		return True


def hub_enabled() -> bool:
	"""Công tắc Marketing Hub (cực tính ngược: hub_disabled)."""
	try:
		return not cint(get("hub_disabled"))
	except Exception:
		return False


def require_approval() -> bool:
	return bool(cint(get("require_approval")))


def quality_threshold() -> int:
	return max(cint(get("quality_threshold")), 0)


def late_window_minutes() -> int:
	return max(cint(get("late_window_minutes")), 0)


def ensure_initialized() -> None:
	"""Ghi mặc định vào Single nếu chưa từng lưu (patch cài đặt gọi); không đè giá trị đã có."""
	have = {r[0] for r in frappe.db.sql("select field from `tabSingles` where doctype=%s", (DOCTYPE,))}
	doc = frappe.get_single(DOCTYPE)
	changed = False
	for field, value in DEFAULTS.items():
		if field not in have and value not in (None, ""):
			doc.set(field, value)
			changed = True
	if changed:
		doc.flags.ignore_permissions = True
		doc.save()
