"""API hộp thư cho sale: danh sách hội thoại, đọc hội thoại (gửi trả lời và duyệt nháp thêm ở D1-09)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

from omni_crm.crm_bridge.timeline import TIMELINE_EVENTS, parse_attachments

LEAD = "CRM Lead"
INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
MAX_CANDIDATES = 300  # số Lead gần nhất được xét cho một lần tải danh sách
THREAD_LIMIT = 300
SNIPPET_LEN = 120
PAGE_LENGTH_MAX = 100
OUT_STATUSES = ("Queued", "Sent")  # tin ra tính là đã trả lời; Failed/Blocked thì chưa
SAFE_FILE_PREFIXES = ("http://", "https://", "/files/", "/private/files/")


def _filters(filters: Any) -> dict:
	if isinstance(filters, str):
		try:
			filters = json.loads(filters or "{}")
		except ValueError:
			filters = {}
	return filters if isinstance(filters, dict) else {}


def _in_clause(values: list[str]) -> tuple[str, list[str]]:
	return ", ".join(["%s"] * len(values)), list(values)


def _last_events(leads: list[str]) -> dict[str, dict]:
	"""Sự kiện hội thoại cuối cùng của mỗi Lead: tin vào của khách, tin ra, hoặc oa_echo (nhân viên trả lời ngoài)."""
	if not leads:
		return {}
	marks, params = _in_clause(leads)
	ev_marks, ev_params = _in_clause(sorted(TIMELINE_EVENTS))
	inbound = frappe.db.sql(
		f"""
		select m.lead, m.channel_type, m.event_type, m.text, m.received_at as at
		from `tab{INBOUND}` m
		join (
			select lead, max(received_at) as mx from `tab{INBOUND}`
			where lead in ({marks}) and event_type in ({ev_marks}) and status != 'Ignored'
			group by lead
		) t on t.lead = m.lead and t.mx = m.received_at
		where m.event_type in ({ev_marks}) and m.status != 'Ignored'
		""",
		params + ev_params + ev_params,
		as_dict=True,
	)
	st_marks, st_params = _in_clause(list(OUT_STATUSES))
	outbound = frappe.db.sql(
		f"""
		select m.lead, c.channel_type, m.text, coalesce(m.sent_at, m.creation) as at
		from `tab{OUTBOUND}` m
		left join `tabOmni Channel` c on c.name = m.channel
		join (
			select lead, max(coalesce(sent_at, creation)) as mx from `tab{OUTBOUND}`
			where lead in ({marks}) and status in ({st_marks})
			group by lead
		) t on t.lead = m.lead and t.mx = coalesce(m.sent_at, m.creation)
		where m.status in ({st_marks})
		""",
		params + st_params + st_params,
		as_dict=True,
	)
	last: dict[str, dict] = {}
	candidates = [
		{
			"lead": r.lead,
			"direction": "out" if r.event_type == "oa_echo" else "in",
			"channel_type": r.channel_type,
			"text": r.text,
			"at": r.at,
		}
		for r in inbound
	] + [
		{"lead": r.lead, "direction": "out", "channel_type": r.channel_type, "text": r.text, "at": r.at}
		for r in outbound
	]
	for c in candidates:
		cur = last.get(c["lead"])
		if not cur or get_datetime(c["at"]) >= get_datetime(cur["at"]):
			last[c["lead"]] = c
	return last


def _channels_by_lead(leads: list[str]) -> dict[str, list[str]]:
	if not leads:
		return {}
	rows = frappe.get_all(
		IDENTITY, filters={"lead": ["in", leads]}, fields=["lead", "channel_type"], order_by="creation asc"
	)
	out: dict[str, list[str]] = {}
	for r in rows:
		if r.channel_type not in out.setdefault(r.lead, []):
			out[r.lead].append(r.channel_type)
	return out


def _snippet(text: str | None) -> str:
	return " ".join((text or "").split())[:SNIPPET_LEN]


@frappe.whitelist()
def list_conversations(filters: dict | str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""Danh sách hội thoại (theo Lead) mà người dùng được xem, mới nhất trước.

	`filters`: {"channel": "Zalo OA", "unanswered": 1, "mine": 1, "search": "tên"}.
	"""
	f = _filters(filters)
	page = max(cint(page), 1)
	page_length = min(max(cint(page_length) or 20, 1), PAGE_LENGTH_MAX)
	lead_filters: list = [["omni_last_inbound_at", "is", "set"], ["omni_merged_into", "is", "not set"]]
	if f.get("search"):
		lead_filters.append(["lead_name", "like", f"%{str(f['search']).strip()}%"])
	user = frappe.session.user
	or_filters = None
	if cint(f.get("mine")):
		or_filters = [["lead_owner", "=", user], ["_assign", "like", f'%"{user}"%']]
	# get_list áp quyền CRM Lead (chủ sở hữu, được giao, phân cấp): sale chỉ thấy Lead của mình.
	leads = frappe.get_list(
		LEAD,
		filters=lead_filters,
		or_filters=or_filters,
		fields=[
			"name",
			"lead_name",
			"lead_owner",
			"status",
			"converted",
			"mobile_no",
			"omni_last_inbound_at",
			"omni_first_channel",
		],
		order_by="omni_last_inbound_at desc",
		limit_page_length=MAX_CANDIDATES,
	)
	names = [r.name for r in leads]
	last = _last_events(names)
	channels = _channels_by_lead(names)
	rows = []
	for r in leads:
		ev = last.get(r.name)
		chans = channels.get(r.name) or ([r.omni_first_channel] if r.omni_first_channel else [])
		unanswered = bool(ev and ev["direction"] == "in")
		if f.get("channel") and f["channel"] not in chans:
			continue
		if cint(f.get("unanswered")) and not unanswered:
			continue
		rows.append(
			{
				"lead": r.name,
				"lead_name": r.lead_name or r.name,
				"lead_owner": r.lead_owner,
				"status": r.status,
				"converted": cint(r.converted),
				"channels": chans,
				"unanswered": unanswered,
				"last_at": ev["at"] if ev else r.omni_last_inbound_at,
				"last_direction": ev["direction"] if ev else None,
				"last_channel": ev["channel_type"] if ev else None,
				"last_text": _snippet(ev["text"]) if ev else "",
			}
		)
	start = (page - 1) * page_length
	return {
		"rows": rows[start : start + page_length],
		"page": page,
		"page_length": page_length,
		"total": len(rows),
		"has_more": start + page_length < len(rows),
	}


