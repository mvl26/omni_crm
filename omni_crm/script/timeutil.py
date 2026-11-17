"""Mốc thời gian cho bot kịch bản v3 (kịch bản mục 1.4, 3.5, 4.1, 5.1, C.1, C.5).

Mỗi hàm công khai có tham số `cfg` tùy chọn = `(hours, holidays)` (dạng của `utils.business_hours`);
không truyền thì đọc Omni Settings qua `load_config()`. Lõi tính toán là hàm thuần, test truyền thẳng cấu hình.

Quy ước suy ra từ giờ đóng cửa của từng ngày (`close`) để tôn trọng cấu hình (vd thứ Bảy 08:00-12:00):
- Giờ chốt nhận việc (3.5) = min(16:30, close - 1 giờ); hạn xử lý = close (17:30 ngày thường).
- Khung gửi chủ động (C.5) = max(09:00, open) → min(16:30, close - 1 giờ), chỉ ngày làm việc.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import frappe
from frappe import _

from omni_crm.utils import business_hours as bh

Config = tuple[dict, frozenset]

CUTOFF = time(16, 30)  # mốc chốt nhận việc trong ngày (3.5) và cuối khung gửi chủ động (C.5)
PROACTIVE_START = time(9, 0)  # đầu khung gửi chủ động (C.5)
CUTOFF_BEFORE_CLOSE = timedelta(hours=1)  # 17:30 - 16:30
SCAN_DAYS = bh.MAX_SCAN_DAYS


def load_config() -> Config:
	"""Đọc `(hours, holidays)` từ Omni Settings bằng bộ đọc có sẵn của `utils.business_hours`."""
	try:
		doc = frappe.get_cached_doc("Omni Settings")
		raw_hours, raw_holidays = doc.get("business_hours"), doc.get("holidays")
	except Exception:
		raw_hours, raw_holidays = None, None
	return bh.parse_hours(raw_hours), bh.parse_holidays(raw_holidays)


def _cfg(cfg: Config | None) -> Config:
	return cfg if cfg is not None else load_config()


def _window(day: date, cfg: Config) -> tuple[time, time] | None:
	hours, holidays = cfg
	return None if day in holidays else hours.get(day.weekday())


def _cutoff_time(day: date, close: time) -> time:
	"""Giờ chốt trong ngày: 16:30, hoặc sớm hơn nếu ngày đóng cửa sớm (close - 1 giờ)."""
	early = (datetime.combine(day, close) - CUTOFF_BEFORE_CLOSE).time()
	return min(CUTOFF, early)


def _proactive_bounds(day: date, cfg: Config) -> tuple[datetime, datetime] | None:
	window = _window(day, cfg)
	if not window:
		return None
	start = max(PROACTIVE_START, window[0])
	end = _cutoff_time(day, window[1])
	if end <= start:
		return None
	return datetime.combine(day, start), datetime.combine(day, end)


def in_hours(at: datetime, cfg: Config | None = None) -> bool:
	"""`at` có nằm trong giờ làm việc (theo `business_hours`, trừ ngày lễ) không."""
	hours, holidays = _cfg(cfg)
	return bh.is_business_time(at, hours, holidays)


def is_working_day(day: date, cfg: Config | None = None) -> bool:
	"""`day` có khung giờ làm việc trong cấu hình và không phải ngày lễ."""
	return _window(day, _cfg(cfg)) is not None


def next_working_day(at: datetime, cfg: Config | None = None) -> date:
	"""Ngày nhân viên làm việc kế tiếp: hôm nay nếu chưa mở cửa, còn lại ngày làm việc đầu tiên sau `at`."""
	cfg = _cfg(cfg)
	today = _window(at.date(), cfg)
	if today and at.time() < today[0]:
		return at.date()
	for offset in range(1, SCAN_DAYS):
		day = at.date() + timedelta(days=offset)
		if _window(day, cfg):
			return day
	return at.date() + timedelta(days=1)


def weekday_name(day: date) -> str:
	"""Tên thứ tiếng Việt của `day` (thứ Hai … Chủ nhật)."""
	names = (
		_("thứ Hai"),
		_("thứ Ba"),
		_("thứ Tư"),
		_("thứ Năm"),
		_("thứ Sáu"),
		_("thứ Bảy"),
		_("Chủ nhật"),
	)
	return names[day.weekday()]


def next_working_day_text(at: datetime, cfg: Config | None = None) -> str:
	"""Giá trị `{ngay_lam_viec_tiep_theo}` (1.4): "sáng nay", "sáng mai" hoặc "sáng thứ Hai 23/11"."""
	day = next_working_day(at, cfg)
	if day == at.date():
		return _("sáng nay")
	if day == at.date() + timedelta(days=1):
		return _("sáng mai")
	return _("sáng {0} {1}").format(weekday_name(day), day.strftime("%d/%m"))


def cutoff_1630(at: datetime, cfg: Config | None = None) -> bool:
	"""True nếu yêu cầu lúc `at` tính sang ngày làm việc kế tiếp (3.5: sau 16:30, trước giờ mở, ngày nghỉ)."""
	window = _window(at.date(), _cfg(cfg))
	if not window:
		return True
	return not (window[0] <= at.time() <= _cutoff_time(at.date(), window[1]))


def task_due(at: datetime, cfg: Config | None = None) -> datetime:
	"""Hạn nhân viên phải liên hệ (3.5): giờ đóng cửa cùng ngày, hoặc giờ đóng cửa ngày làm việc kế tiếp."""
	cfg = _cfg(cfg)
	day = next_working_day(at, cfg) if cutoff_1630(at, cfg) else at.date()
	window = _window(day, cfg)
	return datetime.combine(day, window[1] if window else time(17, 30))


def in_proactive_window(at: datetime, cfg: Config | None = None) -> bool:
	"""`at` nằm trong khung bot được tự gửi tin (C.5: 09:00-16:30 ngày làm việc)."""
	bounds = _proactive_bounds(at.date(), _cfg(cfg))
	return bool(bounds and bounds[0] <= at <= bounds[1])


def next_proactive_slot(at: datetime, cfg: Config | None = None) -> datetime:
	"""`at` nếu đang trong khung gửi chủ động, ngược lại đầu khung kế tiếp (09:00 ngày làm việc tới)."""
	cfg = _cfg(cfg)
	for offset in range(SCAN_DAYS):
		bounds = _proactive_bounds(at.date() + timedelta(days=offset), cfg)
		if not bounds:
			continue
		if at <= bounds[1]:
			return max(at, bounds[0])
	return at


def last_proactive_moment(at: datetime, cfg: Config | None = None) -> datetime | None:
	"""Thời điểm muộn nhất ≤ `at` còn trong khung gửi chủ động; None nếu không tìm thấy."""
	cfg = _cfg(cfg)
	for offset in range(SCAN_DAYS):
		bounds = _proactive_bounds(at.date() - timedelta(days=offset), cfg)
		if not bounds:
			continue
		if at >= bounds[0]:
			return min(at, bounds[1])
	return None


def postdemo_send_at(demo_end: datetime, cfg: Config | None = None) -> datetime:
	"""Giờ gửi tin sau demo (5.1): khoảng 2 giờ sau nếu vẫn trước 16:30, không thì 09:00 ngày làm việc kế tiếp."""
	return next_proactive_slot(demo_end + timedelta(hours=2), _cfg(cfg))
