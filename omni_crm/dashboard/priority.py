"""Nhãn ưu tiên của hội thoại/khách (docs/13 mục 1.3), dùng chung cho Tổng quan và Hộp thư.

Thứ tự (số nhỏ = ưu tiên cao): 1 Khiếu nại, 2 Gọi nhỡ chưa gọi lại, 3 Quá hạn phản hồi, 4 Lead nóng có tin chờ,
5 Có nháp bot chờ duyệt, 6 Đang chờ. Mức 1, 2, 4, 5 thuộc Đợt 2-3 nên chỉ áp dụng khi tính năng đang bật.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.bot import scoring
from omni_crm.utils import business_hours

COMPLAINT = "complaint"
CALLBACK = "callback"
OVERDUE = "overdue"
HOT = "hot"
DRAFT = "draft"
WAITING = "waiting"
RANK = {COMPLAINT: 1, CALLBACK: 2, OVERDUE: 3, HOT: 4, DRAFT: 5, WAITING: 6}
LABELS = {
	COMPLAINT: "Khiếu nại",
	CALLBACK: "Gọi nhỡ chưa gọi lại",
	OVERDUE: "Quá hạn phản hồi",
	HOT: "Lead nóng có tin chờ",
	DRAFT: "Có nháp bot chờ duyệt",
	WAITING: "Đang chờ",
}


def label(code: str) -> str:
	"""Nhãn tiếng Việt (dịch được) của một mã ưu tiên."""
	return _(LABELS[code])


def telephony_enabled() -> bool:
	"""Có kênh tổng đài đang bật không (khối/ô Gọi nhỡ chỉ hiện khi có)."""
	return bool(frappe.db.exists("Omni Channel", {"channel_type": "Telephony", "enabled": 1}))


def features() -> dict[str, bool]:
	"""Trạng thái các tính năng Đợt 2-3 lấy từ công tắc thật."""
	mode = flags.bot_reply_mode()
	return {
		"telephony": telephony_enabled(),
		"analyze": flags.bot_analyze_enabled(),
		"scoring": scoring.scoring_enabled() and bool(scoring.load_rules()),
		"bot": mode != "Off",
		"draft": mode == "Draft",
	}


@dataclass
class Context:
	"""Dữ liệu dùng chung để phân loại nhiều Lead mà không truy vấn lại cho từng Lead."""

	now: datetime = field(default_factory=now_datetime)
	sla: int = 15
	threshold: int = 60
	hours: dict = field(default_factory=dict)
	holidays: frozenset = frozenset()
	feats: dict[str, bool] = field(default_factory=dict)
	callback_leads: dict[str, datetime] = field(
		default_factory=dict
	)  # Lead -> thời điểm việc gọi lại mở sớm nhất
	draft_leads: set[str] = field(default_factory=set)

	def business_minutes(self, since: datetime) -> float:
		"""Phút trong giờ làm việc từ `since` tới bây giờ (đi tắt khi chưa đủ SLA theo đồng hồ treo tường)."""
		wall = (self.now - since).total_seconds() / 60.0
		if wall <= 0:
			return 0.0
		if wall <= self.sla:
			return wall  # phút làm việc không thể vượt phút treo tường; đủ để biết chưa quá hạn
		return business_hours.business_minutes_between(since, self.now, self.hours, self.holidays)


def build_context(leads: list[str] | None = None, now: datetime | None = None) -> Context:
	"""Nạp công tắc, ngưỡng, giờ làm việc và các tập Lead có việc gọi lại/nháp mở (theo `leads` nếu có)."""
	ctx = Context(now=now or now_datetime())
	ctx.sla = cint(flags.care_setting("response_sla_minutes"))
	ctx.threshold = scoring.hot_threshold()
	ctx.hours = business_hours._hours()
	ctx.holidays = business_hours._holidays()
	ctx.feats = features()
	if ctx.feats["telephony"]:
		ctx.callback_leads = open_callbacks(leads)
	if ctx.feats["draft"]:
		ctx.draft_leads = pending_draft_leads(leads)
	return ctx


def open_callbacks(leads: list[str] | None = None) -> dict[str, datetime]:
	"""{Lead: thời điểm sớm nhất của việc gọi lại còn mở} (CRM Task do `create_callback_task` tạo, nối qua Omni Call)."""
	extra, params = "", [] if not leads else [tuple(leads)]
	if leads:
		extra = " and c.lead in %s"
	rows = frappe.db.sql(
		f"""select c.lead, min(t.creation) from `tabOmni Call` c
		join `tabCRM Task` t on t.name = c.callback_task
		where t.status in ('Backlog', 'Todo', 'In Progress') and ifnull(c.lead, '') != ''{extra}
		group by c.lead""",
		params,
	)
	return {lead: get_datetime(at) for lead, at in rows}


def pending_draft_leads(leads: list[str] | None = None) -> set[str]:
	"""Lead có nháp bot chờ duyệt (mode Draft, decision Pending)."""
	extra, params = "", [] if not leads else [tuple(leads)]
	if leads:
		extra = " and lead in %s"
	rows = frappe.db.sql(
		f"select distinct lead from `tabOmni Bot Draft` where mode = 'Draft' and decision = 'Pending'{extra}",
		params,
	)
	return {r[0] for r in rows if r[0]}


def classify(
	lead: str,
	ctx: Context,
	waiting_since: datetime | None,
	intent: str | None = None,
	score: int | None = None,
) -> tuple[str, float] | None:
	"""(mã ưu tiên, phút làm việc đã chờ) của một Lead; None nếu không có gì cần xử lý.

	`waiting_since` là lúc tin khách chưa được trả lời bắt đầu chờ (None nếu không có tin chờ).
	"""
	waited = ctx.business_minutes(waiting_since) if waiting_since else 0.0
	if waiting_since and ctx.feats.get("analyze") and intent == "Khiếu nại":
		return COMPLAINT, waited
	if lead in ctx.callback_leads:
		return CALLBACK, ctx.business_minutes(ctx.callback_leads[lead])
	if not waiting_since:
		return (DRAFT, 0.0) if lead in ctx.draft_leads else None
	if waited > ctx.sla:
		return OVERDUE, waited
	if ctx.feats.get("scoring") and cint(score) >= ctx.threshold:
		return HOT, waited
	if lead in ctx.draft_leads:
		return DRAFT, waited
	return WAITING, waited
