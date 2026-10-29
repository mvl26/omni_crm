"""API hộp thư cho sale: danh sách hội thoại, đọc hội thoại (gửi trả lời và duyệt nháp thêm ở D1-09)."""

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
PHONE_REQUEST_COOLDOWN_DAYS = 7  # docs/06 mục 6
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
		],
		order_by="creation desc",
		limit_page_length=THREAD_LIMIT,
	)
	names = {
		r.name: r.display_name
		for r in frappe.get_all(IDENTITY, filters={"lead": lead}, fields=["name", "display_name"])
	}
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
	for c in call_rows:
		items.append(_call_item(c))
	# Tin Sale trả lời ngoài hệ thống (oa_echo) có cả Omni Outbound Message cùng mã tin: chỉ hiện bản echo.
	echo_ids = {m.external_message_id for m in inbound if m.event_type == "oa_echo" and m.external_message_id}
	for o in outbound:
		if o.external_message_id and o.external_message_id in echo_ids:
			continue
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
				"error": o.last_error if o.status in ("Failed", "Blocked") else None,
				"queued": o.status == "Queued",
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
	return {
		"lead": info,
		"score": score,  # {score, reasons, hot} hoặc None khi chưa chấm (D2-06)
		"deal": deal,
		"crm_url": f"/crm/deals/{deal}" if deal else f"/crm/leads/{lead}",
		"identities": frappe.get_all(
			IDENTITY, filters={"lead": lead}, fields=["name", "channel_type", "display_name", "is_follower"]
		),
		"messages": items,
		"truncated": max(len(inbound), len(outbound), len(call_rows)) >= THREAD_LIMIT,
		"compose": compose_state(lead),
	}


def _duration_text(seconds: int | None) -> str:
	minutes, secs = divmod(max(0, int(seconds or 0)), 60)
	if not minutes:
		return _("{0} giây").format(secs)
	return _("{0} phút {1} giây").format(minutes, secs) if secs else _("{0} phút").format(minutes)


def _call_item(c: Any) -> dict:
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
	agent = frappe.db.get_value("User", c.agent, "full_name") or c.agent if c.agent else None
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
	state["zalo"] = {
		"can_send": bool(ok),
		"paid": bool(ok and reason),  # được gửi nhưng có thể tính phí: cần xác nhận
		"message": reason or "",
		"can_request_phone": bool(ok and not has_phone and _phone_request_due(identity_doc)),
		"has_phone": has_phone,
		"sandbox": bool(flags.sandbox()),
	}
	return state


def _has_phone(lead: str) -> bool:
	row = frappe.db.get_value(LEAD, lead, ["mobile_no", "phone"], as_dict=True) or {}
	return any(row.values())


def _has_channel(lead: str, channel_type: str) -> bool:
	return bool(frappe.db.exists(IDENTITY, {"lead": lead, "channel_type": channel_type}))


def _phone_request_due(identity_doc: Any) -> bool:
	last = identity_doc.get("phone_requested_at")
	return not last or get_datetime(last) < frappe.utils.add_to_date(
		frappe.utils.now_datetime(), days=-PHONE_REQUEST_COOLDOWN_DAYS
	)


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
	text = (text or "").strip()
	if not text:
		frappe.throw(_("Nội dung tin nhắn đang trống"))
	if len(text) > outbound_pipeline.MAX_TEXT_CHARS:
		frappe.throw(_("Tin nhắn dài quá {0} ký tự").format(outbound_pipeline.MAX_TEXT_CHARS))
	target = _zalo_target(lead)
	if not target:
		frappe.throw(_("Khách này chưa có tài khoản Zalo liên kết với Lead"))
	identity_doc, channel_doc = target
	adapter = get_adapter("Zalo OA")
	ok, reason = adapter.can_send_now(identity_doc, channel_doc, source="Sale")
	if not ok:
		name = _record_blocked(channel_doc, identity_doc, lead, text, reason)
		return {"status": "Blocked", "outbound": name, "reason": reason}
	if reason and not cint(confirm_paid):
		return {"status": "confirm_required", "warning": reason}
	name = outbound_pipeline.queue_outbound(
		OutboundRequest(
			channel=channel_doc.name,
			identity=identity_doc.name,
			text=text,
			reply_to_external_id=None,
			source="Sale",
		),
		user=frappe.session.user,
		confirmed_paid=bool(cint(confirm_paid)),
	)
	timeline.notify(lead, ["Zalo OA"])
	return {"status": "Queued", "outbound": name, "warning": reason or ""}


