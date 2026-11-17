"""Chọn kênh cho tin bot tự gửi (kịch bản mục 4.2) - hàm thuần, không đọc/ghi DB khi truyền `cfg`.

Thứ tự 4.2:
1. Tại mốc mục tiêu vẫn còn hạn chat → chat đúng mốc.
2. Hết hạn tại mốc, nhưng còn khung gửi chủ động trước hạn (sớm hơn hạn ≥ 1 giờ, sau lúc gửi tài liệu ≥ 3 giờ)
   → chat sớm, vào khung muộn nhất còn hợp lệ.
3. Còn lại → email đúng mốc (chưa bật tự gửi email: trả `email_task` để tạo việc cho nhân viên) kèm ZNS nếu
   bật ZNS và có SĐT; không email mà có ZNS → chỉ ZNS; không gì cả → `none`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from frappe import _

from omni_crm.script import timeutil

EXPIRY_MARGIN = timedelta(hours=1)  # gửi sớm phải trước hạn chat ít nhất 1 giờ
MIN_AFTER_START = timedelta(hours=3)  # và cách lúc gửi tài liệu ít nhất 3 giờ


def chat_deadline(last_inbound_at: datetime | None, free_hours: float | None) -> datetime | None:
	"""Hạn chat = tin cuối của khách + số giờ miễn phí của kênh; None nếu chưa có tin hoặc kênh không có hạn."""
	if not last_inbound_at or not free_hours or free_hours <= 0:
		return None
	return last_inbound_at + timedelta(hours=float(free_hours))


def pick_channel(
	now: datetime,
	last_inbound_at: datetime | None,
	free_hours: float | None,
	due_at: datetime,
	has_email: bool,
	has_phone: bool,
	zns_enabled: bool,
	*,
	start_at: datetime | None = None,
	cfg: timeutil.Config | None = None,
) -> dict:
	"""Áp bảng 4.2, trả {"kind": chat|email_task|zns|none, "at": datetime|None, "zns": bool, "early": bool, "reason": str}."""
	deadline = chat_deadline(last_inbound_at, free_hours)
	if deadline and due_at < deadline:
		return _result("chat", due_at, reason=_("Còn hạn chat tại mốc mục tiêu"))

	if deadline:
		early = _early_slot(now, deadline, start_at, cfg)
		if early:
			return _result("chat", early, early=True, reason=_("Nhắn chat sớm trước khi hết hạn chat"))

	zns = bool(zns_enabled and has_phone)
	if has_email:
		return _result("email_task", due_at, zns=zns, reason=_("Hết hạn chat: email (nhân viên gửi)"))
	if zns:
		return _result("zns", due_at, zns=True, reason=_("Hết hạn chat, không có email: gửi ZNS"))
	return _result("none", None, reason=_("Hết kênh liên hệ"))


def _early_slot(
	now: datetime, deadline: datetime, start_at: datetime | None, cfg: timeutil.Config | None
) -> datetime | None:
	"""Thời điểm muộn nhất trong khung gửi chủ động, ≤ hạn - 1 giờ, ≥ bây giờ (và ≥ lúc gửi tài liệu + 3 giờ)."""
	latest = deadline - EXPIRY_MARGIN
	earliest = max(now, start_at + MIN_AFTER_START) if start_at else now
	if latest < earliest:
		return None
	slot = timeutil.last_proactive_moment(latest, cfg)
	return slot if slot and slot >= earliest else None


def _result(
	kind: str, at: datetime | None, *, zns: bool = False, early: bool = False, reason: str = ""
) -> dict:
	return {"kind": kind, "at": at, "zns": zns, "early": early, "reason": reason}
