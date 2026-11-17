"""Gặp nhân viên, tự chuyển người, tạm dừng / trả lại bot, dừng hẳn (kịch bản mục 1.4, C.2, C.4).

Câu gửi khách lấy từ Omni Script Node (mã `GAP_NV_*`, `C4_*`); module này không chứa câu nào cho khách.
Trạng thái một lần chuyển nằm trong `Omni Identity.script_state`:
- `handoff`: {at, service, assignee, reason, task, mode: in_hours | after_hours, escalated, phone}
- `flow`: `handoff_service` (đang chờ khách chọn dịch vụ) hoặc `handoff_phone` (đang chờ SĐT gọi lại)

Thứ tự bắt buộc khi tạm dừng / dừng hẳn: câu cho khách phải được GỬI XONG trước khi đặt `bot_paused_until` hoặc
`omni_do_not_contact`, vì `send.send_time_check` kiểm lại điều kiện dừng ngay trước khi gửi (xem `flush_pending`).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.bot import handoff as bot_handoff
from omni_crm.script import engine, router, send, state, tasks, timeutil, validate
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
PAUSE_HOURS = 24 * 365  # 1.4: bot im cho đến khi nhân viên bấm "Trả lại cho bot" (không tự mở lại sau 24 giờ)
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
	"""Gửi ngay các tin kịch bản còn chờ của định danh (đường gửi chung `send_outbound`); trả số tin đã xử lý.

	Phải gọi trước khi tạm dừng bot / đánh dấu không liên hệ: nếu không, câu vừa xếp bị chặn lúc gửi.
	"""
	if not identity:
		return 0
	from omni_crm.pipeline import outbound

	frappe.db.commit()  # không giữ khóa DB khi gọi ra ngoài
	names = frappe.get_all(
		OUTBOUND,
		filters={"identity": identity, "source": "Bot", "status": "Queued", "script_node": ["is", "set"]},
		pluck="name",
		order_by="creation asc",
	)
	for name in names:
		try:
			outbound.send_outbound(name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: không gửi kịp tin kịch bản trước khi dừng", message=f"out={name}")
	return len(names)


def _handoff_state(identity: str) -> dict:
	value = state.get(identity)["script_state"].get("handoff")
	return value if isinstance(value, dict) else {}


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
	if _handoff_state(ctx.identity).get("assignee"):  # đang có người giữ cuộc trò chuyện
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
	previous = _handoff_state(ctx.identity)
	# Khách đổi chủ đề giữa chừng: người đang nhận giữ cuộc trò chuyện (1.4)
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


def _pause(lead: str | None, identity: str, reason: str = "") -> None:
	"""Tạm dừng bot đến khi nhân viên bấm "Trả lại cho bot"; gửi hết tin đang chờ trước, rồi ghi dòng thời gian."""
	flush_pending(identity)
	if not lead:
		frappe.db.set_value(
			IDENTITY,
			identity,
			"bot_paused_until",
			now_datetime() + timedelta(hours=PAUSE_HOURS),
			update_modified=False,
		)
		return
	already = bot_handoff.paused_until(lead)
	bot_handoff.pause_lead(lead, PAUSE_HOURS)
	if not already:
		bot_handoff.note_on_lead(
			lead,
			_('Bot kịch bản chuyển người: {0}. Bot tạm dừng đến khi nhân viên bấm "Trả lại cho bot".').format(
				reason_label(reason)
			),
		)


def _after_hours(ctx: RouteContext, svc: str, reason: str, send_text: bool = True) -> None:
	"""Ngoài giờ: câu hẹn + nút [Nhắn tin tại đây] [Gọi điện cho tôi], tạo việc hạn ngày làm việc kế tiếp; bot KHÔNG dừng."""
	previous = _handoff_state(ctx.identity)
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
	state.update_state(
		ctx.identity,
		flow=None,
		handoff_pending=None,
		handoff={
			"at": str(now_datetime()),
			"service": svc,
			"assignee": previous.get("assignee") or tasks.assignee_for(ctx.lead, svc),
			"reason": reason,
			"task": task,
			"mode": MODE_AFTER,
			"escalated": cint(previous.get("escalated")),
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
	"""Gửi câu xin SĐT gọi lại và đặt trạng thái chờ số; trả tên tin ra (None nếu không gửi được)."""
	name = send.send_node(identity, NODE_CALL, variables or {})
	state.update_state(identity, flow=FLOW_PHONE, handoff_phone_tries=0)
	state.set_node(identity, NODE_CALL, "1")
	return name


def on_phone_typed(ctx: RouteContext) -> bool:
	"""Trạng thái `handoff_phone`: nhận SĐT khách gõ (kiểm bằng `validate`); sai thì hỏi lại 1 lần rồi thôi."""
	if ctx.payload_code:
		return False
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


def should_escalate(
	started_at: datetime | None,
	now: datetime,
	*,
	staff_replied: bool,
	escalated: bool,
	in_hours: bool,
	minutes: int = UNANSWERED_MINUTES,
) -> bool:
	"""Hàm thuần: đã quá `minutes` phút trong giờ mà chưa có nhân viên nhận và chưa báo quản lý lần nào."""
	if not started_at or staff_replied or escalated or not in_hours:
		return False
	return now - started_at >= timedelta(minutes=minutes)


def _staff_replied(identity: str, since: datetime) -> bool:
	return bool(
		frappe.db.exists(OUTBOUND, {"identity": identity, "source": "Sale", "creation": [">=", since]})
	)


def check_unanswered(now: datetime | None = None) -> dict[str, int]:
	"""Tác vụ định kỳ: khách đã được chuyển trong giờ mà 15 phút chưa ai nhận → bot xin SĐT và báo quản lý (QUAN_LY).

	Để hỏi được SĐT, bot được mở lại với khách đó (như trường hợp ngoài giờ: bot vẫn tư vấn trong lúc chờ).
	Commit sau từng khách; lỗi một khách không chặn khách khác.
	"""
	now = now or now_datetime()
	out = {"checked": 0, "escalated": 0}
	rows = frappe.get_all(
		IDENTITY,
		filters={"script_state": ["like", '%"handoff"%'], "bot_paused_until": [">", now]},
		fields=["name", "lead"],
		limit_page_length=SCAN_LIMIT,
	)
	in_hours = timeutil.in_hours(now)
	for row in rows:
		out["checked"] += 1
		try:
			if _escalate_one(row.name, row.lead, now, in_hours):
				out["escalated"] += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi kiểm 15 phút chuyển người", message=f"identity={row.name}")
	return out


def _escalate_one(identity: str, lead: str | None, now: datetime, in_hours: bool) -> bool:
	info = _handoff_state(identity)
	if info.get("mode") != MODE_IN or not info.get("at"):
		return False
	started = get_datetime(info["at"])
	if not should_escalate(
		started,
		now,
		staff_replied=_staff_replied(identity, started),
		escalated=bool(cint(info.get("escalated"))),
		in_hours=in_hours,
	):
		return False
	state.update_state(identity, handoff={**info, "escalated": 1})
	if lead:
		bot_handoff.resume_lead(lead)
	else:
		frappe.db.set_value(IDENTITY, identity, "bot_paused_until", None, update_modified=False)
	frappe.db.commit()
	ask_phone(identity, {"ngay_lam_viec_tiep_theo": timeutil.next_working_day_text(now)})
	if lead:
		manager = tasks.recipient(MANAGER)
		label = _("Khách chờ nhân viên quá {0} phút").format(UNANSWERED_MINUTES)
		if manager:
			_notify(lead, [manager], label, identity)
		else:
			frappe.log_error(
				title="Omni: quá 15 phút chưa ai nhận, chưa cấu hình quản lý", message=f"lead={lead}"
			)
		tasks.append_note(info.get("task"), label + ". " + _("Bot đã xin số điện thoại của khách."))
		bot_handoff.note_on_lead(lead, label + ". " + _("Bot đã xin số điện thoại và báo quản lý."))
	return True


# --- Trả lại cho bot ----------------------------------------------------------------------------------------------


def clear(lead: str) -> int:
	"""Xóa trạng thái chuyển người của mọi định danh của Lead (khi nhân viên bấm "Trả lại cho bot"); trả số định danh."""
	done = 0
	for identity in frappe.get_all(IDENTITY, filters={"lead": lead}, pluck="name"):
		data = state.get(identity)["script_state"]
		if not (
			data.get("handoff") or data.get("handoff_pending") or str(data.get("flow") or "")[:7] == "handoff"
		):
			continue
		values: dict = {"handoff": None, "handoff_pending": None, "handoff_phone_tries": None}
		if str(data.get("flow") or "").startswith("handoff"):
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


def stop_contact(lead: str | None, identity: str | None = None) -> None:
	"""Dừng hẳn với khách (C.4): gửi nốt câu xác nhận đang chờ, bật `omni_do_not_contact`, hành trình "Không liên hệ",
	hủy mọi tin hẹn giờ. Lead rác (Junk) giữ nguyên trạng thái. Không commit."""
	flush_pending(identity)
	if identity:
		state.update_state(
			identity,
			flow=None,
			handoff_pending=None,
			handoff_phone_tries=None,
			stopped_at=str(now_datetime()),
		)
	if not lead or not frappe.db.exists(LEAD, lead):
		return
	if frappe.db.get_value(LEAD, lead, "status") == JUNK_STATUS:
		frappe.db.set_value(LEAD, lead, "omni_do_not_contact", 1, update_modified=False)
	else:
		with flags.omni_bot():
			state.set_journey(lead, JOURNEY_STOPPED)
	cancel_scheduled(lead)
	frappe.logger("omni").info("omni script: dừng liên hệ lead=%s", lead)


def on_stop(ctx: RouteContext) -> None:
	"""Router: mã `C4_HUYDANGKY` (khách bấm hoặc bộ phân loại trả về) → câu ghi nhận rồi dừng hẳn."""
	_send(ctx, NODE_STOP)
	stop_contact(ctx.lead, ctx.identity)


def request_data_deletion(ctx: RouteContext, send_text: bool = True) -> str | None:
	"""Khách yêu cầu xoá dữ liệu (C.4): gửi ô hướng dẫn (đang Tắt khi chưa có link → không gửi) và tạo việc cho
	người phụ trách tuân thủ (`TUAN_THU`). Trả tên việc."""
	if send_text:
		_send(ctx, NODE_DELETE_DATA)
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
