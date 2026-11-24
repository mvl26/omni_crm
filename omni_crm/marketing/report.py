"""Báo cáo marketing (V3-10, Prompt 6 mục 2-3). Truy vấn tổng hợp bằng SQL; chỉ số chưa có trả None (giao diện hiện
"—", không hiện 0 gây hiểu nhầm). Cột chỉ số của Omni Content Target là Int (mặc định 0) nên chỉ cộng khi tên chỉ số
có trong `insights_fields`.

Bộ lọc: chiến dịch, nền tảng, từ ngày, đến ngày (theo giờ đăng dự kiến của nền tảng; Lead theo ngày tạo Lead).
"""

from __future__ import annotations

import io
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from omni_crm.marketing import rules
from omni_crm.marketing.publisher.errors import TERMINAL

ITEM = "Omni Content Item"
TARGET = "Omni Content Target"
DASH = "—"
ENGAGEMENT = ("likes", "comments", "shares")


def _metric(key: str) -> str:
	return f"sum(case when find_in_set('{key}', t.insights_fields) then t.{key} end)"


def _engagement() -> str:
	parts = " + ".join(
		f"case when find_in_set('{k}', t.insights_fields) then t.{k} else 0 end" for k in ENGAGEMENT
	)
	any_known = " or ".join(f"find_in_set('{k}', t.insights_fields)" for k in ENGAGEMENT)
	return f"sum(case when {any_known} then {parts} end)"


def parse_filters(filters: dict | str | None) -> dict[str, Any]:
	"""Chuẩn hóa bộ lọc: campaign, platform, from_date, to_date (ngày)."""
	f = frappe.parse_json(filters) if isinstance(filters, str) else dict(filters or {})
	out: dict[str, Any] = {}
	if f.get("campaign"):
		out["campaign"] = str(f["campaign"])
	if f.get("platform"):
		if f["platform"] not in rules.PLATFORMS:
			frappe.throw(_("Nền tảng không hợp lệ"))
		out["platform"] = f["platform"]
	for key in ("from_date", "to_date"):
		if f.get(key):
			out[key] = getdate(f[key])
	if out.get("from_date") and out.get("to_date") and out["to_date"] < out["from_date"]:
		frappe.throw(_("Đến ngày phải sau Từ ngày"))
	return out


def _target_where(f: dict) -> tuple[str, dict]:
	cond = ["1=1"]
	params: dict[str, Any] = {}
	if f.get("campaign"):
		cond.append("i.campaign = %(campaign)s")
		params["campaign"] = f["campaign"]
	if f.get("platform"):
		cond.append("t.platform = %(platform)s")
		params["platform"] = f["platform"]
	if f.get("from_date"):
		cond.append("t.scheduled_at >= %(from_dt)s")
		params["from_dt"] = f["from_date"]
	if f.get("to_date"):
		cond.append("t.scheduled_at < %(to_dt)s")
		params["to_dt"] = add_days(f["to_date"], 1)
	return " and ".join(cond), params


def post_rows(f: dict) -> list[dict]:
	"""Một dòng mỗi bài: nền tảng, trụ cột, giai đoạn, tiếp cận, tương tác, lượt bấm, số nền tảng thành công/lỗi."""
	where, params = _target_where(f)
	return frappe.db.sql(
		f"""select i.name, i.title, i.status, i.pillar, i.funnel_stage, i.campaign,
			group_concat(distinct t.platform order by t.platform separator ', ') as platforms,
			min(t.scheduled_at) as scheduled_at,
			{_metric("reach")} as reach, {_metric("impressions")} as impressions,
			{_metric("clicks")} as clicks, {_engagement()} as engagement, {_metric("video_views")} as video_views,
			sum(t.status = %(ok)s) as ok_targets, sum(t.status = %(failed)s) as failed_targets
		from `tab{ITEM}` i join `tab{TARGET}` t on t.content_item = i.name
		where {where}
		group by i.name
		order by min(t.scheduled_at) desc""",
		{**params, "ok": rules.T_SUCCESS, "failed": rules.T_FAILED},
		as_dict=True,
	)


