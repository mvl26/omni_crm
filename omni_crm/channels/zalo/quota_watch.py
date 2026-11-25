"""Theo dõi quỹ tin tư vấn ngoài khung 48 giờ (V3-07, brief mục B "Chốt chặn hạn mức").

- `check_level`: quỹ đã dùng >= 80% / 95% / 100% thì cảnh báo qua `monitoring.alerts`, mỗi mức MỘT lần mỗi chu kỳ.
  Gọi sau mỗi tin ăn vào quỹ và trong job hằng ngày.
- `daily` (07:15): đối chiếu số tự đếm với số Zalo báo (POST /v3.0/oa/quota/message, quota_type = sub_quota) và tìm
  tin Zalo đã gửi mà không qua `routing.pick_message_kind` (thiếu `message_kind`). Lệch quá 5 tin, hoặc có tin không
  qua bộ chọn loại tin, thì cảnh báo: lệch nghĩa là có đường gửi nào đó đi vòng, đó mới là vấn đề thật.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm import flags
from omni_crm.channels.zalo import quota
from omni_crm.pipeline.retry import redact

MAX_DRIFT = 5
ALERT_KIND = "quỹ tin Zalo ngoài 48 giờ"
LEVEL_KEY = "omni:zalo:quota_level_alerted"
LEVEL_TEXT = {
	quota.WARN: "đã dùng 80%",
	quota.SOFT: "đã dùng 95%, chỉ Omni Admin được gửi",
	quota.HARD: "đã hết, hệ thống từ chối gửi tin ngoài 48 giờ",
}
PAID_QUOTA_TYPES = ("sub_quota",)  # quỹ tin theo gói OA (tài liệu "Kiểm tra hạn mức tin nhắn OA")


def _deliver(scope: str, subject: str, detail: str) -> bool:
	from omni_crm.monitoring import alerts

	return alerts.deliver_now(alerts.Alert(kind=ALERT_KIND, scope=scope, subject=subject, detail=detail))


def check_level() -> str:
	"""Cảnh báo một lần cho mỗi mức (80/95/100%) trong chu kỳ hiện tại; trả mức hiện tại."""
	info = quota.usage()
	if info.level == quota.OK:
		return info.level
	_start, end = quota.cycle_bounds()
	key = f"{LEVEL_KEY}:{end.date().isoformat()}:{info.level}"
	if frappe.cache().get_value(key, expires=True):
		return info.level
	sent = _deliver(
		f"muc:{info.level}",
		_("Quỹ tin Zalo ngoài khung 48 giờ {0}").format(_(LEVEL_TEXT[info.level])),
		_("Đã dùng {0}/{1} tin trong chu kỳ đến {2}. Còn {3} tin.").format(
			info.used, info.total, end.date().isoformat(), info.remain
		),
	)
	if sent:
		frappe.cache().set_value(key, 1, expires_in_sec=400 * 86400)
	return info.level


def check_level_safe() -> None:
	"""Như check_level nhưng không bao giờ ném lỗi (gọi ngay sau khi gửi tin)."""
	try:
		check_level()
	except Exception:
		frappe.logger("omni").warning("omni: kiểm mức quỹ Zalo lỗi", exc_info=True)


def unrouted_count() -> int:
	"""Tin Zalo đã gửi (không sandbox) trong chu kỳ mà không có `message_kind`: đi vòng qua bộ chọn loại tin."""
	start, end = quota.cycle_bounds()
	row = frappe.db.sql(
		"""
		select count(*) from `tabOmni Outbound Message` o
		join `tabOmni Channel` c on c.name = o.channel
		where c.channel_type = 'Zalo OA' and o.status = 'Sent' and ifnull(o.is_sandbox, 0) = 0
			and ifnull(o.message_kind, '') = '' and o.sent_at >= %(start)s and o.sent_at < %(end)s
		""",
		{"start": start, "end": end},
	)
	return int(row[0][0] or 0)


def zalo_used() -> dict[str, Any] | None:
	"""Số tin quỹ gói OA Zalo báo đã dùng {used, total, rows}; None nếu không hỏi được (sandbox, chưa có kênh, lỗi)."""
	if flags.sandbox():
		return None
	from omni_crm.channels.zalo.client import ZaloClient

	channels = frappe.get_all("Omni Channel", filters={"channel_type": "Zalo OA", "enabled": 1}, pluck="name")
	for name in channels:
		try:
			rows = ZaloClient(name).get_cs_quota()
		except Exception as e:
			frappe.logger("omni").warning(
				"omni: hỏi quỹ tin Zalo lỗi (kênh %s): %s", name, redact(str(e))[:200]
			)
			continue
		paid = [
			r
			for r in rows
			if r.get("quota_type") in PAID_QUOTA_TYPES and r.get("product_type") in (None, "cs")
		]
		total = sum(int(r.get("total") or 0) for r in paid)
		remain = sum(int(r.get("remain") or 0) for r in paid)
		return {
			"used": total - remain,
			"total": total,
			"rows": [{k: r.get(k) for k in ("quota_type", "total", "remain", "valid_through")} for r in paid],
		}
	return None


def daily() -> dict[str, Any]:
	"""Job hằng ngày: cảnh báo mức quỹ, đối chiếu với Zalo, tìm tin đi vòng. Trả số liệu (để chạy tay xem)."""
	level = check_level()
	ours = quota.count_used()
	bypass = unrouted_count()
	theirs = zalo_used()
	out: dict[str, Any] = {"level": level, "ours": ours, "unrouted": bypass, "zalo": theirs, "alerted": []}
	if bypass:
		_deliver(
			"di_vong",
			_("Có {0} tin Zalo gửi không qua bộ chọn loại tin").format(bypass),
			_(
				"Các tin này không có Loại tin nên không biết có ăn vào quỹ ngoài 48 giờ không. "
				"Kiểm tra Omni Outbound Message của kênh Zalo có Loại tin trống trong chu kỳ hiện tại."
			),
		)
		out["alerted"].append("unrouted")
	if theirs is not None and abs(theirs["used"] - ours) > MAX_DRIFT:
		_deliver(
			"lech_so",
			_("Số tin ngoài 48 giờ lệch với Zalo: hệ thống đếm {0}, Zalo báo {1}").format(
				ours, theirs["used"]
			),
			_("Zalo báo tổng quỹ {0} tin. Lệch quá {1} tin nghĩa là có đường gửi đi vòng bộ đếm.").format(
				theirs["total"], MAX_DRIFT
			),
		)
		out["alerted"].append("drift")
	return out
