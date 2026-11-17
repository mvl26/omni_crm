"""Đồng bộ chỉ số bài đã tự đăng (ADR 0029), 06:30 hằng ngày, công tắc `content_metrics_enabled`.

Lượt cảm xúc, bình luận, chia sẻ đọc từ chính bài (cần pages_read_engagement). Lượt tiếp cận và lượt bấm link đọc từ insights
(cần read_insights); tên chỉ số cấu hình trong Omni Settings vì Meta đổi tên chỉ số theo phiên bản Graph [Chưa xác nhận].
Bài đăng tay không có mã bài trên nền tảng: số liệu nhập tay trên form.
"""

from __future__ import annotations

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.marketing import content_posts
from omni_crm.marketing.constants import POST, PUBLISHED
from omni_crm.marketing.graph import ContentGraphError, PageClient

DAYS = 30
DEFAULT_REACH = "post_impressions_unique"
DEFAULT_CLICKS = "post_clicks_by_type"
LINK_CLICK_KEYS = ("link clicks", "link_clicks")


def _metric_value(value) -> int | None:
	"""Số từ giá trị insights: số thì lấy luôn; dict (theo loại) thì lấy lượt bấm link, không có thì cộng hết."""
	if isinstance(value, bool):
		return None
	if isinstance(value, int | float):
		return int(value)
	if isinstance(value, dict):
		for key in LINK_CLICK_KEYS:
			if key in value:
				return int(value[key] or 0)
		return int(sum(v for v in value.values() if isinstance(v, int | float)))
	return None


def sync_daily() -> None:
	"""Cập nhật chỉ số các bài Đã đăng có mã bài trên nền tảng trong DAYS ngày gần nhất."""
	s = content_posts.settings()
	if not cint(s.get("content_metrics_enabled")) or flags.sandbox() or not s.get("content_publish_channel"):
		return
	client = PageClient(frappe.get_doc("Omni Channel", s.content_publish_channel))
	since = now_datetime() - timedelta(days=DAYS)
	posts = frappe.get_all(
		POST,
		filters={"status": PUBLISHED, "external_post_id": ["is", "set"], "published_at": [">=", since]},
		pluck="name",
	)
	reach_metric = (s.get("content_reach_metric") or DEFAULT_REACH).strip()
	clicks_metric = (s.get("content_clicks_metric") or DEFAULT_CLICKS).strip()
	for name in posts:
		sync_post(name, client, reach_metric, clicks_metric)
		frappe.db.commit()


def sync_post(name: str, client: PageClient, reach_metric: str, clicks_metric: str) -> dict:
	"""Đọc chỉ số một bài và ghi vào form; lỗi của từng phần ghi vào `last_error`, phần đọc được vẫn lưu."""
	post_id = frappe.db.get_value(POST, name, "external_post_id")
	values: dict = {}
	errors: list[str] = []
	try:
		values.update(client.post_stats(post_id))
	except ContentGraphError as e:
		errors.append(_("Tương tác: {0}").format(e.message_vi))
	try:
		raw = client.post_insights(post_id, [reach_metric, clicks_metric])
	except ContentGraphError as e:
		raw = {}
		errors.append(_("Insights: {0}").format(e.message_vi))
	if raw:
		values["metrics_json"] = json.dumps(raw, ensure_ascii=False, indent=1)
		if reach_metric in raw and _metric_value(raw[reach_metric]) is not None:
			values["reach"] = _metric_value(raw[reach_metric])
		if clicks_metric in raw and _metric_value(raw[clicks_metric]) is not None:
			values["link_clicks"] = _metric_value(raw[clicks_metric])
	values["metrics_synced_at"] = now_datetime()
	values["last_error"] = "; ".join(errors)[:1000] or None
	frappe.db.set_value(POST, name, values, update_modified=False)
	return values
