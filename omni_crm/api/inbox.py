"""API hộp thư cho sale: danh sách hội thoại, đọc hội thoại, gửi trả lời (D1-09); duyệt nháp bot ở api/drafts.py (D3-01)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

from omni_crm import flags
from omni_crm.bot import scoring
from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.crm_bridge.timeline import TIMELINE_EVENTS, parse_attachments
from omni_crm.ingest.events import OutboundRequest
from omni_crm.pipeline import outbound as outbound_pipeline
from omni_crm.pipeline.retry import redact
from omni_crm.utils import media

LEAD = "CRM Lead"
INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
CALL = "Omni Call"
CALL_FINAL_STATUSES = ("Answered", "Missed", "Busy", "Failed", "Voicemail")
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


SEARCH_FIELDS = (
	"lead_name",
	"mobile_no",
	"phone",
	"email",
	"organization",
	"omni_phone_e164",
	"omni_email_norm",
)
SEARCH_LIMIT = 2000


def _search_matches(term: str) -> list[str]:
	"""Tên các Lead (người dùng được xem) khớp `term` theo tên, SĐT, email hoặc đơn vị.

	Tìm riêng rồi lọc `name in (...)` (không trộn vào `or_filters` của "Của tôi", vì hai nhóm OR đó sẽ thành "hoặc").
	"""
	term = term.strip()
	if not term:
		return []
	return frappe.get_list(
		LEAD,
		filters=[["omni_merged_into", "is", "not set"]],
		or_filters=[[f, "like", f"%{term}%"] for f in SEARCH_FIELDS],
		pluck="name",
		limit_page_length=SEARCH_LIMIT,
	)


def _fit_tiers(organizations: list[str]) -> dict[str, str]:
	"""{tên nhà thầu: hạng phù hợp A/B/C} cho các tên đã có CRM Organization (một truy vấn); rỗng khi tắt điểm phù hợp."""
	from omni_crm.prospect import fit

	organizations = [o for o in set(organizations) if o]
	if not organizations or not fit.fit_enabled():
		return {}
	rows = frappe.get_all(
		"CRM Organization",
		filters={"name": ["in", organizations], "omni_fit_tier": ["is", "set"]},
		fields=["name", "omni_fit_tier"],
	)
	return {r.name: r.omni_fit_tier for r in rows}


@frappe.whitelist()
def list_conversations(filters: dict | str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""Danh sách hội thoại (theo Lead) mà người dùng được xem, mới nhất trước.

	`filters`: {"channel": "Zalo OA", "unanswered": 1, "mine": 1, "has_draft": 1, "search": "tên/SĐT/email/đơn vị"}.
	Mỗi dòng có thêm `organization`, `fit_tier`, nhãn ưu tiên (`priority`, `priority_label`) và `unrecorded`.
	"""
	from omni_crm.crm_bridge import care_log
	from omni_crm.dashboard import priority

	f = _filters(filters)
	page = max(cint(page), 1)
	page_length = min(max(cint(page_length) or 20, 1), PAGE_LENGTH_MAX)
	lead_filters: list = [["omni_last_inbound_at", "is", "set"], ["omni_merged_into", "is", "not set"]]
	term = str(f.get("search") or "").strip()
	if term:
		matches = _search_matches(term)
		if not matches:
			return {"rows": [], "page": page, "page_length": page_length, "total": 0, "has_more": False}
		lead_filters.append(["name", "in", matches])
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
			"organization",
			"omni_intent",
			"omni_score",
			"omni_last_inbound_at",
			"omni_first_channel",
		],
		order_by="omni_last_inbound_at desc",
		limit_page_length=MAX_CANDIDATES,
	)
	names = [r.name for r in leads]
	last = _last_events(names)
	channels = _channels_by_lead(names)
	from omni_crm.api import drafts as drafts_mod

	pending = drafts_mod.leads_with_pending(names)
	tiers = _fit_tiers([r.organization for r in leads])
	unrecorded = care_log.unrecorded_by_lead(names) if flags.care_log_enabled() else {}
	ctx = priority.build_context(names)
	rows = []
	for r in leads:
		ev = last.get(r.name)
		chans = channels.get(r.name) or ([r.omni_first_channel] if r.omni_first_channel else [])
		unanswered = bool(ev and ev["direction"] == "in")
		if f.get("channel") and f["channel"] not in chans:
			continue
		if cint(f.get("unanswered")) and not unanswered:
			continue
		if cint(f.get("has_draft")) and r.name not in pending:
			continue
		label = priority.classify(
			r.name,
			ctx,
			get_datetime(ev["at"]) if unanswered else None,
			r.omni_intent,
			r.omni_score,
			r.organization,
		)
		rows.append(
			{
				"lead": r.name,
				"lead_name": r.lead_name or r.name,
				"lead_owner": r.lead_owner,
				"status": r.status,
				"converted": cint(r.converted),
				"organization": r.organization or "",
				"fit_tier": tiers.get(r.organization),
				"channels": chans,
				"unanswered": unanswered,
				"draft_pending": r.name in pending,
				"unrecorded": r.name in unrecorded,
				"priority": label[0] if label else None,
				"priority_label": priority.label(label[0]) if label else None,
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
	"""Đính kèm đã lọc URL: {url, name} (+ type, thumb, inline nếu có). `inline` = được vẽ thành ảnh (ADR 0020)."""
	out = []
	for a in parse_attachments(raw):
		url = str(a.get("url") or "")
		if not media.is_safe_link(url):
			continue
		kind = str(a.get("type") or "").strip().lower()[:30]
		name = str(a.get("name") or "")
		if not name or name == url:
			name = (
				""
				if kind in media.INLINE_IMAGE_TYPES
				else media.filename_from_url(url) or url.rsplit("/", 1)[-1]
			)
		item = {"url": url, "name": name}
		if kind:
			item["type"] = kind
		thumb = str(a.get("thumb") or "")
		if thumb and media.is_inline_image_url(thumb):
			item["thumb"] = thumb
		if kind in media.INLINE_IMAGE_TYPES and media.is_inline_image_url(url):
			item["inline"] = True
		out.append(item)
	return out


def _outbound_attachments(row: Any) -> list[dict]:
	"""Ảnh/tệp sale gửi kèm (V3-09), qua cùng bộ lọc URL với đính kèm của khách."""
	if not row.get("attachment"):
		return []
	kind = row.get("attachment_kind") or "file"
	return _safe_attachments(
		[{"url": row.attachment, "name": row.get("attachment_name") or "", "type": kind}]
	)


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
	return thread_data(lead)


def thread_data(lead: str) -> dict:
	"""Dữ liệu hội thoại của Lead; người gọi PHẢI đã gọi `check_lead_access` (dùng chung với tab Zalo, V3-01)."""
	from omni_crm.api import bot_control  # import trễ: drafts.py dùng lại các hàm của module này
	from omni_crm.api import customer as customer_api
	from omni_crm.api import drafts as drafts_mod

	inbound = frappe.get_all(
		INBOUND,
		filters={"lead": lead, "event_type": ["in", sorted(TIMELINE_EVENTS)], "status": ["!=", "Ignored"]},
		fields=[
			"name",
			"channel_type",
			"event_type",
			"identity",
			"external_sender_id",
			"external_message_id",
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
			"external_message_id",
			"last_error",
			"bot_draft",
			"attachment",
			"attachment_kind",
			"attachment_name",
		],
		order_by="creation desc",
		limit_page_length=THREAD_LIMIT,
	)
	bot_meta = drafts_mod.bot_sent_meta(
		lead, [o.bot_draft for o in outbound if o.source == "Bot" and o.bot_draft]
	)  # nhãn "Bot đã gửi", chấm điểm, nút tắt nhóm (D3-05)
	names = {
		r.name: r.display_name
		for r in frappe.get_all(IDENTITY, filters={"lead": lead}, fields=["name", "display_name"])
	}
	lead_label = frappe.db.get_value(LEAD, lead, "lead_name")
	call_rows = frappe.get_all(
		CALL,
		filters={
			"lead": lead,
			"status": ["in", list(CALL_FINAL_STATUSES)],
		},  # bỏ cuộc gọi tạm (chưa kết thúc)
		fields=[
			"name",
			"direction",
			"status",
			"duration_seconds",
			"agent",
			"started_at",
			"ended_at",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=THREAD_LIMIT,
	)
	channel_types = {
		r.name: r.channel_type
		for r in frappe.get_all("Omni Channel", fields=["name", "channel_type"])
		if r.name in {o.channel for o in outbound}
	}
	items: list[dict] = []
	user_names: dict[
		str, str
	] = {}  # tên người dùng theo lượt gọi: bỏ truy vấn User lặp lại cho mỗi tin/cuộc gọi
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
				else timeline.sender_label(
					names.get(m.identity), lead_label, m.channel_type, m.external_sender_id
				),
				"kind": "echo" if echo else "customer",
				"text": m.text or "",
				"attachments": _safe_attachments(m.attachments),
				"at": m.received_at,
				"status": None,
			}
		)
	for c in call_rows:
		items.append(_call_item(c, user_names))
	# Tin Sale trả lời ngoài hệ thống (oa_echo) có cả Omni Outbound Message cùng mã tin: chỉ hiện bản echo.
	echo_ids = {m.external_message_id for m in inbound if m.event_type == "oa_echo" and m.external_message_id}
	for o in outbound:
		if o.external_message_id and o.external_message_id in echo_ids:
			continue
		if o.sent_by:
			sender = _user_full_name(o.sent_by, user_names)
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
				"attachments": _outbound_attachments(o),
				"at": o.sent_at or o.creation,
				"status": o.status,
				"is_sandbox": cint(o.is_sandbox),
				"error": o.last_error if o.status in ("Failed", "Blocked") else None,
				"queued": o.status == "Queued",
				"bot_sent": o.source == "Bot",
				"source": o.source,
				"kind": "bot" if o.source == "Bot" else "sale",
				"bot": bot_meta.get(o.bot_draft) if o.source == "Bot" else None,
			}
		)
	items.sort(key=lambda i: (_ts(i["at"]), i["name"]))
	info = frappe.db.get_value(
		LEAD,
		lead,
		[
			"name",
			"lead_name",
			"lead_owner",
			"status",
			"mobile_no",
			"email",
			"converted",
			"omni_intent",
			"omni_bot_summary",
			"omni_score",
			"omni_score_breakdown",
		],
		as_dict=True,
	)
	score = scoring.score_view(info)
	info.pop("omni_score_breakdown", None)  # JSON thô không đưa ra giao diện
	info["omni_score"] = score["score"] if score else None
	deal = frappe.db.get_value("CRM Deal", {"lead": lead}, "name") if info.converted else None
	profile = customer_api.get_mini_profile(lead)  # V2-04: hồ sơ rút gọn (đơn vị, hạng, bước tiếp theo)
	if profile[
		"person_refused"
	]:  # từ chối/đã xóa dữ liệu: SĐT/email không ra khỏi API (không chỉ ẩn ở giao diện)
		info["mobile_no"] = info["email"] = None
	return {
		"lead": info,
		"score": score,  # {score, reasons, hot} hoặc None khi chưa chấm (D2-06)
		"deal": deal,
		"crm_url": f"/crm/deals/{deal}" if deal else f"/crm/leads/{lead}",
		"identities": frappe.get_all(
			IDENTITY, filters={"lead": lead}, fields=["name", "channel_type", "display_name", "is_follower"]
		),
		"messages": items,
		"drafts": drafts_mod.thread_drafts(lead),  # nháp của bot chờ duyệt (D3-01)
		"bot": bot_control.state(lead, bool(frappe.has_permission(LEAD, "write", doc=lead))),  # D3-02
		"truncated": max(len(inbound), len(outbound), len(call_rows)) >= THREAD_LIMIT,
		"compose": compose_state(lead),
		"profile": profile,
	}


