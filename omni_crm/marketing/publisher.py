"""Job đăng bài đến giờ lên Page Facebook (ADR 0029), chạy mỗi phút.

Thứ tự an toàn cho mỗi bài:
1. Chiếm bài: khóa dòng, kiểm lại trạng thái Đã lên lịch, đổi sang Đang đăng, commit (không giữ khóa DB khi gọi Meta).
2. Đọc lại nội dung mới nhất, gọi Meta.
3. Ghi kết quả: Đã đăng, Lỗi (Meta từ chối, chắc chắn chưa đăng), Cần kiểm (mất kết nối hoặc Meta lỗi 5xx khi đăng:
   có thể bài đã lên, KHÔNG thử lại để tránh đăng hai lần).
Quá cửa sổ đăng muộn thì Bỏ lỡ. Lỗi, Cần kiểm, Bỏ lỡ đều báo người tạo bài.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm.marketing import content_posts, media
from omni_crm.marketing.constants import (
	AUTO,
	FACEBOOK,
	FAILED,
	MISSED,
	POST,
	PUBLISHED,
	PUBLISHING,
	SCHEDULED,
	UNCERTAIN,
)
from omni_crm.marketing.graph import ContentGraphError, PageClient
from omni_crm.marketing.utm import render_message

BATCH = 5
STUCK_MINUTES = 15


def run_due() -> None:
	"""Cron mỗi phút: dọn bài kẹt ở Đang đăng, rồi đăng tối đa BATCH bài đã đến giờ."""
	sweep_stuck()
	if content_posts.auto_publish_state():
		return  # tắt, sandbox, hoặc chưa cấu hình kênh: không động vào bài (form hiện lý do)
	channel_doc = frappe.get_doc("Omni Channel", content_posts.settings().content_publish_channel)
	client = PageClient(channel_doc)
	now = now_datetime()
	due = frappe.get_all(
		POST,
		filters={"status": SCHEDULED, "publish_mode": AUTO, "platform": FACEBOOK, "publish_at": ["<=", now]},
		order_by="publish_at asc",
		pluck="name",
		limit=BATCH,
	)
	for name in due:
		try:
			publish_one(name, client, now)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi job đăng bài", message=f"post={name}")


def publish_one(name: str, client: PageClient, now=None) -> str | None:
	"""Đăng một bài đã đến giờ; trả trạng thái mới, hoặc None nếu bài không còn ở Đã lên lịch."""
	now = now or now_datetime()
	row = frappe.db.sql(
		f"select status, publish_at from `tab{POST}` where name=%s for update", (name,), as_dict=True
	)
	if not row or row[0].status != SCHEDULED:
		frappe.db.rollback()
		return None
	if now - get_datetime(row[0].publish_at) > timedelta(minutes=content_posts.late_window_minutes()):
		_finish(
			name,
			MISSED,
			last_error=_("Quá giờ đăng hơn {0} phút: không đăng").format(content_posts.late_window_minutes()),
		)
		_notify(name, _("Bài {0} bị bỏ lỡ: quá giờ đăng").format(name))
		frappe.db.commit()
		return MISSED
	frappe.db.set_value(POST, name, {"status": PUBLISHING, "last_attempt_at": now}, update_modified=False)
	frappe.db.commit()  # chiếm xong, nhả khóa trước khi gọi Meta

	doc = frappe.get_doc(POST, name)
	try:
		message, link_inside = render_message(doc.content, doc.utm_link)
		image = media.read_image(name, doc.image) if doc.image else None
		post_id = client.publish(message, None if link_inside else doc.utm_link, image)
	except media.MediaError as e:
		return _fail(name, FAILED, str(e))
	except ContentGraphError as e:
		if e.uncertain:
			return _fail(
				name,
				UNCERTAIN,
				_("{0}. Mở Page kiểm bài đã lên chưa rồi bấm nút tương ứng").format(e.message_vi),
			)
		return _fail(name, FAILED, e.message_vi)
	url = client.permalink(post_id) or f"https://www.facebook.com/{post_id}"
	_finish(
		name, PUBLISHED, external_post_id=post_id, post_url=url, published_at=now_datetime(), last_error=None
	)
	frappe.db.commit()
	return PUBLISHED


def _fail(name: str, status: str, message: str) -> str:
	_finish(name, status, last_error=message[:1000])
	_notify(name, _("Bài {0}: {1}").format(name, _(status)), message)
	frappe.db.commit()
	return status


def _finish(name: str, status: str, **values) -> None:
	values["status"] = status
	frappe.db.set_value(POST, name, values, update_modified=False)
	doc = frappe.get_doc(POST, name)
	doc.add_comment(
		"Info",
		_("Job đăng bài: {0}").format(_(status))
		+ (f" · {values['last_error']}" if values.get("last_error") else ""),
	)


def sweep_stuck() -> None:
	"""Bài Đang đăng quá STUCK_MINUTES (worker chết giữa chừng): Cần kiểm, không thử lại."""
	limit = now_datetime() - timedelta(minutes=STUCK_MINUTES)
	for name in frappe.get_all(
		POST, filters={"status": PUBLISHING, "last_attempt_at": ["<", limit]}, pluck="name"
	):
		_fail(
			name, UNCERTAIN, _("Job đăng dừng giữa chừng: mở Page kiểm bài đã lên chưa rồi bấm nút tương ứng")
		)


def _notify(name: str, subject: str, content: str = "") -> None:
	"""Báo người tạo bài và người sửa gần nhất bằng Notification Log. Không commit."""
	owner, modified_by = frappe.db.get_value(POST, name, ["owner", "modified_by"]) or (None, None)
	for user in {u for u in (owner, modified_by) if u and u not in ("Administrator", "Guest")} or {
		"Administrator"
	}:
		if not cint(frappe.db.get_value("User", user, "enabled")):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": content,
					"document_type": POST,
					"document_name": name,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Omni: không báo được kết quả đăng bài", message=f"post={name}")
