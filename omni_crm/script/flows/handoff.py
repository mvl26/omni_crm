"""Gặp nhân viên, tự chuyển người, tạm dừng / trả lại bot, dừng hẳn (kịch bản mục 1.4, C.2, C.4).

Câu gửi khách lấy từ Omni Script Node (mã `GAP_NV_*`, `C4_*`); module này không chứa câu nào cho khách.
Trạng thái một lần chuyển nằm trong `Omni Identity.script_state`:
- `handoff`: {at, service, assignee, reason, task, mode: in_hours | after_hours, escalated, phone}
- `flow`: `handoff_service` (đang chờ khách chọn dịch vụ) hoặc `handoff_phone` (đang chờ SĐT gọi lại)

Thứ tự bắt buộc khi tạm dừng / dừng hẳn: câu cho khách phải được GỬI XONG trước khi đặt `bot_paused_until` hoặc
`omni_do_not_contact`, vì `send.send_time_check` kiểm lại điều kiện dừng ngay trước khi gửi (xem `flush_pending`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.bot import handoff as bot_handoff
from omni_crm.script import engine, router, send, state, switch, tasks, timeutil, validate
from omni_crm.script.router import RouteContext

IDENTITY = "Omni Identity"
LEAD = "CRM Lead"
OUTBOUND = "Omni Outbound Message"
CHANNEL = "Omni Channel"

NODE_ENTRY = "GAP_NHANVIEN"
NODE_IN_HOURS = "GAP_NV_TRONGGIO"
NODE_AFTER_HOURS = "GAP_NV_NGOAIGIO"
NODE_ASK_SERVICE = "GAP_NV_HOIDICHVU"
NODE_CHAT = "GAP_NV_NHANTIN"
NODE_CALL = "GAP_NV_GOIDIEN"
NODE_CALL_CONFIRM = "GAP_NV_GOIDIEN_XACNHAN"
NODE_CLOSING = "GAP_NV_CAUKET"
NODE_BAD_PHONE = "SAI_SDT"
NODE_STOP = "C4_HUYDANGKY"
NODE_DELETE_DATA = "C4_XOADULIEU"
SERVICE_BUTTONS = {
	"GAP_NV_VATTU": "VATTU",
	"GAP_NV_PHANMEM": "SUPPLYCORE",
	"GAP_NV_SPD": "SPD",
	"GAP_NV_KHAC": "KHAC",
}

FLOW_SERVICE = "handoff_service"
FLOW_PHONE = "handoff_phone"
MODE_IN, MODE_AFTER = "in_hours", "after_hours"
UNANSWERED_MINUTES = 15  # 1.4: sau 15 phút trong giờ chưa có nhân viên nhận
PHONE_REASKS = 1  # 3.4: số sai chỉ hỏi lại 1 lần
NODE_CALL_CONFIRM_IN = (
	"GAP_NV_GOIDIEN_XACNHAN_TRONGGIO"  # seed/nodes_k3.json (Cần duyệt): xác nhận gọi lại trong giờ
)
COMPLIANCE = "TUAN_THU"
MANAGER = "QUAN_LY"
JOURNEY_STOPPED = "Không liên hệ"
JUNK_STATUS = "Junk"
SCAN_LIMIT = 200

# Lý do tự chuyển người (C.2); khóa là giá trị `reason` của `start`
REASON_REQUESTED = "requested"
REASON_COMPLAINT = "complaint"
REASON_MISUNDERSTOOD = "misunderstood_3"
REASON_NOT_IN_KB = "not_in_kb"
REASON_AGREES_TO_BUY = "agrees_to_buy"
HIGH_PRIORITY = {REASON_COMPLAINT, REASON_AGREES_TO_BUY}
KNOWN_REASONS = {
	REASON_REQUESTED,
	REASON_COMPLAINT,
	REASON_MISUNDERSTOOD,
	REASON_NOT_IN_KB,
	REASON_AGREES_TO_BUY,
}
REASON_MAX = 200
NODE_REASONS = {  # ô kịch bản có lệnh `handoff` → lý do
	"KB_CHUYENVIEN": REASON_NOT_IN_KB,
	"SD_BATDAU": REASON_AGREES_TO_BUY,
	"DAT_HANG": REASON_AGREES_TO_BUY,
}
STOP_EXACT = {"không cần", "ko cần", "khong can", "stop", "unsubscribe"}
STOP_CONTAINS = (
	"đừng nhắn nữa",
	"dừng nhắn",
	"đừng nhắn tin",
	"hủy đăng ký",
	"huỷ đăng ký",
	"huy dang ky",
	"dung nhan nua",
	"ngừng nhận tin",
)


def reason_label(reason: str | None) -> str:
	"""Nhãn cố định của lý do chuyển người (cho nhân viên; không chứa lời khách)."""
	labels = {
		REASON_REQUESTED: _("Khách muốn gặp nhân viên"),
		REASON_COMPLAINT: _("Khách phàn nàn hoặc bực bội"),
		REASON_MISUNDERSTOOD: _("Bot không hiểu khách 3 lần liên tiếp"),
		REASON_NOT_IN_KB: _("Câu hỏi không có trong kho kiến thức"),
		REASON_AGREES_TO_BUY: _("Khách đồng ý mua"),
	}
	return labels.get((reason or "").strip() or REASON_REQUESTED, _("Bot kịch bản chuyển người"))


# --- Tiện ích ----------------------------------------------------------------------------------------------------


def _vars(ctx: RouteContext, extra: dict | None = None) -> dict:
	"""Biến cho các ô `GAP_NV_*`: lấy từ engine (K2) nếu đã có, luôn tự tính `ngay_lam_viec_tiep_theo`."""
	values: dict = {}
	try:
		values = dict(engine.variables(ctx, extra) or {})
	except Exception:  # engine chưa gộp (NotImplementedError) hoặc lỗi: tự dựng biến tối thiểu
		values = {}
	values.setdefault("ngay_lam_viec_tiep_theo", timeutil.next_working_day_text(now_datetime()))
	values.update(extra or {})
	return values


def _send(ctx: RouteContext, code: str, extra: dict | None = None) -> str | None:
	"""Gửi một ô qua `send.send_node` (KHÔNG chạy chuỗi action của ô, tránh gọi vòng lại `handoff`)."""
	return send.send_node(ctx.identity, code, _vars(ctx, extra))


def flush_pending(identity: str | None) -> int:
	"""Gửi ngay các tin kịch bản còn chờ của định danh (đường gửi chung `send_outbound`); trả số tin CÒN chờ (chưa đi).

	Phải gọi trước khi tạm dừng bot / đánh dấu không liên hệ: nếu không, câu vừa xếp bị chặn lúc gửi. Trả > 0 nghĩa là
	còn tin chưa gửi được (lỗi tạm, đang thử lại): bên gọi không nên tạm dừng ngay kẻo lần thử lại bị chặn.
	"""
	if not identity:
		return 0
	from omni_crm.pipeline import outbound

	frappe.db.commit()  # không giữ khóa DB khi gọi ra ngoài
	filters = {"identity": identity, "source": "Bot", "status": "Queued", "script_node": ["is", "set"]}
	for name in frappe.get_all(OUTBOUND, filters=filters, pluck="name", order_by="creation asc"):
		try:
			outbound.send_outbound(name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: không gửi kịp tin kịch bản trước khi dừng", message=f"out={name}")
	return cint(frappe.db.count(OUTBOUND, filters))


def _handoff_state(identity: str) -> dict:
	value = state.get(identity)["script_state"].get("handoff")
	return value if isinstance(value, dict) else {}


def _live_state(identity: str) -> dict:
	"""Trạng thái chuyển người còn hiệu lực: chỉ khi bot đang tạm dừng với khách (nhân viên đang giữ cuộc trò chuyện).

	Bot đã được mở lại (hết hạn tạm dừng, nút cũ "Cho bot tiếp tục", "Trả lại cho bot") thì trạng thái cũ là rác.
	"""
	return _handoff_state(identity) if bot_handoff.is_bot_paused(identity) else {}


def _flow(identity: str) -> str:
	return str(state.get(identity)["script_state"].get("flow") or "")


def _current_service(lead: str | None) -> str:
	"""Dịch vụ khách đang hỏi: mã ghi sau cùng trên Lead (`omni_services`); chưa có trả chuỗi rỗng."""
	items = tasks.lead_services(lead)
	return items[-1] if items else ""


def _lead_label(lead: str | None) -> str:
	name = frappe.db.get_value(LEAD, lead, "lead_name") if lead else None
	return " ".join(str(name or lead or "").split())[:60]


def _assign_lead(lead: str, user: str) -> bool:
	"""Giao cuộc trò chuyện (Lead) cho `user` bằng assign_to của Frappe; đã được giao thì bỏ qua."""
	from frappe.desk.form import assign_to

	if frappe.db.exists(
		"ToDo",
		{"reference_type": LEAD, "reference_name": lead, "allocated_to": user, "status": "Open"},
	):
		return False
	try:
		with flags.omni_bot(), tasks.as_admin():
			assign_to.add(
				{
					"assign_to": [user],
					"doctype": LEAD,
					"name": lead,
					"description": _("Khách cần gặp nhân viên (bot kịch bản)"),
				},
				ignore_permissions=True,
			)
		return True
	except Exception:
		frappe.log_error(title="Omni: không giao được Lead khi chuyển người", message=f"lead={lead}")
		return False


def _notify(lead: str, users: list[str], label: str, identity: str | None = None) -> None:
	"""Báo nhân viên bằng thông báo hệ thống + chuông CRM có sẵn (nội dung không chứa lời khách)."""
	users = [u for u in dict.fromkeys(users) if u]
	if not users:
		return
	try:
		channel = send.channel_of(identity) if identity else None
		channel_type = frappe.db.get_value(CHANNEL, channel, "channel_type") if channel else None
		bot_handoff._notify_system(lead, users, label, channel_type)
	except Exception:
		frappe.log_error(title="Omni: không báo được chuyển người (kịch bản)", message=f"lead={lead}")


def _task_title(lead: str | None) -> str:
	return _("Khách cần gặp nhân viên: {0}").format(_lead_label(lead))


def _description(ctx: RouteContext, reason: str, service: str, extra: str = "") -> str:
	lines = [
		tasks.summary(ctx.lead, ctx.identity),
		_("Lý do chuyển: {0}").format(reason_label(reason)),
		_("Người nhận theo dịch vụ: {0}").format(tasks.service_label(service)),
	]
	if (
		reason and reason not in KNOWN_REASONS
	):  # lý do do luồng khác ghi (có thể trích lời khách): che email/SĐT
		lines.append(_("Chi tiết: {0}").format(tasks.mask_pii(" ".join(reason.split()))[:REASON_MAX]))
	if extra:
		lines.append(extra)
	return "\n".join(lines)


# --- Bắt đầu chuyển người ------------------------------------------------------------------------------------------


def start(ctx: RouteContext, service: str | None = None, reason: str = "", task_only: bool = False) -> None:
	"""Chuyển khách cho nhân viên (1.4, C.2). `reason`: requested | complaint | misunderstood_3 | not_in_kb | agrees_to_buy.

	Trong giờ: câu chờ, giao Lead cho người nhận của dịch vụ, tạo việc kèm tóm tắt, tạm dừng bot. Ngoài giờ (hoặc
	`task_only`): câu hẹn + 2 nút, tạo việc hạn ngày làm việc kế tiếp, bot vẫn tư vấn. Chưa rõ dịch vụ: hỏi 1 lần.
	"""
	if send.blocked_reason(ctx.identity):
		return
	reason = (reason or "").strip() or REASON_REQUESTED
	if not ctx.lead:  # vẫn trả lời khách, nhưng không tạo được việc: báo quản trị
		tasks.warn_no_lead(ctx.identity, reason_label(reason))
	svc = _resolve_service(ctx, service, reason, task_only)
	if not svc:
		return  # đã hỏi dịch vụ, chờ khách chọn
	if task_only or not timeutil.in_hours(now_datetime()):
		_after_hours(ctx, svc, reason)
	else:
		_in_hours(ctx, svc, reason)


def _resolve_service(ctx: RouteContext, service: str | None, reason: str, task_only: bool) -> str:
	"""Dịch vụ để chọn người nhận; chưa có thì gửi câu hỏi dịch vụ (một lần) và trả chuỗi rỗng."""
	svc = tasks.normalize_service(service) or _current_service(ctx.lead)
	if svc:
		return svc
	if _live_state(ctx.identity).get("assignee"):  # đang có người giữ cuộc trò chuyện
		return tasks.OTHER
	if _flow(ctx.identity) == FLOW_SERVICE:  # đã hỏi rồi: không hỏi lại
		return tasks.OTHER
	if not _send(ctx, NODE_ASK_SERVICE):
		return tasks.OTHER  # ô hỏi dịch vụ không gửi được: giao dòng "Khác"
	state.update_state(
		ctx.identity, flow=FLOW_SERVICE, handoff_pending={"reason": reason, "task_only": int(task_only)}
	)
	state.set_node(ctx.identity, NODE_ASK_SERVICE, "1")
	return ""


def _pending(identity: str) -> dict:
	value = state.get(identity)["script_state"].get("handoff_pending")
	return value if isinstance(value, dict) else {}


def _in_hours(ctx: RouteContext, svc: str, reason: str, send_text: bool = True) -> None:
	"""Trong giờ: câu chờ → giao Lead → việc + tóm tắt → báo người nhận → tạm dừng bot (sau khi câu đã gửi)."""
	previous = _live_state(ctx.identity)
	# Khách đổi chủ đề khi nhân viên đang giữ cuộc trò chuyện: người đang nhận giữ nguyên (1.4)
	assignee = previous.get("assignee") or tasks.assignee_for(ctx.lead, svc)
	if send_text:
		_send(ctx, NODE_IN_HOURS)
	task = None
	if ctx.lead:
		if assignee:
			_assign_lead(ctx.lead, assignee)
		task = tasks.create_task(
			ctx.lead,
			previous.get("service") or svc,
			_task_title(ctx.lead),
			_description(ctx, reason, svc),
			priority="High" if reason in HIGH_PRIORITY else "Medium",
		)
		if assignee:
			_notify(ctx.lead, [assignee], reason_label(reason), ctx.identity)
	state.update_state(
		ctx.identity,
		flow=None,
		handoff_pending=None,
		handoff={
			"at": str(now_datetime()),  # mốc tính 15 phút chờ nhân viên
			"service": previous.get("service") or svc,
			"assignee": assignee,
			"reason": reason,
			"task": task,
			"mode": MODE_IN,
			"escalated": cint(previous.get("escalated")),
		},
	)
	state.set_node(ctx.identity, NODE_IN_HOURS, "1")
	_pause(ctx.lead, ctx.identity, reason)


def _pause(lead: str | None, identity: str, reason: str = "") -> bool:
	"""Tạm dừng bot theo `handoff_pause_hours` (cơ chế có sẵn; nhân viên bấm "Trả lại cho bot" để mở sớm).

	Gửi hết tin đang chờ trước. Còn tin chưa đi được (lỗi tạm) thì CHƯA tạm dừng (kẻo lần thử lại bị chặn), ghi
	`pause_pending` để `check_unanswered` tạm dừng sau; việc và thông báo cho nhân viên vẫn đã tạo. True nếu đã dừng.
	"""
	left = flush_pending(identity)
	if left:
		state.update_state(identity, pause_pending=1, pause_reason=reason or None)
		frappe.log_error(
			title="Omni: chưa tạm dừng bot vì câu chờ chưa gửi xong",
			message=f"identity={identity} con_cho={left}",
		)
		return False
	state.update_state(identity, pause_pending=None, pause_reason=None)
	hours = bot_handoff.pause_hours() or bot_handoff.DEFAULT_PAUSE_HOURS
	if not lead:
		until = add_to_date(None, hours=hours)
		frappe.db.set_value(IDENTITY, identity, "bot_paused_until", until, update_modified=False)
		return True
	already = bot_handoff.paused_until(lead)
	until = bot_handoff.pause_lead(lead, hours)
	if not already:
		bot_handoff.note_on_lead(
			lead,
			_(
				'Bot kịch bản chuyển người: {0}. Bot tạm dừng đến {1} hoặc đến khi nhân viên bấm "Trả lại cho bot".'
			).format(reason_label(reason), frappe.utils.format_datetime(until)),
		)
	return True


def finish_pending_pauses() -> int:
	"""Tạm dừng nốt các khách đã chuyển người mà lúc đó câu chờ chưa gửi xong; trả số khách đã tạm dừng."""
	rows = frappe.get_all(
		IDENTITY,
		filters={"script_state": ["like", '%"pause_pending": 1%']},
		fields=["name", "lead"],
		limit_page_length=SCAN_LIMIT,
	)
	done = 0
	for row in rows:
		try:
			reason = state.get(row.name)["script_state"].get("pause_reason") or REASON_REQUESTED
			if switch.enabled_for(row.name) and _pause(row.lead, row.name, reason):
				done += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi tạm dừng bù sau chuyển người", message=f"identity={row.name}")
	return done


def _after_hours(ctx: RouteContext, svc: str, reason: str, send_text: bool = True) -> None:
	"""Ngoài giờ: câu hẹn + nút [Nhắn tin tại đây] [Gọi điện cho tôi], tạo việc hạn ngày làm việc kế tiếp; bot KHÔNG dừng."""
	previous = _live_state(ctx.identity)
	assignee = previous.get("assignee") or tasks.assignee_for(ctx.lead, svc)
	if send_text:
		_send(ctx, NODE_AFTER_HOURS)
	task = None
	if ctx.lead:
		task = tasks.create_task(
			ctx.lead,
			svc,
			_task_title(ctx.lead),
			_description(ctx, reason, svc),
			priority="High" if reason in HIGH_PRIORITY else "Medium",
		)
		if assignee:
			_notify(ctx.lead, [assignee], reason_label(reason), ctx.identity)
	state.update_state(
		ctx.identity,
		flow=None,
		handoff_pending=None,
		handoff={
			"at": str(now_datetime()),
			"service": svc,
			"assignee": assignee,
			"reason": reason,
			"task": task,
			"mode": MODE_AFTER,
			"escalated": 0,
		},
	)
	state.set_node(ctx.identity, NODE_AFTER_HOURS, "1")


# --- Nút và tin gõ trong luồng -------------------------------------------------------------------------------------


def on_request(ctx: RouteContext) -> None:
	"""Router: khách bấm "Gặp nhân viên" (`#GAP_NHANVIEN`)."""
	start(ctx, reason=REASON_REQUESTED)


def on_service_button(ctx: RouteContext) -> None:
	"""Router: khách chọn dịch vụ ở câu hỏi "trao đổi về dịch vụ nào" → ghi dịch vụ rồi chuyển người."""
	svc = SERVICE_BUTTONS.get(ctx.payload_code or "", tasks.OTHER)
	pending = _pending(ctx.identity)
	state.add_service(ctx.lead, svc)
	start(ctx, svc, pending.get("reason") or REASON_REQUESTED, bool(cint(pending.get("task_only"))))


def on_service_typed(ctx: RouteContext) -> bool:
	"""Trạng thái `handoff_service`: khách gõ thay vì chọn → không hỏi lại, giao người nhận dòng "Khác" (1.4)."""
	if is_stop_phrase(ctx.text):
		on_stop(ctx)
		return True
	pending = _pending(ctx.identity)
	start(ctx, tasks.OTHER, pending.get("reason") or REASON_REQUESTED, bool(cint(pending.get("task_only"))))
	return True


def on_chat_here(ctx: RouteContext) -> None:
	"""Router: khách chọn "Nhắn tin tại đây" → câu xác nhận + câu kết; ghi lựa chọn vào việc."""
	_send(ctx, NODE_CHAT)
	_send(ctx, NODE_CLOSING)
	state.update_state(ctx.identity, flow=None)
	tasks.append_note(_handoff_state(ctx.identity).get("task"), _("Khách chọn: nhắn tin trong khung chat."))


def on_call_me(ctx: RouteContext) -> None:
	"""Router: khách chọn "Gọi điện cho tôi" → xin SĐT, chờ khách gõ số (trạng thái `handoff_phone`)."""
	ask_phone(ctx.identity, _vars(ctx))


def ask_phone(identity: str, variables: dict | None = None) -> str | None:
	"""Gửi câu xin SĐT gọi lại; gửi được thì đặt trạng thái chờ số. Trả tên tin ra (None nếu không gửi được)."""
	name = send.send_node(identity, NODE_CALL, variables or {})
	if name:
		state.update_state(identity, flow=FLOW_PHONE, handoff_phone_tries=0)
		state.set_node(identity, NODE_CALL, "1")
	return name


def on_phone_typed(ctx: RouteContext) -> bool:
	"""Trạng thái `handoff_phone`: nhận SĐT khách gõ (kiểm bằng `validate`); sai thì hỏi lại 1 lần rồi thôi."""
	if ctx.payload_code:
		return False
	if is_stop_phrase(ctx.text):
		on_stop(ctx)
		return True
	phone = validate.find_phone(ctx.text) or validate.validate_phone(ctx.text)
	if phone:
		_phone_received(ctx, phone)
		return True
	tries = cint(state.get(ctx.identity)["script_state"].get("handoff_phone_tries")) + 1
	if tries > PHONE_REASKS:
		state.update_state(ctx.identity, flow=None, handoff_phone_tries=None)
		return False  # không ép khách: để bộ phân loại xử lý tin này như bình thường
	state.update_state(ctx.identity, handoff_phone_tries=tries)
	if not _send(
		ctx, NODE_BAD_PHONE
	):  # ô "Cần duyệt" chưa gửi được cho khách thường → hỏi lại bằng câu xin số
		_send(ctx, NODE_CALL)
	return True


def _phone_received(ctx: RouteContext, phone: str) -> None:
	local = validate.phone_local(phone)
	info = _handoff_state(ctx.identity)
	_save_phone(ctx.lead, phone)
	if timeutil.in_hours(now_datetime()):
		# Trong giờ (15 phút chưa ai nhận): không hứa "ngày làm việc kế tiếp"; ô riêng đang "Cần duyệt"
		if not _send(ctx, NODE_CALL_CONFIRM_IN, {"sdt": local}):
			frappe.logger("omni").info(
				"omni script: chưa có câu xác nhận gọi lại trong giờ identity=%s", ctx.identity
			)
	else:
		_send(ctx, NODE_CALL_CONFIRM, {"sdt": local})
	_send(ctx, NODE_CLOSING)
	state.update_state(
		ctx.identity, flow=None, handoff_phone_tries=None, handoff={**info, "phone": phone, "contact": "call"}
	)
	tasks.append_note(info.get("task"), _("Khách chọn: gọi điện. Số gọi lại: {0}").format(local))
	frappe.logger("omni").info(
		"omni script: nhận SĐT gọi lại identity=%s sdt=%s", ctx.identity, validate.mask_phone(phone)
	)


def _save_phone(lead: str | None, phone: str) -> None:
	"""Ghi SĐT vào Lead nếu Lead chưa có số (không ghi đè số nhân viên đã nhập); lỗi chỉ ghi log."""
	if not lead or frappe.db.get_value(LEAD, lead, "mobile_no"):
		return
	try:
		with flags.omni_bot():
			doc = frappe.get_doc(LEAD, lead)
			doc.mobile_no = phone
			doc.save(ignore_permissions=True)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: không ghi được SĐT gọi lại vào Lead", message=f"lead={lead}")


# --- 15 phút chưa có nhân viên nhận (1.4) --------------------------------------------------------------------------


def business_wait_minutes(
	started_at: datetime | None, now: datetime, cfg: timeutil.Config | None = None
) -> float:
	"""Hàm thuần (khi truyền `cfg`): số phút nhân viên đã có để nhận khách trong khung giờ làm việc ĐANG mở lúc `now`.

	Ngoài giờ trả 0; chuyển người từ khung trước (hôm qua 17:20) chỉ tính từ giờ mở cửa của khung hiện tại.
	"""
	cfg = cfg if cfg is not None else timeutil.load_config()
	hours, holidays = cfg
	window = None if now.date() in holidays else hours.get(now.weekday())
	if not started_at or not window:
		return 0.0
	open_at, close_at = datetime.combine(now.date(), window[0]), datetime.combine(now.date(), window[1])
	if not (open_at <= now <= close_at):
		return 0.0
	return max(0.0, (now - max(started_at, open_at)).total_seconds() / 60)


def should_escalate(
	waited_minutes: float, *, staff_replied: bool, escalated: bool, minutes: int = UNANSWERED_MINUTES
) -> bool:
	"""Hàm thuần: nhân viên đã có đủ `minutes` phút làm việc mà chưa nhận khách và chưa báo quản lý lần nào."""
	if staff_replied or escalated:
		return False
	return waited_minutes >= minutes


def _staff_replied(identity: str, since: datetime) -> bool:
	return bool(
		frappe.db.exists(OUTBOUND, {"identity": identity, "source": "Sale", "creation": [">=", since]})
	)


def check_unanswered(now: datetime | None = None) -> dict[str, int]:
	"""Tác vụ định kỳ (5 phút): khách đã được chuyển trong giờ mà 15 phút làm việc chưa ai nhận → bot xin SĐT và báo
	quản lý (QUAN_LY). Cũng tạm dừng bù các khách mà lúc chuyển câu chờ chưa gửi xong.

	Chỉ xét định danh bot kịch bản đang bật (`switch.enabled_for`). Commit sau từng khách; lỗi một khách không chặn khách khác.
	"""
	now = now or now_datetime()
	out = {"checked": 0, "escalated": 0, "paused": finish_pending_pauses()}
	rows = frappe.get_all(
		IDENTITY,
		filters=[
			["script_state", "like", '%"handoff"%'],
			["script_state", "not like", '%"escalated": 1%'],  # đã xử lý thì không quét lại
			["bot_paused_until", ">", now],
		],
		fields=["name", "lead"],
		order_by="modified desc",
		limit_page_length=SCAN_LIMIT,
	)
	for row in rows:
		out["checked"] += 1
		try:
			if _escalate_one(row.name, row.lead, now):
				out["escalated"] += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi kiểm 15 phút chuyển người", message=f"identity={row.name}")
	return out


def _escalate_one(identity: str, lead: str | None, now: datetime) -> bool:
	if not switch.enabled_for(identity):  # công tắc tắt với khách này: không gửi, không mở lại bot
		return False
	info = _handoff_state(identity)
	if info.get("mode") != MODE_IN or not info.get("at"):
		return False
	started = get_datetime(info["at"])
	if not should_escalate(
		business_wait_minutes(started, now),
		staff_replied=_staff_replied(identity, started),
		escalated=bool(cint(info.get("escalated"))),
	):
		return False
	state.update_state(identity, handoff={**info, "escalated": 1})
	names = frappe.get_all(IDENTITY, filters={"lead": lead}, pluck="name") if lead else [identity]
	paused = {n: frappe.db.get_value(IDENTITY, n, "bot_paused_until") for n in names}
	for n in names:  # phải mở bot thì câu xin số mới gửi được
		frappe.db.set_value(IDENTITY, n, "bot_paused_until", None, update_modified=False)
	asked = ask_phone(identity, {"ngay_lam_viec_tiep_theo": timeutil.next_working_day_text(now)})
	if not asked:  # không gửi được câu xin số: giữ nguyên tạm dừng, vẫn báo quản lý
		for n, until in paused.items():
			frappe.db.set_value(IDENTITY, n, "bot_paused_until", until, update_modified=False)
	frappe.db.commit()
	label = _("Khách chờ nhân viên quá {0} phút").format(UNANSWERED_MINUTES)
	if not lead:
		tasks.warn_no_lead(identity, label)
		return True
	manager = tasks.recipient(MANAGER)
	if manager:
		_notify(lead, [manager], label, identity)
	else:
		frappe.log_error(
			title="Omni: quá 15 phút chưa ai nhận, chưa cấu hình quản lý", message=f"lead={lead}"
		)
	done = (
		_("Bot đã xin số điện thoại của khách.") if asked else _("Bot chưa gửi được câu xin số điện thoại.")
	)
	tasks.append_note(info.get("task"), f"{label}. {done}")
	bot_handoff.note_on_lead(
		lead, f"{label}. {done} " + _("Đã báo quản lý.") if manager else f"{label}. {done}"
	)
	return True


# --- Trả lại cho bot ----------------------------------------------------------------------------------------------


def clear(lead: str) -> int:
	"""Xóa trạng thái chuyển người của mọi định danh của Lead (khi nhân viên bấm "Trả lại cho bot"); trả số định danh."""
	done = 0
	for identity in frappe.get_all(IDENTITY, filters={"lead": lead}, pluck="name"):
		data = state.get(identity)["script_state"]
		in_flow = str(data.get("flow") or "").startswith("handoff")
		if not (data.get("handoff") or data.get("handoff_pending") or data.get("pause_pending") or in_flow):
			continue
		values: dict = dict.fromkeys(
			("handoff", "handoff_pending", "handoff_phone_tries", "pause_pending", "pause_reason")
		)
		if in_flow:
			values["flow"] = None
		state.update_state(identity, **values)
		state.reset_reask(identity)
		done += 1
	return done


# --- Dừng hẳn (C.4) -----------------------------------------------------------------------------------------------


def is_stop_phrase(text: str | None) -> bool:
	"""Hàm thuần: tin khách là yêu cầu ngừng nhận tin ("không cần", "đừng nhắn nữa", "hủy đăng ký"...)."""
	raw = " ".join((text or "").lower().split()).strip(" .!,")
	if not raw:
		return False
	return raw in STOP_EXACT or any(p in raw for p in STOP_CONTAINS)


def is_customer(lead: str | None) -> bool:
	"""Lead đã thành khách hàng (đã chuyển thành Deal: `converted` = 1, kể cả Lead phụ đã gộp) → không gửi tin chào bán."""
	if not lead:
		return False
	from omni_crm.crm_bridge import lead as lead_bridge

	return bool(cint(frappe.db.get_value(LEAD, lead, "converted"))) or bool(
		lead_bridge.get_deal_for_lead(lead)
	)


def cancel_scheduled(lead: str | None) -> int:
	"""Hủy các tin hẹn giờ đang chờ của Lead (`scheduler.cancel_for`, phần K5; chưa gộp thì bỏ qua)."""
	if not lead:
		return 0
	try:
		from omni_crm.script import scheduler
	except ImportError:
		return 0
	try:
		return cint(scheduler.cancel_for(lead))
	except Exception:
		frappe.log_error(title="Omni: không hủy được tin hẹn giờ", message=f"lead={lead}")
		return 0


def stop_contact(
	lead: str | None, identity: str | None = None, confirm: Callable[[], object] | None = None
) -> None:
	"""Dừng hẳn với khách (C.4). Thứ tự: hủy tin hẹn giờ → gửi câu ghi nhận (`confirm`, nếu có) và đẩy tin đang chờ →
	bật `omni_do_not_contact` (ghi thẳng DB, luôn chạy dù bước gửi lỗi) → hành trình "Không liên hệ" (lỗi chỉ ghi log).

	Cờ không liên hệ phải bật SAU câu ghi nhận vì `send` chặn mọi tin khi cờ đã bật. Lead rác (Junk) giữ nguyên trạng thái.
	"""
	has_lead = bool(lead and frappe.db.exists(LEAD, lead))
	if has_lead:
		cancel_scheduled(lead)
	try:
		if confirm:
			confirm()
		if flush_pending(identity):
			frappe.logger("omni").info(
				"omni script: câu ghi nhận dừng liên hệ chưa gửi được identity=%s", identity
			)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: lỗi gửi câu ghi nhận dừng liên hệ", message=f"identity={identity}")
	if has_lead:
		frappe.db.set_value(LEAD, lead, "omni_do_not_contact", 1, update_modified=False)
	if identity:
		state.update_state(
			identity,
			flow=None,
			handoff_pending=None,
			handoff_phone_tries=None,
			pause_pending=None,
			stopped_at=str(now_datetime()),
		)
	frappe.db.commit()
	if not has_lead:
		return
	try:
		if frappe.db.get_value(LEAD, lead, "status") != JUNK_STATUS:
			with flags.omni_bot():
				state.set_journey(lead, JOURNEY_STOPPED)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: không ghi được hành trình Không liên hệ", message=f"lead={lead}")
	cancel_scheduled(lead)  # tin hẹn giờ có thể vừa được đặt trong lúc gửi câu ghi nhận
	frappe.logger("omni").info("omni script: dừng liên hệ lead=%s", lead)


def on_stop(ctx: RouteContext) -> None:
	"""Router: mã `C4_HUYDANGKY` (khách bấm, gõ câu dừng, hoặc bộ phân loại trả về) → câu ghi nhận rồi dừng hẳn."""
	stop_contact(ctx.lead, ctx.identity, confirm=lambda: _send(ctx, NODE_STOP))


def request_data_deletion(ctx: RouteContext, send_text: bool = True) -> str | None:
	"""Khách yêu cầu xoá dữ liệu (C.4): gửi ô hướng dẫn (đang Tắt khi chưa có link → không gửi) và tạo việc cho
	người phụ trách tuân thủ (`TUAN_THU`). Trả tên việc."""
	if send_text:
		_send(ctx, NODE_DELETE_DATA)
	if not ctx.lead:
		tasks.warn_no_lead(ctx.identity, _("Khách yêu cầu xoá dữ liệu"))
		return None
	task = tasks.create_task(
		ctx.lead,
		COMPLIANCE,
		_("Khách yêu cầu xoá dữ liệu: {0}").format(_lead_label(ctx.lead)),
		tasks.summary(ctx.lead, ctx.identity) + "\n" + _("Khách yêu cầu xoá dữ liệu cá nhân (C.4)."),
		priority="High",
	)
	user = tasks.recipient(COMPLIANCE)
	if ctx.lead and user:
		_notify(ctx.lead, [user], _("Khách yêu cầu xoá dữ liệu"), ctx.identity)
	return task


def on_delete_data(ctx: RouteContext) -> None:
	"""Router: mã `C4_XOADULIEU`."""
	request_data_deletion(ctx)


# --- Lệnh action của engine ---------------------------------------------------------------------------------------


def action_handoff(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `handoff` | `handoff:<SVC>` | `handoff:task` của ô kịch bản."""
	code = str((node or {}).get("code") or "").upper()
	arg = (arg or "").strip()
	if arg.upper() == COMPLIANCE:
		request_data_deletion(ctx, send_text=False)  # engine đã gửi câu của ô
		return None
	if send.blocked_reason(ctx.identity):
		return None
	if code == NODE_IN_HOURS:  # engine vừa gửi câu chờ: chỉ làm phần giao việc + tạm dừng
		_in_hours(ctx, _current_service(ctx.lead) or tasks.OTHER, REASON_REQUESTED, send_text=False)
		return None
	if arg.lower() == "task":
		reason = NODE_REASONS.get(code, REASON_REQUESTED)
		if code == NODE_AFTER_HOURS:  # engine vừa gửi câu hẹn: chỉ tạo việc
			_after_hours(ctx, _current_service(ctx.lead) or tasks.OTHER, reason, send_text=False)
		else:
			start(ctx, reason=reason, task_only=True)
		return None
	start(ctx, service=arg or None, reason=NODE_REASONS.get(code, REASON_REQUESTED))
	return None


def action_pause_bot(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `pause_bot`: tắt bot trong cuộc trò chuyện đến khi nhân viên bấm "Trả lại cho bot"."""
	_pause(ctx.lead, ctx.identity, _handoff_state(ctx.identity).get("reason") or REASON_REQUESTED)
	return None


def action_stop(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""Lệnh `stop` / `do_not_contact`: dừng hẳn với khách (engine đã gửi câu của ô trước đó)."""
	stop_contact(ctx.lead, ctx.identity)
	return None


engine.register_action("handoff", action_handoff)
engine.register_action("pause_bot", action_pause_bot)
engine.register_action("stop", action_stop)
engine.register_action("do_not_contact", action_stop)
engine.register_state(FLOW_PHONE, on_phone_typed)
engine.register_state(FLOW_SERVICE, on_service_typed)
router.register(NODE_ENTRY, on_request)
for _code in SERVICE_BUTTONS:
	router.register(_code, on_service_button)
router.register(NODE_CHAT, on_chat_here)
router.register(NODE_CALL, on_call_me)
router.register(NODE_STOP, on_stop)
router.register(NODE_DELETE_DATA, on_delete_data)
