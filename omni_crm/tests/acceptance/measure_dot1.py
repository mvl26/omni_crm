"""Đo các chỉ số nghiệm thu Đợt 1 (docs/00 mục 2) từ dữ liệu thật trong một khoảng thời gian.

Chạy cuối tuần chạy thử (CHỈ ĐỌC, không ghi gì):

	bench --site <site> execute omni_crm.tests.acceptance.measure_dot1.run \
		--kwargs '{"start": "2026-11-02 00:00:00", "end": "2026-11-09 00:00:00"}'

Tham số tùy chọn: `exclude_prefixes` (mặc định bỏ kênh/định danh dữ liệu giả: LOAD, PLT, TD1, DEMO, RV),
`include_sandbox` (tính cả tin ra sandbox; mặc định không vì sandbox không tới khách),
`webhook_2xx_count` (số request webhook trả 2xx lấy từ nhật ký nginx/gunicorn, để đối chiếu "tin mất"),
`uptime_pct` (độ sẵn sàng đo bởi bộ giám sát NGOÀI, vì DB không đo được).

Định nghĩa từng chỉ số và truy vấn tương đương nằm ở docs/nghiem-thu/dot-1.md. Kết quả in JSON và bảng Markdown.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

from omni_crm.utils import business_hours

DEFAULT_EXCLUDE = ("LOAD", "PLT", "TD1", "DEMO", "RV")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
OPEN_STATES = ("New", "Processing", "Retry")


def _pct(values: list[float], p: float) -> float | None:
	if not values:
		return None
	v = sorted(values)
	return round(v[max(0, min(len(v) - 1, math.ceil(p * len(v)) - 1))], 1)


def _in_business_hours(at: datetime, hours: dict[str, str]) -> bool:
	"""`hours` dạng Omni Settings.business_hours: {"mon-fri": "08:00-17:30", "sat": "08:00-12:00"}; dùng module chung."""
	return business_hours.is_business_time(at, business_hours.parse_hours(hours), frozenset())


def _excluded_channels(prefixes: tuple[str, ...]) -> list[str]:
	names = frappe.get_all("Omni Channel", pluck="name")
	return [n for n in names if any(n.startswith(p) for p in prefixes)]


def lost_messages(start, end, channels_excl: list[str], webhook_2xx_count: int | None) -> dict[str, Any]:
	"""Tin bị mất = tin kẹt (New/Processing/Retry quá 10 phút) + Manual chưa xử lý, và chênh lệch với nhật ký webhook."""
	cutoff = now_datetime() - timedelta(minutes=10)
	f = {"creation": ["between", [start, end]], "channel": ["not in", channels_excl or [""]]}
	total = frappe.db.count("Omni Inbound Message", {**f, "status": ["!=", "Ignored"]})
	stuck = frappe.db.count(
		"Omni Inbound Message", {**f, "status": ["in", list(OPEN_STATES)], "creation": ["<", cutoff]}
	)
	manual = frappe.db.count("Omni Inbound Message", {**f, "status": "Manual"})
	out = {
		"inbound_total": total,
		"stuck_over_10min": stuck,
		"manual_unresolved": manual,
		"lost_estimate": stuck + manual,
	}
	if webhook_2xx_count is not None:
		all_rows = frappe.db.count("Omni Inbound Message", f)
		out["webhook_2xx_count"] = int(webhook_2xx_count)
		out["db_rows"] = all_rows
		out["missing_vs_access_log"] = max(0, int(webhook_2xx_count) - all_rows)
		out["note"] = (
			"Zalo gửi lại sự kiện trùng nên số request 2xx có thể lớn hơn số dòng DB; đối chiếu cần lọc trùng"
		)
	return out


def duplicate_leads(start, end) -> dict[str, Any]:
	"""Lead trùng = Lead bị gộp (omni_merged_into đặt) trong kỳ / Lead mới do Omni tạo trong kỳ."""
	created = frappe.db.count(
		"CRM Lead", {"creation": ["between", [start, end]], "omni_first_channel": ["is", "set"]}
	)
	merged = frappe.db.sql(
		"""select count(*) from `tabOmni Duplicate Candidate`
		where status='Merged' and modified between %s and %s""",
		(start, end),
	)[0][0]
	merged_leads = frappe.db.count(
		"CRM Lead", {"omni_merged_into": ["is", "set"], "modified": ["between", [start, end]]}
	)
	open_cand = frappe.db.count("Omni Duplicate Candidate", {"status": "Open"})
	return {
		"leads_created": created,
		"merged_candidates": merged,
		"leads_marked_merged": merged_leads,
		"open_candidates_not_yet_decided": open_cand,
		"ratio": round(merged_leads / created, 4) if created else None,
	}


def message_to_lead(start, end, channels_excl: list[str]) -> dict[str, Any]:
	"""Thời gian từ tin đầu tiên của Lead (received_at) đến lúc Lead được tạo (creation), cho Lead Omni tạo."""
	rows = frappe.db.sql(
		"""select l.name, l.creation, min(m.received_at) first_in
		from `tabCRM Lead` l join `tabOmni Inbound Message` m on m.lead = l.name
		where l.creation between %s and %s and l.omni_first_channel is not null
			and m.channel not in %s and m.event_type != 'oa_echo'
		group by l.name, l.creation""",
		(start, end, tuple(channels_excl) or ("",)),
	)
	secs = [max(0.0, (c - f).total_seconds()) for _n, c, f in rows if f]
	return {
		"leads": len(secs),
		"p50_s": _pct(secs, 0.5),
		"p95_s": _pct(secs, 0.95),
		"max_s": round(max(secs), 1) if secs else None,
		"share_under_60s": round(sum(1 for s in secs if s < 60) / len(secs), 4) if secs else None,
	}


def first_response(start, end, channels_excl: list[str], include_sandbox: bool) -> dict[str, Any]:
	"""Phản hồi lần đầu (V2-01, docs/13 mục 1.2): Lead tạo trong kỳ có tin đầu rơi vào giờ làm việc; đo từ tin đầu tới
	`CRM Lead.omni_first_response_at`, chỉ đếm phút trong giờ làm việc (utils/business_hours, có ngày lễ).

	Trường này không tính tin sandbox (định nghĩa docs/15 mục 7 bước 4), nên `include_sandbox` không còn tác dụng.
	"""
	leads = frappe.db.sql(
		"""select l.name, min(m.received_at) first_in, l.omni_first_response_at
		from `tabCRM Lead` l join `tabOmni Inbound Message` m on m.lead = l.name
		where l.creation between %s and %s and l.omni_first_channel is not null
			and m.channel not in %s and m.event_type not in ('oa_echo','follow','unfollow')
		group by l.name""",
		(start, end, tuple(channels_excl) or ("",)),
	)
	waits: list[float] = []
	unanswered = 0
	considered = 0
	for _lead, first_in, responded_at in leads:
		if not first_in or not business_hours.is_business_time(get_datetime(first_in)):
			continue
		considered += 1
		if not responded_at or get_datetime(responded_at) < get_datetime(first_in):
			unanswered += 1  # chưa phản hồi (hoặc phản hồi có trước tin đầu: không tính là trả lời tin đó)
			continue
		waits.append(
			business_hours.business_minutes_between(get_datetime(first_in), get_datetime(responded_at))
		)
	return {
		"leads_considered": considered,
		"answered": len(waits),
		"unanswered": unanswered,
		"median_min": _pct(waits, 0.5),
		"p90_min": _pct(waits, 0.9),
		"share_within_15min": round(sum(1 for w in waits if w <= 15) / considered, 4) if considered else None,
		"note": "Lead chưa có phản hồi tính là không đạt; tin Zalo sale trả lời ngoài hệ thống (oa_echo) cũng tính vì có Outbound Sale",
	}


def manual_handling(start, end, channels_excl: list[str]) -> dict[str, Any]:
	"""Tin phải xử lý tay = Manual hiện còn + tin đã thử lại >= 3 lần; chia cho tổng tin (trừ Ignored)."""
	f = {"creation": ["between", [start, end]], "channel": ["not in", channels_excl or [""]]}
	total = frappe.db.count("Omni Inbound Message", {**f, "status": ["!=", "Ignored"]})
	manual = frappe.db.count("Omni Inbound Message", {**f, "status": "Manual"})
	retried = frappe.db.count("Omni Inbound Message", {**f, "attempts": [">=", 1]})
	return {
		"inbound_total": total,
		"manual_now": manual,
		"ever_retried": retried,
		"ratio_manual": round(manual / total, 4) if total else None,
		"note": "'Xử lý lại' của Omni Admin đặt attempts=0 nên tin đã xử lý lại không còn thấy ở đây: chạy script mỗi ngày và cộng dồn Manual",
	}


def gateway_activity(start, end, channels_excl: list[str], uptime_pct: float | None) -> dict[str, Any]:
	"""Cổng hoạt động: DB chỉ cho biết khoảng lặng; độ sẵn sàng thật phải do bộ giám sát ngoài đo."""
	rows = frappe.db.sql(
		"""select channel, received_at from `tabOmni Inbound Message`
		where received_at between %s and %s and channel not in %s order by channel, received_at""",
		(start, end, tuple(channels_excl) or ("",)),
	)
	longest: dict[str, float] = {}
	prev: dict[str, datetime] = {}
	for ch, at in rows:
		if ch in prev and business_hours.is_business_time(at) and business_hours.is_business_time(prev[ch]):
			gap = (at - prev[ch]).total_seconds() / 3600
			longest[ch] = max(longest.get(ch, 0.0), gap)
		prev[ch] = at
	return {
		"uptime_pct_external": uptime_pct,
		"longest_gap_hours_in_business_time_by_channel": {k: round(v, 2) for k, v in longest.items()},
		"note": "Khoảng lặng dài chỉ là gợi ý. Mục tiêu >= 99,5% cần đo từ bộ giám sát ngoài (vd GET tới webhook phải trả 405)",
	}


def run(
	start: str,
	end: str | None = None,
	exclude_prefixes: str | list | None = None,
	include_sandbox: int | str = 0,
	webhook_2xx_count: int | str | None = None,
	uptime_pct: float | str | None = None,
) -> None:
	"""Tính mọi chỉ số Đợt 1 trong [start, end) (giờ theo múi giờ của site) và in JSON + bảng Markdown."""
	s = get_datetime(start)
	e = get_datetime(end) if end else now_datetime()
	prefixes = tuple(exclude_prefixes) if exclude_prefixes else DEFAULT_EXCLUDE
	if isinstance(exclude_prefixes, str):
		prefixes = tuple(x for x in exclude_prefixes.split(",") if x)
	excl = _excluded_channels(prefixes)
	res = {
		"period": [str(s), str(e)],
		"excluded_channels": excl,
		"lost": lost_messages(
			s, e, excl, int(webhook_2xx_count) if webhook_2xx_count not in (None, "") else None
		),
		"duplicate_leads": duplicate_leads(s, e),
		"message_to_lead": message_to_lead(s, e, excl),
		"first_response": first_response(s, e, excl, bool(int(include_sandbox))),
		"manual": manual_handling(s, e, excl),
		"gateway": gateway_activity(s, e, excl, float(uptime_pct) if uptime_pct not in (None, "") else None),
	}
	print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
	print(_table(res))


def _verdict(ok: bool | None) -> str:
	return "chưa đủ dữ liệu" if ok is None else ("ĐẠT" if ok else "KHÔNG ĐẠT")


def _table(r: dict[str, Any]) -> str:
	lost = r["lost"]["lost_estimate"]
	dup = r["duplicate_leads"]["ratio"]
	p95 = r["message_to_lead"]["p95_s"]
	fr = r["first_response"]["median_min"]
	man = r["manual"]["ratio_manual"]
	up = r["gateway"]["uptime_pct_external"]
	rows = [
		("Tin bị mất", "0", lost, _verdict(lost == 0)),
		("Lead trùng / Lead mới", "< 3%", dup, _verdict(None if dup is None else dup < 0.03)),
		("Tin -> Lead (p95)", "< 60 giây", p95, _verdict(None if p95 is None else p95 < 60)),
		(
			"Phản hồi lần đầu trong giờ HC (trung vị, phút)",
			"< 15 phút",
			fr,
			_verdict(None if fr is None else fr < 15),
		),
		("Tin xử lý tay", "< 1%", man, _verdict(None if man is None else man < 0.01)),
		("Cổng hoạt động", ">= 99,5%", up, _verdict(None if up is None else up >= 99.5)),
	]
	lines = ["| Chỉ số | Ngưỡng | Đo được | Kết luận |", "| --- | --- | --- | --- |"]
	lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
	return "\n".join(lines)
