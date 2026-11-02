"""API Tổng quan CSKH (docs/13 mục 1, 4): `snapshot(period, scope)`.

Mọi số liệu qua quyền xem CRM Lead của người gọi (`dashboard/access.py`); `scope` không mở rộng quyền. Kết quả cache
60 giây theo (người dùng, kỳ, phạm vi). Khối thuộc tính năng đang tắt không có mặt trong `blocks`.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime

from omni_crm import flags
from omni_crm.dashboard import access, blocks

CACHE_SECONDS = 60
CACHE_PREFIX = "omni:dashboard:"


def _cache_key(user: str, period: str, scope: str) -> str:
	return f"{CACHE_PREFIX}{user}:{period}:{scope}"


def clear_cache() -> None:
	"""Xóa cache Tổng quan (dùng khi đổi cấu hình/thử nghiệm)."""
	frappe.cache().delete_keys(CACHE_PREFIX)


@frappe.whitelist()
def snapshot(period: str = "today", scope: str | None = None) -> dict[str, Any]:
	"""Số liệu Tổng quan cho kỳ "today" | "7d" | "30d" và phạm vi "mine" | "team" (cache 60 giây).

	Trả {"enabled", "generated_at", "period", "scope", "can_team", "blocks": {khóa: dữ liệu}}.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Cần đăng nhập"), frappe.PermissionError)
	if not flags.dashboard_enabled():
		return {"enabled": False, "blocks": {}, "message": _("Dashboard CSKH đang tắt")}
	period = (
		period if period in blocks.PERIODS else "today"
	)  # V2-05: danh sách cho phép trước khi vào khóa cache
	q = blocks.make_query(period, scope, user)
	key = _cache_key(user, period, q.scope)
	cache = frappe.cache()
	hit = cache.get_value(
		key, expires=True
	)  # expires=True: không giữ bản trong bộ nhớ tiến trình (sẽ không bao giờ hết hạn)
	if hit:
		return hit
	result = {
		"enabled": True,
		"generated_at": str(now_datetime()),
		"period": period,
		"scope": q.scope,
		"can_team": access.is_team_lead(user),
		"sla_minutes": flags.care_setting("response_sla_minutes"),
		"blocks": blocks.run_blocks(q),
	}
	cache.set_value(key, result, expires_in_sec=CACHE_SECONDS)
	return result