def _lead_where(f: dict, campaigns: list[str]) -> tuple[str, dict]:
	cond = ["omni_campaign in %(campaigns)s"]
	params: dict[str, Any] = {"campaigns": tuple(campaigns) or ("",)}
	if f.get("from_date"):
		cond.append("creation >= %(from_dt)s")
		params["from_dt"] = f["from_date"]
	if f.get("to_date"):
		cond.append("creation < %(to_dt)s")
		params["to_dt"] = add_days(f["to_date"], 1)
	return " and ".join(cond), params


def _campaigns(f: dict, rows: list[dict]) -> list[str]:
	if f.get("campaign"):
		return [f["campaign"]]
	return sorted({r.campaign for r in rows})


def lead_counts(f: dict, campaigns: list[str]) -> dict[str, Any]:
	"""Số Lead theo chiến dịch nguồn (và đã chuyển Deal), theo bài đăng nguồn."""
	where, params = _lead_where(f, campaigns)
	total, deals = frappe.db.sql(
		f"select count(*), coalesce(sum(converted), 0) from `tabCRM Lead` where {where}", params
	)[0]
	by_item = dict(
		frappe.db.sql(
			f"""select omni_content_item, count(*) from `tabCRM Lead`
			where {where} and coalesce(omni_content_item, '') != '' group by omni_content_item""",
			params,
		)
	)
	return {"leads": int(total), "deals": int(deals), "by_item": by_item}


def _sum(values: list) -> int | None:
	known = [v for v in values if v is not None]
	return int(sum(known)) if known else None


def errors(f: dict) -> list[dict]:
	"""Lỗi đăng tải gom theo loại lỗi và câu lỗi; lỗi quyền/token (terminal) kèm cờ mở Thiết lập > Mạng xã hội."""
	where, params = _target_where(f)
	rows = frappe.db.sql(
		f"""select t.error_type, t.last_error, count(*) as count, max(t.modified) as last_at,
			group_concat(distinct t.platform separator ', ') as platforms
		from `tab{TARGET}` t join `tab{ITEM}` i on i.name = t.content_item
		where {where} and t.status = %(failed)s
		group by t.error_type, t.last_error
		order by count(*) desc""",
		{**params, "failed": rules.T_FAILED},
		as_dict=True,
	)
	for r in rows:
		r["permission"] = r.error_type == TERMINAL
	return rows


def build(filters: dict | str | None) -> dict[str, Any]:
	"""Toàn bộ số liệu trang Báo cáo marketing."""
	f = parse_filters(filters)
	rows = post_rows(f)
	campaigns = _campaigns(f, rows)
	leads = lead_counts(f, campaigns)
	ok = sum(int(r.ok_targets or 0) for r in rows)
	failed = sum(int(r.failed_targets or 0) for r in rows)
	posts = []
	for r in rows:
		posts.append(
			{
				"name": r.name,
				"title": r.title,
				"campaign": r.campaign,
				"platforms": r.platforms,
				"pillar": r.pillar or "",
				"funnel_stage": r.funnel_stage or "",
				"scheduled_at": r.scheduled_at,
				"reach": None if r.reach is None else int(r.reach),
				"engagement": None if r.engagement is None else int(r.engagement),
				"clicks": None if r.clicks is None else int(r.clicks),
				"video_views": None if r.video_views is None else int(r.video_views),
				"leads": int(leads["by_item"].get(r.name, 0)),
				"status": r.status,
			}
		)
	pillars: dict[str, dict] = {}
	for p in posts:
		key = p["pillar"] or _("(chưa gán)")
		row = pillars.setdefault(key, {"pillar": key, "posts": 0, "engagement": [], "leads": 0})
		row["posts"] += 1
		row["engagement"].append(p["engagement"])
		row["leads"] += p["leads"]
	funnel_total = len(posts)
	funnel = []
	for stage in ("Nhận biết", "Cân nhắc", "Chuyển đổi"):
		n = sum(1 for p in posts if p["funnel_stage"] == stage)
		funnel.append(
			{
				"stage": stage,
				"posts": n,
				"percent": round(100 * n / funnel_total, 1) if funnel_total else None,
			}
		)
	statuses = [p["status"] for p in posts]
	return {
		"filters": {k: str(v) for k, v in f.items()},
		"campaigns": campaigns,
		"cards": {
			"total_posts": len(posts),
			"published_posts": sum(1 for s in statuses if s in (rules.I_PUBLISHED, rules.I_PARTIAL)),
			"waiting_review": sum(1 for s in statuses if s == rules.I_REVIEW),
			"processing": sum(1 for s in statuses if s in (rules.I_SCHEDULED, rules.I_PROCESSING)),
			"success_rate": round(100 * ok / (ok + failed), 1) if (ok + failed) else None,
			"reach": _sum([p["reach"] for p in posts]),
			"engagement": _sum([p["engagement"] for p in posts]),
			"video_views": _sum([p["video_views"] for p in posts]),
			"leads": leads["leads"],
			"deals": leads["deals"],
		},
		"pillars": [
			{**v, "engagement": _sum(v["engagement"])}
			for v in sorted(pillars.values(), key=lambda x: x["pillar"])
		],
		"funnel": funnel,
		"posts": posts,
		"errors": errors(f),
	}


