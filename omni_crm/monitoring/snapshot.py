"""Số liệu cho màn hình giám sát và luật cảnh báo (docs/11 mục 1, Đợt 1). Chỉ truy vấn gộp, không nạp từng tin."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, today

from omni_crm import flags
from omni_crm.channels.telephony.registry import reconcile_key
from omni_crm.channels.telephony.worker_hooks import UNVERIFIED_COUNTER_KEY
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


def _telephony(now) -> dict[str, Any]:
	"""Khối Tổng đài: cuộc gọi hôm nay (đã kết thúc), gọi nhỡ, kênh tổng đài. Số khác bổ sung ở D2-03."""
	by_status = {"Answered": 0, "Missed": 0, "Busy": 0, "Failed": 0, "Voicemail": 0}
	by_status.update(
		dict(
			frappe.db.sql(
				"""select status, count(*) from `tabOmni Call`
				where creation >= %s and status in ('Answered', 'Missed', 'Busy', 'Failed', 'Voicemail')
				group by status""",
				today(),
			)
		)
	)
	missed_in = frappe.db.sql(
		"select count(*) from `tabOmni Call` where creation >= %s and status = 'Missed' and direction = 'Inbound'",
		today(),
	)[0][0]
	last_call = dict(frappe.db.sql("select provider, max(creation) from `tabOmni Call` group by provider"))
	channels = frappe.get_all(
		CHANNEL,
		filters={"channel_type": "Telephony"},
		fields=["name", "enabled", "telephony_provider", "last_success_at"],
		order_by="creation asc",
	)
	overdue = frappe.db.sql(
		"""select t.name, t.assigned_to from `tabCRM Task` t
		where t.status in ('Backlog', 'Todo', 'In Progress') and t.due_date < %s
		and t.name in (select callback_task from `tabOmni Call` where callback_task is not null)""",
		now,
		as_dict=True,
	)
	cache = frappe.cache()
	reconcile, unverified = {}, {}
	for c in channels:
		reconcile[c.name] = cache.get_value(reconcile_key(c.name))  # None nếu chưa chạy / đã hết hạn
		try:
			unverified[c.name] = cint(
				cache.get(cache.make_key(UNVERIFIED_COUNTER_KEY.format(channel=c.name)))
			)
		except Exception:
			unverified[c.name] = None  # Redis lỗi: không biết
	return {
		"callbacks_overdue": len(overdue),
		"callbacks_overdue_users": sorted({r.assigned_to for r in overdue if r.assigned_to}),
		"reconcile": reconcile,
		"unverified_calls": unverified,
		"calls_today": sum(cint(v) for v in by_status.values()),
		"answered_today": cint(by_status["Answered"]),
		"missed_today": cint(missed_in),
		"by_status_today": {k: cint(v) for k, v in by_status.items()},
		"channels": [
			{
				"name": c.name,
				"enabled": cint(c.enabled),
				"provider": c.telephony_provider,
				"last_call_at": last_call.get(c.telephony_provider),
				"last_success_at": c.last_success_at,
			}
			for c in channels
		],
	}


def _bot(now) -> dict[str, Any]:
	"""Khối Bot (docs/11): token hôm nay / hạn mức, số lần gọi AI và số lần lỗi trong 24 giờ. Không có nội dung tin."""
	from omni_crm.bot.ai_client import breaker_open, tokens_today

	since = add_to_date(now, hours=-24)
	counts = dict(
		frappe.db.sql(
			"select status, count(*) from `tabOmni Bot Run` where creation >= %s group by status", since
		)
	)
	budget = cint(frappe.db.get_single_value("Omni Settings", "daily_token_budget"))
	used = tokens_today()
	return {
		"tokens_today": used,
		"daily_token_budget": budget,
		"usage_pct": round(used * 100 / budget, 1) if budget > 0 else None,
		"runs_24h": sum(cint(v) for v in counts.values()),
		"errors_24h": cint(counts.get("Error")) + cint(counts.get("Invalid Output")),
		"skipped_budget_24h": cint(counts.get("Skipped Budget")),
		"breaker_open": bool(breaker_open()),
	}


def _crm_lead_sync_enabled() -> int:
	"""Số `Lead Sync Source` (đồng bộ lead Facebook có sẵn của app crm) đang bật; 0 nếu app crm không có DocType này (V2-01, ĐQ-11)."""
	try:
		return cint(frappe.db.count("Lead Sync Source", {"enabled": 1}))
	except Exception:
		return 0


AD_CHANNEL_TYPES = ("Meta Lead Ads", "Google Lead Form", "Web Form")


def _ads(now) -> dict[str, Any]:
	"""Lead quảng cáo (V2-05): số chiến dịch đang chạy, kênh quảng cáo đang bật, lead thật gần nhất (không tính lead thử nghiệm)."""
	running = frappe.db.sql(
		"""select count(*), min(creation) from `tabOmni Campaign`
		where status = 'Đang chạy' and platform in ('Meta', 'Google', 'Website')"""
	)[0]
	channels = cint(
		frappe.db.count("Omni Channel", {"channel_type": ["in", list(AD_CHANNEL_TYPES)], "enabled": 1})
	)
	last = frappe.db.sql(
		"""select max(m.received_at) from `tabOmni Inbound Message` m left join `tabCRM Lead` l on l.name = m.lead
		where m.event_type = 'form_lead' and m.status != 'Ignored' and ifnull(l.omni_is_test, 0) = 0"""
	)[0][0]
	return {
		"campaigns_running": cint(running[0]),
		"first_campaign_created": str(running[1]) if running[1] else None,
		"channels_enabled": channels,
		"last_lead_at": str(last) if last else None,
		"silence_hours": flags.ads_setting("ads_silence_hours"),
	}


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
			"bot_analyze_enabled": cint(settings.get("bot_analyze_enabled")),
			"bot_reply_mode": settings.get("bot_reply_mode") or "Off",
			"crm_lead_sync_sources_enabled": _crm_lead_sync_enabled(),
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
		"telephony": _telephony(now),
		"ads": _ads(now),
		"bot": _bot(now),
		"sweeper": {
			"last_run_age_seconds": sweep_age,
			"stale": sweep_age is None or sweep_age > SWEEP_STALE_MINUTES * 60,
			"counts": sweep,
		},
	}
