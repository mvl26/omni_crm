"""Bộ hẹn giờ của bot kịch bản v3 (K5; kịch bản mục 4.1, 4.2, 4.6, 4.7, C.1, C.5).

`run_due()` chạy mỗi 15 phút: nhận từng dòng `Omni Scheduled Message` đã đến hạn bằng một lệnh UPDATE có điều kiện
(đổi `Pending` -> `Sent` rồi commit ngay), nên hai lượt chạy chồng nhau không bao giờ gửi hai lần. Sau khi nhận, kiểm
lại mọi điều kiện "không nhắn nếu" (4.1, C.4), khung gửi chủ động (C.5), rồi chọn kênh bằng `channelpick.pick_channel`
(bảng 4.2). Email do nhân viên gửi tay (tạo CRM Task); ZNS chưa nối API nào.

Câu gửi khách luôn lấy từ Omni Script Node qua `send.send_node`; tệp này không chứa câu chữ gửi khách.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime

from omni_crm.script import channelpick, send, state, store, switch, timeutil

SCHED = "Omni Scheduled Message"
IDENTITY = "Omni Identity"
LEAD = "CRM Lead"
OUTBOUND = "Omni Outbound Message"
CHANNEL = "Omni Channel"

KINDS = (
	"followup_docs",
	"followup_quote",
	"demo_remind_1d",
	"demo_remind_1h",
	"demo_noshow",
	"silence_3d",
	"postdemo_1",
	"lost_3m",
)
DEMO_KINDS = ("demo_remind_1d", "demo_remind_1h", "demo_noshow")
REPLY_CANCELS = ("followup_docs", "followup_quote", "silence_3d")  # khách nhắn lại thì hủy
WINDOW_EXEMPT = ("demo_remind_1h",)  # C.5: riêng tin nhắc trước 1 giờ gửi đúng giờ
CHAT_ONLY = ("demo_remind_1h", "demo_noshow")  # sát giờ: không tạo việc gửi email
NURTURE_WHEN_DONE = ("silence_3d",)  # gửi xong nhắc im lặng -> "Chăm sóc lại" (C.1)
NURTURE_WHEN_NO_CHANNEL = ("followup_docs", "followup_quote", "silence_3d", "lost_3m")
SALES_KINDS = (
	"followup_docs",
	"followup_quote",
	"silence_3d",
	"postdemo_1",
	"lost_3m",
)  # tin chào bán (K6 chặn)
KEEP_STATE = ("silence_3d", "demo_remind_1d", "demo_remind_1h", "demo_noshow")  # không đổi ô đang đứng
DEFAULT_NODE = {
	"followup_docs": "NL_TAILIEU",
	"followup_quote": "NL_BAOGIA",
	"demo_remind_1d": "DEMO_NHAC_1NGAY",
	"demo_remind_1h": "DEMO_NHAC_1GIO",
	"demo_noshow": "DEMO_VANGMAT",
	"silence_3d": "NHAC3N_B12",
	"postdemo_1": "SD_CAMNHAN",
	"lost_3m": "HOITHAM_3THANG",
}
EMAIL_NODE = {"followup_docs": "EMAIL_NL_TAILIEU"}  # mẫu email có sẵn trong kho (4.5)
SILENCE_NODE = {"1": "NHAC3N_B12", "2": "NHAC3N_B12", "3": "NHAC3N_B3", "4": "NHAC3N_B4", "5": "NHAC3N_B5"}
SERVICE_SUFFIX = {"VATTU": "VATTU", "SUPPLYCORE": "PHANMEM", "SPD": "SPD"}
SERVICE_LABEL = {
	"VATTU": "cung ứng vật tư y tế",
	"SUPPLYCORE": "phần mềm SupplyCore",
	"SPD": "hệ thống SPD",
}
CLOSED_JOURNEY = ("Đồng ý mua – chờ ký hợp đồng", "Thua", "Không liên hệ")  # noqa: RUF001 (đúng option Select)
AFTER_DEMO_BOOKED = ("Đã hẹn demo", "Đã demo", "Thương lượng", *CLOSED_JOURNEY)
NURTURE = "Chăm sóc lại"
NO_CHANNEL_REASON = "Hết kênh liên hệ"
SILENCE_DAYS = 3
DOCS_FOLLOWUP_DAYS = 1  # 4.1: khoảng 1 ngày làm việc
QUOTE_FOLLOWUP_DAYS = 2  # 4.6: sau 2 ngày làm việc
LOST_FOLLOWUP_DAYS = 90
NOSHOW_AFTER = timedelta(minutes=15)
NOSHOW_WAIT = timedelta(hours=2)  # chờ nhân viên đánh dấu "Khách vắng" tối đa 2 giờ
DEMO_MINUTES = 30
SWEEP_LOOKBACK = timedelta(days=10)
BATCH = 200
MARKER_ACTIONS = {"silence_3d", "demo_remind_1d", "demo_remind_1h", "demo_noshow"}
_VAR = re.compile(r"\{(\w+)\}")
OPTIONAL_VARS = {"ten"}
LEAD_FIELDS = (
	"status",
	"converted",
	"first_name",
	"email",
	"mobile_no",
	"omni_services",
	"omni_journey_status",
	"omni_journey_need",
	"omni_do_not_contact",
	"omni_no_bot_followup",
	"omni_docs_sent_at",
	"omni_quote_sent_at",
	"omni_demo_at",
	"omni_demo_mode",
	"omni_demo_result",
)


def _now() -> datetime:
	return now_datetime()


# --- Tiện ích dùng chung với flows/demo.py ---------------------------------------------------------------------


def fast_for(identity: str | None) -> bool:
	"""Hẹn giờ rút ngắn (ngày -> phút): chỉ khi bật `script_fast_timers_for_test` VÀ định danh trong danh sách thử."""
	return bool(cint(switch.setting("script_fast_timers_for_test"))) and switch.is_test(identity)


def lead_row(lead: str | None) -> dict:
	"""Các trường Lead mà bộ hẹn giờ cần (dict rỗng nếu không có Lead)."""
	if not lead:
		return {}
	return frappe.db.get_value(LEAD, lead, list(LEAD_FIELDS), as_dict=True) or {}


def identity_of(lead: str | None) -> str | None:
	"""Định danh chat (Zalo OA) của Lead có tin vào gần nhất."""
	if not lead:
		return None
	rows = frappe.get_all(
		IDENTITY,
		filters={"lead": lead, "channel_type": "Zalo OA"},
		fields=["name"],
		order_by="last_inbound_at desc",
		limit=1,
	)
	return rows[0].name if rows else None


def current_service(identity: str | None, lead: str | None, row: dict | None = None) -> str:
	"""Mã dịch vụ đang tư vấn (`VATTU`/`SUPPLYCORE`/`SPD`/...): `script_state.service`, không có thì dịch vụ cuối trên Lead."""
	st = state.get(identity)["script_state"] if identity else {}
	svc = cstr(st.get("service")).strip().upper()
	if svc:
		return "SUPPLYCORE" if svc == "PHANMEM" else svc
	raw = (row if row is not None else lead_row(lead)).get("omni_services") or ""
	items = [s.strip().upper() for s in raw.split(",") if s.strip()]
	return items[-1] if items else ""


def variant(code: str, service: str) -> str:
	"""Mã ô biến thể theo dịch vụ (`NL_BAOGIA` -> `NL_BAOGIA_VATTU`) nếu ô đó có trong kho, không thì giữ nguyên."""
	suffix = SERVICE_SUFFIX.get(service)
	if suffix and frappe.db.exists(store.NODE, f"{code}_{suffix}"):
		return f"{code}_{suffix}"
	return code


def hour_label(at: datetime) -> str:
	"""Giờ dạng "9:00" (không có số 0 đứng đầu)."""
	return f"{at.hour}:{at.minute:02d}"


def day_label(day: date) -> str:
	"""Thứ và ngày dạng "Thứ Ba 18/11"."""
	name = timeutil.weekday_name(day)
	return f"{name[:1].upper()}{name[1:]} {day.strftime('%d/%m')}"


def demo_variables(demo_at: datetime | None, mode: str | None = None) -> dict:
	"""Biến `{thu_ngay}`, `{gio_bat_dau}`, `{gio_ket_thuc}`, `{gio}`, `{hinh_thuc}` của một lịch demo."""
	if not demo_at:
		return {}
	demo_at = get_datetime(demo_at)
	return {
		"thu_ngay": day_label(demo_at.date()),
		"gio_bat_dau": hour_label(demo_at),
		"gio_ket_thuc": hour_label(demo_at + timedelta(minutes=DEMO_MINUTES)),
		"gio": hour_label(demo_at),
		"hinh_thuc": mode or "",
	}


def variables_for(identity: str | None, lead: str | None, extra: dict | None = None) -> dict:
	"""Giá trị biến `{...}` cho tin gửi theo lịch (không có lượt tin của khách); `extra` ghi đè."""
	row = lead_row(lead)
	st = state.get(identity)["script_state"] if identity else {}
	service = current_service(identity, lead, row)
	quote = bool(row.get("omni_quote_sent_at")) or row.get("omni_journey_need") == "Báo giá"
	missing = [
		label
		for label, value in ((_("email"), row.get("email")), (_("số điện thoại"), row.get("mobile_no")))
		if not value
	]
	channel = send.channel_of(identity) if identity else None
	values = {
		"ten": row.get("first_name") or "",
		"dich_vu": SERVICE_LABEL.get(service, ""),
		"email": row.get("email") or "",
		"sdt": row.get("mobile_no") or "",
		"tai_lieu": st.get("tai_lieu") or _("tài liệu"),
		"tai_lieu_bao_gia": _("báo giá") if quote else (st.get("tai_lieu") or _("tài liệu")),
		"thong_tin_con_thieu": _(" và ").join(missing),
		"kenh_chat": frappe.db.get_value(CHANNEL, channel, "channel_type") if channel else "",
		**demo_variables(row.get("omni_demo_at"), row.get("omni_demo_mode")),
	}
	values.update(_engine_variables(identity, lead))
	values.update({k: v for k, v in (extra or {}).items() if v not in (None, "")})
	return values


def _engine_variables(identity: str | None, lead: str | None) -> dict:
	"""Biến do bộ chạy K2 tính (nếu đã có); lỗi hoặc chưa có thì trả rỗng và dùng giá trị của tệp này."""
	if not identity:
		return {}
	try:
		from omni_crm.script import engine
		from omni_crm.script.router import RouteContext

		ctx = RouteContext("", identity, lead, send.channel_of(identity), "", None, state=state.get(identity))
		values = engine.variables(ctx) or {}
	except Exception:
		return {}
	return {k: v for k, v in values.items() if v not in (None, "")}


def missing_variables(code: str, values: dict) -> list[str]:
	"""Biến có trong câu của ô `code` mà chưa có giá trị (trừ `{ten}` được phép trống)."""
	node = store.get_node(code) or {}
	names = set(_VAR.findall(node.get("text") or ""))
	return sorted(n for n in names if n not in OPTIONAL_VARS and values.get(n) in (None, ""))


def working_days_after(at: datetime, days: int, cfg: timeutil.Config | None = None) -> datetime:
	"""Cùng giờ sau `days` ngày làm việc, kéo vào khung gửi chủ động của ngày đó (4.1)."""
	cfg = cfg if cfg is not None else timeutil.load_config()
	day, left = at.date(), days
	for _i in range(timeutil.SCAN_DAYS * max(days, 1)):
		if left <= 0:
			break
		day += timedelta(days=1)
		if timeutil.is_working_day(day, cfg):
			left -= 1
	return clamp_proactive(datetime.combine(day, at.time()), cfg)


def clamp_proactive(at: datetime, cfg: timeutil.Config | None = None) -> datetime:
	"""`at` nếu trong khung 09:00-16:30; sau khung thì cuối khung cùng ngày; còn lại đầu khung kế tiếp."""
	cfg = cfg if cfg is not None else timeutil.load_config()
	if timeutil.in_proactive_window(at, cfg):
		return at
	last = timeutil.last_proactive_moment(at, cfg)
	if last and last.date() == at.date():
		return last
	return timeutil.next_proactive_slot(at, cfg)


# --- Đặt lịch / hủy lịch ---------------------------------------------------------------------------------------


def _dedupe_key(kind: str, owner: str | None, due_at: datetime, payload: dict) -> str:
	mark = cstr(payload.get("key")).strip() or get_datetime(due_at).date().isoformat()
	return f"{kind}:{owner or ''}:{mark}"[:140]


def schedule(
	kind: str,
	identity: str | None,
	lead: str | None,
	due_at: datetime,
	node: str | None,
	payload: dict | None = None,
) -> str | None:
	"""Đặt một tin hẹn giờ; trùng `dedupe_key` đang Pending thì trả dòng cũ, đã hủy thì mở lại, đã xử lý thì None."""
	if kind not in KINDS or not due_at or not (identity or lead):
		return None
	payload = dict(payload or {})
	key = _dedupe_key(kind, lead or identity, due_at, payload)
	old = frappe.db.get_value(SCHED, {"dedupe_key": key}, ["name", "status"], as_dict=True)
	if old:
		if old.status == "Pending":
			return old.name
		if old.status != "Cancelled":
			return None  # mỗi mốc chỉ xử lý một lần (4.2): đã gửi / đã bỏ qua / lỗi thì không đặt lại
		frappe.db.set_value(
			SCHED,
			old.name,
			{
				"status": "Pending",
				"due_at": due_at,
				"node": node,
				"payload": _dump(payload),
				"reason": None,
				"sent_at": None,
			},
		)
		return old.name
	doc = frappe.get_doc(
		{
			"doctype": SCHED,
			"kind": kind,
			"identity": identity,
			"lead": lead,
			"due_at": due_at,
			"status": "Pending",
			"dedupe_key": key,
			"node": store.normalize_code(node) or DEFAULT_NODE.get(kind),
			"payload": _dump(payload),
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		return frappe.db.get_value(SCHED, {"dedupe_key": key, "status": "Pending"}, "name")
	return doc.name


def cancel_for(lead: str | None, kinds: list | tuple | None = None, reason: str | None = None) -> int:
	"""Hủy các tin hẹn giờ còn Pending của Lead (tất cả, hoặc chỉ các `kinds`); trả số dòng đã hủy."""
	if not lead:
		return 0
	filters: dict = {"lead": lead, "status": "Pending"}
	if kinds:
		filters["kind"] = ["in", list(kinds)]
	elif not cint(frappe.db.get_value(LEAD, lead, "omni_do_not_contact")):
		filters["kind"] = [
			"!=",
			"lost_3m",
		]  # hỏi thăm 3 tháng chỉ hủy khi nêu rõ loại hoặc Lead "Không liên hệ"
	return _cancel(filters, reason or _("Đã hủy theo luồng kịch bản"))


def _cancel(filters: dict, reason: str) -> int:
	names = frappe.get_all(SCHED, filters=filters, pluck="name")
	for name in names:
		frappe.db.set_value(SCHED, name, {"status": "Cancelled", "reason": reason})
	return len(names)


def on_inbound(identity: str | None, lead: str | None) -> int:
	"""Khách vừa nhắn: hủy tin nhắn lại và tin nhắc im lặng đang chờ (4.1, C.1); nhắc lịch demo giữ nguyên."""
	reason = _("Khách đã nhắn lại")
	count = cancel_for(lead, REPLY_CANCELS, reason)
	if identity:
		count += _cancel(
			{"identity": identity, "status": "Pending", "kind": ["in", list(REPLY_CANCELS)]}, reason
		)
	return count


def on_docs_sent(lead: str) -> str | None:
	"""Nhân viên bấm "Đã gửi tài liệu": hẹn tin nhắn lại sau khoảng 1 ngày làm việc (4.1)."""
	return _schedule_followup("followup_docs", lead, DOCS_FOLLOWUP_DAYS)


def on_quote_sent(lead: str) -> str | None:
	"""Đã gửi báo giá: hẹn tin hỏi lại sau 2 ngày làm việc (4.6)."""
	return _schedule_followup("followup_quote", lead, QUOTE_FOLLOWUP_DAYS)


def _schedule_followup(kind: str, lead: str, days: int) -> str | None:
	identity = identity_of(lead)
	if not identity or not switch.enabled_for(identity):
		return None  # bot kịch bản chưa chạy với khách này
	now = _now()
	if fast_for(identity):
		due = target = now + timedelta(minutes=days)
	else:
		cfg = timeutil.load_config()
		due = target = working_days_after(now, days, cfg)
		row = lead_row(lead)
		choice = channelpick.pick_channel(
			now,
			_last_inbound(identity),
			_free_hours(identity),
			target,
			bool(row.get("email")),
			bool(row.get("mobile_no")),
			_zns_on(),
			start_at=now,
			cfg=cfg,
		)
		if choice["kind"] == "chat" and choice["early"] and choice["at"]:
			due = choice["at"]  # 4.2 thứ tự 2: nhắn chat sớm trước khi hết hạn chat
	payload = {"key": target.date().isoformat(), "start_at": now.isoformat(sep=" ", timespec="seconds")}
	return schedule(kind, identity, lead, due, DEFAULT_NODE[kind], payload)


def schedule_silence(identity: str, lead: str | None, now: datetime | None = None) -> str | None:
	"""Hẹn tin nhắc im lặng (C.1) nếu tin kịch bản cuối của bot là câu hỏi/nút mà khách chưa trả lời; mỗi lần im một tin."""
	now = now or _now()
	ident = (
		frappe.db.get_value(
			IDENTITY, identity, ["script_last_bot_at", "last_inbound_at", "script_step"], as_dict=True
		)
		or {}
	)
	bot_at, in_at = ident.get("script_last_bot_at"), ident.get("last_inbound_at")
	if not bot_at or (in_at and get_datetime(in_at) >= get_datetime(bot_at)):
		return None
	last = frappe.get_all(
		OUTBOUND,
		filters={"identity": identity, "script_node": ["is", "set"]},
		fields=["script_node", "buttons", "text"],
		order_by="creation desc",
		limit=1,
	)
	if not last or not (last[0].buttons or "?" in (last[0].text or "")):
		return None
	node = store.get_node(last[0].script_node) or {}
	if set(_actions_of(node)) & MARKER_ACTIONS:
		return None  # tin nhắc và tin nhắc lịch demo không tự sinh thêm tin nhắc
	row = lead_row(lead)
	if row.get("omni_journey_status") in (*CLOSED_JOURNEY, "Đã hẹn demo"):
		return None
	step = cstr(node.get("step") or ident.get("script_step")).strip()
	code = SILENCE_NODE.get(step)
	if not code:
		return None
	bot_at = get_datetime(bot_at)
	if fast_for(identity):
		due = bot_at + timedelta(minutes=SILENCE_DAYS)
	else:
		due = timeutil.next_proactive_slot(bot_at + timedelta(days=SILENCE_DAYS))
	mark = get_datetime(in_at).isoformat(timespec="seconds") if in_at else "none"
	return schedule("silence_3d", identity, lead, due, code, {"key": mark})


def _actions_of(node: dict) -> list[str]:
	return [a.split(":", 1)[0].strip().lower() for a in (node.get("action") or "").split(";") if a.strip()]


def _sweep_silence(now: datetime) -> int:
	"""Tìm khách đang im sau câu hỏi của bot và đặt tin nhắc (không cần móc vào `send.py`)."""
	everyone = bool(cint(switch.setting("script_bot_enabled")))
	tests = switch.test_identities()
	if not everyone and not tests:
		return 0
	filters: dict = {"script_last_bot_at": [">=", now - SWEEP_LOOKBACK], "lead": ["is", "set"]}
	if not everyone:
		filters["name"] = ["in", list(tests)]
	rows = frappe.get_all(IDENTITY, filters=filters, fields=["name", "lead"], limit=1000)
	made = 0
	for r in rows:
		if schedule_silence(r.name, r.lead, now):
			made += 1
	frappe.db.commit()
	return made


# --- Chạy tin đến hạn ------------------------------------------------------------------------------------------


def run_due() -> dict:
	"""Gửi các tin hẹn giờ đã đến hạn (cron 15 phút); trả thống kê {trạng thái: số dòng}."""
	stats: dict = {}
	now = _now()
	try:
		_sweep_silence(now)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: quét khách im lặng lỗi")
	names = frappe.get_all(
		SCHED,
		filters={"status": "Pending", "due_at": ["<=", now]},
		order_by="due_at asc",
		pluck="name",
		limit=BATCH,
	)
	for name in names:
		if not _claim(name):
			continue  # lượt chạy khác đã nhận dòng này
		try:
			status, reason, extra = _process(name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: tin hẹn giờ lỗi", message=f"scheduled={name}")
			status, reason, extra = "Failed", _("Lỗi khi xử lý, xem Error Log"), {}
		_finish(name, status, reason, extra)
		stats[status] = stats.get(status, 0) + 1
	return stats


def _claim(name: str) -> bool:
	"""Nhận một dòng Pending bằng UPDATE có điều kiện rồi commit; False nếu lượt khác đã nhận."""
	frappe.db.sql(
		f"update `tab{SCHED}` set status='Sent', sent_at=%s, modified=%s where name=%s and status='Pending'",
		(_now(), now_datetime(), name),
	)
	claimed = frappe.db.sql("select row_count()")[0][0] == 1
	frappe.db.commit()
	return claimed


def _finish(name: str, status: str, reason: str | None, extra: dict | None = None) -> None:
	values: dict = {"status": status, "reason": reason, **(extra or {})}
	if status != "Sent":
		values.setdefault("sent_at", None)
	frappe.db.set_value(SCHED, name, values)
	frappe.db.commit()


def _process(name: str) -> tuple[str, str | None, dict]:
	"""Xử lý một dòng đã nhận: kiểm điều kiện dừng, khung giờ, chọn kênh, gửi. Trả (status, reason, giá trị ghi thêm)."""
	row = frappe.db.get_value(
		SCHED, name, ["kind", "identity", "lead", "due_at", "node", "payload", "creation"], as_dict=True
	)
	now = _now()
	lead = lead_row(row.lead)
	ident = (
		frappe.db.get_value(IDENTITY, row.identity, ["last_inbound_at", "script_last_bot_at"], as_dict=True)
		or {}
	)
	payload = store.as_dict(row.payload)
	blocked = _blocked(row, lead, ident, payload, now)
	if blocked:
		return blocked
	fast = fast_for(row.identity)
	cfg = timeutil.load_config()
	if row.kind not in WINDOW_EXEMPT and not fast and not timeutil.in_proactive_window(now, cfg):
		if row.kind == "demo_noshow":
			return "Skipped", _("Ngoài khung gửi tin chủ động"), {}
		return "Pending", None, {"due_at": timeutil.next_proactive_slot(now, cfg)}
	if row.kind == "lost_3m":
		return _send_lost_3m(row.lead, now)
	choice = channelpick.pick_channel(
		now,
		ident.get("last_inbound_at") and get_datetime(ident.get("last_inbound_at")),
		_free_hours(row.identity),
		now,
		bool(lead.get("email")),
		bool(lead.get("mobile_no")),
		_zns_on(),
		cfg=cfg,
	)
	return _deliver(row, lead, payload, choice, now)


def _blocked(
	row, lead: dict, ident: dict, payload: dict, now: datetime
) -> tuple[str, str | None, dict] | None:
	"""Điều kiện "không nhắn nếu" (4.1, C.4) kiểm ngay trước khi gửi; None nếu được gửi."""
	if not row.identity:
		return "Skipped", _("Không có định danh chat"), {}
	why = send.blocked_reason(
		row.identity
	)  # công tắc, tạm dừng/nhân viên đang trò chuyện, không liên hệ, rác
	if why:
		return "Skipped", why, {}
	if cint(lead.get("converted")) or lead.get("status") == "Converted":
		return "Skipped", _("Lead đã là khách hàng"), {}
	if cint(lead.get("omni_no_bot_followup")):
		return "Skipped", _("Lead tắt nhắn lại của bot"), {}
	kind, journey = row.kind, lead.get("omni_journey_status")
	if kind in SALES_KINDS and not _sales_allowed(row.lead):
		return "Skipped", _("Không được gửi tin chào bán cho Lead này"), {}
	if kind in REPLY_CANCELS:
		last_in = ident.get("last_inbound_at")
		if last_in and get_datetime(last_in) > get_datetime(row.creation):
			return "Cancelled", _("Khách đã nhắn lại"), {}
	if kind == "followup_docs" and journey in AFTER_DEMO_BOOKED:
		return "Cancelled", _("Lead đã sang bước sau"), {}
	if kind == "followup_quote" and journey in ("Thương lượng", *CLOSED_JOURNEY):
		return "Cancelled", _("Lead đã có kết quả"), {}
	if kind == "silence_3d":
		if journey in CLOSED_JOURNEY:
			return "Cancelled", _("Lead đã chốt"), {}
		bot_at = ident.get("script_last_bot_at")
		if bot_at and not fast_for(row.identity):
			again = timeutil.next_proactive_slot(get_datetime(bot_at) + timedelta(days=SILENCE_DAYS))
			if again > now:
				return "Pending", None, {"due_at": again}  # bot vừa nhắn thêm: tính lại 3 ngày
	if kind in DEMO_KINDS:
		return _demo_blocked(kind, row, lead, payload, now)
	return None


def _demo_blocked(
	kind: str, row, lead: dict, payload: dict, now: datetime
) -> tuple[str, str | None, dict] | None:
	demo_at, planned = lead.get("omni_demo_at"), payload.get("demo_at")
	if not demo_at or (planned and get_datetime(planned) != get_datetime(demo_at)):
		return "Cancelled", _("Lịch demo đã đổi hoặc hủy"), {}
	result = lead.get("omni_demo_result")
	if kind != "demo_noshow":
		return ("Cancelled", _("Buổi demo đã có kết quả"), {}) if result else None
	if result == "Khách vắng":
		return None
	if result:
		return "Cancelled", _("Khách không vắng mặt"), {}
	if now - get_datetime(row.due_at) > NOSHOW_WAIT:
		return "Skipped", _("Nhân viên không đánh dấu khách vắng"), {}
	return "Pending", None, {}  # chờ nhân viên đánh dấu "Khách vắng" (xét lại ở lượt sau)


def _deliver(row, lead: dict, payload: dict, choice: dict, now: datetime) -> tuple[str, str | None, dict]:
	kind = row.kind
	service = current_service(row.identity, row.lead, lead)
	code = variant(_node_code(row, lead, now), service)
	values = variables_for(row.identity, row.lead, {**payload, **store.as_dict(payload.get("vars"))})
	if choice["kind"] == "chat":
		lacking = missing_variables(code, values)
		if lacking:
			return "Skipped", _("Thiếu biến: {0}").format(", ".join(lacking)), {}
		sent = send.send_node(row.identity, code, values, purpose="followup")
		if not sent:
			return "Failed", _("Không xếp được tin chat (ô {0})").format(code), {}
		if kind not in KEEP_STATE:
			state.set_node(row.identity, code, (store.get_node(code) or {}).get("step") or None)
		_after_sent(kind, row.lead)
		return "Sent", _("Chat"), {"node": code, "sent_at": now}
	if choice["kind"] == "email_task" and kind not in CHAT_ONLY:
		task = _email_task(kind, row, service, code, values, now)
		if not task:
			return "Failed", _("Không tạo được việc gửi email"), {}
		_after_sent(kind, row.lead)
		return "Sent", _("Email: nhân viên gửi tay (việc {0})").format(task), {"sent_at": now}
	if choice["kind"] == "zns" and kind not in CHAT_ONLY:
		# VERIFY: API ZNS và mẫu tin chưa được xác nhận/đăng ký với Zalo -> không gọi API nào, không tạo gì.
		return "Skipped", _("ZNS chưa nối (cần xác nhận mẫu và API)"), {}
	if kind in NURTURE_WHEN_NO_CHANNEL:
		state.set_journey(row.lead, NURTURE)
	return "Skipped", _(NO_CHANNEL_REASON), {}


def _sales_allowed(lead: str | None) -> bool:
	"""Chốt chặn tin chào bán của K6 (`postdemo.sales_allowed`); phần đó chưa gộp thì coi là được."""
	try:
		from omni_crm.script.flows import postdemo
	except ImportError:
		return True
	return bool(postdemo.sales_allowed(lead))


def _send_lost_3m(lead: str | None, now: datetime) -> tuple[str, str | None, dict]:
	"""Tin hỏi thăm sau 3 tháng do K6 gửi (`postdemo.send_lost_3m`), không gửi ô HOITHAM_3THANG qua chat ở đây."""
	try:
		from omni_crm.script.flows import postdemo
	except ImportError:
		return "Skipped", _("Chưa có luồng sau demo (postdemo)"), {}
	postdemo.send_lost_3m(lead)
	return "Sent", _("Hỏi thăm 3 tháng (postdemo)"), {"sent_at": now}


def _node_code(row, lead: dict, now: datetime) -> str:
	code = store.normalize_code(row.node) or DEFAULT_NODE.get(row.kind, "")
	if row.kind == "followup_docs" and code == "NL_TAILIEU":
		sent_at = lead.get("omni_docs_sent_at") or store.as_dict(row.payload).get("start_at")
		if sent_at and get_datetime(sent_at).date() != now.date() - timedelta(days=1):
			return "NL_TAILIEU_VUAROI"  # 4.3: không phải "hôm qua" thì dùng câu "Vừa rồi"
	return code


def _after_sent(kind: str, lead: str | None) -> None:
	if kind in NURTURE_WHEN_DONE:
		state.set_journey(lead, NURTURE)


def _email_task(kind: str, row, service: str, code: str, values: dict, now: datetime) -> str | None:
	"""Tạo việc cho nhân viên gửi email tay (tự gửi email nằm sau `script_email_enabled`, chưa nối)."""
	try:
		from omni_crm.script import tasks
	except ImportError:
		return None
	template = variant(EMAIL_NODE.get(kind, code), service)
	body = store.fill((store.get_node(template) or {}).get("text") or "", values)
	title = _("Gửi email cho khách thay tin bot ({0})").format(kind)
	description = _("Đã hết hạn nhắn chat. Nội dung gợi ý:") + "\n\n" + body
	return tasks.create_task(row.lead, service or "KHAC", title, description, due=timeutil.task_due(now))


def _last_inbound(identity: str | None) -> datetime | None:
	value = frappe.db.get_value(IDENTITY, identity, "last_inbound_at") if identity else None
	return get_datetime(value) if value else None


def _free_hours(identity: str | None) -> float | None:
	channel = send.channel_of(identity) if identity else None
	return flt(frappe.db.get_value(CHANNEL, channel, "zalo_cs_free_hours")) if channel else None


def _zns_on() -> bool:
	return bool(
		cint(switch.setting("script_zns_enabled")) and cstr(switch.setting("script_zns_template_id")).strip()
	)


def _dump(payload: dict) -> str | None:
	return json.dumps(payload, ensure_ascii=False, default=str) if payload else None
