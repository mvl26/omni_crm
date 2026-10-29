"""Số liệu cho màn hình giám sát và luật cảnh báo (docs/11 mục 1, Đợt 1). Chỉ truy vấn gộp, không nạp từng tin."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, today

from omni_crm.pipeline.retry import redact
from omni_crm.pipeline.sweeper import COUNTS_CACHE_KEY

INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
CHANNEL = "Omni Channel"
CANDIDATE = "Omni Duplicate Candidate"
STATUSES = ["New", "Processing", "Retry", "Manual", "Done", "Ignored"]
OUT_STATUSES = ["Queued", "Sent", "Failed", "Blocked"]
SWEEP_STALE_MINUTES = 15  # tác vụ quét chạy mỗi 5 phút: quá 15 phút chưa chạy là scheduler/worker có vấn đề


def _percentiles(since) -> dict[str, Any]:
	"""p50/p95 (giây) của modified - creation trên tin Done trong cửa sổ; modified = thời điểm Done (worker)."""
	n = cint(
		frappe.db.sql(
			"select count(*) from `tabOmni Inbound Message` where status = 'Done' and modified >= %s", since
		)[0][0]
	)
	if not n:
		return {"count": 0, "p50": None, "p95": None}

	def at(q: float) -> int:
		offset = min(n - 1, int(q * n))
		row = frappe.db.sql(
			"""select timestampdiff(second, creation, modified) d from `tabOmni Inbound Message`
			where status = 'Done' and modified >= %s order by d limit 1 offset %s""",
			(since, offset),
		)
		return cint(row[0][0]) if row else 0

	return {"count": n, "p50": at(0.5), "p95": at(0.95)}


def _channel_rows(now) -> list[dict[str, Any]]:
	channels = frappe.get_all(
		CHANNEL,
		fields=[
			"name",
			"channel_type",
			"enabled",
			"outbound_enabled",
			"last_success_at",
			"last_error",
			"zalo_token_expires_at",
			"zalo_skip_signature",
			"creation",
		],
		order_by="creation asc",
	)
	if not channels:
		return []
	last_in = dict(
		frappe.db.sql("select channel, max(received_at) from `tabOmni Inbound Message` group by channel")
	)
	last_out = dict(
		frappe.db.sql(
			"select channel, max(sent_at) from `tabOmni Outbound Message` where status = 'Sent' group by channel"
		)
	)
	rows = []
	for c in channels:
		expires = c.zalo_token_expires_at if c.channel_type == "Zalo OA" else None
		rows.append(
			{
				"name": c.name,
				"channel_type": c.channel_type,
				"enabled": cint(c.enabled),
				"outbound_enabled": cint(c.outbound_enabled),
				"skip_signature": cint(c.zalo_skip_signature),
				"created": c.creation,
				"last_inbound_at": last_in.get(c.name),
				"last_outbound_at": last_out.get(c.name),
				"last_success_at": c.last_success_at,
				"last_error": redact(c.last_error or "")[:300] or None,
				"token_expires_at": expires,
				"token_seconds_left": int((get_datetime(expires) - now).total_seconds()) if expires else None,
			}
		)
	return rows


def build_snapshot() -> dict[str, Any]:
	"""Toàn bộ số liệu Đợt 1 của màn hình giám sát. Không chứa bí mật hay nội dung tin."""
	now = now_datetime()
	counts = {s: 0 for s in STATUSES}
	counts.update(
		dict(frappe.db.sql("select status, count(*) from `tabOmni Inbound Message` group by status"))
	)
	oldest_new = frappe.db.sql("select min(creation) from `tabOmni Inbound Message` where status = 'New'")[0][
		0
	]
	out_today = {s: 0 for s in OUT_STATUSES}
	out_today.update(
		dict(
			frappe.db.sql(
				"select status, count(*) from `tabOmni Outbound Message` where creation >= %s group by status",
				today(),
			)
		)
	)
	# Tin ra không có mốc thời gian Failed (outbound dùng update_modified=False): lấy tin tạo trong 1 giờ qua
	failed_1h = frappe.db.sql(
		"select count(*) from `tabOmni Outbound Message` where status = 'Failed' and creation >= %s",
		add_to_date(now, hours=-1),
	)[0][0]
	settings = frappe.get_cached_doc("Omni Settings")
	sweep = frappe.cache().get_value(COUNTS_CACHE_KEY)
	sweep_age = None
	if sweep and sweep.get("checked_at"):
		sweep_age = int((now - get_datetime(sweep["checked_at"])).total_seconds())
	return {
		"generated_at": str(now),
		"switches": {
			"sandbox_mode": cint(settings.sandbox_mode),
			"developer_mode": cint(frappe.conf.get("developer_mode")),
			"alert_recipients_set": bool((settings.alert_recipients or "").strip()),
			"raw_payload_retention_days": cint(settings.raw_payload_retention_days),
		},
		"channels": _channel_rows(now),
		"queue": {
			**{s.lower(): cint(counts[s]) for s in STATUSES},
			"oldest_new_age_seconds": int((now - get_datetime(oldest_new)).total_seconds())
			if oldest_new
			else None,
		},
		"timing": {"window_hours": 24, **_percentiles(add_to_date(now, hours=-24))},
		"outbound": {
			**{s.lower(): cint(out_today[s]) for s in OUT_STATUSES},
			"failed_last_hour": cint(failed_1h),
		},
		"duplicates": {"open": cint(frappe.db.count(CANDIDATE, {"status": "Open"}))},
		"sweeper": {
			"last_run_age_seconds": sweep_age,
			"stale": sweep_age is None or sweep_age > SWEEP_STALE_MINUTES * 60,
			"counts": sweep,
		},
	}
