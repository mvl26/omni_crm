"""Job mỗi phút: lấy nền tảng đăng đến giờ, khóa nguyên tử từng dòng rồi xếp hàng `run.publish_target`.

- "Đã lên lịch" và scheduled_at <= now: quá `late_window_minutes` thì Thất bại (không đăng); không thì khóa bằng
  UPDATE ... WHERE status='Đã lên lịch' và chỉ xếp hàng khi đúng 1 dòng bị đổi (2 lượt chạy chồng không đăng trùng).
- "Đang xử lý" có next_retry_at <= now: thử lại (khóa bằng cách xóa next_retry_at có điều kiện).
- "Đang xử lý" không có next_retry_at và không đổi quá STUCK_MINUTES (worker chết giữa chừng): hẹn thử lại ngay;
  lần thử đó luôn đối chiếu (reconcile) trước, không đăng mù.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from omni_crm.marketing import rules
from omni_crm.marketing import settings as mk_settings

TARGET = "Omni Content Target"
BATCH = 50
STUCK_MINUTES = 15
LATE_MESSAGE = "Đã quá cửa sổ cho phép đăng muộn"


def _rowcount() -> int:
	return int(frappe.db._cursor.rowcount or 0)


def _claim_scheduled(name: str) -> bool:
	frappe.db.sql(
		"""update `tabOmni Content Target` set status=%s, next_retry_at=null, modified=%s
		where name=%s and status=%s""",
		(rules.T_PROCESSING, now_datetime(), name, rules.T_SCHEDULED),
	)
	return _rowcount() == 1


def _claim_retry(name: str, now) -> bool:
	frappe.db.sql(
		"""update `tabOmni Content Target` set next_retry_at=null, modified=%s
		where name=%s and status=%s and next_retry_at is not null and next_retry_at <= %s""",
		(now_datetime(), name, rules.T_PROCESSING, now),
	)
	return _rowcount() == 1


def _fail_late(name: str) -> bool:
	frappe.db.sql(
		"""update `tabOmni Content Target` set status=%s, error_type='terminal', last_error=%s, modified=%s
		where name=%s and status=%s""",
		(rules.T_FAILED, _(LATE_MESSAGE), now_datetime(), name, rules.T_SCHEDULED),
	)
	return _rowcount() == 1


def _enqueue(name: str) -> None:
	frappe.enqueue(
		"omni_crm.marketing.publisher.run.publish_target",
		queue="long",
		job_id=f"oc-publish-{name}",
		deduplicate=True,
		name=name,
	)


def due_targets(now=None) -> dict[str, list]:
	"""Các dòng đến giờ (chưa khóa): {"scheduled": [...], "retry": [...], "stuck": [...]}."""
	now = now or now_datetime()
	scheduled = frappe.get_all(
		TARGET,
		filters={"status": rules.T_SCHEDULED, "scheduled_at": ["<=", now]},
		fields=["name", "scheduled_at", "content_item"],
		order_by="scheduled_at asc",
		limit=BATCH,
	)
	retry = frappe.get_all(
		TARGET,
		filters={"status": rules.T_PROCESSING, "next_retry_at": ["<=", now]},
		fields=["name", "content_item"],
		order_by="next_retry_at asc",
		limit=BATCH,
	)
	stuck = frappe.get_all(
		TARGET,
		filters={
			"status": rules.T_PROCESSING,
			"next_retry_at": ["is", "not set"],
			"modified": ["<", add_to_date(now, minutes=-STUCK_MINUTES)],
		},
		pluck="name",
		limit=BATCH,
	)
	return {"scheduled": scheduled, "retry": retry, "stuck": stuck}


def dispatch_due() -> dict[str, int]:
	"""Tác vụ mỗi phút (hooks.py). Trả số dòng đã xử lý (để chạy tay xem)."""
	out = {"enqueued": 0, "late": 0, "skipped": 0, "stuck": 0}
	if not mk_settings.hub_enabled():
		return out
	from omni_crm.marketing.publisher import run

	now = now_datetime()
	late_before = add_to_date(now, minutes=-mk_settings.late_window_minutes())
	rows = due_targets(now)
	for name in rows["stuck"]:
		frappe.db.set_value(TARGET, name, "next_retry_at", now, update_modified=False)
		frappe.db.commit()
		out["stuck"] += 1
	for row in rows["scheduled"]:
		if get_datetime(row.scheduled_at) < late_before:
			if _fail_late(row.name):
				frappe.db.commit()
				run.log_attempt(row.name, outcome="error", error_type="terminal", message=_(LATE_MESSAGE))
				run.after_change(row.name)
				out["late"] += 1
			continue
		if _claim_scheduled(row.name):
			frappe.db.commit()  # khóa phải thấy được trước khi worker đọc dòng
			_enqueue(row.name)
			out["enqueued"] += 1
		else:
			frappe.db.rollback()
			out["skipped"] += 1
	for row in rows["retry"] + [frappe._dict(name=n) for n in rows["stuck"]]:
		if _claim_retry(row.name, now_datetime()):
			frappe.db.commit()
			_enqueue(row.name)
			out["enqueued"] += 1
		else:
			frappe.db.rollback()
			out["skipped"] += 1
	return out
