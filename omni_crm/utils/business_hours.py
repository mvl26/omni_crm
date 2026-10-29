"""Giờ làm việc (Omni Settings.business_hours) và phép tính thời gian trong giờ làm việc."""

from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta

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


def is_business_time(dt: datetime | None = None, hours: dict | None = None) -> bool:
	"""`dt` (mặc định bây giờ) có nằm trong giờ làm việc không."""
	dt = dt or frappe.utils.now_datetime()
	window = (hours if hours is not None else _hours()).get(dt.weekday())
	return bool(window and window[0] <= dt.time() <= window[1])


def business_seconds_between(start: datetime, end: datetime, hours: dict | None = None) -> float:
	"""Số giây nằm trong giờ làm việc giữa `start` và `end` (0 nếu end <= start)."""
	hours = hours if hours is not None else _hours()
	if end <= start:
		return 0.0
	total = 0.0
	day = start.date()
	while day <= end.date():
		window = hours.get(day.weekday())
		if window:
			lo = max(datetime.combine(day, window[0]), start)
			hi = min(datetime.combine(day, window[1]), end)
			if hi > lo:
				total += (hi - lo).total_seconds()
		day += timedelta(days=1)
	return total