def _record_blocked(channel_doc: Any, identity_doc: Any, lead: str, text: str, reason: str) -> str:
	doc = frappe.get_doc(
		{
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
	"""Gửi tin yêu cầu thông tin (xin SĐT) qua Zalo; chỉ khi Lead chưa có SĐT và 7 ngày chưa xin (khung có phí cần confirm_paid)."""
	lead = check_lead_write(lead)
	if _has_phone(lead):
		frappe.throw(_("Lead đã có số điện thoại"))
	target = _zalo_target(lead)
	if not target:
		frappe.throw(_("Khách này chưa có tài khoản Zalo liên kết với Lead"))
	identity_doc, channel_doc = target
	ok, reason = get_adapter("Zalo OA").can_send_now(identity_doc, channel_doc, source="Sale")
	if not ok:
		frappe.throw(reason)
	if reason and not cint(confirm_paid):  # khung có phí: giống send_reply, cần sale xác nhận
		return {"status": "confirm_required", "warning": reason}
	previous = _reserve_phone_request(
		identity_doc.name
	)  # giữ chỗ trước khi gọi API: bấm hai lần không gửi hai tin
	if previous is False:
		frappe.throw(_("Đã xin số điện thoại trong {0} ngày qua").format(PHONE_REQUEST_COOLDOWN_DAYS))
	frappe.db.commit()  # nhả khóa dòng trước khi gọi API ngoài
	try:
		message_id, shown = get_adapter("Zalo OA").request_user_info(identity_doc, channel_doc)
	except Exception as e:
		frappe.db.set_value(
			IDENTITY, identity_doc.name, "phone_requested_at", previous, update_modified=False
		)
		frappe.db.commit()
		message = getattr(e, "message_vi", None) or _("Không gửi được yêu cầu, thử lại sau")
		frappe.logger("omni").warning("omni: xin SĐT lỗi: %s", redact(str(e))[:200])
		frappe.throw(message)
	doc = frappe.get_doc(
		{
			"doctype": OUTBOUND,
			"channel": channel_doc.name,
			"identity": identity_doc.name,
			"lead": lead,
			"text": shown,
			"source": "Sale",
			"sent_by": frappe.session.user,
			"status": "Sent",
			"sent_at": frappe.utils.now_datetime(),
			"external_message_id": None if flags.sandbox() else message_id,
			"is_sandbox": 1 if flags.sandbox() else 0,
		}
	)
	doc.insert(ignore_permissions=True)
	if not flags.sandbox() and message_id:  # echo có thể tới trước khi ta ghi mã tin (Z9)
		outbound_pipeline.drop_echo_duplicate(doc, message_id)
	timeline.append_outbound(lead_bridge.get_deal_for_lead(lead) or lead, doc.name)
	timeline.notify(lead, ["Zalo OA"])
	return {"status": "Sent", "outbound": doc.name, "sandbox": bool(flags.sandbox())}


def _reserve_phone_request(identity: str) -> Any:
	"""Ghi phone_requested_at = bây giờ dưới khóa dòng; trả giá trị cũ (có thể None) hoặc False nếu chưa đến hạn."""
	last = frappe.db.get_value(IDENTITY, identity, "phone_requested_at", for_update=True)
	if last and get_datetime(last) >= frappe.utils.add_to_date(
		frappe.utils.now_datetime(), days=-PHONE_REQUEST_COOLDOWN_DAYS
	):
		frappe.db.rollback()
		return False
	frappe.db.set_value(
		IDENTITY, identity, "phone_requested_at", frappe.utils.now_datetime(), update_modified=False
	)
	return last


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