def _safe_attachments(raw: Any) -> list[dict]:
	out = []
	for a in parse_attachments(raw):
		url = str(a.get("url") or "")
		if url.startswith(SAFE_FILE_PREFIXES):
			out.append({"url": url, "name": str(a.get("name") or url.rsplit("/", 1)[-1])})
	return out


def check_lead_access(lead: str) -> None:
	"""Ném PermissionError nếu người dùng không được đọc Lead (kiểm theo Lead, không theo DocType)."""
	if not lead or not frappe.db.exists(LEAD, lead):
		frappe.throw(_("Không tìm thấy Lead"), frappe.DoesNotExistError)
	if not frappe.has_permission(LEAD, "read", doc=lead):
		frappe.throw(_("Bạn không có quyền xem Lead này"), frappe.PermissionError)


@frappe.whitelist()
def get_thread(lead: str) -> dict:
	"""Toàn bộ hội thoại của Lead trên mọi kênh, theo thời gian tăng dần (không kèm payload gốc)."""
	check_lead_access(lead)
	inbound = frappe.get_all(
		INBOUND,
		filters={"lead": lead, "event_type": ["in", sorted(TIMELINE_EVENTS)], "status": ["!=", "Ignored"]},
		fields=[
			"name",
			"channel_type",
			"event_type",
			"identity",
			"external_sender_id",
			"text",
			"attachments",
			"received_at",
		],
		order_by="received_at desc",
		limit_page_length=THREAD_LIMIT,
	)
	outbound = frappe.get_all(
		OUTBOUND,
		filters={"lead": lead},
		fields=[
			"name",
			"channel",
			"text",
			"source",
			"sent_by",
			"status",
			"sent_at",
			"creation",
			"is_sandbox",
		],
		order_by="creation desc",
		limit_page_length=THREAD_LIMIT,
	)
	names = {
		r.name: r.display_name
		for r in frappe.get_all(IDENTITY, filters={"lead": lead}, fields=["name", "display_name"])
	}
	channel_types = {
		r.name: r.channel_type
		for r in frappe.get_all("Omni Channel", fields=["name", "channel_type"])
		if r.name in {o.channel for o in outbound}
	}
	items: list[dict] = []
	for m in inbound:
		echo = m.event_type == "oa_echo"
		items.append(
			{
				"name": m.name,
				"doctype": INBOUND,
				"direction": "out" if echo else "in",
				"channel_type": m.channel_type,
				"sender": _("Nhân viên (ngoài hệ thống)")
				if echo
				else names.get(m.identity) or m.external_sender_id,
				"text": m.text or "",
				"attachments": _safe_attachments(m.attachments),
				"at": m.received_at,
				"status": None,
			}
		)
	for o in outbound:
		if o.sent_by:
			sender = frappe.db.get_value("User", o.sent_by, "full_name") or o.sent_by
		else:
			sender = _("Bot") if o.source == "Bot" else _("Hệ thống")
		items.append(
			{
				"name": o.name,
				"doctype": OUTBOUND,
				"direction": "out",
				"channel_type": channel_types.get(o.channel),
				"sender": sender,
				"text": o.text or "",
				"attachments": [],
				"at": o.sent_at or o.creation,
				"status": o.status,
				"is_sandbox": cint(o.is_sandbox),
			}
		)
	items.sort(key=lambda i: (_ts(i["at"]), i["name"]))
	info = frappe.db.get_value(
		LEAD,
		lead,
		["name", "lead_name", "lead_owner", "status", "mobile_no", "email", "converted"],
		as_dict=True,
	)
	deal = frappe.db.get_value("CRM Deal", {"lead": lead}, "name") if info.converted else None
	return {
		"lead": info,
		"deal": deal,
		"crm_url": f"/crm/deals/{deal}" if deal else f"/crm/leads/{lead}",
		"identities": frappe.get_all(
			IDENTITY, filters={"lead": lead}, fields=["name", "channel_type", "display_name", "is_follower"]
		),
		"messages": items,
		"truncated": len(inbound) >= THREAD_LIMIT or len(outbound) >= THREAD_LIMIT,
	}


def _ts(value: Any) -> datetime:
	return get_datetime(value) if value else datetime.min
