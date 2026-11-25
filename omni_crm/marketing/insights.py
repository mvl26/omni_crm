"""Đồng bộ số liệu bài đã đăng (V3-10, Prompt 6 mục 1).

Cron mỗi giờ gọi `sync_due`; job chỉ chạy thật khi đã qua `insights_sync_hours` giờ kể từ lần trước (0 = tắt).
Lấy target "Đăng thành công" trong 30 ngày, bỏ bài chế độ thử (`dryrun-…`). Lỗi một bài không dừng cả job; mỗi bài
commit riêng. Zalo OA chưa có API số liệu bài viết được xác nhận nên để trống.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from omni_crm.marketing import rules
from omni_crm.marketing import settings as mk_settings
from omni_crm.marketing.publisher.base import get_adapter
from omni_crm.pipeline.retry import redact

TARGET = "Omni Content Target"
WINDOW_DAYS = 30
METRICS = ("reach", "impressions", "likes", "comments", "shares", "clicks")
LAST_RUN_KEY = "omni:mk:insights_last_run"


def due_targets() -> list[dict[str, Any]]:
	"""Target đã đăng thật trong 30 ngày gần nhất (không gồm bài chế độ thử)."""
	return frappe.get_all(
		TARGET,
		filters={
			"status": rules.T_SUCCESS,
			"published_at": [">=", add_to_date(now_datetime(), days=-WINDOW_DAYS)],
			"external_post_id": ["not like", "dryrun-%"],
			"platform": ["in", [rules.FACEBOOK, rules.ZALO]],
		},
		fields=["name", "platform", "channel_account", "external_post_id"],
		order_by="published_at desc",
	)


def sync_target(row: dict) -> bool:
	"""Lấy và lưu số liệu một target; trả True nếu lưu được. Chỉ ghi chỉ số có giá trị (None = chưa có, giữ trống)."""
	data = get_adapter(row["platform"]).fetch_insights(row["channel_account"], row["external_post_id"])
	values: dict[str, Any] = {k: int(data[k]) for k in METRICS if data.get(k) is not None}
	values["insights_fields"] = ",".join(values)  # cột Int mặc định 0: ghi rõ chỉ số nào thật sự có
	values["insights_synced_at"] = now_datetime()
	frappe.db.set_value(TARGET, row["name"], values, update_modified=False)
	return True


def sync_all() -> dict[str, int]:
	"""Đồng bộ mọi target đến hạn; trả {ok, errors}."""
	ok = errors = 0
	messages: list[str] = []
	for row in due_targets():
		try:
			sync_target(row)
			frappe.db.commit()
			ok += 1
		except Exception as e:
			frappe.db.rollback()
			errors += 1
			if len(messages) < 10:
				messages.append(f"{row['name']}: {redact(str(e))[:200]}")
	if errors:
		frappe.log_error(
			title="Omni Marketing: lỗi đồng bộ số liệu bài đăng",
			message=f"{errors} bài lỗi, {ok} bài thành công\n" + "\n".join(messages),
		)
		frappe.db.commit()
	return {"ok": ok, "errors": errors}


def sync_due() -> dict[str, int] | None:
	"""Cron mỗi giờ: chạy khi hub bật và đã đủ `insights_sync_hours` giờ từ lần chạy trước."""
	if not mk_settings.hub_enabled():
		return None
	hours = cint(mk_settings.get("insights_sync_hours"))
	if hours <= 0:
		return None
	cache = frappe.cache()
	key = cache.make_key(LAST_RUN_KEY)
	last = cache.get(key)
	if last and get_datetime(last.decode() if isinstance(last, bytes) else last) > add_to_date(
		now_datetime(), hours=-hours, minutes=5
	):
		return None
	cache.set(key, str(now_datetime()), ex=hours * 3600 + 3600)
	return sync_all()
