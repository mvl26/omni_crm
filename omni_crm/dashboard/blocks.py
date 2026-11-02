"""Các khối của Tổng quan CSKH (docs/13 mục 1) và sổ đăng ký khối theo công tắc.

Mỗi khối là một hàm `(Query) -> dict` đăng ký bằng `@block(key, phase, enabled=...)`. `snapshot()` chỉ chạy và trả khối
nào đang bật: khối tắt KHÔNG có mặt trong kết quả (không hiện số 0 giả). Điểm nối cho tính năng mới: viết hàm khối
rồi đăng ký, giao diện tự hiện theo khóa.

Số liệu là truy vấn gộp có điều kiện quyền CRM Lead (xem `access.py`), không N+1: mỗi khối cố định số truy vấn,
chỉ phần "Cần xử lý ngay" lấy thêm tin cuối của tối đa 20 Lead bằng truy vấn gộp.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm.dashboard import access, priority
from omni_crm.prospect import fit as fit_mod
from omni_crm.utils import business_hours

LEAD = "CRM Lead"
INBOUND = "Omni Inbound Message"
CUSTOMER_EVENTS = ("message", "user_info")
PERIODS = {"today": 1, "1": 1, "7d": 7, "7": 7, "30d": 30, "30": 30}
PERIOD_LABELS = {1: "hôm qua", 7: "7 ngày trước", 30: "30 ngày trước"}
ATTENTION_LIMIT = 20
SNIPPET = 80
FIRST_STAGES = ("Tiềm năng", "New")  # Lead chưa liên hệ: giai đoạn đầu B2B hoặc "New" của CRM
FUNNEL_LEAD_STAGES = ("Tiềm năng", "Đã liên hệ", "Đã trao đổi nhu cầu")
FUNNEL_DEAL_STAGES = ("Đề xuất gói", "Đàm phán", "Ký hợp đồng")
NOT_FIT = "Không phù hợp"
TIERS = ("A", "B", "C")
REGIONS = ("Miền Bắc", "Miền Trung", "Miền Nam")


@dataclass
class Query:
	"""Ngữ cảnh một lần tính: người dùng, phạm vi, kỳ, điều kiện quyền và bộ nhớ đệm giữa các khối."""

	user: str
	scope: str
	days: int
	now: datetime
	start: datetime
	end: datetime
	prev_start: datetime
	where: str = ""
	params: dict = field(default_factory=dict)
	feats: dict[str, bool] = field(default_factory=dict)
	memo: dict[str, Any] = field(default_factory=dict)

	def lead_filter(self) -> str:
		"""SQL con chọn tên các CRM Lead được phép (dùng làm `lead in (...)` trên bảng khác)."""
		return f"select `tabCRM Lead`.`name` from `tabCRM Lead` where {self.where}"


@dataclass
class Block:
	key: str
	phase: int
	run: Callable[[Query], Any]
	enabled: Callable[[Query], bool]


REGISTRY: dict[str, Block] = {}


def block(key: str, phase: int = 1, enabled: Callable[[Query], bool] | None = None):
	"""Đăng ký một khối của Tổng quan; `enabled` (mặc định luôn bật) quyết định khối có mặt trong kết quả không."""

	def deco(fn: Callable[[Query], Any]) -> Callable[[Query], Any]:
		REGISTRY[key] = Block(key, phase, fn, enabled or (lambda q: True))
		return fn

	return deco


def period_range(period: str | None, now: datetime | None = None) -> tuple[int, datetime, datetime, datetime]:
	"""(số ngày, đầu kỳ, cuối kỳ, đầu kỳ trước) của kỳ "today" | "7d" | "30d" (mặc định hôm nay)."""
	now = now or now_datetime()
	days = PERIODS.get(str(period or "today"), 1)
	start = datetime.combine(now.date() - timedelta(days=days - 1), time.min)
	return days, start, now + timedelta(seconds=1), start - timedelta(days=days)


def make_query(period: str | None, scope: str | None, user: str | None = None) -> Query:
	"""Dựng ngữ cảnh tính cho một người dùng; `scope` không bao giờ mở rộng quyền xem."""
	user = user or frappe.session.user
	days, start, end, prev = period_range(period)
	scope = access.normalize_scope(scope, user)
	where, params = access.lead_where(scope, user)
	return Query(
		user=user,
		scope=scope,
		days=days,
		now=end - timedelta(seconds=1),
		start=start,
		end=end,
		prev_start=prev,
		where=where,
		params=params,
		feats=priority.features(),
	)


def _names(users: list[str | None]) -> dict[str, str]:
	users = [u for u in set(users) if u]
	if not users:
		return {}
	rows = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	return {r.name: r.full_name or r.name for r in rows}


def _sql(q: Query, sql: str, extra: dict | None = None, as_dict: bool = True) -> list:
	return frappe.db.sql(sql, {**q.params, **(extra or {})}, as_dict=as_dict)


# --- hội thoại chờ trả lời ----------------------------------------------------------------------------------


def waiting_leads(q: Query) -> list[dict]:
	"""Lead có tin khách (message/user_info) mới hơn mọi tin ra (Sent/Queued, oa_echo, thư gửi đi), đã qua quyền xem."""
	if "waiting" in q.memo:
		return q.memo["waiting"]
	rows = _sql(
		q,
		f"""
		select `tabCRM Lead`.`name` as lead, `tabCRM Lead`.`lead_name`, `tabCRM Lead`.`lead_owner`,
			`tabCRM Lead`.`organization`, `tabCRM Lead`.`omni_intent`, `tabCRM Lead`.`omni_score`, li.last_in
		from `tabCRM Lead`
		join (
			select `lead`, max(`received_at`) as last_in,
				max(case when `event_type` = 'form_lead' then `received_at` end) as last_form from `tab{INBOUND}`
			where `event_type` in ('message', 'user_info', 'form_lead') and `status` != 'Ignored' and ifnull(`lead`, '') != ''
			group by `lead`
		) li on li.`lead` = `tabCRM Lead`.`name`
		left join (
			select `lead`, max(ts) as last_out from (
				select `lead`, coalesce(`sent_at`, `creation`) as ts from `tabOmni Outbound Message`
					where `status` in ('Queued', 'Sent') and ifnull(`lead`, '') != ''
				union all
				select `lead`, `received_at` from `tab{INBOUND}`
					where `event_type` = 'oa_echo' and `status` != 'Ignored' and ifnull(`lead`, '') != ''
				union all
				select `reference_name`, `communication_date` from `tabCommunication`
					where `reference_doctype` = 'CRM Lead' and `sent_or_received` = 'Sent' and `communication_medium` = 'Email'
				union all
				select d.`lead`, c.`communication_date` from `tabCommunication` c
					join `tabCRM Deal` d on d.`name` = c.`reference_name`
					where c.`reference_doctype` = 'CRM Deal' and c.`sent_or_received` = 'Sent'
						and c.`communication_medium` = 'Email' and ifnull(d.`lead`, '') != ''
			) u group by `lead`
		) lo on lo.`lead` = `tabCRM Lead`.`name`
		where {q.where} and (lo.last_out is null or lo.last_out < li.last_in)
			and not (ifnull(li.last_form = li.last_in, 0) and (
				ifnull(`tabCRM Lead`.`omni_first_response_at` >= li.last_in, 0)
				or exists (select 1 from `tabOmni Care Log` cl where cl.`lead` = `tabCRM Lead`.`name`
					and ifnull(cl.`outcome_by`, '') != '' and cl.`outcome_at` >= li.last_in)
				or exists (select 1 from `tabOmni Call` oc where oc.`lead` = `tabCRM Lead`.`name` and oc.`direction` = 'Outbound'
					and oc.`status` = 'Answered' and coalesce(oc.`answered_at`, oc.`started_at`) >= li.last_in)
			))
		""",
	)
	q.memo["waiting"] = rows
	return rows


def _context(q: Query) -> priority.Context:
	if "ctx" not in q.memo:
		ctx = priority.build_context(now=q.now)
		ctx.feats = q.feats
		q.memo["ctx"] = ctx
	return q.memo["ctx"]


# --- phản hồi lần đầu / Lead mới ----------------------------------------------------------------------------


def _first_response_rows(q: Query, start: datetime, end: datetime) -> list[dict]:
	"""Lead vào qua kênh (có `omni_first_channel`), tạo trong [start, end), đã có `omni_first_response_at`."""
	key = f"fr:{start}:{end}"
	if key not in q.memo:
		q.memo[key] = _first_response_query(q, start, end)
	return q.memo[key]


def _first_response_query(q: Query, start: datetime, end: datetime) -> list[dict]:
	return _sql(
		q,
		f"""select `tabCRM Lead`.`name`, `tabCRM Lead`.`lead_owner`, `tabCRM Lead`.`creation`,
			`tabCRM Lead`.`omni_first_response_at` from `tabCRM Lead`
		where {q.where} and `tabCRM Lead`.`creation` >= %(s)s and `tabCRM Lead`.`creation` < %(e)s
			and ifnull(`tabCRM Lead`.`omni_first_channel`, '') != '' and `tabCRM Lead`.`omni_first_response_at` is not null""",
		{"s": start, "e": end},
	)


def _avg_minutes(q: Query, rows: list[dict]) -> float | None:
	rows = [r for r in rows if r.omni_first_response_at]
	if not rows:
		return None
	ctx = _context(q)
	clock = business_hours.BusinessClock(
		min(get_datetime(r.creation) for r in rows).date(),
		max(get_datetime(r.omni_first_response_at) for r in rows).date(),
		ctx.hours,
		ctx.holidays,
	)
	values = [
		clock.minutes_between(get_datetime(r.creation), get_datetime(r.omni_first_response_at)) for r in rows
	]
	return round(sum(values) / len(values), 1) if values else None


def _count_new(q: Query, start: datetime, end: datetime) -> int:
	row = _sql(
		q,
		f"""select count(*) as n from `tabCRM Lead` where {q.where} and `tabCRM Lead`.`creation` >= %(s)s
		and `tabCRM Lead`.`creation` < %(e)s and ifnull(`tabCRM Lead`.`omni_first_channel`, '') != ''""",
		{"s": start, "e": end},
	)
	return cint(row[0].n)


def _delta(current: float | None, previous: float | None, good_when_up: bool, q: Query) -> dict | None:
	"""Biến động so với kỳ trước; None nếu chưa có mốc so sánh có nghĩa (mốc trống hoặc bằng 0)."""
	if current is None or not previous:
		return None
	diff = round(current - previous, 1)
	return {
		"value": diff,
		"label": _("so với {0}").format(_(PERIOD_LABELS[q.days])),
		"good": (diff >= 0) == good_when_up,
	}


@block("tile_waiting", 1)
def tile_waiting(q: Query) -> dict:
	"""Hội thoại chờ trả lời (Đợt 1)."""
	return {"label": _("Hội thoại chờ trả lời"), "value": len(waiting_leads(q)), "unit": "", "delta": None}


@block("tile_first_response", 1)
def tile_first_response(q: Query) -> dict:
	"""Phản hồi lần đầu trung bình (phút làm việc) của Lead vào trong kỳ (Đợt 1)."""
	rows = _first_response_rows(q, q.start, q.end)
	avg = _avg_minutes(q, rows)
	prev = _avg_minutes(q, _first_response_rows(q, q.prev_start, q.start))
	return {
		"label": _("Phản hồi lần đầu (TB)"),
		"value": avg,
		"unit": _("phút làm việc"),
		"count": len(rows),
		"delta": _delta(avg, prev, False, q),
	}


@block("tile_new_leads", 1)
def tile_new_leads(q: Query) -> dict:
	"""Lead mới trong kỳ có kênh đầu vào (Đợt 1)."""
	value = _count_new(q, q.start, q.end)
	prev = _count_new(q, q.prev_start, q.start)
	return {"label": _("Lead mới"), "value": value, "unit": "", "delta": _delta(value, prev, True, q)}


@block("tile_missed_calls", 2, enabled=lambda q: q.feats["telephony"])
def tile_missed_calls(q: Query) -> dict:
	"""Gọi nhỡ chưa gọi lại: việc gọi lại còn mở; `overdue` = số quá hạn (Đợt 2, khi có kênh tổng đài)."""
	rows = _sql(
		q,
		f"""select count(*) as n, coalesce(sum(t.`due_date` < %(now)s), 0) as overdue from `tabCRM Task` t
		where t.`status` in ('Backlog', 'Todo', 'In Progress') and t.`name` in (
			select `callback_task` from `tabOmni Call` where ifnull(`callback_task`, '') != ''
			and `lead` in ({q.lead_filter()}))""",
		{"now": q.now},
	)
	return {
		"label": _("Gọi nhỡ chưa gọi lại"),
		"value": cint(rows[0].n),
		"overdue": cint(rows[0].overdue),
		"unit": "",
		"delta": None,
	}


@block("tile_hot_leads", 2, enabled=lambda q: q.feats["scoring"])
def tile_hot_leads(q: Query) -> dict:
	"""Lead nóng: Lead đang mở có `omni_score` >= ngưỡng (Đợt 2, khi bật chấm điểm)."""
	ctx = _context(q)
	blocked = (  # Lead của nhà thầu hạng phù hợp C không nóng (docs/14 mục 3.3); fit tắt thì giữ quy tắc cũ
		"""and not exists (select 1 from `tabCRM Organization` o
			where o.`name` = `tabCRM Lead`.`organization` and o.`omni_fit_tier` = %(c)s)"""
		if fit_mod.fit_enabled()
		else ""
	)
	row = _sql(
		q,
		f"""select count(*) as n from `tabCRM Lead` where {q.where} and `tabCRM Lead`.`converted` = 0
		and `tabCRM Lead`.`omni_score` >= %(t)s
		and `tabCRM Lead`.`status` not in (select `name` from `tabCRM Lead Status` where `type` = 'Lost')
		{blocked}""",
		{"t": ctx.threshold, "c": priority.scoring.FIT_BLOCKED_TIER},
	)
	return {"label": _("Lead nóng"), "value": cint(row[0].n), "unit": "", "delta": None}


@block("tile_drafts", 3, enabled=lambda q: q.feats["draft"])
def tile_drafts(q: Query) -> dict:
	"""Nháp bot chờ duyệt: Omni Bot Draft mode Draft, decision Pending (Đợt 3, khi `bot_reply_mode = Draft`)."""
	row = _sql(
		q,
		f"""select count(*) as n from `tabOmni Bot Draft` where `mode` = 'Draft' and `decision` = 'Pending'
		and `lead` in ({q.lead_filter()})""",
	)
	return {"label": _("Nháp bot chờ duyệt"), "value": cint(row[0].n), "unit": "", "delta": None}


# --- Cần xử lý ngay -------------------------------------------------------------------------------------------


@block("attention", 1)
def attention(q: Query) -> dict:
	"""Bảng "Cần xử lý ngay": tối đa 20 dòng theo thứ tự ưu tiên rồi thời gian chờ giảm dần (docs/13 mục 1.3)."""
	from omni_crm.api import inbox

	ctx = _context(q)
	waiting = {r.lead: r for r in waiting_leads(q)}
	starts = [get_datetime(r.last_in) for r in waiting.values() if r.get("last_in")]
	starts += list(ctx.callback_leads.values())
	if starts:  # tính phút làm việc O(1) cho từng Lead thay vì lặp qua từng ngày
		ctx.clock = business_hours.BusinessClock(min(starts).date(), q.now.date(), ctx.hours, ctx.holidays)
	extra = set(ctx.callback_leads) | ctx.draft_leads
	extra -= set(waiting)
	if extra:  # Lead có việc gọi lại/nháp mở nhưng không có tin chờ: vẫn phải qua quyền xem
		allowed = _sql(
			q,
			f"""select `tabCRM Lead`.`name` as lead, `tabCRM Lead`.`lead_name`, `tabCRM Lead`.`lead_owner`,
				`tabCRM Lead`.`organization`, `tabCRM Lead`.`omni_intent`, `tabCRM Lead`.`omni_score`
			from `tabCRM Lead` where {q.where} and `tabCRM Lead`.`name` in %(extra)s""",
			{"extra": tuple(extra)},
		)
		waiting.update({r.lead: r for r in allowed})
	ranked = []
	for lead, r in waiting.items():
		since = get_datetime(r.get("last_in")) if r.get("last_in") else None
		result = priority.classify(lead, ctx, since, r.omni_intent, r.omni_score, r.get("organization"))
		if result:
			ranked.append((priority.RANK[result[0]], -result[1], lead, result[0], result[1], r))
	ranked.sort(key=lambda x: (x[0], x[1], x[2]))
	top = ranked[:ATTENTION_LIMIT]
	last = inbox._last_events([x[2] for x in top])
	owners = _names([x[5].lead_owner for x in top])
	rows = []
	for _rank, _w, lead, code, waited, r in top:
		ev = last.get(lead) or {}
		rows.append(
			{
				"lead": lead,
				"customer": r.lead_name or lead,
				"organization": r.organization or "",
				"channel": ev.get("channel_type"),
				"last_text": " ".join((ev.get("text") or "").split())[:SNIPPET],
				"waited_minutes": round(waited, 1),
				"reason": code,
				"reason_label": priority.label(code),
				"owner": r.lead_owner,
				"owner_name": owners.get(r.lead_owner) or "",
			}
		)
	return {"rows": rows, "total": len(ranked), "limit": ATTENTION_LIMIT}


# --- Theo kênh ---------------------------------------------------------------------------------------------


@block("channels", 1)
def channels(q: Query) -> dict:
	"""Theo kênh: số khách liên hệ trong kỳ và Lead mới theo kênh đầu vào (Email, Zalo OA; Điện thoại khi có tổng đài).

	"Liên hệ" = số định danh (khách trên kênh đó) có `last_inbound_at` trong kỳ; kỳ kết thúc ở hiện tại nên đó chính là
	khách có liên hệ trong kỳ. Đọc bảng định danh (nhỏ) thay vì quét mọi tin vào.
	"""
	contacts = {
		r.channel_type: cint(r.n)
		for r in _sql(
			q,
			f"""select `channel_type`, count(*) as n from `tabOmni Identity`
			where `last_inbound_at` >= %(s)s and `last_inbound_at` < %(e)s and `lead` in ({q.lead_filter()})
			group by `channel_type`""",
			{"s": q.start, "e": q.end},
		)
	}
	new = {
		r.omni_first_channel: cint(r.n)
		for r in _sql(
			q,
			f"""select `tabCRM Lead`.`omni_first_channel`, count(*) as n from `tabCRM Lead` where {q.where}
			and `tabCRM Lead`.`creation` >= %(s)s and `tabCRM Lead`.`creation` < %(e)s
			and ifnull(`tabCRM Lead`.`omni_first_channel`, '') != '' group by `tabCRM Lead`.`omni_first_channel`""",
			{"s": q.start, "e": q.end},
		)
	}
	types = ["Email", "Zalo OA"]
	if q.feats["telephony"]:
		types.append("Telephony")
	biggest = max([contacts.get(t, 0) for t in types] + [1])
	return {
		"rows": [
			{
				"channel": t,
				"label": _("Điện thoại") if t == "Telephony" else t,
				"contacts": contacts.get(t, 0),
				"new_leads": new.get(t, 0),
				"ratio": round(contacts.get(t, 0) / biggest, 3),
			}
			for t in types
		]
	}


# --- Bot hôm nay ----------------------------------------------------------------------------------------------


@block("bot_today", 3, enabled=lambda q: q.feats["bot"])
def bot_today(q: Query) -> dict:
	"""Bot hôm nay: nháp đã soạn, gửi nguyên văn, sửa/bỏ, chuyển người và lý do (ẩn khi `bot_reply_mode = Off`)."""
	from omni_crm.bot import handoff

	today = datetime.combine(q.now.date(), time.min)
	rows = _sql(
		q,
		f"""select `mode`, `action`, `decision`, `guard_flags`, `question_group`, count(*) as n from `tabOmni Bot Draft`
		where `creation` >= %(s)s and `lead` in ({q.lead_filter()})
		group by `mode`, `action`, `decision`, `guard_flags`, `question_group`""",
		{"s": today},
	)
	out = {"drafted": 0, "sent_as_is": 0, "edited_or_discarded": 0, "handoff": 0, "reasons": []}
	reasons: dict[str, int] = defaultdict(int)
	for r in rows:
		n = cint(r.n)
		if r.action == "Handoff":
			out["handoff"] += n
			reasons[handoff.reason_label(r.guard_flags, r.question_group)] += n
			continue
		if r.mode == "Draft":
			out["drafted"] += n
		if r.decision == "Sent As Is":
			out["sent_as_is"] += n
		elif r.decision in ("Sent Edited", "Discarded"):
			out["edited_or_discarded"] += n
	out["reasons"] = [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])]
	return out


# --- Theo nhân viên ---------------------------------------------------------------------------------------------


@block("staff", 1)
def staff(q: Query) -> dict:
	"""Theo nhân viên: đang phụ trách, chờ trả lời, phản hồi lần đầu TB; gọi lại quá hạn/nháp đã duyệt khi bật."""
	table: dict[str | None, dict] = defaultdict(
		lambda: {
			"open": 0,
			"waiting": 0,
			"first_response": None,
			"overdue_callbacks": 0,
			"drafts_approved": 0,
		}
	)
	for r in _sql(
		q,
		f"""select `tabCRM Lead`.`lead_owner`, count(*) as n from `tabCRM Lead` where {q.where}
		and `tabCRM Lead`.`converted` = 0
		and `tabCRM Lead`.`status` not in (select `name` from `tabCRM Lead Status` where `type` = 'Lost')
		group by `tabCRM Lead`.`lead_owner`""",
	):
		table[r.lead_owner]["open"] = cint(r.n)
	for w in waiting_leads(q):
		table[w.lead_owner]["waiting"] += 1
	by_owner: dict[str | None, list] = defaultdict(list)
	for r in _first_response_rows(q, q.start, q.end):
		by_owner[r.lead_owner].append(r)
	for owner, rows in by_owner.items():
		table[owner]["first_response"] = _avg_minutes(q, rows)
	if q.feats["telephony"]:
		for r in _sql(
			q,
			f"""select t.`assigned_to`, count(*) as n from `tabCRM Task` t
			where t.`status` in ('Backlog', 'Todo', 'In Progress') and t.`due_date` < %(now)s and t.`name` in (
				select `callback_task` from `tabOmni Call` where ifnull(`callback_task`, '') != ''
				and `lead` in ({q.lead_filter()})) group by t.`assigned_to`""",
			{"now": q.now},
		):
			table[r.assigned_to]["overdue_callbacks"] = cint(r.n)
	if q.feats["draft"]:
		for r in _sql(
			q,
			f"""select `decided_by`, count(*) as n from `tabOmni Bot Draft`
			where `mode` = 'Draft' and `decision` in ('Sent As Is', 'Sent Edited') and `decided_at` >= %(s)s
			and `decided_at` < %(e)s and `lead` in ({q.lead_filter()}) group by `decided_by`""",
			{"s": q.start, "e": q.end},
		):
			table[r.decided_by]["drafts_approved"] = cint(r.n)
	names = _names(list(table))
	rows = [
		{"user": u, "name": names.get(u) or (_("Chưa giao") if not u else u), **v}
		for u, v in table.items()
		if v["open"]
		or v["waiting"]
		or v["first_response"] is not None
		or v["overdue_callbacks"]
		or v["drafts_approved"]
	]
	rows.sort(key=lambda r: (-r["waiting"], -r["open"], r["name"]))
	return {
		"rows": rows,
		"columns": {"callbacks": q.feats["telephony"], "drafts": q.feats["draft"]},
	}


# --- B2B (docs/14 mục 3.3, 4, 8) ------------------------------------------------------------------------------------


def _fit_on(q: Query) -> bool:
	return fit_mod.fit_enabled()


@block("funnel", 1, enabled=lambda q: bool(frappe.db.exists("CRM Lead Status", "Tiềm năng")))
def funnel(q: Query) -> dict:
	"""Phễu theo giai đoạn B2B: Lead theo trạng thái Lead, Deal theo trạng thái Deal, và "Không phù hợp"."""
	leads = {
		r.status: cint(r.n)
		for r in _sql(
			q,
			f"""select `tabCRM Lead`.`status`, count(*) as n from `tabCRM Lead` where {q.where}
			and `tabCRM Lead`.`converted` = 0 group by `tabCRM Lead`.`status`""",
		)
	}
	mine = " and `tabCRM Deal`.`deal_owner` = %(me)s" if q.scope == "mine" else ""
	deals = {
		r.status: cint(r.n)
		for r in _sql(
			q,
			f"""select `tabCRM Deal`.`status`, count(*) as n from `tabCRM Deal`
			where ({access.deal_condition(q.user)}){mine} group by `tabCRM Deal`.`status`""",
		)
	}
	stages = [
		{"stage": s, "kind": "Lead", "count": leads.get(s, 0)}
		for s in FUNNEL_LEAD_STAGES
		if frappe.db.exists("CRM Lead Status", s)
	]
	stages += [
		{"stage": s, "kind": "Deal", "count": deals.get(s, 0)}
		for s in FUNNEL_DEAL_STAGES
		if frappe.db.exists("CRM Deal Status", s)
	]
	stages.append({"stage": NOT_FIT, "kind": "Lost", "count": leads.get(NOT_FIT, 0) + deals.get(NOT_FIT, 0)})
	top = max([s["count"] for s in stages] + [1])
	for s in stages:
		s["ratio"] = round(s["count"] / top, 3)
	return {"stages": stages}


def _org_join(q: Query) -> str:
	"""Truy vấn con: mỗi nhà thầu có ít nhất một Lead xem được (chưa gộp) kèm cờ "đã liên hệ"."""
	first = ", ".join(f"'{s}'" for s in FIRST_STAGES)
	return f"""(select `tabCRM Lead`.`organization` as organization, count(*) as leads,
			sum(`tabCRM Lead`.`omni_first_response_at` is not null or `tabCRM Lead`.`status` not in ({first})) as contacted,
			min(`tabCRM Lead`.`name`) as lead, min(`tabCRM Lead`.`lead_owner`) as lead_owner
		from `tabCRM Lead` where {q.where} and `tabCRM Lead`.`converted` = 0 and ifnull(`tabCRM Lead`.`organization`, '') != ''
		group by `tabCRM Lead`.`organization`) x"""


@block("prospects_ab", 1, enabled=_fit_on)
def prospects_ab(q: Query) -> dict:
	"""Nhà thầu hạng A/B chưa liên hệ, sắp theo điểm phù hợp; chỉ nhà thầu có Lead mà người xem được xem."""
	rows = _sql(
		q,
		f"""select o.`name` as organization, o.`omni_tax_id`, o.`omni_fit_tier`, o.`omni_fit_score`, o.`omni_region`,
			o.`omni_province`, x.lead, x.lead_owner
		from `tabCRM Organization` o join {_org_join(q)} on x.organization = o.`name`
		where o.`omni_fit_tier` in ('A', 'B') and x.contacted = 0
		order by o.`omni_fit_score` desc, o.`name` limit 20""",
	)
	owners = _names([r.lead_owner for r in rows])
	for r in rows:
		r["owner_name"] = owners.get(r.lead_owner) or ""
	return {"rows": rows}


@block("tier_region", 1, enabled=_fit_on)
def tier_region(q: Query) -> dict:
	"""Số nhà thầu (có Lead xem được) theo hạng phù hợp x khu vực."""
	data = _sql(
		q,
		f"""select ifnull(o.`omni_fit_tier`, '') as tier, ifnull(o.`omni_region`, '') as region, count(*) as n
		from `tabCRM Organization` o join {_org_join(q)} on x.organization = o.`name`
		group by ifnull(o.`omni_fit_tier`, ''), ifnull(o.`omni_region`, '')""",
	)
	cell = {(r.tier, r.region): cint(r.n) for r in data}
	regions = [*REGIONS, ""]
	rows = []
	for tier in (*TIERS, ""):
		counts = {region or "unknown": cell.get((tier, region), 0) for region in regions}
		total = sum(counts.values())
		if tier == "" and not total:
			continue
		rows.append({"tier": tier or "-", "regions": counts, "total": total})
	return {"rows": rows, "regions": [*REGIONS, "Chưa rõ"]}


# --- Theo chiến dịch (docs/15 mục 9) ----------------------------------------------------------------------------

CAMPAIGN_ROW_LIMIT = 50
PROGRESSED_STATUSES = ("Đã trao đổi nhu cầu", "Qualified", "Converted")


def _campaigns_on(q: Query) -> bool:
	return bool(frappe.db.exists("Omni Campaign", {})) or bool(
		frappe.db.exists(LEAD, {"omni_ad_platform": ["in", ["Meta", "Google", "Website"]]})
	)


def _median(values: list[float]) -> float | None:
	if not values:
		return None
	v = sorted(values)
	mid = len(v) // 2
	return round(v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2, 1)


@block("campaigns", 1, enabled=_campaigns_on)
def campaigns(q: Query) -> dict:
	"""Theo chiến dịch (kỳ đang chọn): Lead, Lead hợp lệ, % phản hồi trong hạn, trung vị phản hồi lần đầu, hạng A/B, đã trao đổi nhu cầu, Deal.

	Lead thử nghiệm đã bị loại ở điều kiện quyền (`access.lead_where`). "Hợp lệ" = có SĐT hoặc email hợp lệ. Thời điểm vào là
	`received_at` của tin form đầu tiên (không phải lúc worker xong). Chi phí mỗi Lead hợp lệ = chi phí nhập tay / số Lead hợp lệ
	CỦA CẢ CHIẾN DỊCH (không theo kỳ, vì chi phí không chia theo kỳ).
	"""
	ctx = _context(q)
	# V2-08: KHÔNG dùng JOIN: điều kiện quyền của CRM cho Sales User viết `name` / `lead_owner` không qualifier nên JOIN gây lỗi
	# "Column 'name' is ambiguous" (500) với mọi Sales User khi đã có Lead quảng cáo. Dùng truy vấn con tương quan.
	entered = (
		f"coalesce((select min(f.`received_at`) from `tab{INBOUND}` f where f.`lead` = `tabCRM Lead`.`name` "
		"and f.`event_type` = 'form_lead'), `tabCRM Lead`.`creation`)"
	)
	rows = _sql(
		q,
		f"""select `tabCRM Lead`.`name` as lead, `tabCRM Lead`.`omni_campaign` as campaign, `tabCRM Lead`.`omni_ad_platform` as platform,
			`tabCRM Lead`.`status`, `tabCRM Lead`.`converted`, `tabCRM Lead`.`omni_first_response_at` as responded,
			(ifnull(`tabCRM Lead`.`omni_phone_e164`, '') != '' or ifnull(`tabCRM Lead`.`omni_email_norm`, '') != '') as valid,
			{entered} as entered,
			(select o.`omni_fit_tier` from `tabCRM Organization` o where o.`name` = `tabCRM Lead`.`organization`) as tier
		from `tabCRM Lead`
		where {q.where} and ifnull(`tabCRM Lead`.`omni_ad_platform`, '') in ('Meta', 'Google', 'Website', 'Danh sách mua sắm công')
			and {entered} >= %(s)s and {entered} < %(e)s""",
		{"s": q.start, "e": q.end},
	)
	deals: dict[str, int] = defaultdict(int)
	if rows:
		for r in frappe.db.sql(
			"select lead, count(*) from `tabCRM Deal` where lead in %s group by lead",
			(tuple(r.lead for r in rows),),
		):
			deals[r[0]] = cint(r[1])
	groups: dict[str, dict] = {}
	for r in rows:
		key = r.campaign or ""
		g = groups.setdefault(
			key,
			{
				"campaign": r.campaign,
				"platform": r.platform,
				"leads": 0,
				"valid": 0,
				"on_time": 0,
				"times": [],
				"ab": 0,
				"progressed": 0,
				"deals": 0,
			},
		)
		g["leads"] += 1
		if not cint(r.valid):
			continue
		g["valid"] += 1
		if r.responded:
			minutes = business_hours.business_minutes_between(
				get_datetime(r.entered), get_datetime(r.responded), ctx.hours, ctx.holidays
			)
			g["times"].append(minutes)
			g["on_time"] += int(minutes <= ctx.sla)
		g["ab"] += int(r.tier in ("A", "B"))
		g["progressed"] += int(bool(cint(r.converted)) or r.status in PROGRESSED_STATUSES)
		g["deals"] += deals.get(r.lead, 0)
	out = []
	for key, g in sorted(groups.items(), key=lambda kv: -kv[1]["leads"])[:CAMPAIGN_ROW_LIMIT]:
		spend = cost = None
		if key:
			spend = frappe.db.get_value("Omni Campaign", key, "spend")
			total_valid = cint(
				frappe.db.sql(
					"""select count(*) from `tabCRM Lead` where omni_campaign = %s and ifnull(omni_is_test, 0) = 0
					and omni_merged_into is null and (ifnull(omni_phone_e164, '') != '' or ifnull(omni_email_norm, '') != '')""",
					key,
				)[0][0]
			)
			cost = round(spend / total_valid) if spend and total_valid else None
		out.append(
			{
				"campaign": key or None,
				"label": key or _("(Chưa rõ chiến dịch)"),
				"platform": g["platform"],
				"leads": g["leads"],
				"valid": g["valid"],
				"on_time_pct": round(100 * g["on_time"] / g["valid"]) if g["valid"] else None,
				"median_first_response": _median(g["times"]),
				"tier_ab": g["ab"],
				"progressed": g["progressed"],
				"deals": g["deals"],
				"spend": spend or None,
				"cost_per_valid_lead": cost,
			}
		)
	return {"rows": out, "sla_minutes": ctx.sla, "total_campaigns": len(groups)}


# --- Điểm vào ---------------------------------------------------------------------------------------------------


def run_blocks(q: Query) -> dict[str, Any]:
	"""Chạy các khối đang bật (khối tắt không có mặt trong kết quả)."""
	return {key: b.run(q) for key, b in REGISTRY.items() if b.enabled(q)}


def enabled_keys(q: Query) -> list[str]:
	"""Khóa các khối đang bật."""
	return [key for key, b in REGISTRY.items() if b.enabled(q)]
