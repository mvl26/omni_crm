"""Báo cáo "Hiệu quả bài đăng" (ADR 0029): mỗi bài một dòng, số Lead (không tính Lead thử) và số Deal tính tại lúc xem.

Có chọn chiến dịch thì thêm dòng "Không rõ bài": Lead có chiến dịch đầu tiên là chiến dịch đó nhưng không gắn bài nào
(vào trang không qua link có mã bài, hoặc nhắn tin thẳng).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import get_datetime

from omni_crm.marketing.attribution import LEAD_FIELD, lead_field_ready
from omni_crm.marketing.constants import AUTO, POST

UNKNOWN = "Không rõ bài"


def _columns() -> list[dict]:
	return [
		{"fieldname": "post", "label": _("Mã bài"), "fieldtype": "Link", "options": POST, "width": 120},
		{"fieldname": "title", "label": _("Tiêu đề"), "fieldtype": "Data", "width": 240},
		{"fieldname": "campaign", "label": _("Chiến dịch"), "fieldtype": "Link", "options": "Omni Campaign", "width": 180},
		{"fieldname": "platform", "label": _("Kênh"), "fieldtype": "Data", "width": 90},
		{"fieldname": "publish_mode", "label": _("Cách đăng"), "fieldtype": "Data", "width": 90},
		{"fieldname": "status", "label": _("Trạng thái"), "fieldtype": "Data", "width": 100},
		{"fieldname": "publish_at", "label": _("Giờ đăng"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "late_minutes", "label": _("Trễ (phút, bài tự động)"), "fieldtype": "Int", "width": 110},
		{"fieldname": "reach", "label": _("Tiếp cận"), "fieldtype": "Int", "width": 90},
		{"fieldname": "engagement", "label": _("Tương tác"), "fieldtype": "Int", "width": 90},
		{"fieldname": "link_clicks", "label": _("Bấm link"), "fieldtype": "Int", "width": 90},
		{"fieldname": "leads", "label": _("Lead"), "fieldtype": "Int", "width": 70},
		{"fieldname": "deals", "label": _("Deal"), "fieldtype": "Int", "width": 70},
	]


def _post_filters(filters: dict) -> dict[str, Any]:
	cond: dict[str, Any] = {}
	if filters.get("campaign"):
		cond["campaign"] = filters["campaign"]
	if filters.get("status"):
		cond["status"] = filters["status"]
	lo, hi = filters.get("from_date"), filters.get("to_date")
	if lo and hi:
		cond["publish_at"] = ["between", [lo, f"{hi} 23:59:59"]]
	elif lo:
		cond["publish_at"] = [">=", lo]
	elif hi:
		cond["publish_at"] = ["<=", f"{hi} 23:59:59"]
	return cond


def _lead_deal_counts(posts: list[str]) -> dict[str, tuple[int, int]]:
	"""{bài: (số Lead không phải Lead thử, số Deal từ các Lead đó)}."""
	if not posts or not lead_field_ready():
		return {}
	rows = frappe.db.sql(
		f"""select l.`{LEAD_FIELD}` as post, count(distinct l.name) as leads, count(distinct d.name) as deals
		from `tabCRM Lead` l left join `tabCRM Deal` d on d.lead = l.name
		where l.`{LEAD_FIELD}` in %(posts)s and ifnull(l.omni_is_test, 0) = 0
		group by l.`{LEAD_FIELD}`""",
		{"posts": tuple(posts)},
		as_dict=True,
	)
	return {r.post: (r.leads, r.deals) for r in rows}


def _unknown_row(campaign: str) -> dict | None:
	if not lead_field_ready():
		return None
	row = frappe.db.sql(
		f"""select count(distinct l.name) as leads, count(distinct d.name) as deals
		from `tabCRM Lead` l left join `tabCRM Deal` d on d.lead = l.name
		where l.omni_campaign = %s and ifnull(l.`{LEAD_FIELD}`, '') = '' and ifnull(l.omni_is_test, 0) = 0""",
		(campaign,),
		as_dict=True,
	)[0]
	return {"post": None, "title": _(UNKNOWN), "campaign": campaign, "leads": row.leads, "deals": row.deals}


def execute(filters: dict | None = None):
	"""Cột và dòng của báo cáo; chỉ bài người xem được đọc."""
	filters = frappe._dict(filters or {})
	posts = frappe.get_list(
		POST,
		filters=_post_filters(filters),
		fields=[
			"name",
			"title",
			"campaign",
			"platform",
			"publish_mode",
			"status",
			"publish_at",
			"published_at",
			"reach",
			"reactions",
			"comments",
			"shares",
			"link_clicks",
		],
		order_by="publish_at asc",
		limit_page_length=0,
	)
	counts = _lead_deal_counts([p.name for p in posts])
	data = []
	for p in posts:
		late = None
		if p.publish_mode == AUTO and p.published_at and p.publish_at:
			late = max(0, int((get_datetime(p.published_at) - get_datetime(p.publish_at)).total_seconds() // 60))
		leads, deals = counts.get(p.name, (0, 0))
		data.append(
			{
				"post": p.name,
				"title": p.title,
				"campaign": p.campaign,
				"platform": _(p.platform or ""),
				"publish_mode": _(p.publish_mode or ""),
				"status": _(p.status or ""),
				"publish_at": p.publish_at,
				"late_minutes": late,
				"reach": p.reach,
				"engagement": (p.reactions or 0) + (p.comments or 0) + (p.shares or 0),
				"link_clicks": p.link_clicks,
				"leads": leads,
				"deals": deals,
			}
		)
	if filters.get("campaign") and frappe.has_permission("CRM Lead", "report"):
		unknown = _unknown_row(filters.campaign)
		if unknown:
			data.append(unknown)
	return _columns(), data
