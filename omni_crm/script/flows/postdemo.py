"""Bước 5 của bot kịch bản (K6, mục 5.1-5.6): hỏi cảm nhận sau demo, chốt, từ chối, hỏi thăm lại sau 3 tháng.

Câu gửi khách nằm trong Omni Script Node (bước 5). Bot chỉ dùng tên khách, tên dịch vụ và ngày demo;
KHÔNG đọc `omni_demo_note` (ghi chú nội bộ). Bot không nêu con số, không thuyết phục thêm khi khách từ chối.
"""

from __future__ import annotations

from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import add_months, cint, escape_html, get_datetime, getdate, now_datetime

from omni_crm.script import engine, router, send, state, store, timeutil, validate
from omni_crm.script.router import RouteContext

LEAD = "CRM Lead"
IDENTITY = "Omni Identity"
SCHEDULED = "Omni Scheduled Message"
FLOW = "postdemo"  # script_state["flow"]
STEP = "5"

RESULT_DONE = "Đã demo"
J_DEMOED = "Đã demo"
J_NEGOTIATE = "Thương lượng"
J_AGREED = "Đồng ý mua – chờ ký hợp đồng"  # noqa: RUF001 (đúng option Select)
J_LOST = "Thua"

NODE_FIRST = "SD_CAMNHAN"
NODE_CONCERN = "SD_TIMHIEUTHEM"
NODE_ASK_AGAIN = "SD_HOILAI"
NODE_REFUSE = "SD_CHUAPHUHOP"
NODE_CLOSE = "SD_KETTHUC"
NODE_COST = "SD_CHIPHI"
NODE_3M = "HOITHAM_3THANG"
NODE_ASK_PHONE = "HOI_SDT"
NODE_BAD_PHONE = "SAI_SDT"
NODE_ORDER_ASK = "CHOT_VATTU"
WON_NODES = ("SD_BATDAU", "DAT_HANG")
CONCERN_HANDOFF_NODES = ("SD_BK_CHIPHI", "SD_BK_KETNOI", "SD_BK_KHAC")
NODE_CONCERN_KB = "SD_BK_TRIENKHAI"
REASON_NODES = ("SD_LD_CHIPHI", "SD_LD_CHUANHUCAU", "SD_LD_DONVIKHAC", "SD_LD_KHAC")

KIND_FIRST = "postdemo_1"
KIND_3M = "lost_3m"
# Tin chào bán theo lịch bị hủy khi Lead chốt (đồng ý / thua); KHÔNG gồm lost_3m
SALES_KINDS = (
	"followup_docs",
	"followup_quote",
	"demo_remind_1d",
	"demo_remind_1h",
	"demo_noshow",
	"silence_3d",
	KIND_FIRST,
)
MANAGER = "QUAN_LY"
MAX_PHONE_TRIES = 2  # hỏi lại SĐT sai 1 lần (3.4), sau đó không ép
TEXT_MAX = 500
# Giá trị biến theo `seed/VARIABLES.md` (chỉ dùng khi engine.variables của K2 chưa có)
SERVICE_NAMES = {
	"VATTU": "cung ứng vật tư y tế",
	"SUPPLYCORE": "phần mềm SupplyCore",
	"SPD": "hệ thống SPD",
}
DAY_WORDS = ("hôm nay", "hôm qua", "vừa qua")


# --- Dữ liệu Lead -----------------------------------------------------------------------------------------------


def service_of(lead: str | None) -> str | None:
	"""Mã dịch vụ đang tư vấn của Lead (mã cuối trong `omni_services`)."""
	raw = frappe.db.get_value(LEAD, lead, "omni_services") if lead else None
	items = [s.strip().upper() for s in (raw or "").split(",") if s.strip()]
	return items[-1] if items else None


def identity_of(lead: str | None) -> str | None:
	"""Định danh Zalo OA của Lead có tin vào gần nhất."""
	if not lead:
		return None
	rows = frappe.get_all(
		IDENTITY,
		filters={"lead": lead, "channel_type": "Zalo OA"},
		order_by="last_inbound_at desc",
		limit=1,
		pluck="name",
	)
	return rows[0] if rows else None


