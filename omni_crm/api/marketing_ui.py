"""API cho Marketing Hub trong giao diện CRM `/crm` (ADR 0031): menu Marketing, cửa sổ Thiết lập, mục Marketing trang Lead.

Mọi hàm kiểm quyền DocType (hoặc quyền đọc đúng Lead); ghi đi qua `Document.save`/`insert` nên giữ nguyên mọi kiểm tra
của ADR 0029 (mã bài, trạng thái, khóa nội dung) và ADR 0030 (ảnh trên S3). Trạng thái bài đổi bằng `api/content_posts.py`.
Không hàm nào gọi Meta.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

from omni_crm.marketing import attribution, content_posts
from omni_crm.marketing.constants import (
	AUTO,
	CAMPAIGN,
	FAILED,
	MISSED,
	PILLAR,
	POST,
	PUBLISHED,
	SCHEDULED,
	UNCERTAIN,
)

ADMIN_ROLES = {"Omni Admin", "System Manager"}
CAMPAIGN_FIELDS = (
	"campaign_name",
	"platform",
	"status",
	"utm_campaign",
	"start_date",
	"end_date",
	"owner_team",
	"objective",
	"target_audience",
	"landing_url",
	"lead_target",
	"budget",
	"spend",
	"brief",
)
POST_FIELDS = (
	"campaign",
	"title",
	"platform",
	"publish_mode",
	"publish_at",
	"content",
	"image",
	"pillar",
	"funnel_stage",
	"landing_url",
	"post_url",
	"reach",
	"reactions",
	"comments",
	"shares",
	"link_clicks",
)
POST_LIST_FIELDS = [
	"name",
	"title",
	"campaign",
	"platform",
	"publish_mode",
	"publish_at",
	"status",
	"pillar",
	"funnel_stage",
	"post_url",
	"last_error",
	"lead_count",
	"deal_count",
]
NEEDS_ATTENTION = (FAILED, UNCERTAIN, MISSED)
SETTINGS_FIELDS = (
	"content_publish_enabled",
	"content_publish_channel",
	"content_late_window_minutes",
	"content_metrics_enabled",
	"content_reach_metric",
	"content_clicks_metric",
	"s3_media_enabled",
	"s3_endpoint_url",
	"s3_bucket",
	"s3_prefix",
	"s3_access_key",
	"s3_region",
	"s3_verify_tls",
	"s3_ca_bundle",
)
CHECK_FIELDS = {"content_publish_enabled", "content_metrics_enabled", "s3_media_enabled", "s3_verify_tls"}


def _is_admin() -> bool:
	return bool(ADMIN_ROLES & set(frappe.get_roles()))


def _require_admin() -> None:
	if not _is_admin():
		raise frappe.PermissionError(_("Chỉ Omni Admin sửa được thiết lập Marketing"))


def _pick(values, allowed) -> dict:
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	return {k: values[k] for k in allowed if k in values}


# --- trạng thái giao diện ----------------------------------------------------------------------------------------


@frappe.whitelist()
def ui_state() -> dict:
	"""Nhóm menu Marketing có hiện không, và người dùng được làm gì."""
	enabled = bool(frappe.db.exists("DocType", POST)) and frappe.has_permission(CAMPAIGN, "read")
	return {
		"enabled": enabled,
		"can_create_campaign": enabled and frappe.has_permission(CAMPAIGN, "create"),
		"can_edit_campaign": enabled and frappe.has_permission(CAMPAIGN, "write"),
		"can_create_post": enabled and frappe.has_permission(POST, "create"),
		"can_edit_post": enabled and frappe.has_permission(POST, "write"),
		"can_edit_pillar": enabled and frappe.has_permission(PILLAR, "write"),
		"is_admin": enabled and _is_admin(),
	}


# --- chiến dịch -----------------------------------------------------------------------------------------------------


def _lead_counts_by_campaign(names: list[str]) -> dict:
	"""{chiến dịch: (số Lead lần chạm đầu không phải Lead thử, số Deal từ các Lead đó)}."""
	if not names:
		return {}
	rows = frappe.db.sql(
		"""select l.omni_campaign as campaign, count(distinct l.name) as leads, count(distinct d.name) as deals
		from `tabCRM Lead` l left join `tabCRM Deal` d on d.lead = l.name
		where l.omni_campaign in %(names)s and ifnull(l.omni_is_test, 0) = 0
		group by l.omni_campaign""",
		{"names": tuple(names)},
		as_dict=True,
	)
	return {r.campaign: (r.leads, r.deals) for r in rows}


@frappe.whitelist()
def campaigns(status: str | None = None) -> list[dict]:
	"""Chiến dịch người dùng đọc được, kèm số bài theo trạng thái, số Lead, số Deal."""
	frappe.has_permission(CAMPAIGN, "read", throw=True)
	filters = {"status": status} if status else {}
	rows = frappe.get_list(
		CAMPAIGN,
		filters=filters,
		fields=["name", "campaign_name", "platform", "status", "start_date", "end_date", "utm_campaign"],
		order_by="modified desc",
		limit_page_length=200,
	)
	names = [r.name for r in rows]
	stats: dict = {}
	if names:
		for r in frappe.get_list(
			POST,
			filters={"campaign": ["in", names]},
			fields=["campaign", "status", "count(name) as n"],
			group_by="campaign, status",
			limit_page_length=0,
		):
			stats.setdefault(r.campaign, {})[r.status] = r.n
	counts = _lead_counts_by_campaign(names)
	for r in rows:
		by = stats.get(r.name, {})
		r["posts_total"] = sum(by.values())
		r["posts_published"] = by.get(PUBLISHED, 0)
		r["posts_scheduled"] = by.get(SCHEDULED, 0)
		r["posts_attention"] = sum(by.get(s, 0) for s in NEEDS_ATTENTION)
		r["leads"], r["deals"] = counts.get(r.name, (0, 0))
	return rows


@frappe.whitelist()
def campaign(name: str) -> dict:
	"""Một chiến dịch: các trường, số Lead và Deal, các bài theo giờ đăng."""
	doc = frappe.get_doc(CAMPAIGN, name)
	doc.check_permission("read")
	data = {f: doc.get(f) for f in CAMPAIGN_FIELDS}
	data["name"] = doc.name
	data["leads"], data["deals"] = _lead_counts_by_campaign([doc.name]).get(doc.name, (0, 0))
	data["posts"] = frappe.get_list(
		POST,
		filters={"campaign": doc.name},
		fields=POST_LIST_FIELDS,
		order_by="publish_at asc",
		limit_page_length=0,
	)
	data["can_edit"] = bool(frappe.has_permission(CAMPAIGN, "write", doc=doc))
	return data


@frappe.whitelist(methods=["POST"])
def save_campaign(values, name: str | None = None) -> str:
	"""Tạo (không có `name`) hoặc sửa chiến dịch; trả tên chiến dịch."""
	data = _pick(values, CAMPAIGN_FIELDS)
	if name:
		doc = frappe.get_doc(CAMPAIGN, name)
		data.pop("campaign_name", None)  # tên là khóa (autoname), không đổi ở đây
		doc.update(data)
		doc.save()
	else:
		doc = frappe.get_doc({"doctype": CAMPAIGN, **data})
		doc.insert()
	return doc.name


# --- bài đăng --------------------------------------------------------------------------------------------------------


@frappe.whitelist()
def post(name: str) -> dict:
	"""Một bài: các trường, link có UTM, số Lead và Deal, lý do tự đăng chưa chạy, quyền sửa."""
	doc = frappe.get_doc(POST, name)
	doc.check_permission("read")
	data = {
		f: doc.get(f) for f in (*POST_FIELDS, "post_code", "status", "utm_link", "published_at", "last_error")
	}
	data.update(name=doc.name, metrics_synced_at=doc.metrics_synced_at, counts=attribution.counts(doc.name))
	data["auto_publish_off"] = content_posts.auto_publish_state() if doc.publish_mode == AUTO else None
	data["can_edit"] = bool(frappe.has_permission(POST, "write", doc=doc))
	return data


@frappe.whitelist(methods=["POST"])
def save_post(values, name: str | None = None) -> str:
	"""Tạo (không có `name`, cần `post_code`) hoặc sửa bài; trả mã bài."""
	data = _pick(values, POST_FIELDS)
	if name:
		doc = frappe.get_doc(POST, name)
		doc.update(data)
		doc.save()
	else:
		code = (frappe.parse_json(values) if isinstance(values, str) else values or {}).get("post_code")
		doc = frappe.get_doc({"doctype": POST, "post_code": code, **data})
		doc.insert()
	return doc.name


@frappe.whitelist()
def pillars() -> list[dict]:
	"""Trụ cột nội dung (còn dùng trước)."""
	frappe.has_permission(PILLAR, "read", throw=True)
	return frappe.get_list(
		PILLAR,
		fields=["name", "pillar_name", "enabled", "description"],
		order_by="enabled desc, pillar_name asc",
	)


# --- lịch -------------------------------------------------------------------------------------------------------------


@frappe.whitelist()
def calendar(start: str, end: str, campaign: str | None = None) -> list[dict]:
	"""Bài có giờ đăng trong [start, end] (theo quyền đọc), cho lịch tháng."""
	frappe.has_permission(POST, "read", throw=True)
	filters = [[POST, "publish_at", ">=", get_datetime(start)], [POST, "publish_at", "<=", get_datetime(end)]]
	if campaign:
		filters.append([POST, "campaign", "=", campaign])
	return frappe.get_list(
		POST, filters=filters, fields=POST_LIST_FIELDS, order_by="publish_at asc", limit_page_length=1000
	)


# --- báo cáo ------------------------------------------------------------------------------------------------------------


@frappe.whitelist()
def report(
	campaign: str | None = None,
	platform: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
) -> dict:
	"""Số liệu trang Báo cáo marketing: chỉ số tổng, theo trụ cột, theo phễu, bảng bài, lỗi gần đây."""
	from omni_crm.omni_crm.report.hiệu_quả_bài_đăng.hiệu_quả_bài_đăng import _lead_deal_counts, _unknown_row

	frappe.has_permission(POST, "read", throw=True)
	filters: dict = {}
	if campaign:
		filters["campaign"] = campaign
	if platform:
		filters["platform"] = platform
	if from_date and to_date:
		filters["publish_at"] = ["between", [from_date, f"{to_date} 23:59:59"]]
	elif from_date:
		filters["publish_at"] = [">=", from_date]
	elif to_date:
		filters["publish_at"] = ["<=", f"{to_date} 23:59:59"]
	posts = frappe.get_list(
		POST,
		filters=filters,
		fields=[*POST_LIST_FIELDS, "reach", "reactions", "comments", "shares", "link_clicks", "published_at"],
		order_by="publish_at asc",
		limit_page_length=0,
	)
	counts = _lead_deal_counts([p.name for p in posts])
	by_pillar: dict = {}
	by_funnel: dict = {}
	totals = dict(
		posts=len(posts), published=0, attention=0, reach=0, engagement=0, clicks=0, leads=0, deals=0
	)
	for p in posts:
		p["leads"], p["deals"] = counts.get(p.name, (0, 0))
		p["engagement"] = cint(p.reactions) + cint(p.comments) + cint(p.shares)
		totals["published"] += p.status == PUBLISHED
		totals["attention"] += p.status in NEEDS_ATTENTION
		for key, value in (("reach", p.reach), ("engagement", p.engagement), ("clicks", p.link_clicks)):
			totals[key] += cint(value)
		totals["leads"] += p.leads
		totals["deals"] += p.deals
		pil = by_pillar.setdefault(
			p.pillar or _("Chưa chọn trụ cột"), {"posts": 0, "engagement": 0, "leads": 0}
		)
		pil["posts"] += 1
		pil["engagement"] += p.engagement
		pil["leads"] += p.leads
		stage = p.funnel_stage or _("Chưa chọn")
		by_funnel[stage] = by_funnel.get(stage, 0) + 1
	attempted = totals["published"] + totals["attention"]
	totals["success_rate"] = round(100 * totals["published"] / attempted) if attempted else None
	unknown = _unknown_row(campaign) if campaign and frappe.has_permission("CRM Lead", "report") else None
	errors = sorted(
		(p for p in posts if p.status in NEEDS_ATTENTION),
		key=lambda p: get_datetime(p.publish_at),
		reverse=True,
	)[:10]
	return {
		"totals": totals,
		"by_pillar": [
			{"pillar": k, **v} for k, v in sorted(by_pillar.items(), key=lambda kv: -kv[1]["engagement"])
		],
		"by_funnel": [
			{"stage": k, "posts": v, "share": round(100 * v / len(posts)) if posts else 0}
			for k, v in sorted(by_funnel.items(), key=lambda kv: -kv[1])
		],
		"posts": posts,
		"unknown": {"leads": unknown["leads"], "deals": unknown["deals"]} if unknown else None,
		"errors": [
			{"name": p.name, "title": p.title, "status": p.status, "last_error": p.last_error} for p in errors
		],
	}


# --- thiết lập ------------------------------------------------------------------------------------------------------


def _channels() -> list[dict]:
	rows = frappe.get_all(
		"Omni Channel",
		filters={"channel_type": "Meta Messenger"},
		fields=[
			"name",
			"enabled",
			"outbound_enabled",
			"meta_page_id",
			"meta_page_name",
			"meta_connected_at",
			"meta_subscribed_fields",
			"meta_token_checked_at",
			"meta_token_valid",
			"meta_token_never_expires",
			"meta_token_expires_at",
			"meta_token_note",
		],
		order_by="name asc",
	)
	return rows


@frappe.whitelist()
def settings() -> dict:
	"""Thiết lập Marketing (không có secret) và trạng thái kênh Page. Chỉ Omni Admin, System Manager."""
	_require_admin()
	s = frappe.get_single("Omni Settings")
	values = {f: s.get(f) for f in SETTINGS_FIELDS}
	from omni_crm.marketing.s3_media import verify_tls

	values["s3_verify_tls"] = int(verify_tls(s.get("s3_verify_tls")))
	values["content_late_window_minutes"] = content_posts.late_window_minutes()
	return {
		"values": values,
		"secret_set": bool(s.get_password("s3_secret_key", raise_exception=False)),
		"channels": _channels(),
		"auto_publish_off": content_posts.auto_publish_state(),
	}


@frappe.whitelist(methods=["POST"])
def save_settings(values) -> dict:
	"""Lưu thiết lập Marketing (chỉ các trường trong SETTINGS_FIELDS; secret chỉ khi gửi giá trị mới)."""
	_require_admin()
	raw = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	data = _pick(raw, SETTINGS_FIELDS)
	for f in CHECK_FIELDS & data.keys():
		data[f] = 1 if cint(data[f]) else 0
	if "content_late_window_minutes" in data:
		data["content_late_window_minutes"] = max(0, cint(data["content_late_window_minutes"]))
	s = frappe.get_single("Omni Settings")
	s.update(data)
	secret = (raw.get("s3_secret_key") or "").strip()
	if secret:
		s.s3_secret_key = secret
	s.save()
	return settings()


# --- trang Lead -----------------------------------------------------------------------------------------------------


@frappe.whitelist()
def lead_marketing(lead: str) -> dict:
	"""Mục Marketing ở cột bên trang Lead (chỉ đọc): chiến dịch, bài đăng, kênh/UTM, phễu, trang đích."""
	frappe.has_permission("CRM Lead", "read", doc=lead, throw=True)
	fields = [
		"omni_campaign",
		"omni_last_campaign",
		"omni_ad_platform",
		"omni_utm_source",
		"omni_utm_medium",
		"omni_utm_campaign",
		"omni_utm_content",
		"omni_landing_page",
	]
	if attribution.lead_field_ready():
		fields.append(attribution.LEAD_FIELD)
	row = frappe.db.get_value("CRM Lead", lead, fields, as_dict=True) or {}
	post_info = None
	post_name = row.get(attribution.LEAD_FIELD)
	if post_name and frappe.has_permission(POST, "read", doc=post_name):
		post_info = frappe.db.get_value(
			POST,
			post_name,
			["name", "title", "platform", "publish_at", "funnel_stage", "post_url", "status"],
			as_dict=True,
		)
	data = {
		"campaign": row.get("omni_campaign"),
		"last_campaign": row.get("omni_last_campaign"),
		"platform": row.get("omni_ad_platform"),
		"utm": {k: row.get(f"omni_utm_{k}") for k in ("source", "medium", "campaign", "content")},
		"landing_page": row.get("omni_landing_page"),
		"post": post_info,
	}
	data["visible"] = bool(
		data["campaign"] or data["post"] or any(data["utm"].values()) or data["landing_page"]
	)
	return data
