"""Bước 1-2 và luồng chung C.3: chào, menu, nút bấm, phân loại tin khách gõ, trả lời bằng Kho tri thức.

Đăng ký với router: `FOLLOW` (khách quan tâm OA) và `*` (mọi nút `#CODE` chưa có handler riêng + tin gõ tự do).
Câu gửi khách đều lấy từ Omni Script Node qua `engine.run_node`; câu Kho tri thức do bộ máy Reply sẵn có soạn
(`bot.reply.make_draft`, đủ chốt chặn đầu ra) rồi gửi qua `script.send.send_text`.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.script import engine, router, send, state, store, switch
from omni_crm.script.router import MENU_CODE, RouteContext

UNCLEAR = "CHUA_RO"  # = classify.UNKNOWN
CONF_MIN = 0.6  # = classify.LOW_CONFIDENCE (dùng khi chưa có module classify)
MAX_REASK = 2  # 1.2: hỏi lại tối đa 2 lần, lần 3 chuyển nhân viên
FLOW_KB = "kb_wait"  # ô "Câu hỏi khác": tin gõ kế tiếp trả lời bằng Kho tri thức
CLASSIFIED_KEY = "_classified"
DETAIL_FLAG = "omni_script_classify_detail"  # = classify.DETAIL_FLAG
DETAIL_KEY = "_classify_detail"  # dict của classify_detail: dịch vụ và thông tin khách đã nêu trong tin
LEAD = "CRM Lead"
DRAFT = "Omni Bot Draft"
IDENTITY = "Omni Identity"

NODE_GREETING = "GREETING"
NODE_REASK = "REASK_CHUARO"
NODE_HANDOFF = "GAP_NHANVIEN"
NODE_KB_NONE = "KB_CHUYENVIEN"
NODE_FILE_RECEIVED = "DANHMUC_VATTU_DANHAN"
NODE_NO_THANKS = "KHONG_CAMON"
NODE_STOP = "C4_HUYDANGKY"
NODE_ASK_EMAIL_DOCS = "HOI_EMAIL_TAILIEU"
MAX_LABEL_TEXT = 40
KB_AUTO_CONF = "omni_script_kb_auto_send"
KB_CODE = "KB_ANSWER"  # script_node của tin trả lời bằng Kho tri thức
MAX_QUESTION = 500
# mã phân loại -> ô gửi thẳng (bảng 1.2, 2.5, C.3, C.4)
DIRECT = {
	"MENU_VATTU": "MENU_VATTU",
	"MENU_PHANMEM": "MENU_PHANMEM",
	"MENU_SPD": "MENU_SPD",
	"MENU_KHAC": "MENU_KHAC",
	"GAP_NHANVIEN": NODE_HANDOFF,
	"BOT_HOI": "C3_BOT",
	"HUY_DANGKY": NODE_STOP,
	"MENU": MENU_CODE,
}
BY_SERVICE = {
	"BAOGIA": ("BAOGIA", "BAOGIA_CHUARO"),
	"TAILIEU": ("TAILIEU", MENU_CODE),
	"DEMO": ("DEMO", MENU_CODE),
}
THEN_MENU = {
	"Y_KHOA": "C3_YKHOA",
	"KHONG_LIENQUAN": "C3_KHONGLIENQUAN",
}  # câu cố định rồi menu, không chuyển người


# --- Điểm vào từ router ------------------------------------------------------------------------------------------


def handle(ctx: RouteContext) -> None:
	"""Handler `*`: nút bấm `#CODE` hoặc tin gõ tự do."""
	_notify_scheduler(ctx)
	if ctx.event_type == "follow":
		on_follow(ctx)
	elif ctx.payload_code:
		on_button(ctx)
	else:
		on_text(ctx)


def on_follow(ctx: RouteContext) -> None:
	"""Khách quan tâm OA: chào một lần (1.1); khách cũ không chào lại."""
	if ctx.is_first_contact:
		greet(ctx)


def greet(ctx: RouteContext) -> None:
	"""Gửi tin chào kèm 5 nút và đánh dấu đã chào."""
	state.mark_greeted(ctx.identity)
	if not engine.run_node(ctx, NODE_GREETING):
		engine.run_node(ctx, MENU_CODE)


def on_button(ctx: RouteContext) -> None:
	"""Khách bấm nút (lượt nhiều nút: router chỉ lấy nút cuối; chống bấm lặp do router lo)."""
	if ctx.is_first_contact:
		state.mark_greeted(ctx.identity)
	state.reset_reask(ctx.identity)
	state.update_state(  # bấm nút = rời câu đang hỏi; ô mới cần hỏi thì tự ghi lại
		ctx.identity, flow=None, field=None, node=None, pending=None, wrong=None, force=None, resend=None
	)
	engine.run_button(ctx, ctx.payload_code)


def on_text(ctx: RouteContext) -> None:
	"""Khách gõ tự do: luồng đang dở xử lý trước, không thì phân loại và đưa về đúng nhánh (1.2, 2.5, C.3)."""
	first = ctx.is_first_contact
	if first:
		state.mark_greeted(ctx.identity)
	st = engine.script_state(ctx)
	text = (ctx.text or "").strip()
	flow = (st.get("flow") or "").lower()
	button = _typed_button(ctx, text)
	if button and flow in ("", "collect"):  # gõ đúng chữ trên nút của câu đang hỏi = bấm nút đó
		ctx.payload_code = button
		handler = _foreign_handler(button)
		if handler is not None:
			handler(ctx)
		else:
			on_button(ctx)
		return
	handler = engine._state_handlers.get(flow)
	if handler is not None and handler(ctx):
		return
	if ctx.attachments:
		_keep_files(ctx)
	if not text:
		if (
			ctx.attachments and engine.current_service(ctx) == "VATTU"
		):  # 2.5: file/ảnh danh mục ở nhánh Vật tư
			state.reset_reask(ctx.identity)
			engine.run_button(ctx, NODE_FILE_RECEIVED)
		elif first:
			greet(ctx)
		else:
			reask(ctx)
		return
	if _is_no_thanks(
		ctx, text
	):  # "không cần" sau câu "hỗ trợ tiếp phần nào": lời kết lịch sự, không dừng nhắn
		state.reset_reask(ctx.identity)
		engine.run_button(ctx, NODE_NO_THANKS)
		return
	if _is_stop_phrase(text):  # C.4: câu dừng nhắn rõ ràng thì không cần hỏi AI
		state.reset_reask(ctx.identity)
		go(ctx, NODE_STOP)
		return
	if _typed_document(ctx, text):
		return
	code, _confidence = classify_text(ctx)
	if first and code == UNCLEAR:  # "Alo", "hi": chào như khách mới mở khung chat
		greet(ctx)
		return
	route(ctx, code)


def _fold(text: str | None) -> str:
	try:
		from omni_crm.script import classify

		return classify.fold(text)
	except Exception:
		return " ".join((text or "").lower().split())


def _typed_button(ctx: RouteContext, text: str) -> str | None:
	"""Mã đích của nút trên ô đang đứng có nhãn trùng (bỏ dấu, hoa thường) với câu khách gõ; không trùng → None."""
	if not text or len(text) > MAX_LABEL_TEXT:
		return None
	node = store.get_node(ctx.state.get("script_node"))
	typed = _fold(text)
	for b in (node or {}).get("buttons") or []:
		if typed and typed == _fold(b.get("label")):
			return store.normalize_code(b.get("target"))
	return None


def _typed_document(ctx: RouteContext, text: str) -> bool:
	"""Ô "cần tài liệu nào" (TAILIEU_*, 3.1: chọn bằng nút hoặc gõ): khách gõ tên tài liệu → ghi lại, hỏi email."""
	node = str(ctx.state.get("script_node") or "")
	if not node.startswith("TAILIEU_") or engine.script_state(ctx).get("branch") != "3a":
		return False
	try:
		from omni_crm.script import classify

		if (
			classify.classify_by_rules(text) is not None
		):  # có ý rõ (gặp người, báo giá, chào…): phân loại bình thường
			return False
	except Exception:
		return False
	from omni_crm.script.flows import collect

	state.reset_reask(ctx.identity)
	collect.save_answer(ctx, "tai_lieu", text, typed=True)
	engine.run_button(ctx, NODE_ASK_EMAIL_DOCS)
	return True


def register_handlers() -> None:
	"""Đăng ký handler của Bước 1-2 với router (gọi khi import; test gọi lại sau khi gỡ)."""
	router.register(router.DEFAULT_KEY, handle)
	router.register(router.FOLLOW_KEY, on_follow)


# --- Phân loại và định tuyến --------------------------------------------------------------------------------------


def classify_text(ctx: RouteContext) -> tuple[str, float]:
	"""(mã, confidence) của tin khách gõ trong lượt; confidence dưới ngưỡng hoặc lỗi → CHUA_RO. Gọi AI tối đa 1 lần/lượt."""
	cached = ctx.state.get(CLASSIFIED_KEY)
	if cached:
		return cached
	result = (UNCLEAR, 0.0)
	text = (ctx.text or "").strip()
	if text:
		try:
			from omni_crm.script import classify as classifier
		except ImportError:
			classifier = None
		if classifier is not None:
			threshold = getattr(classifier, "LOW_CONFIDENCE", CONF_MIN)
			try:
				info = {**ctx.state, "service": engine.current_service(ctx) or ""}
				frappe.flags.pop(DETAIL_FLAG, None)
				code, conf = classifier.classify(
					text, info, lead=ctx.lead, identity=ctx.identity, batch_id=ctx.batch_id
				)
				detail = frappe.flags.pop(DETAIL_FLAG, None) or {}
				if detail.get("batch_id") == ctx.batch_id and detail.get("code") == code:
					ctx.state[DETAIL_KEY] = detail
				conf = float(conf or 0)
				result = (str(code or UNCLEAR).upper(), conf) if conf >= threshold else (UNCLEAR, conf)
			except Exception as e:  # nội dung khách không vào log
				frappe.logger("omni").info(
					"omni script: phân loại lỗi (%s) batch=%s", type(e).__name__, ctx.batch_id
				)
	ctx.state[CLASSIFIED_KEY] = result
	return result


def route(ctx: RouteContext, code: str) -> None:
	"""Đưa mã phân loại về đúng ô kịch bản, như thể khách đã bấm nút."""
	if code == "PHAN_NAN":  # C.2: khách phàn nàn thì tự chuyển nhân viên
		state.reset_reask(ctx.identity)
		auto_handoff(ctx, "REASON_COMPLAINT")
	elif _quote_with_details(ctx, code):
		return
	elif code in DIRECT:
		state.reset_reask(ctx.identity)
		go(ctx, DIRECT[code])
	elif code in BY_SERVICE:
		base, without_service = BY_SERVICE[code]
		state.reset_reask(ctx.identity)
		go(ctx, engine.service_code(ctx, base) or without_service)
	elif code in THEN_MENU:
		state.reset_reask(ctx.identity)
		if not engine.run_node(ctx, THEN_MENU[code]):
			engine.run_node(ctx, MENU_CODE)
	elif code == "KB_HOI":
		state.reset_reask(ctx.identity)
		answer_kb(ctx)
	elif code == "CAN_NGUOI":
		state.reset_reask(ctx.identity)
		go(ctx, _needs_human_node(ctx))
	elif code == "SPAM":
		flag_spam(ctx)  # C.3: không trả lời; KHÔNG dừng hẳn/không liên hệ chỉ vì AI phân loại
	elif code == "XOA_DULIEU":
		if _foreign_handler("C4_XOADULIEU"):
			go(ctx, "C4_XOADULIEU")
		elif not engine.run_node(ctx, "C4_XOADULIEU"):
			engine.fallback(ctx, "C4_XOADULIEU", "TUAN_THU")
	else:
		reask(ctx)


QUOTE_SERVICE_NODE = {"VATTU": "BAOGIA_VATTU", "PHANMEM": "BAOGIA_PHANMEM", "SPD": "BAOGIA_SPD"}


def _quote_with_details(ctx: RouteContext, code: str) -> bool:
	"""Khách gõ nhu cầu kèm thông tin ("đặt 30 cái bơm kim tiêm"): vào thẳng nhánh báo giá của dịch vụ khách nhắc tới
	và ghi sẵn thông tin đã nêu để bot KHÔNG hỏi lại. True nếu đã định tuyến."""
	detail = ctx.state.get(DETAIL_KEY) or {}
	slots = detail.get("slots") or {}
	service = detail.get("service") or ""
	buying = code == "BAOGIA" or (code == "MENU_VATTU" and slots.get("items") and slots.get("quantity"))
	if not buying:
		return False
	if code == "MENU_VATTU":
		service = "VATTU"
	target = QUOTE_SERVICE_NODE.get(service)
	if not target or not store.get_node(target):
		return False  # không rõ dịch vụ: đi đường cũ (theo dịch vụ đang tư vấn / hỏi lại sản phẩm)
	if service == "VATTU" and slots:
		from omni_crm.script.flows import collect

		ctx.state[collect.PREFILL_KEY] = dict(slots)
	state.reset_reask(ctx.identity)
	go(ctx, target)
	return True


def go(ctx: RouteContext, code: str) -> None:
	"""Chạy ô `code` như một lần bấm nút: handler riêng của ô (vd Gặp nhân viên của K3) nếu có, không thì engine."""
	handler = _foreign_handler(code)
	if handler is not None:
		ctx.payload_code = store.normalize_code(code)
		handler(ctx)
		return
	engine.run_button(ctx, code)


def _foreign_handler(code: str):
	"""Handler của luồng khác cho mã ô này (khớp đúng hoặc theo tiền tố, như router); None nếu chỉ có handler `*`."""
	probe = RouteContext("", "", None, None, "", store.normalize_code(code))
	handler = router.find_handler(probe)
	return handler if handler is not None and handler is not handle else None


def _notify_scheduler(ctx: RouteContext) -> None:
	"""Báo bộ hẹn giờ (K5) là khách vừa nhắn; phần đó chưa có hoặc lỗi thì bỏ qua."""
	try:
		from omni_crm.script import scheduler
	except ImportError:
		return
	try:
		scheduler.on_inbound(ctx.identity, ctx.lead)
	except Exception as e:
		frappe.logger("omni").info("omni script: scheduler.on_inbound lỗi (%s)", type(e).__name__)


def auto_handoff(ctx: RouteContext, reason_name: str) -> None:
	"""Bot tự chuyển nhân viên (C.2) qua `flows.handoff.start` của K3; phần đó chưa có thì đi như nút Gặp nhân viên."""
	try:
		from omni_crm.script.flows import handoff
	except ImportError:
		go(ctx, NODE_HANDOFF)
		return
	handoff.start(ctx, engine.current_service(ctx), reason=getattr(handoff, reason_name, ""))


def _is_stop_phrase(text: str) -> bool:
	try:
		from omni_crm.script.flows import handoff
	except ImportError:
		return False
	return bool(handoff.is_stop_phrase(text))


def reask(ctx: RouteContext) -> None:
	"""Chưa rõ ý: hỏi lại bằng nút tối đa 2 lần, lần thứ 3 tự chuyển nhân viên (1.2)."""
	if state.bump_reask(ctx.identity) > MAX_REASK:
		state.reset_reask(ctx.identity)
		auto_handoff(ctx, "REASON_MISUNDERSTOOD")
		return
	if not engine.run_node(ctx, NODE_REASK):
		engine.run_node(ctx, MENU_CODE)


def _needs_human_node(ctx: RouteContext) -> str:
	from omni_crm.script.flows import collect

	st = engine.script_state(ctx)
	in_quote = "3b" in (st.get("branch"), st.get("last_branch"))
	return collect.NODE_DISCOUNT if in_quote and collect.DISCOUNT.search(ctx.text or "") else NODE_KB_NONE


def _is_no_thanks(ctx: RouteContext, text: str) -> bool:
	"""Khách gõ câu phủ định ngắn ("không cần") sau câu hỏi "hỗ trợ tiếp/thêm gì không": lời kết, không phải dừng nhắn."""
	from omni_crm.script.flows import collect

	if len(text) > collect.DECLINE_MAX_LEN or not collect.DECLINE.search(text):
		return False
	code = str(ctx.state.get("script_node") or "")
	if code.startswith("HOITIEP_"):
		return True
	node = store.get_node(code)
	return bool(node) and any(b.get("target") == NODE_NO_THANKS for b in node["buttons"])


def _keep_files(ctx: RouteContext) -> None:
	from omni_crm.script.flows import collect

	collect.remember_files(ctx)


# --- Kho tri thức --------------------------------------------------------------------------------------------------


def kb_action(ctx: RouteContext, arg: str, node: dict) -> None:
	"""Lệnh `kb_answer` của ô (vd "Câu hỏi khác"): tin khách gõ kế tiếp được trả lời bằng Kho tri thức."""
	state.update_state(ctx.identity, flow=FLOW_KB)


def _on_kb_text(ctx: RouteContext) -> bool:
	state.update_state(ctx.identity, flow=None)
	if not (ctx.text or "").strip():
		return False
	state.reset_reask(ctx.identity)
	answer_kb(ctx)
	return True


def answer_kb(ctx: RouteContext) -> None:
	"""Trả lời câu hỏi bằng Kho tri thức rồi hỏi tiếp kèm nút của bước đang đứng; không có câu trả lời thì chuyển người."""
	res = kb_draft(ctx)
	if res and res["action"] == "Reply" and (res["text"] or "").strip():
		if _kb_auto_send(ctx, res):
			if send.send_text(ctx.identity, res["text"], code=KB_CODE):
				router._mark(ctx.batch_id)
				engine.run_node(ctx, engine.service_code(ctx, "HOITIEP") or MENU_CODE)
				return
		elif not _shadow_only(res["group"]) and _to_sale_draft(ctx, res["name"]):
			return  # luồng nháp sẵn có: nhân viên duyệt rồi gửi
	if not engine.run_node(ctx, NODE_KB_NONE, {"question": ctx.text}):
		engine.fallback(ctx, NODE_KB_NONE)


def _kb_auto_send(ctx: RouteContext, res: dict) -> bool:
	"""Câu Kho tri thức được bot tự gửi: khách thử (công tắc riêng) hoặc nhóm đang Auto, và qua các chốt chặn chung."""
	allowed = bool(switch.is_test(ctx.identity) and cint(switch.setting("script_auto_send_kb_for_test")))
	# bật cho mọi khách: khóa ngoài DB `omni_script_kb_auto_send` trong site_config (các chốt chặn bên dưới vẫn áp)
	allowed = allowed or bool(cint(frappe.conf.get(KB_AUTO_CONF) or 0))
	if not allowed and not flags.group_auto_send(res.get("group")):
		return False
	flags_ = [f.strip() for f in str(res.get("flags") or "").splitlines() if f.strip()]
	if any(not f.startswith("info:") for f in flags_):  # chốt chặn đầu ra đã gắn cờ: không tự gửi
		return False
	try:
		from omni_crm.bot import handoff as bot_handoff
		from omni_crm.bot import reply

		if not bot_handoff.bot_active_now(now_datetime()):
			return False
		last = (reply._batch_messages(ctx.batch_id) or [None])[-1]
		if last and reply.sale_replies_after(ctx.lead, last.received_at):
			return False  # nhân viên đã trả lời sau tin này
	except Exception as e:
		frappe.logger("omni").info("omni script: không kiểm được điều kiện tự gửi (%s)", type(e).__name__)
		return False
	return True


def _shadow_only(group: str | None) -> bool:
	return bool(group) and bool(cint(frappe.db.get_value("Omni Question Group", group, "shadow_only")))


def kb_draft(ctx: RouteContext) -> dict | None:
	"""Soạn câu trả lời cho tin hiện tại bằng bộ máy Reply (nháp ngầm, không có đường gửi); lỗi hoặc không có → None."""
	if not ctx.lead:
		return None
	try:
		from omni_crm.bot import reply

		name = frappe.db.get_value(DRAFT, {"batch_id": ctx.batch_id}, "name")
		if not name:
			name = reply.make_draft(
				lead=ctx.lead,
				identity=ctx.identity,
				channel=ctx.channel,
				channel_type=frappe.db.get_value("Omni Channel", ctx.channel, "channel_type"),
				batch_id=ctx.batch_id,
				customer_text=ctx.text,
				mode="Shadow",
			)
		if not name:
			return None
		row = frappe.db.get_value(
			DRAFT, name, ["action", "draft_text", "question_group", "guard_flags"], as_dict=True
		)
	except Exception as e:  # không ghi nội dung khách
		frappe.db.rollback()
		frappe.logger("omni").info(
			"omni script: soạn câu Kho tri thức lỗi (%s) batch=%s", type(e).__name__, ctx.batch_id
		)
		return None
	if not row:
		return None
	return {
		"name": name,
		"action": row.action,
		"text": row.draft_text,
		"group": row.question_group,
		"flags": row.guard_flags,
	}


def _to_sale_draft(ctx: RouteContext, name: str) -> bool:
	"""Nhóm chưa tự gửi: chuyển nháp ngầm thành nháp chờ nhân viên duyệt (chỉ khi chế độ Reply là Draft)."""
	if flags.bot_reply_mode() != "Draft":
		return False
	try:
		from omni_crm.bot import reply

		reply.expire_stale_for_lead(ctx.lead)  # nháp cũ của Lead hết hạn trước khi đưa nháp mới lên
		frappe.db.set_value(DRAFT, name, "mode", "Draft")
		reply.notify_pending_draft(ctx.lead, "Reply")
		return True
	except Exception as e:
		frappe.logger("omni").info("omni script: không chuyển được nháp cho nhân viên (%s)", type(e).__name__)
		return False


def _log_unanswered(ctx: RouteContext, arg: str, node: dict) -> None:
	"""Lệnh `log_unanswered`: ghi nguyên văn câu hỏi bot chưa trả lời được vào Lead để bổ sung Kho tri thức (2.7)."""
	question = (ctx.text or "").strip()[:MAX_QUESTION]
	if not ctx.lead or not question:
		return
	old = frappe.db.get_value(LEAD, ctx.lead, "omni_unanswered_questions") or ""
	line = f"[{now_datetime().strftime('%d/%m/%Y %H:%M')}] {question}"
	frappe.db.set_value(
		LEAD, ctx.lead, "omni_unanswered_questions", f"{old}\n{line}".strip(), update_modified=False
	)


def flag_spam(ctx: RouteContext, arg: str = "", node: dict | None = None) -> None:
	"""Tin bị AI xếp loại quảng cáo/rác: bot im lặng lượt này và ghi chú cho nhân viên xem; không đánh dấu gì lên Lead."""
	router._mark(ctx.batch_id)
	frappe.logger("omni").info(
		"omni script: nghi tin rác, bot không trả lời identity=%s batch=%s", ctx.identity, ctx.batch_id
	)
	if not ctx.lead or ctx.state.get("_spam_flagged"):
		return
	ctx.state["_spam_flagged"] = True
	try:
		frappe.get_doc(LEAD, ctx.lead).add_comment(
			"Comment",
			_(
				"Bot kịch bản: tin vừa rồi của khách có vẻ là quảng cáo/tin rác nên bot không trả lời. Nhờ anh/chị xem lại."
			),
		)
	except Exception as e:
		frappe.logger("omni").info("omni script: không ghi được ghi chú tin rác (%s)", type(e).__name__)


engine.register_action("spam", flag_spam)
engine.register_action("no_lead", lambda ctx, arg, node: None)
engine.register_action("log_unanswered", _log_unanswered)
engine.register_state(FLOW_KB, _on_kb_text)
register_handlers()