def sales_allowed(lead: str | None) -> bool:
	"""Bot còn được gửi tin chào bán cho Lead: không đánh dấu không liên hệ, chưa thành Khách hàng."""
	if not lead:
		return False
	row = frappe.db.get_value(LEAD, lead, ["omni_do_not_contact", "converted", "status"], as_dict=True)
	if not row:
		return False
	return not (cint(row.omni_do_not_contact) or cint(row.converted) or row.status == "Converted")


def day_word(demo_day: date, send_day: date) -> str:
	"""Giá trị `{hom_nay_hom_qua}` theo khoảng cách giữa ngày demo và ngày gửi."""
	gap = (send_day - demo_day).days
	return DAY_WORDS[0] if gap <= 0 else DAY_WORDS[1] if gap == 1 else DAY_WORDS[2]


def lead_vars(lead: str | None, at: datetime | None = None) -> dict:
	"""Biến của câu bước 5 lấy từ Lead: chỉ tên khách, tên dịch vụ và mốc thời gian (không có ghi chú demo)."""
	at = at or now_datetime()
	first = (frappe.db.get_value(LEAD, lead, "first_name") if lead else "") or ""
	words = first.split()
	next_day = timeutil.next_working_day_text(at)
	return {
		"ten": words[-1] if words else "",
		"dich_vu": SERVICE_NAMES.get(service_of(lead) or "", ""),
		"ngay_lam_viec_tiep_theo": next_day,
		"thoi_diem": next_day if timeutil.cutoff_1630(at) else "trong hôm nay",
	}


def flow_vars(ctx: RouteContext, extra: dict | None = None) -> dict:
	"""Biến cho lượt này: `engine.variables` của K2, chưa có thì lấy từ Lead."""
	try:
		return engine.variables(ctx, extra)
	except NotImplementedError:
		return {**lead_vars(ctx.lead), **(extra or {})}


# --- Gọi chéo (import trễ, phần kia chưa gộp thì bỏ qua) --------------------------------------------------------


def create_task(
	lead: str | None, service: str | None, title: str, description: str, priority: str = "Medium"
) -> str | None:
	"""Tạo CRM Task cho nhân viên qua `tasks.create_task` (K3), hạn theo mục 3.5; chưa có module thì trả None."""
	try:
		from omni_crm.script import tasks
	except ImportError:
		frappe.logger("omni").info("omni script: chưa có tasks.py, bỏ qua tạo việc lead=%s", lead)
		return None
	due = timeutil.task_due(now_datetime())
	return tasks.create_task(lead, service, title, description, due=due, priority=priority)


def _schedule(
	kind: str, identity: str | None, lead: str, due_at: datetime, node: str, payload: dict
) -> str | None:
	try:
		from omni_crm.script import scheduler
	except ImportError:
		frappe.logger("omni").info("omni script: chưa có scheduler.py, bỏ qua lịch %s lead=%s", kind, lead)
		return None
	return scheduler.schedule(kind, identity, lead, due_at, node, payload)


def _cancel(lead: str | None, kinds: tuple[str, ...]) -> int:
	if not lead:
		return 0
	try:
		from omni_crm.script import scheduler
	except ImportError:
		return 0
	return scheduler.cancel_for(lead, list(kinds)) or 0


def _handoff(ctx: RouteContext, reason: str, task_only: bool = False) -> None:
	"""Chuyển nhân viên phụ trách (K3); chưa có module thì tạo việc để nhân viên vẫn nhận được."""
	service = service_of(ctx.lead)
	try:
		from omni_crm.script.flows import handoff
	except ImportError:
		create_task(ctx.lead, service, _("Bot chuyển nhân viên sau demo"), reason)
		return
	handoff.start(ctx, service=service, reason=reason, task_only=task_only)


def _manager() -> str | None:
	try:
		from omni_crm.script import tasks
	except ImportError:
		rows = frappe.get_all(
			"Omni Service Recipient",
			filters={"parenttype": "Omni Settings", "service": MANAGER},
			fields=["primary_user", "backup_user"],
			limit=1,
		)
		return (rows[0].primary_user or rows[0].backup_user) if rows else None
	return tasks.recipient(MANAGER)


