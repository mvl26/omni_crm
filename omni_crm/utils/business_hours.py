"""Giờ làm việc (Omni Settings.business_hours) và phép tính thời gian trong giờ làm việc."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta

import frappe

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_HOURS = {"mon-fri": "08:00-17:30", "sat": "08:00-12:00"}
_RANGE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def _parse_days(key: str) -> list[int]:
	parts = [p.strip().lower() for p in str(key).split("-")]
	if len(parts) == 1 and parts[0] in DAYS:
		return [DAYS.index(parts[0])]
	if len(parts) == 2 and parts[0] in DAYS and parts[1] in DAYS:
		a, b = DAYS.index(parts[0]), DAYS.index(parts[1])
		return list(range(a, b + 1)) if a <= b else [*range(a, 7), *range(0, b + 1)]
	return []


def parse_hours(raw: str | dict | None) -> dict[int, tuple[time, time]]:
	"""Đổi JSON giờ làm việc thành {thứ (0=thứ hai): (giờ mở, giờ đóng)}; sai định dạng thì dùng mặc định."""
	try:
		data = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
	except ValueError:
		data = None
	if not isinstance(data, dict) or not data:
		data = DEFAULT_HOURS
	out: dict[int, tuple[time, time]] = {}
	for key, value in data.items():
		m = _RANGE.match(str(value))
		if not m:
			continue
		h1, m1, h2, m2 = (int(x) for x in m.groups())
		if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59 or (h2, m2) <= (h1, m1) or (h2 == 24 and m2):
			continue
		end = time(23, 59, 59) if h2 == 24 else time(h2, m2)
		for d in _parse_days(key):
			out[d] = (time(h1, m1), end)
	return out or parse_hours(DEFAULT_HOURS)


def _hours() -> dict[int, tuple[time, time]]:
	try:
		raw = frappe.get_cached_doc("Omni Settings").business_hours
	except Exception:
		raw = None
	return parse_hours(raw)


def parse_holidays(raw: str | list | None) -> frozenset[date]:
	"""Đổi JSON `["2027-01-01", ...]` thành tập ngày nghỉ; phần tử sai định dạng bị bỏ qua, JSON hỏng -> rỗng."""
	try:
		data = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
	except ValueError:
		return frozenset()
	out: set[date] = set()
	for item in data if isinstance(data, list) else []:
		try:
			out.add(date.fromisoformat(str(item).strip()[:10]))
		except ValueError:
			continue
	return frozenset(out)


def _holidays() -> frozenset[date]:
	try:
		raw = frappe.get_cached_doc("Omni Settings").get("holidays")
	except Exception:
		raw = None
	return parse_holidays(raw)


def _window(day: date, hours: dict, holidays: frozenset[date]) -> tuple[time, time] | None:
	return None if day in holidays else hours.get(day.weekday())


def is_business_time(
	dt: datetime | None = None, hours: dict | None = None, holidays: frozenset[date] | None = None
) -> bool:
	"""`dt` (mặc định bây giờ) có nằm trong giờ làm việc không (ngày nghỉ lễ không tính)."""
	dt = dt or frappe.utils.now_datetime()
	hours = hours if hours is not None else _hours()
	holidays = holidays if holidays is not None else _holidays()
	window = _window(dt.date(), hours, holidays)
	return bool(window and window[0] <= dt.time() <= window[1])


MAX_SCAN_DAYS = 400  # chặn vòng lặp nếu cấu hình không có ngày làm việc nào


def next_business_start(
	dt: datetime, hours: dict | None = None, holidays: frozenset[date] | None = None
) -> datetime:
	"""`dt` nếu đang trong giờ làm việc, ngược lại thời điểm mở cửa kế tiếp (qua đêm, cuối tuần, ngày lễ)."""
	hours = hours if hours is not None else _hours()
	holidays = holidays if holidays is not None else _holidays()
	if is_business_time(dt, hours, holidays):
		return dt
	day = dt.date()
	for offset in range(MAX_SCAN_DAYS):
		d = day + timedelta(days=offset)
		window = _window(d, hours, holidays)
		if not window:
			continue
		opening = datetime.combine(d, window[0])
		if opening >= dt:
			return opening
	return dt


def add_business_minutes(
	dt: datetime, minutes: int, hours: dict | None = None, holidays: frozenset[date] | None = None
) -> datetime:
	"""Cộng `minutes` phút làm việc vào `dt`; thời gian ngoài giờ/cuối tuần/ngày lễ không được tính."""
	hours = hours if hours is not None else _hours()
	holidays = holidays if holidays is not None else _holidays()
	cur = next_business_start(dt, hours, holidays)
	remaining = timedelta(minutes=max(0, int(minutes)))
	for _i in range(MAX_SCAN_DAYS):
		window = _window(cur.date(), hours, holidays)
		close = datetime.combine(cur.date(), window[1]) if window else cur
		available = close - cur
		if window and available >= remaining:
			return cur + remaining
		if window and available > timedelta(0):
			remaining -= available
		cur = next_business_start(
			datetime.combine(cur.date() + timedelta(days=1), time(0, 0)), hours, holidays
		)
	return cur


def business_seconds_between(
	start: datetime, end: datetime, hours: dict | None = None, holidays: frozenset[date] | None = None
) -> float:
	"""Số giây nằm trong giờ làm việc giữa `start` và `end` (0 nếu end <= start)."""
	hours = hours if hours is not None else _hours()
	holidays = holidays if holidays is not None else _holidays()
	if end <= start:
		return 0.0
	total = 0.0
	day = start.date()
	while day <= end.date():
		window = _window(day, hours, holidays)
		if window:
			lo = max(datetime.combine(day, window[0]), start)
			hi = min(datetime.combine(day, window[1]), end)
			if hi > lo:
				total += (hi - lo).total_seconds()
		day += timedelta(days=1)
	return total
