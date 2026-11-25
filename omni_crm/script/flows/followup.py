"""Nhắn lại sau khi gửi tài liệu / báo giá (K5; kịch bản mục 4.1-4.3, 4.6) - phần lệnh action.

Các nhánh của 4.3 (Đã xem rồi, Chưa nhận được, Chưa kịp xem, Có câu hỏi thêm, Để sau) nằm trong kho ô kịch bản
(`NL_*`, `CO_CAUHOI`, `DE_SAU`) và do bộ chạy K2 thực hiện. Tệp này chỉ đăng ký các lệnh thuộc K5:
`schedule:<kind>`, `cancel_followups`, tên các loại tin hẹn giờ (đánh dấu trên ô, không làm gì khi chạy) và lệnh
`task:<loại>` dự phòng khi chưa phần nào khác đăng ký.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _

from omni_crm.script import engine, scheduler, store, timeutil
from omni_crm.script.router import RouteContext

TASK_TITLES = {
	"resend_docs": "Gửi lại tài liệu cho khách",
	"resend_quote": "Gửi lại báo giá cho khách",
	"demo_invite": "Gửi lời mời demo cho khách",
	"docs": "Gửi tài liệu cho khách",
	"quote": "Gửi báo giá cho khách",
}


def action_schedule(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `schedule:<kind>`: đặt tin hẹn giờ theo loại (mốc tính theo kịch bản của từng loại)."""
	kind = (arg or "").strip().lower()
	if kind == "followup_docs":
		scheduler.on_docs_sent(ctx.lead)
	elif kind == "followup_quote":
		scheduler.on_quote_sent(ctx.lead)
	elif kind == "silence_3d":
		scheduler.schedule_silence(ctx.identity, ctx.lead)
	elif kind == "lost_3m":
		from omni_crm.script.flows import (
			postdemo,
		)  # K6 giữ luật "một lần cho mỗi Lead, không gửi khi Không liên hệ"

		postdemo.schedule_lost_3m(ctx.lead, ctx.identity)
	elif kind == "postdemo_1":
		now = scheduler._now()
		due = (
			now + timedelta(minutes=2) if scheduler.fast_for(ctx.identity) else timeutil.postdemo_send_at(now)
		)
		scheduler.schedule(kind, ctx.identity, ctx.lead, due, None)
	return None


def _later(identity: str, days: int):
	now = scheduler._now()
	if scheduler.fast_for(identity):
		return now + timedelta(minutes=min(days, 5))
	return scheduler.clamp_proactive(now + timedelta(days=days))


def action_cancel_followups(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `cancel_followups`: hủy mọi tin hẹn giờ đang chờ của Lead (Để sau, hủy đăng ký, kết thúc)."""
	scheduler.cancel_for(ctx.lead)
	return None


def action_marker(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Tên loại tin hẹn giờ ghi trên ô (`followup_docs`, `silence_3d`...): chỉ đánh dấu, không làm gì."""
	return None


def action_task(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `task:<loại>` dự phòng: tạo việc cho nhân viên bằng `tasks.create_task` (K3)."""
	try:
		from omni_crm.script import tasks
	except ImportError:
		frappe.logger("omni").info("omni script: chưa có tasks, bỏ qua task:%s", arg)
		return None
	kind = (arg or "").strip().lower()
	service = scheduler.current_service(ctx.identity, ctx.lead) or "KHAC"
	title = _(TASK_TITLES.get(kind) or "Việc từ bot kịch bản: {0}").format(kind)
	description = _("Bot kịch bản tạo việc sau ô {0}.").format(store.normalize_code(node.get("code")))
	tasks.create_task(ctx.lead, service, title, description, due=timeutil.task_due(scheduler._now()))
	return None


engine.register_action("schedule", action_schedule)
engine.register_action("cancel_followups", action_cancel_followups)
for _kind in scheduler.KINDS:
	engine.register_action(_kind, action_marker)
if "task" not in engine._actions:  # phần khác (K3/K4) đăng ký sau sẽ thay lệnh dự phòng này
	engine.register_action("task", action_task)