def notify_manager(lead: str, label: str) -> list[str]:
	"""Báo quản lý (dòng QUAN_LY của bảng người nhận) bằng Notification Log + chuông CRM; trả các log đã tạo."""
	user = _manager()
	if not user or not frappe.db.get_value("User", user, "enabled"):
		frappe.logger("omni").info("omni script: chưa có người nhận QUAN_LY, không báo được lead=%s", lead)
		return []
	from omni_crm.crm_bridge import notify

	subject = _("Khách đồng ý mua, mời xem: {0}").format(lead)[:140]
	body = f'{escape_html(label)}. <a href="{escape_html(notify.crm_lead_link(lead))}">{escape_html(_("Mở trong CRM"))}</a>'
	names = []
	try:
		log = frappe.get_doc(
			{
				"doctype": "Notification Log",
				"type": "Alert",
				"for_user": user,
				"subject": subject,
				"email_content": body,
				"document_type": LEAD,
				"document_name": lead,
			}
		).insert(ignore_permissions=True)
		names.append(log.name)
	except Exception:
		frappe.log_error(title="Omni: không báo được quản lý", message=f"lead={lead}")
	notify.crm_notify(lead, [user], f"{subject} · {label}")
	return names


# --- Gửi ô kịch bản ---------------------------------------------------------------------------------------------


def _script_state(identity: str) -> dict:
	return state.get(identity)["script_state"]


def _await(identity: str, what: str | None, **extra) -> None:
	"""Ghi bot đang chờ khách gõ gì ở bước 5 (`phone`/`reason`/`concern`); None = không chờ nữa."""
	if what:
		state.update_state(identity, flow=FLOW, postdemo_await=what, **extra)
	elif _script_state(identity).get("flow") == FLOW:
		state.update_state(
			identity, flow=None, postdemo_await=None, postdemo_pending=None, postdemo_tries=None
		)


def _send(ctx: RouteContext, code: str, extra: dict | None = None) -> bool:
	"""Chỉ gửi câu của ô (không chạy chuỗi action), ghi ô đang đứng."""
	name = send.send_node(ctx.identity, code, flow_vars(ctx, extra))
	if name:
		state.set_node(ctx.identity, code, STEP)
	return bool(name)


def _run(ctx: RouteContext, code: str, extra: dict | None = None) -> bool:
	"""Chạy ô qua engine (K2); engine chưa có thì chỉ gửi câu."""
	try:
		return bool(engine.run_node(ctx, code, extra))
	except NotImplementedError:
		return _send(ctx, code, extra)


# --- 5.1: nhân viên ghi kết quả demo ----------------------------------------------------------------------------


def on_demo_result(lead: str, result: str) -> str | None:
	"""Nhân viên ghi "Kết quả demo": "Đã demo" thì đặt lịch Tin 1 (5.1); trả tên Omni Scheduled Message nếu đã đặt.

	Không đặt lịch khi nhân viên bấm "Không cần bot nhắn", khách đã đồng ý ngay trong buổi demo, Lead không liên hệ
	hoặc đã thành Khách hàng. "Khách vắng"/"Hoãn": không có tin sau demo.
	"""
	if not lead or not frappe.db.exists(LEAD, lead):
		return None
	if (result or "").strip() != RESULT_DONE:
		_cancel(lead, (KIND_FIRST,))
		return None
	row = frappe.db.get_value(
		LEAD, lead, ["omni_journey_status", "omni_no_bot_followup", "omni_demo_at"], as_dict=True
	)
	agreed = row.omni_journey_status == J_AGREED
	if not agreed:
		state.set_journey(lead, J_DEMOED)
	if agreed or cint(row.omni_no_bot_followup) or not sales_allowed(lead):
		return None
	identity = identity_of(lead)
	if not identity:
		return None
	now = now_datetime()
	due = timeutil.postdemo_send_at(now)
	demo_day = getdate(row.omni_demo_at) if row.omni_demo_at else now.date()
	payload = {"demo_date": str(demo_day), "hom_nay_hom_qua": day_word(demo_day, due.date())}
	return _schedule(KIND_FIRST, identity, lead, due, NODE_FIRST, payload)