def _cell(v: Any) -> Any:
	return DASH if v is None or v == "" else v


def to_xlsx(data: dict) -> bytes:
	"""Tệp Excel 4 trang: Tổng quan, Bài đăng, Trụ cột & phễu, Lỗi đăng tải. Chỉ số chưa có ghi "—"."""
	from openpyxl import Workbook

	wb = Workbook()
	ws = wb.active
	ws.title = "Tong quan"
	c = data["cards"]
	ws.append([_("Chỉ số"), _("Giá trị")])
	for label, key in (
		(_("Tổng bài"), "total_posts"),
		(_("Đã đăng"), "published_posts"),
		(_("Chờ duyệt"), "waiting_review"),
		(_("Đang lên lịch / xử lý"), "processing"),
		(_("Tỷ lệ đăng thành công (%)"), "success_rate"),
		(_("Lượt tiếp cận"), "reach"),
		(_("Lượt tương tác"), "engagement"),
		(_("Lượt xem video"), "video_views"),
		(_("Lead từ chiến dịch"), "leads"),
		(_("Lead đã thành Deal"), "deals"),
	):
		ws.append([label, _cell(c[key])])
	ws.append([])
	ws.append([_("Bộ lọc"), ", ".join(f"{k}={v}" for k, v in data["filters"].items()) or _("(tất cả)")])
	ws = wb.create_sheet("Bai dang")
	ws.append(
		[_("Bài viết"), _("Chiến dịch"), _("Kênh"), _("Trụ cột"), _("Giai đoạn"), _("Giờ đăng"),
		 _("Tiếp cận"), _("Tương tác"), _("Lượt bấm"), _("Lượt xem video"), _("Lead"), _("Trạng thái")]
	)  # fmt: skip
	for p in data["posts"]:
		ws.append(
			[p["title"], p["campaign"], p["platforms"], _cell(p["pillar"]), _cell(p["funnel_stage"]),
			 str(p["scheduled_at"] or ""), _cell(p["reach"]), _cell(p["engagement"]), _cell(p["clicks"]),
			 _cell(p["video_views"]), p["leads"], p["status"]]
		)  # fmt: skip
	ws = wb.create_sheet("Tru cot va pheu")
	ws.append([_("Trụ cột"), _("Số bài"), _("Tương tác"), _("Lead")])
	for p in data["pillars"]:
		ws.append([p["pillar"], p["posts"], _cell(p["engagement"]), p["leads"]])
	ws.append([])
	ws.append([_("Giai đoạn phễu"), _("Số bài"), _("%")])
	for p in data["funnel"]:
		ws.append([p["stage"], p["posts"], _cell(p["percent"])])
	ws = wb.create_sheet("Loi dang tai")
	ws.append([_("Loại lỗi"), _("Câu lỗi"), _("Nền tảng"), _("Số lần"), _("Gần nhất")])
	for e in data["errors"]:
		ws.append([_cell(e.error_type), _cell(e.last_error), e.platforms, e.count, str(e.last_at or "")])
	buf = io.BytesIO()
	wb.save(buf)
	return buf.getvalue()
