"""Quỹ tin tư vấn Zalo ngoài khung 48 giờ của gói OA (V3-07, thay cách đếm theo tháng của V21-05).

Gói Tăng trưởng cho 500 tin/năm; hết thì Zalo tính phí từng tin. Hệ thống TỰ ĐẾM, không đợi Zalo báo: mỗi tin ra được
`routing.pick_message_kind` đánh dấu `quota_consumed = 1` khi ăn vào quỹ. Đếm = tin `quota_consumed = 1`, `status = Sent`,
không sandbox, trong chu kỳ của gói. Chu kỳ là 1 năm kết thúc ở `Omni Settings.zalo_quota_cycle_end` (ngày hết hạn gói),
không phải năm dương lịch.

Ba mức: 80% cảnh báo, 95% chỉ Omni Admin gửi được, 100% từ chối.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import frappe
from frappe import _
from frappe.utils import add_days, add_years, cint, getdate

SETTINGS = "Omni Settings"
DEFAULT_TOTAL = 500
DEFAULT_CYCLE_END = date(2027, 11, 9)
WARN_PCT, SOFT_PCT = 80, 95
OK, WARN, SOFT, HARD = "ok", "warn", "soft", "hard"
ADMIN_ROLE = "Omni Admin"


def _setting(fieldname: str):
	try:
		return frappe.get_cached_doc(SETTINGS).get(fieldname)
	except Exception:
		return None


def total() -> int:
	"""Tổng quỹ tin/năm (trống hoặc 0 = 500)."""
	return cint(_setting("zalo_quota_total")) or DEFAULT_TOTAL


def cycle_bounds() -> tuple[datetime, datetime]:
	"""[đầu chu kỳ, đầu ngày sau ngày hết hạn): 10/11/2026 00:00 .. 10/11/2027 00:00 với hạn 09/11/2027."""
	end = getdate(_setting("zalo_quota_cycle_end") or DEFAULT_CYCLE_END)
	start = add_days(add_years(end, -1), 1)
	return datetime.combine(start, time.min), datetime.combine(add_days(end, 1), time.min)


def count_used() -> int:
	"""Số tin đã ăn vào quỹ trong chu kỳ hiện tại (mọi kênh Zalo OA)."""
	start, end = cycle_bounds()
	row = frappe.db.sql(
		"""
		select count(*) from `tabOmni Outbound Message`
		where quota_consumed = 1 and status = 'Sent' and ifnull(is_sandbox, 0) = 0
			and sent_at >= %(start)s and sent_at < %(end)s
		""",
		{"start": start, "end": end},
	)
	return cint(row[0][0])


def level_of(used: int, quota: int) -> str:
	"""Mức chặn theo số đã dùng: ok / warn (>= 80%) / soft (>= 95%) / hard (>= 100%)."""
	if used >= quota:
		return HARD
	if used * 100 >= quota * SOFT_PCT:
		return SOFT
	if used * 100 >= quota * WARN_PCT:
		return WARN
	return OK


@dataclass
class Usage:
	used: int
	total: int
	level: str

	@property
	def remain(self) -> int:
		return max(0, self.total - self.used)

	def as_dict(self) -> dict:
		return {
			"used": self.used,
			"total": self.total,
			"remain": self.remain,
			"level": self.level,
			"text": _("Còn {0}/{1} tin ngoài khung 48h").format(self.remain, self.total),
		}


def usage() -> Usage:
	"""Số đã dùng, tổng và mức chặn hiện tại."""
	used, quota = count_used(), total()
	return Usage(used=used, total=quota, level=level_of(used, quota))


def is_admin(user: str | None) -> bool:
	"""Người dùng có vai trò Omni Admin (được gửi khi quỹ đã dùng >= 95%)."""
	return bool(user) and ADMIN_ROLE in frappe.get_roles(user)


def gate(user: str | None) -> str | None:
	"""Lý do KHÔNG được tiêu thêm một tin của quỹ (None = được); kiểm 95% / 100%."""
	info = usage()
	if info.level == HARD:
		return _(
			"Đã hết quỹ {0} tin tư vấn ngoài khung 48 giờ của gói OA. Gửi thêm sẽ bị Zalo tính phí từng tin, "
			"nên hệ thống không gửi. Hãy đợi khách nhắn trước (tin trong 48 giờ miễn phí) hoặc liên hệ kênh khác."
		).format(info.total)
	if info.level == SOFT and not is_admin(user):
		return _(
			"Quỹ tin ngoài khung 48 giờ chỉ còn {0}/{1} tin: chỉ Omni Admin được gửi. "
			"Hãy nhờ quản trị hoặc đợi khách nhắn trước."
		).format(info.remain, info.total)
	return None
