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


def is_working_day(day: date, hours: dict | None = None, holidays: frozenset[date] | None = None) -> bool:
	"""`day` có giờ làm việc không (không phải ngày nghỉ lễ, và thứ trong tuần có khung giờ cấu hình)."""
	hours = hours if hours is not None else _hours()
	holidays = holidays if holidays is not None else _holidays()
	return _window(day, hours, holidays) is not None


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


def business_minutes_between(
	start: datetime, end: datetime, hours: dict | None = None, holidays: frozenset[date] | None = None
) -> float:
	"""Số phút nằm trong giờ làm việc giữa `start` và `end` (0 nếu end <= start); dùng chung cho mọi hạn/đồng hồ phản hồi."""
	return business_seconds_between(start, end, hours, holidays) / 60.0


class BusinessClock:
	"""Phút làm việc tích lũy theo ngày: tính hàng chục nghìn khoảng `[start, end]` trong O(1) mỗi khoảng.

	Kết quả bằng `business_minutes_between` (cùng giờ làm việc, cùng ngày lễ) nhưng không lặp qua từng ngày cho từng khoảng.
	Phủ các ngày từ `first_day` đến `last_day`; thời điểm ngoài khoảng được kẹp vào hai đầu.
	"""

	def __init__(
		self,
		first_day: date,
		last_day: date,
		hours: dict | None = None,
		holidays: frozenset[date] | None = None,
	):
		hours = hours if hours is not None else _hours()
		holidays = holidays if holidays is not None else _holidays()
		self.first_day = first_day
		self.last_day = max(last_day, first_day)
		self._windows: list[tuple[time, time] | None] = []
		self._before: list[float] = []  # giây làm việc tích lũy trước đầu mỗi ngày
		total = 0.0
		day = self.first_day
		while day <= self.last_day:
			window = _window(day, hours, holidays)
			self._windows.append(window)
			self._before.append(total)
			if window:
				total += (datetime.combine(day, window[1]) - datetime.combine(day, window[0])).total_seconds()
			day += timedelta(days=1)

	def upto(self, at: datetime) -> float:
		"""Số giây làm việc từ đầu `first_day` tới `at`."""
		day = at.date()
		if day < self.first_day:
			return 0.0
		if day > self.last_day:
			return self._before[-1] + self._day_seconds(len(self._before) - 1)
		i = (day - self.first_day).days
		window = self._windows[i]
		if not window:
			return self._before[i]
		lo, hi = datetime.combine(day, window[0]), datetime.combine(day, window[1])
		return self._before[i] + max(0.0, (min(at, hi) - lo).total_seconds())

	def _day_seconds(self, i: int) -> float:
		window = self._windows[i]
		if not window:
			return 0.0
		day = self.first_day + timedelta(days=i)
		return (datetime.combine(day, window[1]) - datetime.combine(day, window[0])).total_seconds()

	def minutes_between(self, start: datetime, end: datetime) -> float:
		"""Phút làm việc giữa `start` và `end` (0 nếu end <= start)."""
		if end <= start:
			return 0.0
		return max(0.0, self.upto(end) - self.upto(start)) / 60.0
