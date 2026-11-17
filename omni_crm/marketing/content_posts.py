"""Đổi trạng thái bài đăng qua nút trên form, link UTM khi chiến dịch đổi, sự kiện cho lịch (ADR 0029).

Không có bước duyệt (người phụ trách 17/11/2026): người có quyền sửa bài bấm "Lên lịch" là đủ. Job đăng kiểm lại trạng thái
ngay trước khi gọi Meta (`publisher.py`).
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm.marketing.constants import (
	AUTO,
	CAMPAIGN,
	CANCELLED,
	DRAFT,
	FACEBOOK,
	FAILED,
	LOCKED_STATUSES,
	MISSED,
	POST,
	PUBLISHED,
	SCHEDULED,
	UNCERTAIN,
)
from omni_crm.marketing.graph import MAX_MESSAGE

SCHEDULE_FROM = (DRAFT, FAILED, MISSED)
CANCEL_FROM = (DRAFT, SCHEDULED, FAILED, MISSED)
DEFAULT_LATE_WINDOW = 30


def settings():
	"""Omni Settings (đã cache)."""
	return frappe.get_cached_doc("Omni Settings")


def late_window_minutes() -> int:
	"""Cửa sổ đăng muộn: quá giờ đăng hơn số phút này thì job không đăng nữa (Bỏ lỡ)."""
	value = cint(settings().get("content_late_window_minutes"))
	return value if value > 0 else DEFAULT_LATE_WINDOW


def auto_publish_state() -> str | None:
	"""Lý do tự đăng đang KHÔNG chạy (để hiện trên form), hoặc None nếu đang chạy được."""
	from omni_crm import flags

	s = settings()
	if not cint(s.get("content_publish_enabled")):
		return _("Tự đăng bài đang tắt (Omni Settings, mục Bài đăng)")
	if flags.sandbox():
		return _("Chế độ thử (sandbox) đang bật: CRM không gọi Meta")
	channel = s.get("content_publish_channel")
	if not channel:
		return _("Chưa chọn kênh Page để đăng bài (Omni Settings, mục Bài đăng)")
	row = frappe.db.get_value(
		"Omni Channel", channel, ["enabled", "outbound_enabled", "meta_page_id"], as_dict=True
	)
	if not row or not row.enabled or not row.outbound_enabled:
		return _("Kênh {0} chưa bật hoặc chưa bật chiều ra").format(channel)
	if not row.meta_page_id:
		return _("Kênh {0} chưa có Page ID").format(channel)
	return None


def _save_status(doc, status: str, **values) -> None:
	doc.status = status
	doc.update(values)
	doc.flags.status_by_api = True
	doc.save()


def check_ready(doc) -> None:
	"""Ném lỗi nếu bài chưa đủ để lên lịch: nội dung, link có UTM, và với bài tự động là kênh Facebook, giờ chưa quá hạn."""
	if not (doc.content or "").strip():
		frappe.throw(_("Bài chưa có nội dung"))
	if not frappe.db.get_value(CAMPAIGN, doc.campaign, "utm_campaign"):
		frappe.throw(
			_("Chiến dịch {0} chưa có utm_campaign: điền trên form chiến dịch trước").format(doc.campaign)
		)
	if not doc.utm_link:
		frappe.throw(_("Bài chưa có link có UTM: điền Link đích của bài hoặc Trang đích của chiến dịch"))
	if doc.publish_mode != AUTO:
		return
	if doc.platform != FACEBOOK:
		frappe.throw(
			_("Đăng tự động hiện chỉ có cho Facebook; kênh {0} thì chọn Đăng tay").format(doc.platform)
		)
	if len(doc.content) > MAX_MESSAGE:
		frappe.throw(_("Nội dung dài quá {0} ký tự").format(MAX_MESSAGE))
	if get_datetime(doc.publish_at) < now_datetime() - timedelta(minutes=late_window_minutes()):
		frappe.throw(_("Giờ đăng đã qua: sửa Giờ đăng rồi lên lịch lại"))


def schedule(name: str) -> str:
	"""Nháp, Lỗi, Bỏ lỡ -> Đã lên lịch."""
	doc = frappe.get_doc(POST, name)
	if doc.status not in SCHEDULE_FROM:
		frappe.throw(_("Bài {0} không lên lịch được").format(_(doc.status)))
	check_ready(doc)
	_save_status(doc, SCHEDULED, last_error=None)
	return doc.status


def unschedule(name: str) -> str:
	"""Đã lên lịch -> Nháp (để sửa tiếp)."""
	doc = frappe.get_doc(POST, name)
	if doc.status != SCHEDULED:
		frappe.throw(_("Chỉ huỷ lịch được bài Đã lên lịch"))
	_save_status(doc, DRAFT)
	return doc.status


def cancel(name: str) -> str:
	"""Huỷ bài (không đăng nữa); giữ bản ghi để báo cáo."""
	doc = frappe.get_doc(POST, name)
	if doc.status not in CANCEL_FROM:
		frappe.throw(_("Bài {0} không huỷ được").format(_(doc.status)))
	_save_status(doc, CANCELLED)
	return doc.status


def reopen(name: str) -> str:
	"""Đã huỷ -> Nháp."""
	doc = frappe.get_doc(POST, name)
	if doc.status != CANCELLED:
		frappe.throw(_("Chỉ mở lại được bài Đã huỷ"))
	_save_status(doc, DRAFT)
	return doc.status


def resolve_uncertain(name: str, published: bool, post_url: str | None = None) -> str:
	"""Bài Cần kiểm: người soạn đã mở Page xem. Có bài thì Đã đăng (kèm link bài), không có thì Lỗi."""
	doc = frappe.get_doc(POST, name)
	if doc.status != UNCERTAIN:
		frappe.throw(_("Chỉ xử lý được bài Cần kiểm"))
	if published:
		url = (post_url or "").strip()
		if not url:
			frappe.throw(_("Dán link bài trên Page"))
		_save_status(
			doc, PUBLISHED, post_url=url, published_at=doc.published_at or now_datetime(), last_error=None
		)
	else:
		_save_status(doc, FAILED, last_error=_("Người soạn xác nhận bài chưa lên Page"))
	return doc.status


def refresh_utm_links(campaign: str) -> int:
	"""Sinh lại link có UTM cho bài chưa đăng của chiến dịch; trả số bài đã đăng (giữ link cũ)."""
	published = 0
	for name in frappe.get_all(POST, filters={"campaign": campaign}, pluck="name"):
		doc = frappe.get_doc(POST, name)
		if doc.status in LOCKED_STATUSES:
			published += 1
			continue
		link = doc.compute_utm_link() or None
		if link != doc.utm_link:
			frappe.db.set_value(POST, name, "utm_link", link, update_modified=False)
	return published


def calendar_events(start: str, end: str, filters=None) -> list[dict]:
	"""Bài có giờ đăng trong [start, end] cho Calendar view, theo quyền đọc của người xem."""
	conditions = frappe.parse_json(filters) if filters else []
	conditions = [
		*(conditions or []),
		[POST, "publish_at", ">=", get_datetime(start)],
		[POST, "publish_at", "<=", get_datetime(end)],
	]
	rows = frappe.get_list(
		POST,
		filters=conditions,
		fields=["name", "title", "publish_at", "status", "publish_mode", "campaign"],
		order_by="publish_at asc",
		limit_page_length=500,
	)
	for r in rows:
		r["publish_end"] = get_datetime(r["publish_at"]) + timedelta(minutes=30)
		r["title"] = f"{r['name']} · {r['title']}"
		r["editable"] = 0
	return rows