# --- 5.2: khách đồng ý ------------------------------------------------------------------------------------------


def _has_phone(lead: str | None) -> bool:
	row = frappe.db.get_value(LEAD, lead, ["mobile_no", "phone"], as_dict=True) if lead else None
	return bool(row and (row.mobile_no or row.phone))


def _ask_phone(ctx: RouteContext, pending: str) -> None:
	_await(ctx.identity, "phone", postdemo_pending=pending, postdemo_tries=0)
	send.send_node(ctx.identity, NODE_ASK_PHONE, flow_vars(ctx))


def ensure_won(lead: str | None, identity: str | None, order: bool = False) -> str | None:
	"""Khách đồng ý mua: trạng thái, việc ưu tiên cao, báo quản lý, hủy tin chào bán đang chờ.

	Không làm lại khi Lead vẫn đang ở "Đồng ý mua" cho cùng loại đồng ý (khách bấm lại nút, hoặc lệnh của ô chạy lặp).
	"""
	if not lead:
		return None
	kind = "order" if order else "demo"
	journey = frappe.db.get_value(LEAD, lead, "omni_journey_status")
	if identity and journey == J_AGREED and _script_state(identity).get("postdemo_won") == kind:
		return None
	state.set_journey(lead, J_AGREED)
	title = (
		_("Khách đặt hàng theo báo giá: gọi xác nhận") if order else _("Khách đồng ý mua sau demo: gọi chốt")
	)
	steps = (
		_("Gọi khách để xác nhận đơn hàng, giao hàng và hoá đơn.")
		if order
		else _("Gọi khách để hướng dẫn ký hợp đồng, thanh toán và thiết lập hệ thống.")
	)
	task = create_task(lead, service_of(lead), title, steps, priority="High")
	notify_manager(lead, title)
	_cancel(lead, SALES_KINDS)
	if identity:
		state.update_state(identity, postdemo_won=kind)
	return task


def _complete_won(ctx: RouteContext, code: str) -> None:
	_await(ctx.identity, None)
	_send(ctx, code)
	ensure_won(ctx.lead, ctx.identity, order=code == "DAT_HANG")


def _on_won(ctx: RouteContext) -> None:
	"""[Bắt đầu sử dụng] / [Đặt hàng]: chưa có SĐT thì xin trước, có rồi thì cảm ơn và giao việc."""
	code = ctx.payload_code if ctx.payload_code in WON_NODES else WON_NODES[0]
	if not _has_phone(ctx.lead):
		_ask_phone(ctx, code)
		return
	_complete_won(ctx, code)


def _on_cost(ctx: RouteContext) -> None:
	"""[Trao đổi thêm về chi phí]: hẹn chuyên viên liên hệ, trạng thái Thương lượng; bot không nêu con số."""
	_await(ctx.identity, None)
	_send(ctx, NODE_COST)
	state.set_journey(ctx.lead, J_NEGOTIATE)
	_handoff(ctx, _("Sau demo: khách muốn trao đổi thêm về chi phí"), task_only=True)


# --- 5.2: khách còn băn khoăn -----------------------------------------------------------------------------------


def _answer_value(code: str, field: str) -> str:
	"""Giá trị `answer:<field>=<giá trị>` ghi trong action của ô (nhãn nút khách bấm)."""
	node = store.get_node(code) or {}
	for part in (node.get("action") or "").split(";"):
		name, _sep, arg = part.strip().partition(":")
		key, _eq, value = arg.partition("=")
		if name.strip().lower() == "answer" and key.strip() == field:
			return value.strip()
	return ""


def _on_concern_menu(ctx: RouteContext) -> None:
	_run(ctx, NODE_CONCERN)
	_await(ctx.identity, "concern")


def _on_concern_kb(ctx: RouteContext) -> None:
	"""[Cách triển khai]: trả lời từ Kho tri thức (lệnh `kb_answer` của K2) rồi hỏi lại."""
	_await(ctx.identity, None)
	if not _run(ctx, NODE_CONCERN_KB):
		_send(ctx, NODE_ASK_AGAIN)