def _duration_text(seconds: int | None) -> str:
	minutes, secs = divmod(max(0, int(seconds or 0)), 60)
	if not minutes:
		return _("{0} giây").format(secs)
	return _("{0} phút {1} giây").format(minutes, secs) if secs else _("{0} phút").format(minutes)


def _user_full_name(user: str | None, cache: dict[str, str] | None = None) -> str | None:
	"""Tên đầy đủ của User (hoặc chính mã nếu trống); `cache` theo lượt gọi để không truy vấn lại cho cùng người."""
	if not user:
		return None
	if cache is not None and user in cache:
		return cache[user]
	name = frappe.db.get_value("User", user, "full_name") or user
	if cache is not None:
		cache[user] = name
	return name


def _call_item(c: Any, names: dict[str, str] | None = None) -> dict:
	"""Dòng cuộc gọi trong hội thoại: chiều, trạng thái, thời lượng, người nghe máy (không kèm số, ghi âm, sự kiện gốc)."""
	inbound = c.direction != "Outbound"
	labels = {
		"Answered": _("Đã nghe máy"),
		"Missed": _("Gọi nhỡ") if inbound else _("Khách không nghe máy"),
		"Busy": _("Máy bận"),
		"Failed": _("Cuộc gọi lỗi"),
		"Voicemail": _("Để lại lời nhắn"),
	}
	parts = [_("Cuộc gọi đến") if inbound else _("Cuộc gọi đi"), labels.get(c.status, c.status)]
	if c.status == "Answered" and c.duration_seconds is not None:
		parts.append(_duration_text(c.duration_seconds))
	agent = _user_full_name(c.agent, names)
	if agent:
		parts.append((_("Nghe máy: {0}") if inbound else _("Gọi bởi: {0}")).format(agent))
	return {
		"name": c.name,
		"doctype": CALL,
		"kind": "call",
		"direction": "in" if inbound else "out",
		"channel_type": "Telephony",
		"sender": agent or _("Tổng đài"),
		"text": " · ".join(parts),
		"attachments": [],
		"at": c.ended_at or c.started_at or c.creation,
		"status": None,
	}


