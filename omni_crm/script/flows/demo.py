"""Đặt lịch demo (K5; kịch bản mục 4.4), dùng chung cho mọi nút [Đặt lịch demo].

Luồng: hình thức -> 3 khung trống của người demo trong 3 ngày làm việc tới -> (Giờ khác: khách gõ) -> số người tham dự
-> xác nhận. Xác nhận tạo `Event` cho người demo, ghi Lead (`omni_demo_at/_mode/_attendees`), hành trình
"Đã hẹn demo", đặt 3 tin hẹn giờ (nhắc 1 ngày, nhắc 1 giờ, vắng mặt +15 phút) và tạo việc cho người demo gửi lời mời
Teams (chưa nối Microsoft 365). Câu gửi khách lấy từ Omni Script Node; tệp này không chứa câu chữ gửi khách.

Các nút `DEMO_*` cần tính lịch được nhận ở tầng router (`router.register("DEMO_", ...)`) để không phụ thuộc thứ tự
"gửi câu rồi mới chạy action" của bộ chạy; các nút còn lại chuyển cho `engine.run_node`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import cint, cstr, get_datetime, now_datetime

from omni_crm.script import engine, router, scheduler, send, state, store, timeutil, validate
from omni_crm.script.router import RouteContext

LEAD = "CRM Lead"
EVENT = "Event"
FLOW = "demo"
WAIT_SLOTS = "slots"  # đã gửi danh sách khung giờ, chờ khách bấm hoặc gõ giờ
DEMO_SERVICES = ("SUPPLYCORE", "SPD")
SLOT_COUNT = 3
SLOT_DAYS = 3  # 3 ngày làm việc tới
SEARCH_DAYS = 10  # tìm khung trống gần nhất tối đa 10 ngày làm việc
DURATION = timedelta(minutes=scheduler.DEMO_MINUTES)
LUNCH = (time(12, 0), time(13, 30))  # không đề xuất khung bắt đầu trong giờ nghỉ trưa (giả định, chờ chốt)
DEFAULT_EVENT_LENGTH = timedelta(hours=1)  # Event không có giờ kết thúc
MAX_ATTENDEES = 500
NODE_FORMAT = "DEMO_HINHTHUC"
NODE_SLOTS = "DEMO_KHUNGGIO"
NODE_OTHER_TIME = "DEMO_GIOKHAC"
NODE_BUSY = "DEMO_GIOBAN"
NODE_OUTSIDE = "DEMO_NGOAIGIO"  # giờ khách muốn là ngày nghỉ / ngoài giờ / giờ nghỉ trưa
NODE_TIME_AGAIN = "DEMO_GIOKHAC_LAI"  # khách mới nêu ngày hoặc giờ: xin đủ cả hai
NODE_HAS_BOOKING = "DEMO_DACOLICH"  # khách đã có lịch demo mà mở đặt lịch lần nữa
NODE_ATTENDEES = "DEMO_SONGUOI"
NODE_ASK_EMAIL = "DEMO_HOI_EMAIL"
NODE_CONFIRM = (
	"DEMO_XACNHAN_THUCONG",
	"DEMO_XACNHAN",
	"DEMO_XACNHAN_KHONGEMAIL",  # Lead chưa có email (seed/nodes_k5.json)
)  # chưa nối Microsoft 365: ưu tiên bản "nhân viên gửi lời mời"
NODE_CANCEL = "DEMO_HUY"
NODE_CANCEL_THANKS = "DEMO_HUY_CAMON"
NODE_NO_DEMO = "DEMO_VATTU"  # Vật tư không có demo (2.5)
STATE_KEYS = (
	"demo_mode",
	"demo_slots",
	"demo_slots_msg",
	"demo_slots_at",
	"demo_at",
	"demo_attendees",
	"demo_wait",
	"demo_email_asked",
	"demo_att_asked",
	"demo_time_asked",
)
MIN_AHEAD = timedelta(hours=2)  # không nhận lịch bắt đầu trong vòng 2 giờ tới
MAX_AHEAD = timedelta(days=45)  # không nhận lịch xa hơn 45 ngày
SLOTS_TTL = timedelta(hours=24)  # nút khung giờ cũ hơn 24 giờ thì đề xuất lại
SLOT_NODES = (NODE_SLOTS, NODE_BUSY)
LOCK_SECONDS = 30
TASK = "CRM Task"
TASK_OPEN = ("Backlog", "Todo", "In Progress")
JOURNEY_BOOKED = "Đã hẹn demo"
JOURNEY_AFTER_CANCEL = "Đang tư vấn"
ALERT_KIND = "lịch demo của bot thay đổi"


def _now() -> datetime:
	return now_datetime()


def _st(identity: str) -> dict:
	return state.get(identity)["script_state"]


def _iso(at: datetime) -> str:
	return at.isoformat(sep=" ", timespec="minutes")


# --- Lịch trống ------------------------------------------------------------------------------------------------


def demo_user(service: str | None) -> str | None:
	"""Người demo của dịch vụ (bảng người nhận `DEMO_SUPPLYCORE`/`DEMO_SPD`); chưa cấu hình trả None."""
	if not service:
		return None
	try:
		from omni_crm.script import tasks

		return tasks.recipient(f"DEMO_{service}")
	except ImportError:
		return None


def slot_label(at: datetime) -> str:
	"""Nhãn nút của một khung demo, dạng "Thứ Ba 18/11, 9:00" (tối đa 20 ký tự)."""
	return f"{scheduler.day_label(at.date())}, {scheduler.hour_label(at)}"


def busy_ranges(user: str | None, start: datetime, end: datetime, lead: str | None = None) -> list[tuple]:
	"""Các khoảng bận trong [start, end): Event đang mở của người demo; chưa có người demo thì lịch demo của Lead khác."""
	if not user:
		rows = frappe.get_all(
			LEAD,
			filters={"omni_demo_at": ["between", [start - DURATION, end]], "name": ["!=", lead or ""]},
			pluck="omni_demo_at",
		)
		return [(get_datetime(at), get_datetime(at) + DURATION) for at in rows]
	rows = frappe.get_all(
		EVENT,
		filters={"status": "Open", "owner": user, "starts_on": ["<", end]},
		fields=["starts_on", "ends_on", "all_day"],
		order_by="starts_on desc",
		limit=500,
	)
	out = []
	for r in rows:
		begin = get_datetime(r.starts_on)
		finish = get_datetime(r.ends_on) if r.ends_on else begin + DEFAULT_EVENT_LENGTH
		if cint(r.all_day):
			begin = datetime.combine(begin.date(), time.min)
			finish = datetime.combine(finish.date(), time.max)
		if finish > start and begin < end:
			out.append((begin, finish))
	return out


def _demo_day(day: date, cfg: timeutil.Config) -> bool:
	"""Ngày hẹn demo được: ngày làm việc thứ Hai - thứ Sáu (4.1/C.5), kể cả khi cấu hình có giờ thứ Bảy."""
	return day.weekday() < 5 and timeutil.is_working_day(day, cfg)


def _grid(day: date, cfg: timeutil.Config) -> list[datetime]:
	"""Các giờ bắt đầu có thể hẹn trong ngày làm việc `day` (giờ chẵn, trong giờ làm việc, tránh giờ nghỉ trưa)."""
	if not _demo_day(day, cfg):
		return []
	out = []
	for hour in range(24):
		at = datetime.combine(day, time(hour, 0))
		if LUNCH[0] <= at.time() < LUNCH[1]:
			continue
		if timeutil.in_hours(at, cfg) and timeutil.in_hours(at + DURATION, cfg):
			out.append(at)
	return out


def in_booking_range(at: datetime, now: datetime | None = None) -> bool:
	"""Giờ bắt đầu cách hiện tại ít nhất 2 giờ và không quá 45 ngày."""
	now = now or _now()
	return now + MIN_AHEAD <= at <= now + MAX_AHEAD


@contextmanager
def slot_lock(user: str | None, at: datetime) -> Iterator[bool]:
	"""Khóa cache theo (người demo, khung giờ) quanh bước kiểm trống + chốt; trả False nếu người khác đang giữ."""
	key = frappe.cache().make_key(f"omni:script:demo:{user or '-'}:{_iso(at)}")
	lock = frappe.cache().lock(key, timeout=LOCK_SECONDS, blocking_timeout=3)
	got = lock.acquire()
	try:
		yield bool(got)
	finally:
		if got:
			try:
				lock.release()
			except Exception:  # khóa đã hết hạn trong lúc xử lý
				pass


def is_free(
	at: datetime,
	user: str | None,
	now: datetime | None = None,
	cfg: timeutil.Config | None = None,
	lead: str | None = None,
) -> bool:
	"""Khung demo bắt đầu lúc `at` hợp lệ: ở tương lai, ngày làm việc, trong giờ làm việc, không trùng lịch bận."""
	cfg = cfg if cfg is not None else timeutil.load_config()
	now = now or _now()
	if not in_booking_range(at, now) or not _demo_day(at.date(), cfg):
		return False
	if not (timeutil.in_hours(at, cfg) and timeutil.in_hours(at + DURATION, cfg)):
		return False
	if at.time() < LUNCH[1] and (at + DURATION).time() > LUNCH[0]:
		return False  # chạm giờ nghỉ trưa
	return not busy_ranges(user, at, at + DURATION, lead)


def outside_hours(at: datetime, cfg: timeutil.Config | None = None) -> bool:
	"""Giờ khách muốn rơi vào ngày nghỉ, ngoài giờ làm việc hoặc giờ nghỉ trưa (khác với "đã kín lịch")."""
	cfg = cfg if cfg is not None else timeutil.load_config()
	if not _demo_day(at.date(), cfg):
		return True
	if not (timeutil.in_hours(at, cfg) and timeutil.in_hours(at + DURATION, cfg)):
		return True
	return at.time() < LUNCH[1] and (at + DURATION).time() > LUNCH[0]


def _free_in(slots: list[datetime], busy: list[tuple]) -> list[datetime]:
	return [s for s in slots if not any(b < s + DURATION and e > s for b, e in busy)]


def _working_days(after: date, count: int, cfg: timeutil.Config) -> list[date]:
	out, day = [], after
	for _i in range(timeutil.SCAN_DAYS * count):
		if len(out) >= count:
			break
		day += timedelta(days=1)
		if _demo_day(day, cfg):
			out.append(day)
	return out


def free_slots(
	now: datetime | None = None,
	user: str | None = None,
	cfg: timeutil.Config | None = None,
	lead: str | None = None,
) -> list[datetime]:
	"""Tối đa 3 khung trống trong 3 ngày làm việc tới: mỗi ngày lấy khung sáng đầu tiên rồi khung chiều đầu tiên."""
	now = now or _now()
	cfg = cfg if cfg is not None else timeutil.load_config()
	out: list[datetime] = []
	for day in _working_days(now.date(), SLOT_DAYS, cfg):
		grid = _grid(day, cfg)
		if not grid:
			continue
		free = _free_in(grid, busy_ranges(user, grid[0], grid[-1] + DURATION, lead))
		morning = [s for s in free if s.time() < LUNCH[0]][:1]
		afternoon = [s for s in free if s.time() >= LUNCH[1]][:1]
		out.extend(morning + afternoon)
	return out[:SLOT_COUNT]


def next_free(
	after: datetime,
	user: str | None = None,
	now: datetime | None = None,
	cfg: timeutil.Config | None = None,
	lead: str | None = None,
) -> datetime | None:
	"""Khung trống gần nhất ngay sau `after` (4.4 bước 3: giờ khách chọn bận hoặc rơi vào ngày nghỉ)."""
	now = now or _now()
	cfg = cfg if cfg is not None else timeutil.load_config()
	after = max(after, now + MIN_AHEAD - timedelta(seconds=1))
	days = [after.date(), *_working_days(after.date(), SEARCH_DAYS, cfg)]
	for day in days:
		grid = [s for s in _grid(day, cfg) if after < s <= now + MAX_AHEAD]
		if not grid:
			continue
		free = _free_in(grid, busy_ranges(user, grid[0], grid[-1] + DURATION, lead))
		if free:
			return free[0]
	return None


# --- Đọc ngày giờ khách gõ -------------------------------------------------------------------------------------

_WEEKDAYS = {
	"hai": 0,
	"2": 0,
	"ba": 1,
	"3": 1,
	"tư": 2,
	"tu": 2,
	"4": 2,
	"năm": 3,
	"nam": 3,
	"5": 3,
	"sáu": 4,
	"sau": 4,
	"6": 4,
	"bảy": 5,
	"bay": 5,
	"7": 5,
}
_RE_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:[/\-]|tháng)\s*(\d{1,2})(?:\s*(?:[/\-]|năm)\s*(\d{4}))?(?!\d)")
_RE_WEEKDAY = re.compile(r"thứ\s*(hai|ba|tư|tu|năm|nam|sáu|sau|bảy|bay|[2-7])(?![\wÀ-ỹ])")
_RE_SUNDAY = re.compile(r"chủ nhật|(?<![\wÀ-ỹ])cn(?![\wÀ-ỹ])")
_RE_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*(?:h|giờ|:)\s*(\d{1,2})?(?!\d)")
_RE_BARE_HOUR = re.compile(r"(?<!\d)(\d{1,2})\s*(?=sáng|chiều|tối|trưa)")
_RE_RELATIVE = (
	(re.compile(r"hôm nay|(?<![\wÀ-ỹ])nay(?![\wÀ-ỹ])"), 0),
	(re.compile(r"ngày kia|(?<![\wÀ-ỹ])mốt(?![\wÀ-ỹ])"), 2),
	(re.compile(r"(?<![\wÀ-ỹ])mai(?![\wÀ-ỹ])"), 1),
)


def parse_time(text: str | None, now: datetime | None = None) -> datetime | None:
	"""Ngày giờ khách gõ ("9h30 thứ 5", "14:00 ngày 20/11", "sáng mai 10 giờ"); thiếu ngày hoặc giờ, hoặc không đọc được → None."""
	now = now or _now()
	raw = " " + unicodedata.normalize("NFC", cstr(text)).lower() + " "
	day, rest = _parse_day(raw, now.date())
	if not day:
		return None
	m = _RE_TIME.search(rest) or _RE_BARE_HOUR.search(rest)
	if not m:
		return None
	hour = int(m.group(1))
	minute = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else 0
	if "rưỡi" in rest and not minute:
		minute = 30
	if hour < 12 and (re.search(r"chiều|tối", rest) or (hour <= 7 and "sáng" not in rest)):
		hour += 12  # "2h chiều", hoặc "3h" không ghi buổi: hiểu là buổi chiều
	if hour > 23 or minute > 59:
		return None
	return datetime.combine(day, time(hour, minute))


def _parse_day(raw: str, today: date) -> tuple[date | None, str]:
	"""(ngày, phần câu còn lại sau khi bỏ cụm ngày) từ câu khách gõ."""
	m = _RE_DATE.search(raw)
	if m:
		rest = raw[: m.start()] + " " + raw[m.end() :]
		year = int(m.group(3)) if m.group(3) else today.year
		try:
			day = date(year, int(m.group(2)), int(m.group(1)))
			if day < today and not m.group(3):
				day = date(year + 1, int(m.group(2)), int(m.group(1)))
		except ValueError:
			return None, rest
		return day, rest
	m = _RE_WEEKDAY.search(raw) or _RE_SUNDAY.search(raw)
	if m:
		rest = raw[: m.start()] + " " + raw[m.end() :]
		target = _WEEKDAYS[m.group(1)] if m.lastindex else 6
		if "tuần sau" in raw or "tuần tới" in raw:
			monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
			return monday + timedelta(days=target), rest
		return today + timedelta(days=(target - today.weekday()) % 7 or 7), rest
	for pattern, offset in _RE_RELATIVE:
		m = pattern.search(raw)
		if m:
			return today + timedelta(days=offset), raw[: m.start()] + " " + raw[m.end() :]
	return None, raw


# --- Đặt / hủy lịch --------------------------------------------------------------------------------------------


def book(
	identity: str,
	lead: str,
	start: datetime,
	mode: str | None = None,
	attendees: int | None = None,
	service: str | None = None,
	now: datetime | None = None,
) -> dict:
	"""Chốt lịch demo: tạo Event, ghi Lead (4.8), hành trình "Đã hẹn demo", 3 tin hẹn giờ, việc gửi lời mời. Trả {event, schedules, task, user}."""
	now = now or _now()
	service = service or scheduler.current_service(identity, lead)
	release(identity, lead, _("Đổi lịch demo"), new_start=start)
	user = demo_user(service)
	label = scheduler.SERVICE_LABEL.get(service, service or "")
	lead_name = frappe.db.get_value(LEAD, lead, "lead_name") or lead
	event = frappe.get_doc(
		{
			"doctype": EVENT,
			"subject": _("Demo {0} - {1}").format(label, lead_name).strip(),
			"event_category": "Meeting",
			"event_type": "Private",
			"starts_on": start,
			"ends_on": start + DURATION,
			"status": "Open",
			"reference_doctype": LEAD,
			"reference_docname": lead,
			"description": _("Hình thức: {0}. Số người tham dự: {1}.").format(
				mode or "-", attendees or _("chưa rõ")
			),
		}
	)
	event.insert(ignore_permissions=True)
	if user:
		frappe.db.set_value(EVENT, event.name, "owner", user, update_modified=False)
	frappe.db.set_value(
		LEAD,
		lead,
		{
			"omni_demo_at": start,
			"omni_demo_mode": mode,
			"omni_demo_attendees": cint(attendees),
			"omni_demo_result": None,
		},
		update_modified=False,
	)
	state.set_journey(lead, "Đã hẹn demo")
	scheduler.cancel_for(lead, scheduler.REPLY_CANCELS, _("Khách đã đặt lịch demo"))
	task = _invite_task(lead, service, start, mode, attendees, user, label, now)
	schedules = _schedule_reminders(identity, lead, start, event.name, task, now)
	state.update_state(identity, demo_event=event.name, demo_task=task, demo_at=_iso(start))
	return {"event": event.name, "schedules": schedules, "task": task, "user": user}


def _schedule_reminders(
	identity: str, lead: str, start: datetime, event: str, task: str | None, now: datetime
) -> list[str]:
	payload = {"key": _iso(start), "demo_at": _iso(start), "event": event, "task": task}
	if scheduler.fast_for(identity):  # danh sách thử: ngày/giờ rút thành phút
		plan = {
			"demo_remind_1d": now + timedelta(minutes=1),
			"demo_remind_1h": now + timedelta(minutes=2),
			"demo_noshow": now + timedelta(minutes=3),
		}
	else:
		day_before = timeutil.last_proactive_moment(start - timedelta(days=1))
		if not day_before or day_before <= now:
			day_before = timeutil.next_proactive_slot(now)
		plan = {
			"demo_remind_1d": day_before if day_before < start - timedelta(hours=1) else None,
			"demo_remind_1h": start - timedelta(hours=1) if start - timedelta(hours=1) > now else None,
			"demo_noshow": start + scheduler.NOSHOW_AFTER,
		}
	names = []
	for kind, due in plan.items():
		name = due and scheduler.schedule(kind, identity, lead, due, scheduler.DEFAULT_NODE[kind], payload)
		if name:
			names.append(name)
	return names


def _invite_task(
	lead: str,
	service: str,
	start: datetime,
	mode: str | None,
	attendees: int | None,
	user: str | None,
	label: str,
	now: datetime,
) -> str | None:
	"""Việc cho người demo gửi lời mời và đường link (chưa nối Microsoft 365); chưa có người demo thì việc không gán."""
	try:
		from omni_crm.script import tasks
	except ImportError:
		return None
	title = _("Gửi lời mời demo {0}: {1}").format(label, slot_label(start))
	lines = [
		_("Khách đã đặt lịch demo qua bot. Vui lòng gửi lời mời và đường link tham gia tới email của khách."),
		_("Thời gian: {0}, {1}-{2}").format(
			scheduler.day_label(start.date()),
			scheduler.hour_label(start),
			scheduler.hour_label(start + DURATION),
		),
		_("Hình thức: {0}").format(mode or "-"),
		_("Số người tham dự: {0}").format(attendees or _("chưa rõ")),
	]
	if not user:
		lines.append(_("Chưa cấu hình người demo cho dịch vụ này (bảng người nhận trong Omni Settings)."))
	return tasks.create_task(
		lead, f"DEMO_{service}", title, "\n".join(lines), due=timeutil.task_due(now), priority="High"
	)


def _bot_bookings(identity: str | None, lead: str) -> tuple[set, set]:
	"""(Event, CRM Task) do bot tạo cho lịch demo của Lead: ghi trong `script_state` và payload các tin hẹn giờ demo."""
	events, tasks_ = set(), set()
	st = _st(identity) if identity else {}
	sources = [{"event": st.get("demo_event"), "task": st.get("demo_task")}]
	rows = frappe.get_all(
		scheduler.SCHED, filters={"lead": lead, "kind": ["in", list(scheduler.DEMO_KINDS)]}, pluck="payload"
	)
	sources += [store.as_dict(p) for p in rows]
	for src in sources:
		if src.get("event"):
			events.add(src["event"])
		if src.get("task"):
			tasks_.add(src["task"])
	return events, tasks_


def release(
	identity: str | None,
	lead: str | None,
	reason: str | None = None,
	journey: str | None = None,
	new_start: datetime | None = None,
) -> bool:
	"""Gỡ lịch demo do bot đặt: Event của bot -> Cancelled, hủy tin hẹn giờ, đóng việc gửi lời mời + báo người demo, xóa `omni_demo_at`.

	`journey` (khi khách hủy) đưa Lead rời "Đã hẹn demo". Không đụng Event do nhân viên tự tạo. True nếu có lịch để gỡ.
	"""
	if not lead:
		return False
	reason = reason or _("Hủy lịch demo")
	old_at = frappe.db.get_value(LEAD, lead, "omni_demo_at")
	events, task_names = _bot_bookings(identity, lead)
	open_events = (
		frappe.get_all(EVENT, filters={"name": ["in", list(events)], "status": "Open"}, pluck="name")
		if events
		else []
	)
	for name in open_events:
		frappe.db.set_value(EVENT, name, "status", "Cancelled")
	scheduler.cancel_for(lead, scheduler.DEMO_KINDS, reason)
	if old_at or open_events:
		_close_invite_tasks(lead, task_names, old_at, reason, new_start)
	if old_at:
		frappe.db.set_value(LEAD, lead, "omni_demo_at", None, update_modified=False)
	if journey and frappe.db.get_value(LEAD, lead, "omni_journey_status") == JOURNEY_BOOKED:
		state.set_journey(lead, journey)
	if identity:
		state.update_state(identity, demo_event=None, demo_task=None, demo_at=None)
	return bool(old_at or open_events)


def _close_invite_tasks(lead: str, names: set, old_at, reason: str, new_start: datetime | None) -> None:
	"""Đóng việc "gửi lời mời demo" còn mở của lịch cũ (ghi chú lý do) và báo người được giao + Omni Admin."""
	old = slot_label(get_datetime(old_at)) if old_at else "-"
	note = _("{0}: lịch demo {1} không còn hiệu lực.").format(reason, old)
	if new_start:
		note += " " + _("Lịch mới: {0} (xem việc mới).").format(slot_label(new_start))
	users = set()
	try:
		from omni_crm.script import tasks
	except ImportError:
		tasks = None
	for name in sorted(names):
		row = frappe.db.get_value(TASK, name, ["status", "assigned_to"], as_dict=True)
		if not row or row.status not in TASK_OPEN:
			continue
		if tasks:
			tasks.append_note(name, note)
		frappe.db.set_value(TASK, name, "status", "Canceled")
		if row.assigned_to:
			users.add(row.assigned_to)
	try:
		from omni_crm.monitoring import alerts

		alerts.deliver_now(
			alerts.Alert(
				kind=ALERT_KIND,
				scope=f"{lead}:{old}",
				subject=_("Lịch demo của khách đã đổi hoặc hủy"),
				detail=_("Lead {0}. {1}").format(lead, note),
				email=False,
				users=tuple(sorted(users)),
			)
		)
	except Exception:
		frappe.log_error(title="Omni: không báo được thay đổi lịch demo", message=f"lead={lead}")


# --- Các bước hội thoại ----------------------------------------------------------------------------------------


def _send(ctx: RouteContext, code: str, extra: dict | None = None) -> str | None:
	"""Gửi ô `code` (đủ biến mới gửi) và ghi ô đang đứng."""
	values = scheduler.variables_for(ctx.identity, ctx.lead, extra)
	if scheduler.missing_variables(code, values):
		return None
	name = send.send_node(ctx.identity, code, values)
	if name:
		state.set_node(ctx.identity, code, (store.get_node(code) or {}).get("step") or None)
	return name


def _service(ctx: RouteContext) -> str:
	return scheduler.current_service(ctx.identity, ctx.lead)


def _handoff(ctx: RouteContext, reason: str) -> None:
	"""Chuyển nhân viên (K3) và thoát luồng demo."""
	state.update_state(ctx.identity, flow=None, demo_wait=None)
	try:
		from omni_crm.script.flows import handoff

		handoff.start(ctx, service=_service(ctx) or None, reason=reason)
	except ImportError:
		frappe.logger("omni").info("omni script demo: chưa có flows.handoff, identity=%s", ctx.identity)


def _answer_value(code: str, field: str) -> str:
	"""Giá trị của lệnh `answer:<field>=<giá trị>` trên ô `code` (câu chữ nằm trong kho, không nằm trong code)."""
	for part in ((store.get_node(code) or {}).get("action") or "").split(";"):
		head, _sep, value = part.strip().partition("=")
		if head.strip().lower() == f"answer:{field}":
			return value.strip()
	return ""


def propose(ctx: RouteContext) -> bool:
	"""Bước 2 (4.4): gửi 3 khung trống của người demo; không còn khung nào thì chuyển nhân viên."""
	now = _now()
	slots = free_slots(now, demo_user(_service(ctx)), lead=ctx.lead)
	if not slots:
		_handoff(ctx, _("Không tìm được khung demo trống trong 3 ngày làm việc tới"))
		return False
	state.update_state(ctx.identity, demo_slots=[_iso(s) for s in slots], flow=None, demo_wait=None)
	labels = {f"khung_{i + 1}": slot_label(s) for i, s in enumerate(slots)}
	sent = _send(ctx, NODE_SLOTS, labels)
	if not sent:
		_handoff(ctx, _("Không gửi được danh sách khung giờ demo"))
		return False
	# khách có thể GÕ giờ mình muốn thay vì bấm một khung: tin gõ kế tiếp được đọc như ngày giờ (`on_text`)
	state.update_state(
		ctx.identity, demo_slots_msg=sent, demo_slots_at=_iso(now), flow=FLOW, demo_wait=WAIT_SLOTS
	)
	return True


def choose(ctx: RouteContext, start: datetime) -> None:
	"""Khách chọn một khung: còn trống thì hỏi số người tham dự, không thì đề xuất khung trống gần nhất."""
	if not is_free(start, demo_user(_service(ctx)), _now(), lead=ctx.lead):
		_offer_nearest(ctx, start)
		return
	state.update_state(ctx.identity, demo_at=_iso(start), flow=FLOW, demo_wait="attendees")
	if not _send(ctx, NODE_ATTENDEES):
		confirm(ctx)  # câu hỏi số người không bắt buộc


def _offer_nearest(ctx: RouteContext, after: datetime) -> None:
	nearest = next_free(after, demo_user(_service(ctx)), _now(), lead=ctx.lead)
	if not nearest:
		_handoff(ctx, _("Khách muốn demo vào giờ đã kín lịch, không tìm được khung thay thế"))
		return
	state.update_state(ctx.identity, demo_slots=[_iso(nearest)], demo_at=None, flow=None, demo_wait=None)
	labels = {"khung_1": slot_label(nearest)}
	sent = (_send(ctx, NODE_OUTSIDE, labels) if outside_hours(after) else None) or _send(ctx, NODE_BUSY, labels)
	if not sent:
		propose(ctx)
		return
	state.update_state(
		ctx.identity, demo_slots_msg=sent, demo_slots_at=_iso(_now()), flow=FLOW, demo_wait=WAIT_SLOTS
	)


def confirm(ctx: RouteContext) -> None:
	"""Bước 5 (4.4): chốt lịch đã chọn (có khóa theo khung giờ) rồi gửi câu xác nhận; không bao giờ chốt im lặng."""
	st = _st(ctx.identity)
	start = get_datetime(st.get("demo_at")) if st.get("demo_at") else None
	if not start or not ctx.lead:
		propose(ctx)
		return
	user = demo_user(_service(ctx))
	mode = st.get("demo_mode") or frappe.db.get_value(LEAD, ctx.lead, "omni_demo_mode")
	with slot_lock(user, start) as locked:
		if not locked or not is_free(start, user, _now(), lead=ctx.lead):
			_offer_nearest(ctx, start)
			return
		booked = book(
			ctx.identity, ctx.lead, start, mode, cint(st.get("demo_attendees")) or None, _service(ctx)
		)
		frappe.db.commit()  # lịch đã chốt là một đơn vị việc; ghi xong mới nhả khóa
	state.update_state(
		ctx.identity,
		flow=None,
		demo_wait=None,
		demo_slots=None,
		demo_slots_msg=None,
		demo_slots_at=None,
		demo_email_asked=None,
	)
	extra = scheduler.demo_variables(start, mode)
	if any(_send(ctx, code, extra) for code in NODE_CONFIRM):
		return
	_confirmation_failed(ctx, booked, start)


def _confirmation_failed(ctx: RouteContext, booked: dict, start: datetime) -> None:
	"""Đã chốt lịch mà không gửi được câu xác nhận nào: ghi vào việc của người demo; không có việc thì chuyển nhân viên."""
	note = _(
		"Bot CHƯA gửi được câu xác nhận lịch demo {0} cho khách. Vui lòng xác nhận trực tiếp với khách."
	).format(slot_label(start))
	frappe.logger("omni").info("omni script demo: không gửi được câu xác nhận, identity=%s", ctx.identity)
	if booked.get("task"):
		try:
			from omni_crm.script import tasks

			tasks.append_note(booked["task"], note)
			return
		except ImportError:
			pass
	_handoff(ctx, note)


def _after_attendees(ctx: RouteContext) -> None:
	if ctx.lead and not frappe.db.get_value(LEAD, ctx.lead, "email"):
		state.update_state(ctx.identity, flow=FLOW, demo_wait="email")
		if _send(ctx, NODE_ASK_EMAIL):
			return
	confirm(ctx)


_ANYTIME = re.compile(r"(?:lúc|giờ|ngày|hôm|khi) nào cũng được|(?:tùy|tuỳ) (?:em|bạn|bên em|miyano)")
_SKIP_COUNT = re.compile(r"chưa biết|chưa rõ|chưa chắc|không rõ|không biết|bỏ qua|để sau|tính sau", re.I)
_DROP_DEMO = re.compile(r"không (?:muốn|cần|xem|demo)|thôi|hủy|huỷ|khỏi|để khi khác|dừng", re.I)
_LEAVE_CODES = ("GAP_NHANVIEN", "CAN_NGUOI", "HUY_DANGKY", "XOA_DULIEU", "PHAN_NAN")


_ORDINALS = (
	(re.compile(r"^(?:số |khung |cái |lựa chọn )?(?:thứ )?(?:1|một|nhất|đầu tiên|đầu)$"), 0),
	(re.compile(r"^(?:số |khung |cái |lựa chọn )?(?:thứ )?(?:2|hai|nhì)$"), 1),
	(re.compile(r"^(?:số |khung |cái |lựa chọn )?(?:thứ )?(?:3|ba)$"), 2),
)
_HOUR_ONLY = re.compile(r"^(?:lúc )?(\d{1,2})\s*(?:h|giờ|:)\s*(\d{1,2})?\s*(sáng|chiều)?$")


def _typed_slot(text: str | None, st: dict) -> datetime | None:
	"""Khung khách chọn bằng cách gõ thứ tự ("khung đầu tiên", "cái thứ 2") hoặc chỉ gõ giờ trùng đúng MỘT khung đã đề xuất."""
	raw = " ".join(unicodedata.normalize("NFC", cstr(text)).lower().split()).strip(" .!,")
	slots = [get_datetime(s) for s in st.get("demo_slots") or []]
	if not raw or not slots:
		return None
	for pattern, index in _ORDINALS:
		if pattern.match(raw):
			return slots[index] if index < len(slots) else None
	m = _HOUR_ONLY.match(raw)
	if not m:
		return None
	hour, minute = int(m.group(1)), int(m.group(2) or 0)
	if m.group(3) == "chiều" and hour < 12:
		hour += 12
	same = [s for s in slots if (s.hour, s.minute) == (hour, minute)]
	return same[0] if len(same) == 1 else None


def _small_talk(text: str | None) -> bool:
	raw = cstr(text).strip()
	if not any(ch.isalnum() for ch in raw):
		return True
	try:
		from omni_crm.script import classify

		return bool(classify.is_greeting_only(raw) or classify.is_ack_only(raw))
	except Exception:
		return False


def _has_booking(ctx: RouteContext) -> datetime | None:
	"""Lịch demo còn hiệu lực (ở tương lai) của Lead, nếu có."""
	at = ctx.lead and frappe.db.get_value(LEAD, ctx.lead, "omni_demo_at")
	at = get_datetime(at) if at else None
	return at if at and at > _now() else None


def _leaves_demo(ctx: RouteContext) -> bool:
	"""Ở bước hỏi số người mà khách đổi ý hoặc nói rõ ý khác: bỏ khung đang chọn dở (chưa đặt gì), trả True."""
	text = ctx.text or ""
	try:
		from omni_crm.script import classify

		hit = classify.classify_by_rules(text)
	except Exception:
		hit = None
	if not (hit and hit[0] in _LEAVE_CODES) and not _DROP_DEMO.search(text):
		return False
	state.update_state(ctx.identity, flow=None, demo_wait=None, demo_at=None, demo_att_asked=None)
	return True


def on_text(ctx: RouteContext) -> bool:
	"""Tin gõ khi khách đang ở giữa luồng demo (giờ khác / số người / email). True nếu đã xử lý."""
	st = _st(ctx.identity)
	wait = st.get("demo_wait")
	if not wait:
		return False
	if wait == WAIT_SLOTS:  # đang chờ khách chọn khung: đọc được ý chọn / ngày giờ thì nhận, không thì để bộ hiểu ý xử lý
		picked = _typed_slot(ctx.text, st)
		if picked:
			scheduler.on_inbound(ctx.identity, ctx.lead)
			choose(ctx, picked)
			return True
		if not parse_time(ctx.text, _now()):
			if _small_talk(ctx.text):  # chào / ok / sticker: gửi lại danh sách khung giờ, không về menu chính
				propose(ctx)
				return True
			if _parse_day(" " + cstr(ctx.text).lower() + " ", _now().date())[0]:  # mới nêu ngày: xin thêm giờ
				state.update_state(ctx.identity, flow=FLOW, demo_wait="time", demo_time_asked=1)
				return bool(_send(ctx, NODE_TIME_AGAIN) or _send(ctx, NODE_OTHER_TIME))
			return False
		wait = "time"
	scheduler.on_inbound(ctx.identity, ctx.lead)
	if wait == "time":
		start = parse_time(ctx.text, _now())
		if not start and _ANYTIME.search(cstr(ctx.text).lower()):  # "lúc nào cũng được": đề xuất lại các khung trống
			propose(ctx)
			return True
		if not start and not st.get("demo_time_asked"):  # chưa đọc được: xin lại đủ ngày và giờ đúng một lần
			state.update_state(ctx.identity, demo_time_asked=1)
			if _send(ctx, NODE_TIME_AGAIN):
				return True
		state.update_state(ctx.identity, demo_time_asked=None)
		if not start:
			_handoff(ctx, _("Khách gõ giờ demo mà bot không đọc được"))
		elif not in_booking_range(start, _now()):
			_handoff(ctx, _("Khách muốn demo trong vòng 2 giờ tới hoặc xa hơn 45 ngày"))
		else:
			choose(ctx, start)
	elif wait == "attendees":
		m = re.search(r"\d{1,4}", ctx.text or "")
		count = int(m.group()) if m and 0 < int(m.group()) <= MAX_ATTENDEES else None
		if count is None and not _SKIP_COUNT.search(ctx.text or ""):
			if _leaves_demo(ctx):
				return False  # khách đổi ý / muốn gặp người / dừng nhắn: KHÔNG chốt lịch, để bộ hiểu ý xử lý
			if not st.get("demo_att_asked"):  # câu không có số: hỏi lại đúng một lần rồi mới chốt
				state.update_state(ctx.identity, demo_att_asked=1)
				if _send(ctx, NODE_ATTENDEES):
					return True
		state.update_state(ctx.identity, demo_attendees=count, demo_att_asked=None)
		_after_attendees(ctx)
	elif wait == "email":
		email = validate.find_email(ctx.text)
		if email:
			frappe.db.set_value(LEAD, ctx.lead, "email", email)
			confirm(ctx)
		elif st.get("demo_email_asked"):
			confirm(ctx)  # vẫn giữ lịch; người demo nhận việc và tự xin email
		else:
			state.update_state(ctx.identity, demo_email_asked=1)
			if not _send(ctx, NODE_ASK_EMAIL):
				confirm(ctx)
	else:
		return False
	return True


# --- Nút bấm (tầng router) -------------------------------------------------------------------------------------


def _on_format(ctx: RouteContext) -> None:
	state.update_state(ctx.identity, demo_mode=_answer_value(ctx.payload_code, "demo_mode") or None)
	propose(ctx)


def _on_slot(ctx: RouteContext) -> None:
	index = cint(ctx.payload_code.rsplit("_", 1)[-1]) - 1
	st = _st(ctx.identity)
	slots = st.get("demo_slots") or []
	if not 0 <= index < len(slots) or not _slots_current(ctx.identity, st):
		propose(ctx)  # nút của tin cũ: không đoán khung, gửi lại danh sách mới
		return
	choose(ctx, get_datetime(slots[index]))


def _slots_current(identity: str, st: dict) -> bool:
	"""Các khung đang lưu đúng là của tin có nút gần nhất gửi cho khách và chưa quá 24 giờ."""
	sent_at = st.get("demo_slots_at")
	if not st.get("demo_slots_msg") or not sent_at or _now() - get_datetime(sent_at) > SLOTS_TTL:
		return False
	last = frappe.get_all(
		"Omni Outbound Message",
		filters={"identity": identity, "script_node": ["in", list(SLOT_NODES)]},
		pluck="name",
		order_by="creation desc",
		limit=1,
	)
	return bool(last) and last[0] == st.get("demo_slots_msg")


def _on_other_time(ctx: RouteContext) -> None:
	state.update_state(ctx.identity, flow=FLOW, demo_wait="time")
	if not _send(ctx, NODE_OTHER_TIME):
		_handoff(ctx, _("Khách muốn chọn giờ demo khác"))


def _on_reschedule(ctx: RouteContext) -> None:
	mode = ctx.lead and frappe.db.get_value(LEAD, ctx.lead, "omni_demo_mode")
	release(ctx.identity, ctx.lead, _("Khách đổi lịch demo"))
	if mode and not _st(ctx.identity).get("demo_mode"):
		state.update_state(ctx.identity, demo_mode=mode)
	propose(ctx)


def _on_cancel(ctx: RouteContext) -> None:
	release(ctx.identity, ctx.lead, _("Khách hủy lịch demo"), journey=JOURNEY_AFTER_CANCEL)
	state.update_state(ctx.identity, flow=None, demo_wait=None, demo_slots=None)
	_send(ctx, NODE_CANCEL)


def _on_cancel_reason(ctx: RouteContext) -> None:
	reason = _answer_value(ctx.payload_code, "demo_cancel_reason")
	if ctx.lead and reason:
		note = _("Khách hủy lịch demo: {0}").format(reason)
		old = frappe.db.get_value(LEAD, ctx.lead, "omni_demo_note") or ""
		frappe.db.set_value(LEAD, ctx.lead, "omni_demo_note", f"{old}\n{note}".strip(), update_modified=False)
	_send(ctx, NODE_CANCEL_THANKS)
	state.set_journey(ctx.lead, scheduler.NURTURE)
	scheduler.cancel_for(ctx.lead)


def _run_engine(ctx: RouteContext, code: str) -> None:
	"""Chuyển ô không cần tính lịch cho bộ chạy K2; bộ chạy chưa có thì mở luồng demo bằng phần tối thiểu của tệp này."""
	try:
		engine.run_node(ctx, code)
		return
	except NotImplementedError:
		pass
	if code == NODE_NO_DEMO:
		return
	service = (store.get_node(code) or {}).get("service")
	if service in DEMO_SERVICES:
		state.add_service(ctx.lead, service)
	_reset(ctx.identity)
	_send(ctx, NODE_FORMAT)


def _reset(identity: str) -> None:
	state.update_state(identity, **dict.fromkeys(STATE_KEYS))


_ROUTES = {
	"DEMO_HT_ONLINE": _on_format,
	"DEMO_HT_TRUCTIEP": _on_format,
	"DEMO_KHUNG_1": _on_slot,
	"DEMO_KHUNG_2": _on_slot,
	"DEMO_KHUNG_3": _on_slot,
	"DEMO_GIOKHAC": _on_other_time,
	"DEMO_DOILICH": _on_reschedule,
	"DEMO_HUY": _on_cancel,
	"DEMO_HUY_BAN": _on_cancel_reason,
	"DEMO_HUY_CHUANHUCAU": _on_cancel_reason,
	"DEMO_HUY_KHAC": _on_cancel_reason,
}


# các nút mở / đi tiếp một lượt đặt lịch: khách đã có lịch thì nhắc lịch đang có trước
BOOKING_ENTRY = (
	"DAT_DEMO",
	"DEMO_PHANMEM",
	"DEMO_SPD",
	"DEMO_HT_ONLINE",
	"DEMO_HT_TRUCTIEP",
	"DEMO_KHUNG_1",
	"DEMO_KHUNG_2",
	"DEMO_KHUNG_3",
	"DEMO_GIOKHAC",
)


def handle(ctx: RouteContext) -> None:
	"""Handler router cho mọi nút `DEMO_*` và `DAT_DEMO`."""
	code = ctx.payload_code or ""
	scheduler.on_inbound(ctx.identity, ctx.lead)
	if code == "DAT_DEMO" and _service(ctx) == "VATTU":
		code = NODE_NO_DEMO  # Vật tư không có demo: mời gửi danh mục (2.5)
	booked = _has_booking(ctx) if code in BOOKING_ENTRY else None
	if booked:
		# đã có lịch: không lặng lẽ mở đặt lịch mới rồi ghi đè; nhắc lịch đang có và hỏi khách muốn đổi hay huỷ
		mode = frappe.db.get_value(LEAD, ctx.lead, "omni_demo_mode")
		if _send(ctx, NODE_HAS_BOOKING, scheduler.demo_variables(booked, mode)):
			return
	fn = _ROUTES.get(code)
	if fn:
		fn(ctx)
	else:
		_run_engine(ctx, code)


# --- Lệnh action cho bộ chạy (khi bộ chạy tự đi tới ô DEMO_*) ----------------------------------------------------


def _sent_since_inbound(identity: str, code: str) -> bool:
	last_in = frappe.db.get_value("Omni Identity", identity, "last_inbound_at")
	filters: dict = {"identity": identity, "script_node": code}
	if last_in:
		filters["creation"] = [">=", last_in]
	return bool(frappe.db.exists("Omni Outbound Message", filters))


def action_demo(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `demo`, `demo:slots`, `demo:book`, `demo:reschedule`, `demo:cancel` của các ô DEMO_*."""
	arg = (arg or "").strip().lower()
	if not arg:
		_reset(ctx.identity)  # bắt đầu luồng đặt lịch mới ở ô hỏi hình thức
	elif arg == "slots":
		if not _sent_since_inbound(ctx.identity, node.get("code") or NODE_SLOTS):
			propose(ctx)
	elif arg == "book":
		st = _st(ctx.identity)
		start = get_datetime(st.get("demo_at")) if st.get("demo_at") else None
		booked = ctx.lead and frappe.db.get_value(LEAD, ctx.lead, "omni_demo_at")
		if start and ctx.lead and (not booked or get_datetime(booked) != start):
			user = demo_user(_service(ctx))
			with slot_lock(user, start) as locked:
				if not locked or not is_free(start, user, _now(), lead=ctx.lead):
					_offer_nearest(ctx, start)
					return True  # khung không còn hợp lệ: dừng chuỗi action của ô xác nhận
				book(
					ctx.identity, ctx.lead, start, st.get("demo_mode"), cint(st.get("demo_attendees")) or None
				)
	elif arg == "reschedule":
		release(ctx.identity, ctx.lead, _("Khách đổi lịch demo"))
	elif arg == "cancel":
		release(ctx.identity, ctx.lead, _("Khách hủy lịch demo"), journey=JOURNEY_AFTER_CANCEL)
	return None


router.register("DEMO_", handle)
router.register("DAT_DEMO", handle)
engine.register_action("demo", action_demo)
engine.register_state(FLOW, on_text)