def _on_concern_handoff(ctx: RouteContext) -> None:
	"""[Chi phí] / [Kết nối hệ thống] / [Khác]: chuyển nhân viên phụ trách kèm điều khách băn khoăn."""
	_await(ctx.identity, None)
	label = _answer_value(ctx.payload_code or "", "concern")
	state.update_state(ctx.identity, concern=label or None)
	_handoff(ctx, _("Sau demo, khách còn băn khoăn: {0}").format(label))


def _log_question(lead: str | None, text: str) -> None:
	if not lead or not text:
		return
	old = frappe.db.get_value(LEAD, lead, "omni_unanswered_questions") or ""
	frappe.db.set_value(
		LEAD, lead, "omni_unanswered_questions", (old + "\n" + text).strip(), update_modified=False
	)


# --- 5.2: khách từ chối -----------------------------------------------------------------------------------------


def _on_refuse(ctx: RouteContext) -> None:
	_run(ctx, NODE_REFUSE)
	_await(ctx.identity, "reason")


def _store_reason(lead: str | None, text: str) -> None:
	if lead and text:
		frappe.db.set_value(LEAD, lead, "omni_lost_reason_text", text[:TEXT_MAX], update_modified=False)


def _on_reason(ctx: RouteContext) -> None:
	"""Khách bấm một lý do từ chối: ghi lý do, gửi câu kết, trạng thái Thua."""
	_store_reason(ctx.lead, _answer_value(ctx.payload_code or "", "lost_reason"))
	_on_close(ctx)


def _on_close(ctx: RouteContext) -> None:
	_await(ctx.identity, None)
	_run(ctx, NODE_CLOSE)
	close_lost(ctx.lead, ctx.identity)


def close_lost(lead: str | None, identity: str | None = None) -> str | None:
	"""Lead thua: trạng thái "Thua", hủy tin chào bán đang chờ, đặt lịch hỏi thăm 3 tháng (một lần)."""
	if not lead:
		return None
	if frappe.db.get_value(LEAD, lead, "omni_journey_status") != J_LOST:
		state.set_journey(lead, J_LOST)
	_cancel(lead, SALES_KINDS)
	return schedule_lost_3m(lead, identity)


def schedule_lost_3m(lead: str, identity: str | None = None) -> str | None:
	"""Đặt lịch hỏi thăm sau 3 tháng cho Lead thua; mỗi Lead chỉ một lần, không đặt nếu không liên hệ."""
	if not sales_allowed(lead):
		return None
	if frappe.db.exists(SCHEDULED, {"kind": KIND_3M, "lead": lead}):
		return None
	due = timeutil.next_proactive_slot(get_datetime(add_months(now_datetime(), 3)))
	return _schedule(KIND_3M, identity or identity_of(lead), lead, due, NODE_3M, {"via": "email_task"})


def send_lost_3m(lead: str) -> str | None:
	"""Đến hạn hỏi thăm 3 tháng: tạo việc cho nhân viên gửi email theo mẫu (không gửi chat); trả tên CRM Task.

	Không làm gì nếu Lead không liên hệ, đã thành Khách hàng hoặc không còn ở trạng thái "Thua". Sau lần này dừng hẳn.
	"""
	if not sales_allowed(lead):
		return None
	if frappe.db.get_value(LEAD, lead, "omni_journey_status") != J_LOST:
		return None
	title = _("Gửi email hỏi thăm khách sau 3 tháng")
	if frappe.db.exists("CRM Task", {"reference_doctype": LEAD, "reference_docname": lead, "title": title}):
		return None
	node = store.get_node(NODE_3M)
	template = store.fill(node["text"], lead_vars(lead)) if node and node["status"] != store.OFF else ""
	lines = [_("Gửi email hỏi thăm từ hộp thư chung của công ty, chỉ gửi một lần."), template]
	if cint(frappe.db.get_single_value("Omni Settings", "script_zns_enabled")):
		lines.append(_("Tin ZNS đang bật: gửi kèm tin ZNS theo mẫu đã duyệt."))
	return create_task(lead, service_of(lead), title, "\n\n".join(x for x in lines if x))