def _ts(value: Any) -> datetime:
	return get_datetime(value) if value else datetime.min


# --- trả lời khách (D1-09) -------------------------------------------------------------------------


def check_lead_write(lead: str) -> str:
	"""Ném PermissionError nếu người dùng không được GHI Lead (đã theo Lead gộp); trả tên Lead đích."""
	if not lead or not frappe.db.exists(LEAD, lead):
		frappe.throw(_("Không tìm thấy Lead"), frappe.DoesNotExistError)
	lead = lead_bridge.follow_merged(lead) or lead
	if not frappe.has_permission(LEAD, "write", doc=lead):
		frappe.throw(_("Bạn không có quyền trả lời khách của Lead này"), frappe.PermissionError)
	return lead


def _zalo_target(lead: str) -> tuple[Any, Any] | None:
	"""(identity_doc, channel_doc) Zalo mới nhất của Lead; None nếu Lead chưa có định danh Zalo."""
	ident = frappe.get_all(
		IDENTITY,
		filters={"lead": lead, "channel_type": "Zalo OA"},
		pluck="name",
		order_by="last_inbound_at desc, creation desc",
		limit_page_length=1,
	)
	if not ident:
		return None
	identity_doc = frappe.get_doc(IDENTITY, ident[0])
	channel = frappe.db.get_value(
		INBOUND, {"identity": identity_doc.name}, "channel", order_by="received_at desc"
	) or frappe.db.get_value("Omni Channel", {"channel_type": "Zalo OA", "enabled": 1}, "name")
	if not channel or not frappe.db.exists("Omni Channel", channel):
		return None
	return identity_doc, frappe.get_doc("Omni Channel", channel)


