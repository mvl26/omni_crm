"""Khung báo giá của bot kịch bản (K7, mục 3.3): hiện bot KHÔNG tự báo giá, luôn chuyển nhân viên báo giá.

Các ô kết quả báo giá (`BG_*_KETQUA`) đang "Tắt". Lệnh `quote_show` hỏi `pricing.can_auto_quote`; chưa đủ điều kiện
thì gửi câu hẹn (ô `BG_XACNHAN_*`) và tạo việc cho nhân viên. Tệp này không phát ra con số nào.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from omni_crm.script import engine, pricing, send, state, timeutil
from omni_crm.script.flows import postdemo
from omni_crm.script.router import RouteContext

LEAD = "CRM Lead"
NODE_PROMISE_IN_HOURS = "BG_XACNHAN_TRONGGIO"
NODE_PROMISE_AFTER_HOURS = "BG_XACNHAN_NGOAIGIO"
J_CONSULT = "Đang tư vấn"
EARLY_JOURNEYS = (None, "", "Mới", J_CONSULT)  # chỉ ghi "Đang tư vấn" khi Lead chưa đi xa hơn
STEP = "3"
VALUE_MAX = 300
# Câu trả lời khách đã cho ở nhánh 3b (K4 lưu trong script_state): khóa -> nhãn cho nhân viên
ANSWER_LABELS = {
	"items": "Mặt hàng",
	"quantity": "Số lượng",
	"need_by": "Thời gian cần hàng",
	"substitute": "Hãng thay thế",
	"sc_items": "Số mặt hàng quản lý",
	"sc_users": "Số người dùng",
	"sc_sites": "Số cơ sở / kho",
	"spd_sites": "Số cơ sở y tế",
	"spd_items": "Số mặt hàng trong danh mục",
	"spd_value": "Giá trị vật tư mỗi tháng",
	"adjust": "Điều chỉnh khách muốn",
}


def _answers(identity: str) -> list[str]:
	data = state.get(identity)["script_state"]
	return [
		f"{_(label)}: {str(data[key])[:VALUE_MAX]}" for key, label in ANSWER_LABELS.items() if data.get(key)
	]


def fallback_to_staff(ctx: RouteContext, service: str | None, why: str = "") -> str | None:
	"""Nhân viên báo giá thay bot: gửi câu hẹn (3.5) và tạo việc; trả tên CRM Task (một lần cho mỗi lượt tin)."""
	if state.get(ctx.identity)["script_state"].get("quote_task_batch") == ctx.batch_id:
		return None
	svc = pricing.normalize_service(service) or postdemo.service_of(ctx.lead)
	code = NODE_PROMISE_AFTER_HOURS if timeutil.cutoff_1630(now_datetime()) else NODE_PROMISE_IN_HOURS
	email = (frappe.db.get_value(LEAD, ctx.lead, "email") if ctx.lead else "") or ""
	if send.send_node(ctx.identity, code, postdemo.flow_vars(ctx, {"email": email})):
		state.set_node(ctx.identity, code, STEP)
	if ctx.lead and frappe.db.get_value(LEAD, ctx.lead, "omni_journey_status") in EARLY_JOURNEYS:
		state.set_journey(ctx.lead, J_CONSULT)
	lines = [_("Bot chưa tự báo giá, nhân viên báo giá cho khách."), why, *_answers(ctx.identity)]
	task = postdemo.create_task(ctx.lead, svc, _("Báo giá cho khách"), "\n".join(x for x in lines if x))
	state.update_state(ctx.identity, quote_task_batch=ctx.batch_id)
	return task


def show_or_fallback(ctx: RouteContext, service: str | None = None) -> bool:
	"""Điểm vào khi đã thu đủ thông tin báo giá: True nếu bot tự báo giá; hiện luôn False (nhân viên báo giá)."""
	svc = pricing.normalize_service(service) or postdemo.service_of(ctx.lead)
	_ok, why = pricing.can_auto_quote(svc)
	# `can_auto_quote` chưa bao giờ đúng (thiếu đầu vào 3.3.5, chưa nối ERP). Khi đúng, câu báo giá phải qua
	# `pricing.assert_no_ai_numbers` trước khi gửi; phần đó chưa xây nên vẫn chuyển nhân viên.
	fallback_to_staff(ctx, svc, why)
	return False


def _act_quote_show(ctx: RouteContext, arg: str, node: dict) -> bool:
	"""`quote_show` trên ô `BG_*_KETQUA`: chưa tự báo giá được thì chuyển nhân viên và dừng chuỗi lệnh của ô."""
	show_or_fallback(ctx, node.get("service"))
	return True  # không chạy tiếp `journey:Đã gửi báo giá` khi chưa có báo giá nào được gửi


def _act_quote(ctx: RouteContext, arg: str, node: dict) -> None:
	"""`quote:manual`: ghi nhớ trường hợp này nhân viên báo giá riêng (vượt bậc cao nhất, chưa rõ quy mô)."""
	if (arg or "").strip().lower() == "manual":
		state.update_state(ctx.identity, quote_manual=1)


def _act_quote_match(ctx: RouteContext, arg: str, node: dict) -> None:
	"""`quote_match`: đối chiếu mặt hàng với ERP chưa được nối, không làm gì."""
	return None


engine.register_action("quote_show", _act_quote_show)
engine.register_action("quote", _act_quote)
engine.register_action("quote_match", _act_quote_match)