# --- 5.3: vật tư ------------------------------------------------------------------------------------------------


def ask_order(ctx: RouteContext) -> bool:
	"""Vật tư, khách đã nhận báo giá mà chưa có đơn: hỏi có đặt hàng không (ô CHOT_VATTU)."""
	if not sales_allowed(ctx.lead):
		return False
	return _run(ctx, NODE_ORDER_ASK)


# --- Khách gõ khi bot đang chờ ----------------------------------------------------------------------------------


def _on_text(ctx: RouteContext) -> bool:
	"""Tin gõ khi `script_state["flow"] == "postdemo"`: SĐT, lý do từ chối hoặc điều còn băn khoăn."""
	data = _script_state(ctx.identity)
	waiting = data.get("postdemo_await")
	text = (ctx.text or "").strip()  # lời khách là dữ liệu: chỉ lưu, không làm theo
	if waiting == "phone":
		return _on_phone_text(ctx, data, text)
	if waiting == "reason":
		_store_reason(ctx.lead, text)
		_on_close(ctx)
		return True
	if waiting == "concern":
		_await(ctx.identity, None)
		_log_question(ctx.lead, text[:TEXT_MAX])
		_handoff(ctx, _("Sau demo, khách còn băn khoăn: {0}").format(text[:TEXT_MAX]))
		return True
	return False


def _on_phone_text(ctx: RouteContext, data: dict, text: str) -> bool:
	pending = data.get("postdemo_pending") or WON_NODES[0]
	phone = validate.find_phone(text)
	if phone:
		if ctx.lead and not _has_phone(ctx.lead):
			frappe.db.set_value(LEAD, ctx.lead, "mobile_no", validate.phone_local(phone))
		_complete_won(ctx, pending)
		return True
	tries = cint(data.get("postdemo_tries")) + 1
	if tries < MAX_PHONE_TRIES:
		state.update_state(ctx.identity, postdemo_tries=tries)
		vars_ = flow_vars(ctx)
		if not send.send_node(ctx.identity, NODE_BAD_PHONE, vars_):
			send.send_node(ctx.identity, NODE_ASK_PHONE, vars_)
		return True
	_complete_won(ctx, pending)  # khách không cho SĐT: không ép (3.4), nhân viên liên hệ qua kênh chat
	return True


# --- Lệnh action của ô bước 5 -----------------------------------------------------------------------------------


def _act_require(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	"""`require:phone`: chưa có SĐT thì xin trước và dừng chuỗi lệnh của ô."""
	if (arg or "").strip().lower() != "phone" or _has_phone(ctx.lead):
		return None
	if _script_state(ctx.identity).get("postdemo_await") == "phone":
		return True
	_ask_phone(ctx, node.get("code") or WON_NODES[0])
	return True


def _act_notify(ctx: RouteContext, arg: str, node: dict) -> None:
	"""`notify:manager`: việc ưu tiên cao + báo quản lý (một lần cho mỗi Lead)."""
	if (arg or "").strip().lower() == "manager":
		ensure_won(ctx.lead, ctx.identity, order=node.get("code") == "DAT_HANG")


def _act_lost_3m(ctx: RouteContext, arg: str, node: dict) -> None:
	if ctx.lead:
		send_lost_3m(ctx.lead)


def _noop(ctx: RouteContext, arg: str, node: dict) -> None:
	return None


engine.register_action("require", _act_require)
engine.register_action("notify", _act_notify)
engine.register_action(KIND_3M, _act_lost_3m)
for _name in (KIND_FIRST, "email_template", "zns_template"):
	engine.register_action(_name, _noop)
engine.register_state(FLOW, _on_text)

for _code in WON_NODES:
	router.register(_code, _on_won)
router.register(NODE_COST, _on_cost)
router.register(NODE_CONCERN, _on_concern_menu)
router.register(NODE_CONCERN_KB, _on_concern_kb)
for _code in CONCERN_HANDOFF_NODES:
	router.register(_code, _on_concern_handoff)
router.register(NODE_REFUSE, _on_refuse)
for _code in REASON_NODES:
	router.register(_code, _on_reason)
router.register(NODE_CLOSE, _on_close)