def compose_state(lead: str) -> dict:
	"""Trạng thái khung soạn tin của Lead: khả năng gửi Zalo, cảnh báo phí, nút xin SĐT, quyền ghi."""
	can_write = bool(frappe.has_permission(LEAD, "write", doc=lead))
	state: dict = {"can_write": can_write, "zalo": None, "email": bool(_has_channel(lead, "Email"))}
	target = _zalo_target(lead)
	if not target:
		return state
	identity_doc, channel_doc = target
	try:
		adapter = get_adapter("Zalo OA")
		ok, reason = adapter.can_send_now(identity_doc, channel_doc, source="Sale")
	except AdapterNotFoundError:
		ok, reason = False, _("Chưa có adapter Zalo")
	has_phone = _has_phone(lead)
	from omni_crm.channels.zalo import media_out, phone_request

	phone_why = phone_request.ineligible_reason(identity_doc, channel_doc, manual=True)
	paid = bool(ok and reason)
	state["zalo"] = {
		"can_send": bool(ok),
		"paid": paid,  # được gửi nhưng ăn vào quỹ tin ngoài 48 giờ: cần xác nhận
		"message": reason or "",
		"can_request_phone": bool(ok and not phone_why),
		"phone_request_reason": phone_why or "",
		"has_phone": has_phone,
		"sandbox": bool(flags.sandbox()),
		# V3-09: nút đính kèm ảnh/tệp; công tắc tắt thì chỉ tài khoản Zalo thử nghiệm
		"can_send_media": bool(ok and media_out.recipient_allowed(identity_doc.get("external_id"))),
		# V3-07: số tin còn lại của quỹ ngoài 48 giờ CHỈ hiện khi khách đã ngoài 48 giờ (trong khung thì không liên quan)
		"quota": _quota_safe() if paid else None,
	}
	return state


