"""Đếm tin tư vấn Zalo mà Sale gửi NGOÀI khung `zalo_cs_free_hours` trong tháng (V21-05, docs/06 mục 7a).

Chỉ để BÁO (Hộp thư), không bao giờ chặn gửi. Một tin tính là "ngoài khung" khi, tại lúc gửi (`sent_at`), khách không có
tin vào nào (trừ `oa_echo`, `unfollow`) trong `zalo_cs_free_hours` giờ trước đó. Đếm tin do hệ thống gửi qua API
(`sent_by` có giá trị, `status = Sent`, không sandbox); tin nhân viên trả lời thẳng trên ứng dụng Zalo OA (`oa_echo`, không có
`sent_by`) không đếm: chưa xác nhận Zalo có tính hạn mức cho chúng hay không (docs/12, C-V21-05).
"""

from __future__ import annotations

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import add_months, cint, get_first_day, now_datetime

# khớp pipeline.worker.NON_INBOUND_EVENTS: không phải khách chủ động liên hệ
_NON_INBOUND = ("oa_echo", "unfollow")


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
	"""[đầu tháng, đầu tháng sau) theo giờ site của `now`."""
	now = now or now_datetime()
	start = datetime.combine(get_first_day(now), datetime.min.time())
	return start, datetime.combine(get_first_day(add_months(start, 1)), datetime.min.time())


def count_out_of_window(channel: str, free_hours: int, now: datetime | None = None) -> int:
	"""Số tin Sale đã gửi qua API của kênh ngoài khung `free_hours` giờ trong tháng chứa `now`."""
	start, end = month_bounds(now)
	row = frappe.db.sql(
		"""
		select count(*) from `tabOmni Outbound Message` o
		where o.channel = %(channel)s and o.status = 'Sent' and o.source = 'Sale'
			and ifnull(o.sent_by, '') != '' and ifnull(o.is_sandbox, 0) = 0
			and o.sent_at >= %(start)s and o.sent_at < %(end)s
			and not exists (
				select 1 from `tabOmni Inbound Message` i
				where i.identity = o.identity and i.event_type not in %(skip)s
					and i.received_at <= o.sent_at
					and i.received_at >= date_sub(o.sent_at, interval %(hours)s hour)
			)
		""",
		{"channel": channel, "start": start, "end": end, "skip": _NON_INBOUND, "hours": int(free_hours)},
	)
	return cint(row[0][0])


def usage(channel_doc, now: datetime | None = None) -> dict:
	"""{"used", "quota" (None = chưa biết), "text"} cho Hộp thư; quota trống hoặc 0 = chưa biết."""
	used = count_out_of_window(channel_doc.name, cint(channel_doc.zalo_cs_free_hours), now)
	quota = cint(channel_doc.get("zalo_cs_paid_quota_month")) or None
	hours = cint(channel_doc.zalo_cs_free_hours)
	if quota:
		text = _("Đã dùng {0} / {1} tin ngoài {2} giờ tháng này").format(used, quota, hours)
	else:
		text = _("Đã gửi {0} tin ngoài {1} giờ tháng này (chưa biết hạn mức)").format(used, hours)
	return {"used": used, "quota": quota, "over": bool(quota and used >= quota), "text": text}
