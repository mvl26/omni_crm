"""Dữ liệu cho các trang Marketing trong CRM (V3-10, Prompt 3): danh sách chiến dịch, chi tiết, bài, lịch tháng."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import frappe
from frappe import _

from omni_crm.marketing import approval, roles, rules
from omni_crm.marketing import settings as mk_settings

ITEM = "Omni Content Item"
TARGET = "Omni Content Target"
CAMPAIGN = "Omni Campaign"
LOG = "Omni Publish Log"


def campaigns(status: str | None = None) -> list[dict[str, Any]]:
	"""Chiến dịch có bài đăng, mới nhất trước; mỗi dòng đếm bài theo trạng thái và điểm trung bình."""
	cond, params = "", {}
	if status:
		cond = "and c.status = %(status)s"
		params["status"] = status
	rows = frappe.db.sql(
		f"""select c.name, c.campaign_name, c.status, c.start_date, c.end_date, c.utm_campaign, c.platform,
			count(i.name) as posts, round(avg(nullif(i.quality_score, 0))) as avg_score,
			sum(i.status = %(draft)s) as draft, sum(i.status = %(review)s) as review,
			sum(i.status in %(scheduled)s) as scheduled, sum(i.status in %(done)s) as published,
			sum(i.status = %(failed)s) as failed, max(i.modified) as last_activity
		from `tab{CAMPAIGN}` c join `tab{ITEM}` i on i.campaign = c.name
		where 1=1 {cond}
		group by c.name
		order by coalesce(c.start_date, c.creation) desc""",
		{
			**params,
			"draft": rules.I_DRAFT,
			"review": rules.I_REVIEW,
			"scheduled": (rules.I_SCHEDULED, rules.I_PROCESSING),
			"done": (rules.I_PUBLISHED, rules.I_PARTIAL),
			"failed": rules.I_FAILED,
		},
		as_dict=True,
	)
	return rows


def _targets_of(items: list[str]) -> dict[str, list]:
	out: dict[str, list] = {}
	for t in frappe.get_all(
		TARGET,
		filters={"content_item": ["in", items or [""]]},
		fields=[
			"name",
			"content_item",
			"platform",
			"post_format",
			"channel_account",
			"scheduled_at",
			"status",
			"attempts",
			"tracking_url",
			"permalink",
			"published_at",
			"error_type",
			"last_error",
			"next_retry_at",
		],
		order_by="scheduled_at asc",
	):
		out.setdefault(t.content_item, []).append(t)
	return out


def campaign_detail(name: str) -> dict[str, Any]:
	doc = frappe.get_doc(CAMPAIGN, name)
	doc.check_permission("read")
	items = frappe.get_all(
		ITEM,
		filters={"campaign": name},
		fields=[
			"name",
			"title",
			"status",
			"pillar",
			"funnel_stage",
			"quality_score",
			"utm_content",
			"modified",
		],
		order_by="creation asc",
	)
	targets = _targets_of([i.name for i in items])
	for i in items:
		i["targets"] = targets.get(i.name, [])
	return {
		"campaign": {
			k: doc.get(k)
			for k in (
				"name",
				"campaign_name",
				"status",
				"platform",
				"start_date",
				"end_date",
				"utm_campaign",
				"objective",
				"audience",
				"key_message",
				"landing_page",
			)
		},
		"items": items,
		"threshold": mk_settings.quality_threshold(),
		"require_approval": mk_settings.require_approval(),
		"is_manager": roles.is_manager(),
	}


def item_detail(name: str) -> dict[str, Any]:
	doc = frappe.get_doc(ITEM, name)
	doc.check_permission("read")
	targets = _targets_of([name]).get(name, [])
	logs = frappe.get_all(
		LOG,
		filters={"target": ["in", [t.name for t in targets] or [""]]},
		fields=[
			"name",
			"target",
			"attempt_no",
			"outcome",
			"http_status",
			"started_at",
			"finished_at",
			"error_type",
			"message",
		],
		order_by="started_at desc",
		limit=100,
	)
	campaign = frappe.db.get_value(
		CAMPAIGN, doc.campaign, ["name", "campaign_name", "utm_campaign", "landing_page"], as_dict=True
	)
	return {
		"item": doc.as_dict(),
		"campaign": campaign,
		"targets": targets,
		"logs": logs,
		"threshold": mk_settings.quality_threshold(),
		"require_approval": mk_settings.require_approval(),
		"is_manager": roles.is_manager(),
		"can_edit": roles.can_edit() and doc.status in (rules.I_DRAFT, rules.I_REVIEW),
		"dry_run": mk_settings.dry_run(),
	}


def approve_campaign(name: str) -> list[dict[str, Any]]:
	"""Gửi duyệt / duyệt mọi bài Nháp, Chờ duyệt của chiến dịch; bài không đủ điều kiện giữ nguyên kèm lý do."""
	frappe.get_doc(CAMPAIGN, name).check_permission("read")
	results = []
	for item in frappe.get_all(
		ITEM,
		filters={"campaign": name, "status": ["in", [rules.I_DRAFT, rules.I_REVIEW]]},
		pluck="name",
		order_by="creation asc",
	):
		results.append(route(item))
	if any(r["status"] == rules.I_SCHEDULED for r in results):
		if frappe.db.get_value(CAMPAIGN, name, "status") == "Nháp":
			frappe.db.set_value(CAMPAIGN, name, "status", "Đang chạy")
	return results


def route(item: str) -> dict[str, Any]:
	"""Đưa một bài qua quy tắc duyệt (Prompt 1); lỗi một bài không làm hỏng cả lô, chỉ ghi lý do."""
	doc = frappe.get_doc(ITEM, item)
	out = {"item": item, "title": doc.title, "score": doc.quality_score, "status": doc.status, "message": ""}
	steps: list = []
	if mk_settings.require_approval():
		if doc.status == rules.I_DRAFT:
			steps.append(approval.submit)
		if roles.is_manager():
			steps.append(approval.approve)
	else:
		steps.append(approval.approve)
	for step in steps:
		frappe.db.savepoint("oc_route")
		try:
			step(item)
		except frappe.ValidationError as e:
			frappe.db.rollback(save_point="oc_route")
			frappe.clear_last_message()
			out["message"] = str(e)
			break
	out["status"] = frappe.db.get_value(ITEM, item, "status")
	return out


def calendar(month: int, year: int, campaign: str | None = None, platform: str | None = None) -> list:
	"""Mục lịch của tháng (giờ Việt Nam), một mục mỗi nền tảng đăng."""
	if not (1 <= month <= 12 and 2000 <= year <= 2100):
		frappe.throw(_("Tháng / năm không hợp lệ"))
	start = datetime(year, month, 1)
	end = datetime(year + (month == 12), month % 12 + 1, 1)
	cond = ["t.scheduled_at >= %(start)s", "t.scheduled_at < %(end)s"]
	params: dict[str, Any] = {"start": start, "end": end}
	if campaign:
		cond.append("i.campaign = %(campaign)s")
		params["campaign"] = campaign
	if platform:
		cond.append("t.platform = %(platform)s")
		params["platform"] = platform
	return frappe.db.sql(
		f"""select t.name as target, t.platform, t.post_format, t.status, t.scheduled_at, t.published_at,
			t.permalink, i.name as item, i.title, i.status as item_status, i.quality_score, i.campaign,
			c.campaign_name
		from `tab{TARGET}` t join `tab{ITEM}` i on i.name = t.content_item
			left join `tab{CAMPAIGN}` c on c.name = i.campaign
		where {" and ".join(cond)}
		order by t.scheduled_at asc""",
		params,
		as_dict=True,
	)