def _quota_safe() -> dict | None:
	"""Quỹ tin ngoài khung 48 giờ của gói OA (V3-07); lỗi đếm không được làm hỏng khung soạn tin."""
	from omni_crm.channels.zalo import quota

	try:
		return quota.usage().as_dict()
	except Exception:
		frappe.logger("omni").warning("omni: đếm hạn mức Zalo lỗi", exc_info=True)
		return None


def _has_phone(lead: str) -> bool:
	row = frappe.db.get_value(LEAD, lead, ["mobile_no", "phone"], as_dict=True) or {}
	return any(row.values())


def _has_channel(lead: str, channel_type: str) -> bool:
	return bool(frappe.db.exists(IDENTITY, {"lead": lead, "channel_type": channel_type}))


@frappe.whitelist(methods=["POST"])
def send_reply(lead: str, channel: str, text: str, confirm_paid: int | str = 0) -> dict:
	"""Sale trả lời khách trên kênh Zalo OA từ Hộp thư.

	Trả {"status": "Queued"|"Blocked"|"confirm_required", ...}. Tin có thể bị tính phí chỉ gửi khi
	confirm_paid=1; email trả lời bằng trình soạn thư của CRM.
	"""
	lead = check_lead_write(lead)
	if channel == "Email":
		frappe.throw(_("Email được trả lời bằng trình soạn thư của CRM; hãy mở Lead trong CRM."))
	if channel != "Zalo OA":
		frappe.throw(_("Kênh {0} chưa hỗ trợ trả lời từ Hộp thư").format(channel))
	return send_zalo(lead, text, confirm_paid)


def send_zalo(lead: str, text: str, confirm_paid: int | str = 0, attachment: dict | None = None) -> dict:
	"""Gửi tin Sale qua Zalo OA của Lead (kiểm nội dung, từ chối liên hệ, khung gửi); người gọi PHẢI đã kiểm quyền ghi.

	`attachment` (V3-09): {"file_url", "kind", "name"} đã kiểm thuộc Lead (media_out.attachment_for_lead); có tệp thì chữ được trống.
	"""
	text = (text or "").strip()
	if not text and not attachment:
		frappe.throw(_("Nội dung tin nhắn đang trống"))
	if len(text) > outbound_pipeline.MAX_TEXT_CHARS:
		frappe.throw(_("Tin nhắn dài quá {0} ký tự").format(outbound_pipeline.MAX_TEXT_CHARS))
	target = _zalo_target(lead)
	if not target:
		frappe.throw(_("Khách này chưa có tài khoản Zalo liên kết với Lead"))
	from omni_crm.api import customer as customer_api

	if customer_api._person_flags(lead)["refused"]:  # V2-08 (N-2): từ chối / đã xóa dữ liệu (docs/14 mục 6)
		frappe.throw(
			_("Người này đã từ chối nhận liên hệ (hoặc đã bị xóa dữ liệu): không gửi tin nhắn từ hệ thống.")
		)
	identity_doc, channel_doc = target
	return queue_zalo_reply(lead, identity_doc, channel_doc, text, confirm_paid, attachment=attachment)


def queue_zalo_reply(
	lead: str,
	identity_doc: Any,
	channel_doc: Any,
	text: str,
	confirm_paid: int | str = 0,
	bot_draft: str | None = None,
	attachment: dict | None = None,
) -> dict:
	"""Kiểm khung gửi Zalo rồi xếp hàng tin của Sale (dùng chung cho trả lời tay và gửi nháp của bot).

	Trả {"status": "Queued"|"Blocked"|"confirm_required", ...}; `bot_draft` gắn vào tin ra khi gửi từ nháp.
	"""
	from omni_crm.channels.zalo import routing

	adapter = get_adapter("Zalo OA")
	ok, reason = adapter.can_send_now(identity_doc, channel_doc, source="Sale")
	if not ok:
		name = _record_blocked(channel_doc, identity_doc, lead, text, reason, attachment)
		return {"status": "Blocked", "outbound": name, "reason": reason}
	try:  # V3-07: ngoài 48 giờ thì NGƯỜI quyết định tiêu quỹ, máy không tự hạ loại tin
		route = routing.pick_message_kind(
			identity_doc,
			"reply",
			channel_doc=channel_doc,
			confirmed=bool(cint(confirm_paid)),
			user=frappe.session.user,
		)
	except routing.NeedsApproval as e:
		return {"status": "confirm_required", "warning": e.message_vi, "quota": _quota_safe()}
	except routing.RoutingError as e:
		name = _record_blocked(channel_doc, identity_doc, lead, text, e.message_vi, attachment)
		return {"status": "Blocked", "outbound": name, "reason": e.message_vi}
	name = outbound_pipeline.queue_outbound(
		OutboundRequest(
			channel=channel_doc.name,
			identity=identity_doc.name,
			text=text,
			reply_to_external_id=None,
			source="Sale",
			bot_draft=bot_draft,
			route=route,
			attachment=attachment,
		),
		user=frappe.session.user,
		confirmed_paid=bool(cint(confirm_paid)),
	)
	timeline.notify(lead, ["Zalo OA"])
	return {"status": "Queued", "outbound": name, "warning": reason or ""}


def _record_blocked(
	channel_doc: Any, identity_doc: Any, lead: str, text: str, reason: str, attachment: dict | None = None
) -> str:
	doc = frappe.get_doc(
		{
			**outbound_pipeline._attachment_fields(attachment),
			"doctype": OUTBOUND,
			"channel": channel_doc.name,
			"identity": identity_doc.name,
			"lead": lead,
			"text": text,
			"source": "Sale",
			"sent_by": frappe.session.user,
			"status": "Blocked",
			"last_error": reason,
			"is_sandbox": 1 if flags.sandbox() else 0,
		}
	)
	doc.insert(ignore_permissions=True)
	timeline.notify(lead, ["Zalo OA"])
	return doc.name


@frappe.whitelist(methods=["POST"])
def request_phone(lead: str, confirm_paid: int | str = 0) -> dict:
	"""Sale bấm gửi thẻ xin số điện thoại qua Zalo (V3-07: chỉ trong 48 giờ, khách chưa có số, chưa xin trong phiên chat).

	`confirm_paid` giữ cho tương thích, không còn tác dụng: thẻ xin số không bao giờ đi bằng quỹ tin ngoài 48 giờ.
	"""
	from omni_crm.channels.zalo import phone_request

	lead = check_lead_write(lead)
	target = _zalo_target(lead)
	if not target:
		frappe.throw(_("Khách này chưa có tài khoản Zalo liên kết với Lead"))
	identity_doc, channel_doc = target
	why = phone_request.ineligible_reason(identity_doc, channel_doc, manual=True)
	if why:
		frappe.throw(why)
	return phone_request.send(identity_doc, channel_doc, source="Sale", user=frappe.session.user)


STATUS_CACHE_KEY = "omni:system_status"
STATUS_CACHE_SECONDS = 30
STATUS_SLOW_SECONDS = 300


def _status_messages() -> list[str]:
	from omni_crm.monitoring.snapshot import build_snapshot

	snap = build_snapshot()
	out: list[str] = []
	for ch in snap["channels"]:
		if not ch["enabled"]:
			continue
		if ch["channel_type"] == "Zalo OA" and not snap["switches"]["sandbox_mode"]:
			left = ch["token_seconds_left"]
			if ch["last_error"] or (left is not None and left <= 0):
				out.append(_("Zalo đang mất kết nối, tin đến vẫn được lưu"))
		if not ch["outbound_enabled"]:
			out.append(_("Gửi tin qua {0} đang tạm tắt").format(ch["channel_type"]))
	age = snap["queue"]["oldest_new_age_seconds"]
	if age is not None and age > STATUS_SLOW_SECONDS:
		out.append(_("Hệ thống xử lý tin đang chậm, tin vẫn được lưu"))
	return list(dict.fromkeys(out))


@frappe.whitelist()
def system_status() -> dict[str, list[str]]:
	"""Khối "Tình trạng hệ thống" của Hộp thư: chỉ thông điệp cố định khi có sự cố kênh (không lộ lỗi thô)."""
	cache = frappe.cache()
	messages = cache.get_value(STATUS_CACHE_KEY)
	if messages is None:
		messages = _status_messages()
		cache.set_value(STATUS_CACHE_KEY, messages, expires_in_sec=STATUS_CACHE_SECONDS)
	return {"messages": messages}
