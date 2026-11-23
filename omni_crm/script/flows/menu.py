"""Bước 1-2 và luồng chung C.3: chào, menu, nút bấm, phân loại tin khách gõ, trả lời bằng Kho tri thức.

Đăng ký với router: `FOLLOW` (khách quan tâm OA) và `*` (mọi nút `#CODE` chưa có handler riêng + tin gõ tự do).
Câu gửi khách đều lấy từ Omni Script Node qua `engine.run_node`; câu Kho tri thức do bộ máy Reply sẵn có soạn
(`bot.reply.make_draft`, đủ chốt chặn đầu ra) rồi gửi qua `script.send.send_text`.
"""

from __future__ import annotations

import re

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
NODE_REASK_CHOICE = "REASK_LUACHON"  # câu dẫn khi hỏi lại đúng câu đang hỏi
NODE_HELLO_AGAIN = "CHAO_LAI"  # câu dẫn khi khách cũ chỉ chào
NODE_YES = "DA_VANG_NGAN"  # câu dẫn "Dạ vâng ạ." trước khi hỏi lại câu đang dở
NODE_BACK = "HOI_TIEP_CAUDO"  # câu dẫn khi quay lại câu đang hỏi dở
NODE_GOT_CONTACT = "DA_NHAN_LIENHE"  # câu dẫn khi khách tự gửi số điện thoại / email
NODE_GOT_FILE = "DA_NHAN_TEP"  # câu dẫn khi khách gửi tệp / ảnh không kèm chữ
NODE_OK = "DA_VANG"  # đáp lại "ok", "vâng", "cảm ơn"
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
TOP_NODES = (MENU_CODE, NODE_GREETING, NODE_REASK)
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
	if flow.startswith("handoff") and _no_staff(text):
		state.update_state(ctx.identity, flow=None, handoff_pending=None, handoff_phone_tries=None)
		state.reset_reask(ctx.identity)
		if _reply_with_menu(ctx, "GAP_NV_HUY"):
			return
	if flow and _understand_on() and _new_session(ctx):
		from omni_crm.script.flows import collect

		collect._clear_flow(ctx)  # luật 13: phiên mới, câu hỏi dở của phiên trước không còn hiệu lực
		state.update_state(ctx.identity, demo_wait=None, handoff_phone_tries=None)
		flow, st = "", engine.script_state(ctx)
	handler = engine._state_handlers.get(flow)
	if handler is not None and _side_talk(ctx, text, st):
		return
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
		elif ctx.attachments and _reply_with_menu(ctx, NODE_GOT_FILE):  # tệp / ảnh không kèm chữ: báo đã nhận, hỏi nhu cầu
			state.reset_reask(ctx.identity)
		else:
			reask(ctx)
		return
	if _is_no_thanks(
		ctx, text
	):  # "không cần" sau câu "hỗ trợ tiếp phần nào": lời kết lịch sự, không dừng nhắn
		state.reset_reask(ctx.identity)
		engine.run_button(ctx, NODE_NO_THANKS)
		return
	if not flow and text and _typed_cancel_reason(ctx, text):
		return
	if not flow and not first and _contact_only(ctx, text):  # khách mới: chào như thường, chưa ghi gì
		return
	if not flow and _polite_decline(text):  # "không cần": cảm ơn rồi thôi, KHÔNG đánh dấu dừng nhắn
		state.reset_reask(ctx.identity)
		if engine.run_button(ctx, NODE_NO_THANKS):
			return
	if not flow and _demo_change(ctx, text):
		return
	if _is_stop_phrase(text):  # C.4: câu dừng nhắn rõ ràng thì không cần hỏi AI
		state.reset_reask(ctx.identity)
		go(ctx, NODE_STOP)
		return
	if _typed_document(ctx, text):
		return
	if not first and _ack_only(text):  # "ok", "vâng", "cảm ơn": đáp lời, không hỏi AI, không nói "chưa rõ ý"
		state.reset_reask(ctx.identity)
		mid = str(ctx.state.get("script_node") or "").startswith(MID_FLOW_PREFIXES)
		if _tap_affirmative(ctx) or (mid and (_resend_slots(ctx) or _resend_current(ctx, NODE_YES))):
			return
		if not engine.run_node(ctx, NODE_OK):
			reask(ctx)
		return
	code, _confidence = classify_text(ctx)
	if first and code == UNCLEAR:  # "Alo", "hi": chào như khách mới mở khung chat
		greet(ctx)
		return
	if _must_clarify(ctx.state.get(DETAIL_KEY) or {}, code) and not flow and _ask_to_clarify(ctx):
		return
	if not flow:
		_several_ideas(ctx, code)  # tin nhiều ý: trả lời câu hỏi trước, xếp các việc khác vào hàng chờ
	if code == "DEMO" and (ctx.state.get(DETAIL_KEY) or {}).get("demo_mode") and not flow and _demo_with_details(ctx):
		state.reset_reask(ctx.identity)  # "Demo" + "Online": vào thẳng chọn khung giờ, không hỏi lại hình thức
		return
	if (ctx.state.get(DETAIL_KEY) or {}).get("social") and not flow and not ai_choice(ctx):
		state.reset_reask(ctx.identity)  # luật 5: xã giao thì đáp ngắn, không hiện menu gốc
		mid = str(ctx.state.get("script_node") or "").startswith(MID_FLOW_PREFIXES) and not _stale(ctx)
		if (mid and _resend_current(ctx, NODE_YES)) or engine.run_node(ctx, NODE_OK):
			return
	picked = ai_choice(ctx)
	if picked and not flow:  # khách trả lời câu đang hỏi bằng lời tự nhiên: coi như bấm lựa chọn đó
		state.reset_reask(ctx.identity)
		_tap(ctx, picked)
		return
	if code == UNCLEAR and _greeting_only(text):  # khách cũ gọi "Alo em ơi": chào lại, không nói "chưa rõ ý"
		state.reset_reask(ctx.identity)
		# câu đang đứng đã quá cũ (khách quay lại sau nhiều giờ / nhiều ngày): chào và đưa menu, không hỏi tiếp câu cũ
		if _stale(ctx) or not _resend_current(ctx, NODE_HELLO_AGAIN):
			ctx.state[engine.LEAD_IN_KEY] = _plain(ctx, NODE_HELLO_AGAIN)
			if not engine.run_node(ctx, MENU_CODE):  # đã gửi lặp quá số lần cho phép: đi đường hỏi lại / chuyển người
				ctx.state.pop(engine.LEAD_IN_KEY, None)
				reask(ctx)
		return
	route(ctx, _refine(ctx, code))


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
	for b in _shown_buttons(ctx, node):
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

	if not any(ch.isalpha() for ch in text) or _greeting_only(text) or _ack_only(text):
		return _resend_current(ctx, NODE_REASK_CHOICE)  # "5", "?", "ok": không phải tên tài liệu
	code, _conf = classify_text(ctx)
	if code not in (UNCLEAR, "TAILIEU"):
		return False  # khách nói ý khác (báo giá, hỏi chuyện khác…): định tuyến ý đó
	if code == UNCLEAR and len(text.split()) < 2:
		return _resend_current(ctx, NODE_REASK_CHOICE)  # một chữ không rõ nghĩa ("asdfgh")
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
		if classifier is not None and _understand_on() and _needs_ai(classifier, text, {**ctx.state}):
			try:  # bước HIỂU mới (yêu cầu 20/11): đọc cả lượt cùng toàn bộ ngữ cảnh
				from omni_crm.script import understand

				thinking(ctx)
				reading = understand.read(ctx)
				router.check_superseded(ctx)
				detail = {**understand.to_detail(reading), "batch_id": ctx.batch_id}
				ctx.state[READING_KEY] = reading
				ctx.state[DETAIL_KEY] = detail
				conf = float(detail["confidence"] or 0)
				keep = conf >= CONF_MIN or detail["relation"] == "TRA_LOI" or detail["social"]
				result = (detail["code"], conf) if keep else (UNCLEAR, conf)
			except router.Superseded:
				raise
			except Exception as e:
				frappe.logger("omni").info("omni script: bước hiểu lỗi (%s) batch=%s", type(e).__name__, ctx.batch_id)
		elif classifier is not None:
			threshold = getattr(classifier, "LOW_CONFIDENCE", CONF_MIN)
			try:
				info = {**ctx.state, "service": engine.current_service(ctx) or ""}
				waited_for_ai = _needs_ai(classifier, text, info)
				if waited_for_ai:
					thinking(ctx)  # sắp nhờ AI hiểu ý: báo khách đợi
					info.update(conversation_context(ctx))  # AI đọc cả mấy lượt gần nhất và câu bot vừa hỏi
				frappe.flags.pop(DETAIL_FLAG, None)
				code, conf = classifier.classify(
					text, info, lead=ctx.lead, identity=ctx.identity, batch_id=ctx.batch_id
				)
				detail = frappe.flags.pop(DETAIL_FLAG, None) or {}
				if detail.get("batch_id") == ctx.batch_id and detail.get("code") == code:
					ctx.state[DETAIL_KEY] = detail
				if waited_for_ai:
					router.check_superseded(ctx)  # khách nhắn thêm trong lúc bot hỏi AI: huỷ lượt, gom lại
				conf = float(conf or 0)
				result = (str(code or UNCLEAR).upper(), conf) if conf >= threshold else (UNCLEAR, conf)
			except router.Superseded:
				raise  # khách nhắn thêm: huỷ cả lượt (router xử lý)
			except Exception as e:  # nội dung khách không vào log
				frappe.logger("omni").info(
					"omni script: phân loại lỗi (%s) batch=%s", type(e).__name__, ctx.batch_id
				)
	ctx.state[CLASSIFIED_KEY] = result
	return result


READING_KEY = "_reading"  # bản đọc hiểu đầy đủ của lượt (script.understand)


def _understand_on() -> bool:
	try:
		from omni_crm.script import understand

		return understand.enabled()
	except Exception:
		return False


CONTEXT_TURNS = 6
OUTBOUND = "Omni Outbound Message"
INBOUND = "Omni Inbound Message"


def conversation_context(ctx: RouteContext) -> dict:
	"""Ngữ cảnh cho AI hiểu ý: mấy lượt gần nhất (`_history`) và câu bot vừa hỏi đang chờ trả lời (`_asked`).
	Lỗi đọc DB thì trả rỗng (AI vẫn phân loại được như trước, chỉ thiếu ngữ cảnh)."""
	try:
		rows = [
			(r.creation, "BOT", r.text, r.script_node)
			for r in frappe.get_all(
				OUTBOUND,
				filters={"identity": ctx.identity, "source": "Bot"},
				fields=["creation", "text", "script_node"],
				order_by="creation desc",
				limit=CONTEXT_TURNS,
			)
			if r.script_node != send.NODE_THINKING
		]
		rows += [
			(r.received_at, "KH", r.text, None)
			for r in frappe.get_all(
				INBOUND,
				filters={"identity": ctx.identity, "event_type": "message", "batch_id": ["!=", ctx.batch_id]},
				fields=["received_at", "text"],
				order_by="received_at desc",
				limit=CONTEXT_TURNS,
			)
			if (r.text or "").strip() and not router.NON_TEXT.match((r.text or "").strip())
		]
		rows = sorted((r for r in rows if r[0]), key=lambda r: str(r[0]))[-CONTEXT_TURNS:]
		history = [[who, ("(bấm nút) " + text.strip()[1:]) if who == "KH" and text.strip().startswith("#") else text] for _at, who, text, _n in rows]
		last_bot = next((r for r in reversed(rows) if r[1] == "BOT"), None)
		asked = ""
		if last_bot and rows and rows[-1] is last_bot:  # bot là bên nói sau cùng trước tin này
			waiting = bool(_pending_question(ctx)) or bool((engine.script_state(ctx).get("choices") or {}).get("items"))
			if waiting or "?" in (last_bot[2] or ""):
				asked = last_bot[2] or ""
		return {"_history": history, "_asked": asked}
	except Exception as e:
		frappe.logger("omni").info("omni script: không đọc được ngữ cảnh hội thoại (%s)", type(e).__name__)
		return {}


def ai_choice(ctx: RouteContext, buttons: list | None = None) -> str | None:
	"""Mã nút mà AI (đọc theo ngữ cảnh) cho là khách vừa chọn bằng lời ("qua teams nhé" → "Online qua Teams").
	Chỉ nhận khi AI coi tin là câu trả lời, đủ chắc chắn và số thứ tự nằm trong danh sách đang hỏi."""
	detail = ctx.state.get(DETAIL_KEY) or {}
	index = cint(detail.get("choice"))
	if detail.get("relation") != "TRA_LOI" or index < 1 or float(detail.get("confidence") or 0) < CONF_MIN:
		return None
	if buttons is None:
		buttons = [
			{"label": label, "target": target}
			for label, target in (engine.script_state(ctx).get("choices") or {}).get("items") or []
		]
	if index > len(buttons):
		return None
	return store.normalize_code(buttons[index - 1].get("target")) or None


def _tap(ctx: RouteContext, target: str) -> None:
	ctx.payload_code = target
	handler = _foreign_handler(target)
	if handler is not None:
		handler(ctx)
	else:
		on_button(ctx)


THINKING_KEY = "_thinking"


def thinking(ctx: RouteContext) -> None:
	"""Gửi câu "Miyano đang suy nghĩ…" một lần cho lượt này (xem `send.send_thinking`). Không bao giờ ném lỗi."""
	if ctx.state.get(THINKING_KEY):
		return
	ctx.state[THINKING_KEY] = True
	try:
		send.send_thinking(ctx.identity)
	except Exception as e:
		frappe.logger("omni").info("omni script: không gửi được câu báo chờ (%s)", type(e).__name__)


def _needs_ai(classifier, text: str, info: dict) -> bool:
	"""Câu này luật cố định và từ vựng đã học không quyết được, tức sắp phải gọi AI (mất vài giây)."""
	try:
		if classifier.classify_by_rules(text, info) is not None:
			return False
		learned = getattr(classifier, "_learned", None)
		return not (learned and learned(text))
	except Exception:
		return False


def route(ctx: RouteContext, code: str) -> None:
	"""Đưa mã phân loại về đúng ô kịch bản, như thể khách đã bấm nút."""
	if code in ("HUY_DANGKY", "XOA_DULIEU") and not _explicit_request(ctx, code):
		# Dừng nhắn / xoá dữ liệu là việc không hoàn tác được với khách: chỉ làm khi khách NÓI RÕ (luật cố định trúng),
		# không làm theo phỏng đoán của AI ("anh quên mất là thứ 6 bận" từng bị hiểu thành dừng nhắn).
		state.reset_reask(ctx.identity)
		if not _show_booking(ctx) and not engine.run_button(ctx, NODE_NO_THANKS):
			reask(ctx)
		return
	if code in NEW_INTENTS and _route_new_intent(ctx, code):
		return
	if code in ("GAP_NHANVIEN", "CAN_NGUOI") and _unsure_handoff(ctx, code):
		# Luật 7: chuyển người (bot sẽ tạm dừng) chỉ làm ngay khi khách nói rõ hoặc AI rất chắc; còn lại hỏi một câu.
		state.reset_reask(ctx.identity)
		if code == "CAN_NGUOI":
			_log_unanswered(ctx, "", {})  # câu khách hỏi vẫn được ghi lại cho chuyên viên
		if engine.run_node(ctx, NODE_CONFIRM_HANDOFF):
			return
	if code == "PHAN_NAN":  # C.2: khách phàn nàn thì tự chuyển nhân viên
		state.reset_reask(ctx.identity)
		auto_handoff(ctx, "REASON_COMPLAINT")
	elif _quote_with_details(ctx, code):
		return
	elif code in SIDE_REPLY and _reply_then_back(ctx, SIDE_REPLY[code]):
		state.reset_reask(ctx.identity)  # đáp câu hỏi chen ngang rồi hỏi lại đúng câu đang dở
	elif code in DIRECT:
		state.reset_reask(ctx.identity)
		go(ctx, DIRECT[code])
	elif code == "DEMO" and _demo_with_details(ctx):
		state.reset_reask(ctx.identity)
	elif code == "DEMO" and _demo_needs_product(ctx):
		state.reset_reask(ctx.identity)
		go(ctx, NODE_DEMO_PRODUCT)
	elif code in BY_SERVICE:
		base, without_service = BY_SERVICE[code]
		state.reset_reask(ctx.identity)
		go(ctx, _detail_service_node(ctx, base) or engine.service_code(ctx, base) or without_service)
	elif code in THEN_MENU:
		if cint(ctx.state.get("script_reask_count")) >= 1 and ctx.state.get("script_node") == MENU_CODE:
			reask(ctx)  # vừa từ chối một câu ngoài lề xong lại ngoài lề tiếp: không lặp y nguyên, lần thứ ba chuyển nhân viên
			return
		state.bump_reask(ctx.identity)
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


DEMO_SERVICE = {"PHANMEM": "SUPPLYCORE", "SPD": "SPD"}  # dịch vụ có demo (Vật tư không có)


NODE_DEMO_PRODUCT = "DEMO_CHONSANPHAM"
AI_DEMO_MODE = {"ONLINE": "DEMO_HT_ONLINE", "TRUC_TIEP": "DEMO_HT_TRUCTIEP"}  # hình thức AI đọc được trong tin


def _demo_needs_product(ctx: RouteContext) -> bool:
	"""Khách GÕ muốn xem demo mà chưa rõ sản phẩm nào (chưa chọn dịch vụ, hoặc đang ở Vật tư – không có demo):
	hỏi khách muốn xem demo Phần mềm hay SPD, thay vì mời gửi danh mục vật tư."""
	if _detail_service_node(ctx, "DEMO") and (ctx.state.get(DETAIL_KEY) or {}).get("service") != "VATTU":
		return False
	return engine.current_service(ctx) not in DEMO_SERVICE.values() and bool(store.get_node(NODE_DEMO_PRODUCT))


def _demo_with_details(ctx: RouteContext) -> bool:
	"""Khách nêu luôn hình thức demo (và có thể cả ngày giờ) trong tin: "demo phần mềm online thứ sáu 14h", hoặc đang
	được hỏi hình thức mà gõ "gặp trực tiếp tại bệnh viện". Không hỏi lại hình thức. True nếu đã xử lý."""
	try:
		from omni_crm.script.flows import demo

		detail = ctx.state.get(DETAIL_KEY) or {}
		mode = demo.mode_in_text(ctx.text) or AI_DEMO_MODE.get(detail.get("demo_mode") or "")
		if not mode:
			return False
		named = DEMO_SERVICE.get((ctx.state.get(DETAIL_KEY) or {}).get("service") or "")
		service = named or engine.current_service(ctx)
		if service not in DEMO_SERVICE.values():
			return False
		if named:  # ghi nhận dịch vụ và nhu cầu như khi khách bấm nút "Đặt lịch demo" của dịch vụ đó
			node = store.get_node(f"DEMO_{(ctx.state.get(DETAIL_KEY) or {}).get('service')}") or {}
			keep = [(n, a) for n, a in engine.split_actions(node.get("action")) if n in ("service", "need")]
			engine.run_actions(ctx, keep, node)
		return demo.start_with(ctx, mode)
	except Exception as e:
		frappe.logger("omni").info("omni script: không vào thẳng được demo (%s)", type(e).__name__)
		return False


SITES = re.compile(r"(?<!\d)(\d{1,3})\s*(?:cơ sở|bệnh viện|bv|chi nhánh|phòng khám|trung tâm|kho|điểm)(?![\wÀ-ỹ])", re.I)
SITE_FIELD = {"PHANMEM": "sc_sites", "SPD": "spd_sites"}
SKIP_CONFIRM = {"PHANMEM": ("BAOGIA_PHANMEM", "BG_SC_MATHANG")}  # khách đã nói rõ sản phẩm: bỏ câu "…đúng không ạ?"


def _known_sites(ctx: RouteContext, service: str) -> dict:
	"""Số cơ sở khách đã nêu trong tin ("báo giá SPD cho 2 bệnh viện") để điền sẵn, không hỏi lại — thuần."""
	m = SITES.search(ctx.text if isinstance(ctx.text, str) else "")
	field = SITE_FIELD.get(service)
	if not field:
		return {}
	if m:
		return {field: m.group(0).strip()}
	state_ = ctx.state if isinstance(ctx.state, dict) else {}
	said = ((state_.get(DETAIL_KEY) or {}).get("extra") or {}).get("sites")  # AI chép nguyên văn từ tin khách
	return {field: said} if said and any(ch.isdigit() for ch in said) else {}


def _skip_confirm(ctx: RouteContext, service: str) -> str | None:
	"""Khách đã nêu rõ sản phẩm thì không hỏi lại "anh/chị cần báo giá … đúng không ạ?": chạy các lệnh ghi nhận của
	ô đó (dịch vụ, nhu cầu, nhánh) rồi trả mã ô kế tiếp. Không áp dụng được → None."""
	pair = SKIP_CONFIRM.get(service)
	node = store.get_node(pair[0]) if pair else None
	if not node or not store.get_node(pair[1]):
		return None
	keep = [(n, a) for n, a in engine.split_actions(node.get("action")) if n in ("service", "need", "branch", "journey")]
	engine.run_actions(ctx, keep, node)
	return pair[1]


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
	from omni_crm.script.flows import collect

	if service == "VATTU" and slots:
		ctx.state[collect.PREFILL_KEY] = dict(slots)
	sites = _known_sites(ctx, service) if detail.get("service") else {}
	if sites:
		ctx.state[collect.PREFILL_KEY] = {**(ctx.state.get(collect.PREFILL_KEY) or {}), **sites}
	state.reset_reask(ctx.identity)
	if detail.get("service"):  # chính khách nêu sản phẩm trong tin này
		after = _skip_confirm(ctx, service)
		if after:
			collect.apply_prefill(ctx)
			go(ctx, after)
			return True
	go(ctx, target)
	return True


DEMO_WORDS = re.compile(r"\b(?:demo|dung thu|xem thu|trai nghiem)\b")
NOT_BOOKING = re.compile(r"\b(?:khong|chua|huy|doi|hoan|doi lich)\b")
MENU_SERVICE = {"MENU_VATTU": "VATTU", "MENU_PHANMEM": "PHANMEM", "MENU_SPD": "SPD"}


def _refine(ctx: RouteContext, code: str) -> str:
	"""Khách nêu dịch vụ VÀ muốn xem demo trong cùng một tin ("bên mình có phần mềm SPD, anh muốn đặt lịch demo"):
	đi thẳng đặt demo của dịch vụ đó thay vì mở menu dịch vụ rồi bắt khách bấm lại."""
	service = MENU_SERVICE.get(code)
	folded = _fold(ctx.text)
	if not service or not DEMO_WORDS.search(folded) or NOT_BOOKING.search(folded):
		return code
	ctx.state[DETAIL_KEY] = {**(ctx.state.get(DETAIL_KEY) or {}), "service": service}
	return "DEMO"


def _detail_service_node(ctx: RouteContext, base: str) -> str | None:
	"""Ô `<base>_<dịch vụ khách nhắc trong tin>` nếu có; không rõ dịch vụ hoặc không có ô thì None."""
	service = (ctx.state.get(DETAIL_KEY) or {}).get("service") or ""
	target = f"{base}_{service}" if service in QUOTE_SERVICE_NODE else ""
	return target if target and store.get_node(target) else None


def _greeting_only(text: str) -> bool:
	try:
		from omni_crm.script import classify

		return bool(classify.is_greeting_only(text))
	except Exception:
		return False


def _typed_cancel_reason(ctx: RouteContext, text: str) -> bool:
	try:
		from omni_crm.script import classify
		from omni_crm.script.flows import demo

		if classify.is_greeting_only(text) or classify.is_ack_only(text) or classify.classify_by_rules(text, {}):
			return False
		return demo.typed_cancel_reason(ctx)
	except Exception:
		return False


CONTACT_ONLY_WORDS = 6  # "sđt anh 09xx nhé": vài chữ đệm quanh số điện thoại / email


def _contact_only(ctx: RouteContext, text: str) -> bool:
	"""Khách tự gửi số điện thoại / email khi bot không hỏi: ghi vào Lead (không ghi đè) để KHÔNG hỏi lại sau này.
	Tin chỉ có thông tin liên hệ thì báo đã ghi nhận và hỏi nhu cầu (True); tin còn ý khác thì ghi rồi xử lý ý đó (False)."""
	if not text:
		return False
	try:
		from omni_crm.script import validate
		from omni_crm.script.flows import collect

		email, phone = validate.find_email(text), validate.find_phone(text)
		if not (email or phone):
			return False
		collect._save_contact(ctx, email, phone)
	except Exception as e:
		frappe.logger("omni").info("omni script: không ghi được liên hệ khách tự gửi (%s)", type(e).__name__)
		return False
	other = [w for w in text.split() if "@" not in w and not any(ch.isdigit() for ch in w)]
	try:
		from omni_crm.script import classify

		has_intent = classify.classify_by_rules(" ".join(other)) not in (None, (UNCLEAR, 0.0), (UNCLEAR, 1.0))
	except Exception:
		has_intent = True
	if len(other) > CONTACT_ONLY_WORDS or has_intent or QUESTION.search(_fold(" ".join(other))):
		return False  # tin còn ý khác ("gửi tài liệu SPD qua mail …", "gọi cho anh số …"): đã ghi liên hệ, xử lý tiếp ý đó
	state.reset_reask(ctx.identity)
	return _reply_with_menu(ctx, NODE_GOT_CONTACT)


def _reply_with_menu(ctx: RouteContext, lead_in: str) -> bool:
	"""Câu dẫn + câu bot đang hỏi dở (nếu đang ở một câu có nút), không thì + menu chính."""
	if _resend_current(ctx, lead_in):
		return True
	ctx.state[engine.LEAD_IN_KEY] = _plain(ctx, lead_in)
	if engine.run_node(ctx, MENU_CODE):
		return True
	ctx.state.pop(engine.LEAD_IN_KEY, None)
	return False


def _polite_decline(text: str) -> bool:
	try:
		from omni_crm.script import classify

		return bool(classify.is_polite_decline(text))
	except Exception:
		return False


NEW_INTENTS = ("DOI_LICH", "HUY_LICH", "SUA_THONG_TIN", "NHAC_LAI", "KHONG_GAP_NGUOI")
NODE_SORRY_WAIT = "XINLOI_CHO"  # "Dạ, Miyano xin lỗi đã để anh/chị chờ ạ."
NODE_KB_NOTED = "KB_GHINHAN"  # chưa có thông tin: ghi lại cho chuyên viên, KHÔNG tạm dừng bot


CLARIFY_OK = re.compile(r"^(?:đổi|dời|chuyển|hủy|huỷ|đặt|sửa|giữ|gửi|nhận|xem|mua|lấy|thêm|bớt|báo giá|đăng ký|hỏi)(?![\wÀ-ỹ])", re.I)
CLARIFY_BAD = re.compile(r"khách|bot|hệ thống|slot|kịch bản|không rõ|chưa rõ", re.I)
CLARIFY_CONF = 0.8  # ý chính đã chắc từ mức này thì không hỏi làm rõ nữa
NODE_CLARIFY = "HOI_LAM_RO"
CLARIFY_MAX_OPTION = 60


def clarify_question(reading: dict) -> str:
	"""Câu hỏi làm rõ ghép từ hai cách hiểu AI nêu ("anh/chị muốn đổi lịch demo, hay đổi ngày nhận hàng ạ?") — thuần.
	Chỉ dùng cách hiểu ngắn, không số tiền, không đường link; không đủ hai cách hiểu sạch thì trả rỗng."""
	from omni_crm.script import brain

	for point in reading.get("chua_ro") or []:
		if not isinstance(point, dict) or point.get("anh_huong") != "DOI_HANH_DONG":
			continue
		options = [brain.safe_text(o, CLARIFY_MAX_OPTION).rstrip(".?!") for o in point.get("cach_hieu") or [] if isinstance(o, str)]
		options = [o[:1].lower() + o[1:] for o in options if o]
		# AI viết cách hiểu cho hệ thống đọc, không phải cho khách: chỉ nhận cụm mở đầu bằng một động từ hành động và
		# không nhắc "khách", "bot" (từng sinh câu "anh/chị muốn khách đang trả lời về số lượng…")
		options = [o for o in options if CLARIFY_OK.match(o) and not CLARIFY_BAD.search(o)][:2]
		if len(options) == 2:
			return f"anh/chị muốn {options[0]}, hay {options[1]} ạ?"
	return ""


# --- Tin có nhiều ý (yêu cầu 20/11: "trả lời câu hỏi trước, hành động sau", không bỏ ý nào) -------------------------
TODO_KEY = "todo"  # script_state: [{intent, service, at}] các việc khách đã nêu, làm sau khi xong việc đang làm
TODO_TTL_MINUTES = 120
TODO_CONF = 0.8
TODO_LABEL = {"TAILIEU": "nhận tài liệu", "DEMO": "đặt lịch demo", "BAOGIA": "báo giá"}
TODO_SERVICE_LABEL = {"VATTU": "vật tư", "PHANMEM": "phần mềm SupplyCore", "SPD": "hệ thống SPD"}
NODE_NOTED_MORE = "GHINHAN_THEM"  # "Miyano đã ghi nhận anh/chị còn cần {viec}; xong phần này Miyano hỗ trợ tiếp ạ."
NODE_NEXT_TODO = "TIEP_VIEC"  # "Dạ, tiếp theo là việc anh/chị đã nhắn lúc trước: {viec}."
FOLLOWUP_PREFIXES = ("HOITIEP_", "TL_HOITHEM")  # ô "còn cần hỗ trợ gì thêm"
DONE_PREFIXES = ("BG_XACNHAN", "DEMO_XACNHAN")  # ô chốt cuối luồng không kèm câu hỏi tiếp


def todo_label(item: dict) -> str:
	"""Tên một việc trong hàng chờ để nói với khách ("nhận tài liệu hệ thống SPD") — thuần, chỉ từ bảng cố định."""
	what = TODO_LABEL.get(item.get("intent") or "", "")
	service = TODO_SERVICE_LABEL.get(item.get("service") or "", "")
	return f"{what} {service}".strip()


def _todo_target(ctx: RouteContext, item: dict) -> str:
	"""Ô mở việc đã xếp hàng: theo dịch vụ khách nêu, không có thì theo dịch vụ đang xem, không rõ thì ô hỏi dịch vụ."""
	intent, service = item.get("intent"), item.get("service")
	if service in QUOTE_SERVICE_NODE and store.get_node(f"{intent}_{service}"):
		if not (intent == "DEMO" and service == "VATTU"):
			return f"{intent}_{service}"
	if intent == "DEMO":
		return NODE_DEMO_PRODUCT
	if intent == "BAOGIA":
		return "BAOGIA_CHUARO"
	return engine.service_code(ctx, intent) or MENU_CODE


def _several_ideas(ctx: RouteContext, code: str) -> None:
	"""Lượt có nhiều ý (theo bản đọc hiểu): trả lời NGAY các câu hỏi thông tin, rồi ghi các việc khác vào hàng chờ và
	báo khách đã ghi nhận. Việc chính (`code`) do nơi gọi làm tiếp. Không bao giờ ném lỗi."""
	try:
		reading = ctx.state.get(READING_KEY) or {}
		detail = ctx.state.get(DETAIL_KEY) or {}
		ideas = [i for i in reading.get("cac_y") or [] if isinstance(i, dict)]
		if len(ideas) < 2 and not (detail.get("questions") and code not in ("KB_HOI", "NHAC_LAI")):
			return
		from omni_crm.script import understand

		questions = detail.get("questions") or []
		if questions and code not in ("KB_HOI", "NHAC_LAI"):  # hai mã này tự trả lời câu hỏi ở bước định tuyến
			_answer_questions(ctx, questions)
		queued = []
		for idea in ideas:
			intent = idea.get("y_dinh")
			if intent not in TODO_LABEL or understand.INTENT_CODE.get(intent, intent) == code:
				continue
			if float(idea.get("do_tin_cay") or 0) < TODO_CONF:
				continue
			queued.append({"intent": intent, "service": understand.SERVICE_CODE.get(idea.get("dich_vu") or "", ""), "at": str(now_datetime())})
		if not queued:
			return
		state.update_state(ctx.identity, **{TODO_KEY: queued[:3]})
		note = _plain_with(ctx, NODE_NOTED_MORE, {"viec": ", ".join(todo_label(q) for q in queued[:3])})
		if note:
			ctx.state[engine.LEAD_IN_KEY] = note
	except Exception as e:
		frappe.logger("omni").info("omni script: xử lý tin nhiều ý lỗi (%s)", type(e).__name__)


def _plain_with(ctx: RouteContext, code: str, extra: dict) -> str:
	try:
		rendered = store.render(store.get_node(code), {**engine.variables(ctx), **extra, "identity": ctx.identity})
	except Exception:
		return ""
	return rendered[0] if rendered else ""


def _answer_questions(ctx: RouteContext, questions: list) -> None:
	"""Trả lời phần CÂU HỎI của một tin nhiều ý thành một tin riêng: Kho tri thức có thì trả lời, không thì nói rõ
	chưa có thông tin và ghi lại cho chuyên viên. Sau đó nơi gọi làm tiếp việc chính."""
	text = "\n".join(str(q).strip() for q in questions[:3] if str(q).strip())
	if not text:
		return
	res = kb_draft(ctx, question=text)
	if res and res["action"] == "Reply" and (res["text"] or "").strip() and _kb_auto_send(ctx, res):
		if send.send_text(ctx.identity, res["text"], code=KB_CODE):
			router._mark(ctx.batch_id)
			return
	_log_unanswered(ctx, "", {})
	if send.send_node(ctx.identity, NODE_KB_NOTED, engine.variables(ctx)):
		router._mark(ctx.batch_id)


def _fresh_todo(ctx: RouteContext) -> list:
	from frappe.utils import get_datetime

	items = engine.script_state(ctx).get(TODO_KEY) or []
	out = []
	for item in items if isinstance(items, list) else []:
		try:
			if (now_datetime() - get_datetime(item.get("at"))).total_seconds() <= TODO_TTL_MINUTES * 60:
				out.append(item)
		except Exception:
			continue
	return out


def _next_todo_before(ctx: RouteContext, code: str) -> str | None:
	"""Bot sắp hỏi "còn cần hỗ trợ gì thêm" mà khách đã nêu sẵn một việc: mở luôn việc đó."""
	if not str(code or "").startswith(FOLLOWUP_PREFIXES):
		return None
	return _take_todo(ctx)


def _next_todo_after(ctx: RouteContext, code: str) -> None:
	"""Vừa gửi ô chốt của một luồng (không có câu hỏi tiếp): còn việc khách đã nêu thì mở luôn."""
	if str(code or "").startswith(DONE_PREFIXES):
		target = _take_todo(ctx)
		if target:
			go(ctx, target)


def _take_todo(ctx: RouteContext) -> str | None:
	try:
		items = _fresh_todo(ctx)
		if not items:
			if engine.script_state(ctx).get(TODO_KEY):
				state.update_state(ctx.identity, **{TODO_KEY: None})
			return None
		item, rest = items[0], items[1:]
		state.update_state(ctx.identity, **{TODO_KEY: rest or None})
		ctx.state[engine.LEAD_IN_KEY] = _plain_with(ctx, NODE_NEXT_TODO, {"viec": todo_label(item)})
		return _todo_target(ctx, item)
	except Exception as e:
		frappe.logger("omni").info("omni script: không mở được việc đã xếp hàng (%s)", type(e).__name__)
		return None


def _no_staff(text: str) -> bool:
	try:
		from omni_crm.script.flows import handoff

		return len(text) <= 80 and bool(handoff.NO_STAFF.search(text))
	except Exception:
		return False


def _must_clarify(detail: dict, code: str) -> bool:
	"""Lượt này có điểm chưa rõ làm ĐỔI hành động và ý chính chưa đủ chắc. Đặt / đổi / huỷ lịch demo có ô hỏi lại riêng."""
	if not detail.get("unclear_action") or code in ("DEMO", "DOI_LICH", "HUY_LICH"):
		return False
	return float(detail.get("confidence") or 0) < CLARIFY_CONF


def _ask_to_clarify(ctx: RouteContext) -> bool:
	"""Hỏi lại đúng điểm chưa rõ (tối đa 2 lần cho một câu đang chờ); KHÔNG ghi gì, KHÔNG đổi luồng. True nếu đã hỏi."""
	question = clarify_question(ctx.state.get(READING_KEY) or {})
	asked = cint(engine.script_state(ctx).get("clarified"))
	if not question or asked >= 2 or not store.get_node(NODE_CLARIFY):
		return False
	if not send.send_node(ctx.identity, NODE_CLARIFY, {**engine.variables(ctx), "cau_hoi": question}):
		return False
	router._mark(ctx.batch_id)
	state.update_state(ctx.identity, clarified=asked + 1)
	state.reset_reask(ctx.identity)
	return True


def _route_new_intent(ctx: RouteContext, code: str) -> bool:
	"""Các ý định của bước hiểu mới: đổi / huỷ lịch demo, khách sửa điều đã nói, khách giục, khách rút yêu cầu gặp người."""
	detail = ctx.state.get(DETAIL_KEY) or {}
	state.reset_reask(ctx.identity)
	if code in ("DOI_LICH", "HUY_LICH"):
		target = "DEMO_DOILICH" if code == "DOI_LICH" else "DEMO_HUY"
		has_booking = bool(ctx.lead and frappe.db.get_value(LEAD, ctx.lead, "omni_demo_at"))
		if code == "HUY_LICH" and has_booking:
			return _show_booking(ctx)  # luật 7: huỷ lịch là việc khó hoàn tác, nhắc lịch và để khách bấm xác nhận
		if has_booking and _foreign_handler(target) is not None:
			go(ctx, target)
			return True
		route(ctx, "DEMO")  # chưa có lịch nào: coi như muốn đặt lịch
		return True
	if code == "SUA_THONG_TIN":
		from omni_crm.script.flows import collect

		changed = False
		for field, value in (detail.get("slots") or {}).items():
			if field in collect.PREFILL_FIELDS and value:
				collect.save_answer(ctx, field, str(value), typed=True)
				changed = True
		if changed and _back_to_pending(ctx, _pending_question(ctx), ""):
			return True
		return False
	if code == "NHAC_LAI":
		pending = _pending_question(ctx)
		if detail.get("questions"):
			ctx.state[engine.LEAD_IN_KEY] = _plain(ctx, NODE_SORRY_WAIT)
			answer_kb(ctx)
			return True
		if pending:  # xin lỗi, đọc lại điều đã ghi được, hỏi lại câu đang dở; hỏi mãi không xong thì mời gặp nhân viên
			from omni_crm.script.flows import collect

			prefix = _plain(ctx, NODE_SORRY_WAIT)
			have = collect.ack_text(list(store.as_dict(engine.script_state(ctx).get("answers")).items()))
			if have:
				noted = store.render(store.get_node(collect.NODE_HAVE), {**engine.variables(ctx), "identity": ctx.identity, "thong_tin": have})
				prefix = f"{prefix} {noted[0]}".strip() if noted else prefix
			node, values = store.get_node(pending), engine.variables(ctx)
			repeat = engine._repeat_count(ctx, pending)
			if node and repeat < engine.MAX_RESEND and send.send_node(ctx.identity, pending, values, prefix=prefix):
				state.update_state(ctx.identity, resend=repeat or None)
				router._mark(ctx.batch_id)
				return True
			ctx.state[engine.LEAD_IN_KEY] = prefix
			if engine.run_node(ctx, NODE_CONFIRM_HANDOFF):
				return True
			ctx.state.pop(engine.LEAD_IN_KEY, None)
		return _back_to_pending(ctx, pending, NODE_SORRY_WAIT) or _reply_with_menu(ctx, NODE_SORRY_WAIT)
	if code == "KHONG_GAP_NGUOI":
		return bool(engine.run_node(ctx, NODE_OK))
	return False


HANDOFF_CONF = 0.9
NODE_CONFIRM_HANDOFF = "GAP_NV_XACNHAN"


def _unsure_handoff(ctx: RouteContext, code: str) -> bool:
	"""AI đoán khách cần người với độ chắc chắn dưới 0,9 và khách không nói rõ (luật cố định không trúng)."""
	_code, conf = ctx.state.get(CLASSIFIED_KEY) or (code, 1.0)
	return float(conf or 0) < HANDOFF_CONF and not _explicit_request(ctx, code) and bool(store.get_node(NODE_CONFIRM_HANDOFF))


def _explicit_request(ctx: RouteContext, code: str) -> bool:
	"""Chính khách nói rõ yêu cầu dừng nhắn / xoá dữ liệu (luật cố định trúng đúng mã đó), không phải AI suy ra."""
	try:
		from omni_crm.script import classify

		hit = classify.classify_by_rules(ctx.text or "")
		return bool(hit and hit[0] == code) or (code == "HUY_DANGKY" and _is_stop_phrase(ctx.text or ""))
	except Exception:
		return False


def _show_booking(ctx: RouteContext) -> bool:
	try:
		from omni_crm.script.flows import demo

		return bool(demo.show_booking(ctx))
	except Exception:
		return False


# có lịch demo rồi mà khách báo bận / muốn dời (không cần nhắc chữ "demo" hay "lịch")
BUSY = re.compile(r"bận|kẹt|vướng|quên mất|không (?:đi|tham gia|dự|xem|kịp|sắp xếp)(?: được)?", re.I)
MOVE_PLAIN = re.compile(r"(?:đổi|dời|chuyển|lùi|hoãn)\b.{0,30}(?:lịch|giờ|ngày|hôm|thứ|tuần|mai|sáng|chiều|buổi)", re.I)


DEMO_MOVE = re.compile(
	r"\b(?:doi|chuyen|hoan|lui|dich)\b.*\b(?:demo|lich)\b|\b(?:demo|lich)\b.*\b(?:sang|qua) (?:ngay|tuan|hom|thu)\b"
)
DEMO_CANCEL = re.compile(r"\b(?:huy|bo|khong (?:tham gia|du|xem) (?:duoc )?)\b.*\b(?:demo|lich)\b")


def _demo_change(ctx: RouteContext, text: str) -> bool:
	"""Khách đã có lịch demo gõ "đổi lịch demo…" / "hủy lịch demo…": đi đúng nút Đổi lịch / Huỷ lịch. True nếu đã xử lý."""
	if not ctx.lead or not frappe.db.get_value(LEAD, ctx.lead, "omni_demo_at"):
		return False
	folded = _fold(text)
	code = "DEMO_HUY" if DEMO_CANCEL.search(folded) else "DEMO_DOILICH" if DEMO_MOVE.search(folded) else ""
	if not code and len(text) <= 120:
		if MOVE_PLAIN.search(text):  # "Đổi sang thứ 7 nhé"
			code = "DEMO_DOILICH"
		elif BUSY.search(text):  # "Anh quên mất là thứ 6 bận": nhắc lịch đang có, hỏi đổi hay huỷ
			state.reset_reask(ctx.identity)
			return _show_booking(ctx)
	if not code or _foreign_handler(code) is None:
		return False
	state.reset_reask(ctx.identity)
	if code == "DEMO_HUY" and _readback_on() and _show_booking(ctx):
		return True  # luật 7: huỷ lịch là việc khó hoàn tác — nhắc lịch đang có, khách bấm "Huỷ lịch" mới huỷ
	go(ctx, code)
	return True


def _readback_on() -> bool:
	return bool(cint(frappe.conf.get("omni_script_readback") or 0))


def _ack_only(text: str) -> bool:
	try:
		from omni_crm.script import classify

		return bool(classify.is_ack_only(text))
	except Exception:
		return False


SIDE_REPLY = {"BOT_HOI": "C3_BOT", "Y_KHOA": "C3_YKHOA", "KHONG_LIENQUAN": "C3_KHONGLIENQUAN"}
SIDE_LEAVE = ("MENU_VATTU", "MENU_PHANMEM", "MENU_SPD", "MENU_KHAC", "CAN_NGUOI", "MENU")
QUESTION = re.compile(
	r"\?|\b(?:gi|sao|the nao|nhu nao|khi nao|bao gio|bao lau|bao nhieu|o dau|la ai|ai vay)\b"
	r"|\b(?:khong|chua|ha|nhi)$"
)
TIME_WORDS = re.compile(r"\d|\b(?:tuan|thang|ngay|thu|mai|nay|gap|quy|cuoi|dau)\b")
AFFIRMATIVE = ("duoc", "dung", "dung roi", "dong y", "co")
DEMO_PENDING = {"attendees": "DEMO_SONGUOI", "time": "DEMO_GIOKHAC", "email": "DEMO_HOI_EMAIL"}


def _shown_buttons(ctx: RouteContext, node: dict | None) -> list[dict]:
	"""Các nút của ô mà khách thật sự nhìn thấy (nút dẫn tới ô `Tắt` đã bị ẩn khi gửi)."""
	buttons = [
		{"label": b.get("label") or "", "target": store.normalize_code(b.get("target"))}
		for b in (node or {}).get("buttons") or []
	]
	try:
		return store._usable_buttons([b for b in buttons if b["target"]], ctx.identity)
	except Exception:
		return []


def _pending_question(ctx: RouteContext) -> str | None:
	"""Mã ô của câu bot đang hỏi dở và chờ khách gõ trả lời (thu thông tin, số người / giờ / email demo); không có → None."""
	st = engine.script_state(ctx)
	flow = (st.get("flow") or "").lower()
	if flow == "collect" and st.get("field"):
		return str(st.get("node") or "") or None
	if flow == "demo":
		return DEMO_PENDING.get(str(st.get("demo_wait") or ""))
	if flow == "handoff_phone":  # = handoff.FLOW_PHONE
		return "GAP_NV_GOIDIEN"
	return None


def _ask_again(ctx: RouteContext, code: str, lead_in: str) -> bool:
	"""Gửi lại câu đang hỏi dở kèm câu dẫn, giữ nguyên trạng thái đang hỏi. True nếu đã gửi."""
	node = store.get_node(code)
	if not node:
		return False
	values = engine.variables(ctx)
	repeat = engine._repeat_count(ctx, code)
	if engine.missing_vars(node["text"], values) or repeat >= engine.MAX_RESEND:
		return False
	if not send.send_node(ctx.identity, code, values, prefix=_plain(ctx, lead_in) if lead_in else ""):
		return False
	state.update_state(ctx.identity, resend=repeat or None)
	router._mark(ctx.batch_id)
	return True


MID_FLOW_PREFIXES = ("DEMO_", "BAOGIA_", "BG_", "TAILIEU_", "TL_", "NL_")


def _resend_slots(ctx: RouteContext) -> bool:
	"""Bot đang chờ khách chọn khung giờ demo: gửi lại danh sách khung giờ (tính lại cho mới)."""
	try:
		from omni_crm.script.flows import demo

		return demo.waiting_for_slot(ctx.identity) and bool(demo.propose(ctx))
	except Exception:
		return False


def _back_to_pending(ctx: RouteContext, pending: str | None, lead_in: str = "") -> bool:
	"""Sau khi đáp một câu chen ngang: quay lại câu bot đang hỏi dở (câu thu thông tin, câu có nút giữa luồng, hoặc
	danh sách khung giờ demo). Đang ở menu thì trả False để nơi gọi gửi câu hỏi tiếp bình thường."""
	if pending:
		return _ask_again(ctx, pending, lead_in)
	if _resend_slots(ctx):
		return True
	code = str(state.get(ctx.identity).get("script_node") or ctx.state.get("script_node") or "")
	return code.startswith(MID_FLOW_PREFIXES) and _resend_current(ctx, lead_in)


def _reply_then_back(ctx: RouteContext, reply: str, pending: str | None = None) -> bool:
	"""Gửi câu đáp cố định (`C3_*`) thành một tin riêng rồi hỏi lại câu đang dở. Không có câu nào đang dở → False."""
	pending = pending or _pending_question(ctx)
	code = str(ctx.state.get("script_node") or "")
	node = store.get_node(code) or {}
	waiting = bool(pending) or (code not in TOP_NODES and bool(node.get("buttons")))
	try:
		from omni_crm.script.flows import demo

		waiting = waiting or demo.waiting_for_slot(ctx.identity)
	except Exception:
		pass
	if not waiting or not send.send_node(ctx.identity, reply, engine.variables(ctx)):
		return False
	router._mark(ctx.batch_id)
	if pending:
		_ask_again(ctx, pending, "")
	elif not _resend_slots(ctx):
		_resend_current(ctx, "")
	return True


def _tap_affirmative(ctx: RouteContext) -> bool:
	"""Khách gõ "ok", "vâng" khi câu đang hỏi có nút đồng ý ("Được", "Đúng"): coi như bấm nút đó."""
	node = store.get_node(_pending_question(ctx) or ctx.state.get("script_node"))
	for b in _shown_buttons(ctx, node):
		if _fold(b["label"]) in AFFIRMATIVE:
			ctx.payload_code = b["target"]
			handler = _foreign_handler(b["target"])
			if handler is not None:
				handler(ctx)
			else:
				on_button(ctx)
			return True
	return False


AI_CHECKED = ("items", "quantity", "file")
OTHER_INTENT = ("DEMO", "TAILIEU", "MENU_PHANMEM", "MENU_SPD", "MENU_KHAC", "GAP_NHANVIEN", "CAN_NGUOI")  # câu trả lời tự do dễ lẫn với chuyện ngoài lề: hỏi AI trước khi ghi
ANSWER_CODES = (UNCLEAR, "MENU_VATTU", "BAOGIA")


_NUM = r"(\d[\d.]*)"
_RANGES = (
	(re.compile(rf"^(?:dưới|duoi|<)\s*{_NUM}$", re.I), lambda a, b=None: (0, a - 1)),
	(re.compile(rf"^(?:trên|tren|>)\s*{_NUM}$", re.I), lambda a, b=None: (a + 1, 10**12)),
	(re.compile(rf"^(?:từ\s*|tu\s*)?{_NUM}\s*[–\-]\s*{_NUM}$", re.I), lambda a, b=None: (a, b)),
	(re.compile(rf"^{_NUM}$"), lambda a, b=None: (a, a)),
)


def _range_of(label: str) -> tuple[int, int] | None:
	"""Khoảng số của một nhãn nút ("Dưới 500", "500–2.000", "Trên 20", "1"); nhãn không phải khoảng số → None."""
	for pattern, make in _RANGES:
		m = pattern.match(label.strip())
		if m:
			return make(*[int(g.replace(".", "")) for g in m.groups()])
	return None


def _number_choice(ctx: RouteContext, text: str, pending: str) -> str | None:
	"""Khách gõ MỘT con số khi câu đang hỏi có các nút chứa chữ số. Nút là khoảng số → chọn khoảng chứa số đó
	("300" → "Dưới 500"). Nút không phải khoảng số ("Trong 1 tuần"…) → số nhỏ là số thứ tự của nút."""
	raw = text.strip().rstrip(".)").replace(".", "")
	buttons = _shown_buttons(ctx, store.get_node(pending))
	if not raw.isdigit() or len(raw) > 9 or len(buttons) < 2:
		return None
	labels = [b["label"] for b in buttons]
	if not any(ch.isdigit() for label in labels for ch in label):
		return None  # danh sách đánh số thường: router đã xử lý
	number, ranges = int(raw), [_range_of(label) for label in labels]
	if all(ranges):
		hits = [b["target"] for b, (low, high) in zip(buttons, ranges) if low <= number <= high]
		return hits[0] if len(hits) == 1 else None
	return buttons[number - 1]["target"] if 1 <= number <= len(buttons) else None


DECLINE_WORDS = 4
def _new_session(ctx: RouteContext) -> bool:
	"""Khách im lặng lâu hơn `care_session_gap_hours` (Omni Settings) kể từ tin cuối của bot: coi là phiên mới."""
	try:
		from frappe.utils import get_datetime

		hours = float(frappe.db.get_single_value("Omni Settings", "care_session_gap_hours") or 0)
		last = ctx.state.get("script_last_bot_at")
		return hours > 0 and bool(last) and (now_datetime() - get_datetime(last)).total_seconds() > hours * 3600
	except Exception:
		return False


STALE_MINUTES = 60  # câu hỏi dở quá lâu không được trả lời thì không còn là "câu đang hỏi"


def _stale(ctx: RouteContext) -> bool:
	"""Tin cuối của bot đã cách đây hơn `STALE_MINUTES` phút."""
	try:
		from frappe.utils import get_datetime

		last = ctx.state.get("script_last_bot_at")
		return bool(last) and (now_datetime() - get_datetime(last)).total_seconds() > STALE_MINUTES * 60
	except Exception:
		return False


NO_AI_FIELDS = ("name_org", "email", "phone")  # tên – đơn vị, liên hệ: không có gì để "hiểu ý", không gửi AI


def _plain_answer(field: str, text: str, st: dict) -> bool:
	"""Câu trả lời đủ rõ để ghi ngay mà không cần AI đọc ngữ cảnh: ô tên / liên hệ, câu có con số ở ô số lượng
	hay số người, ngày giờ đọc được ở bước hẹn demo."""
	if field in NO_AI_FIELDS:
		return True
	has_digit = any(ch.isdigit() for ch in text)
	if field in ("quantity", "attendees") and has_digit:
		return True
	if (st.get("flow") or "") == "demo":
		wait = str(st.get("demo_wait") or "")
		if wait == "attendees" and has_digit:
			return True
		if wait == "email" and "@" in text:
			return True
		if wait == "time":
			try:
				from omni_crm.script.flows import demo

				return bool(demo.parse_time(text))
			except Exception:
				return False
	return False


def _side_talk(ctx: RouteContext, text: str, st: dict) -> bool:
	"""Bot đang hỏi dở mà khách chào, ừ, gửi sticker hay hỏi chen ngang: KHÔNG ghi câu đó làm câu trả lời; đáp lời rồi
	hỏi lại đúng câu đang dở. Khách nói rõ ý khác (dịch vụ khác, cần người) thì rời câu hỏi. True nếu đã xử lý."""
	pending = _pending_question(ctx)
	if not pending:
		return False
	if not text:
		return False if ctx.attachments else _ask_again(ctx, pending, NODE_BACK)
	try:
		from omni_crm.script import classify
		from omni_crm.script.flows import collect
	except Exception:
		return False
	if classify.is_greeting_only(text):
		if _stale(ctx):  # câu đang hỏi dở đã quá cũ: bỏ, để khách bắt đầu lại từ menu
			collect._clear_flow(ctx)
			state.update_state(ctx.identity, demo_wait=None, handoff_phone_tries=None)
			return False
		return _ask_again(ctx, pending, NODE_HELLO_AGAIN)
	picked = _number_choice(ctx, text, pending)
	if picked:  # gõ số cho danh sách khoảng số ("Dưới 500 / 500–2.000…") hoặc số thứ tự của danh sách không đánh số
		ctx.payload_code = picked
		on_button(ctx)
		return True
	if classify.is_ack_only(text):
		return _tap_affirmative(ctx) or _ask_again(ctx, pending, NODE_YES)
	if not any(ch.isalnum() for ch in text):  # "?", "👍": không có gì để ghi
		return _ask_again(ctx, pending, NODE_BACK)
	hit = classify.classify_by_rules(text, ctx.state)
	code = hit[0] if hit else None
	if code in collect.LEAVE_CODES or code in ("BAOGIA", classify.INJECTION_CODE):
		return False  # luồng đang hỏi tự xử lý (rời câu hỏi / xin giá khi đang báo giá / câu chèn lệnh: ghi cho nhân viên, không đáp)
	if code is None or code == UNCLEAR:
		# lời từ chối NGẮN ("không ạ", "bỏ qua", "thôi em"); câu dài mở đầu bằng "thôi…" có thể là ý khác → để AI đọc
		declining = len(text.split()) <= DECLINE_WORDS and collect.DECLINE.search(text)
		if declining or "@" in text:
			return False  # từ chối hoặc đang gõ email: luồng đang hỏi tự xử lý
		field = str(st.get("field") or "")
		if field == collect.DELIVERY_FIELD and TIME_WORDS.search(_fold(text)) and not _understand_on():
			return False  # "thứ 3 tuần sau được không em": vẫn là câu trả lời về thời gian (kèm lời hỏi giao kịp không)
		asking = bool(QUESTION.search(_fold(text)))
		if not asking and _plain_answer(field, text, st):
			return False  # câu trả lời rõ ràng (có con số, tên – đơn vị…): ghi luôn, không cần hỏi AI
		code, _conf = classify_text(ctx)
		detail = ctx.state.get(DETAIL_KEY) or {}
		relation = detail.get("relation") or ""
		if code in ("SUA_THONG_TIN", "NHAC_LAI") and _route_new_intent(ctx, code):
			return True  # khách sửa điều đã nói / giục: xử lý rồi hỏi lại câu đang dở, không ghi vào ô đang hỏi
		if relation == "TRA_LOI":  # AI đọc cả ngữ cảnh và coi đây là câu trả lời cho câu đang hỏi
			if (detail.get("slots") or {}).get(field):
				return False  # có giá trị đã trích cho đúng ô này: luồng thu thông tin ghi giá trị đó
			picked = ai_choice(ctx, _shown_buttons(ctx, store.get_node(pending)))
			if picked:
				_tap(ctx, picked)
				return True
			return False
		if _must_clarify(detail, code):  # bảng quyết định, dòng 1: KHÔNG ghi, KHÔNG đổi luồng; hỏi làm rõ hoặc hỏi lại
			return _ask_to_clarify(ctx) or collect.not_recorded(ctx)
		if relation == "Y_KHAC" and field not in collect.CONTACT_FIELDS:
			if code in SIDE_REPLY:
				return _reply_then_back(ctx, SIDE_REPLY[code], pending)
			if code == "KB_HOI":
				state.reset_reask(ctx.identity)
				answer_kb(ctx)
				return True
			if code != UNCLEAR and code not in ("BAOGIA", "MENU_VATTU") or (code in ("BAOGIA", "MENU_VATTU") and st.get("branch") != "3b"):
				collect._clear_flow(ctx)  # khách nói sang việc khác: rời câu đang hỏi, làm theo ý khách
				state.update_state(ctx.identity, demo_wait=None)
				state.reset_reask(ctx.identity)
				route(ctx, _refine(ctx, code))
				return True
		slots = detail.get("slots") or {}
		plain = not asking and code in ANSWER_CODES and (slots or code != UNCLEAR or len(text.split()) > 1)
		if field in AI_CHECKED and plain:
			return False  # AI coi là câu trả lời (nêu mặt hàng / nhu cầu mua)
		if field in AI_CHECKED and code == UNCLEAR:  # "asdfgh": KHÔNG ghi; hỏi lại (tối đa 2 lần), lần ba mời nhân viên
			return collect.not_recorded(ctx) or _ask_again(ctx, pending, NODE_REASK_CHOICE)
		if field in AI_CHECKED and code in OTHER_INTENT:
			# khách nói sang việc khác hẳn ("Anh muốn demo spd", "Demo cơ mà không phải mua hàng"): KHÔNG ghi làm
			# câu trả lời; rời câu đang hỏi và làm theo ý khách
			collect._clear_flow(ctx)
			state.reset_reask(ctx.identity)
			route(ctx, _refine(ctx, code))
			return True
	if code in SIDE_REPLY:
		return _reply_then_back(ctx, SIDE_REPLY[code], pending)
	if code == "KB_HOI":
		state.reset_reask(ctx.identity)
		answer_kb(ctx)
		return True
	if code in SIDE_LEAVE and (st.get("field") or "") not in collect.CONTACT_FIELDS:
		# (ở câu xin email / số điện thoại, luồng thu thông tin tự xử lý: trả lời ý khác nhưng vẫn giữ câu đang hỏi)
		collect._clear_flow(ctx)
		state.update_state(ctx.identity, demo_wait=None)
		state.reset_reask(ctx.identity)
		route(ctx, code)
		return True
	return False


def _plain(ctx: RouteContext, code: str) -> str:
	"""Phần chữ đã điền biến của một ô chỉ dùng làm câu dẫn; ô tắt / thiếu thì rỗng."""
	try:
		rendered = store.render(store.get_node(code), {**engine.variables(ctx), "identity": ctx.identity})
	except Exception:
		return ""
	return rendered[0] if rendered else ""


def _resend_current(ctx: RouteContext, lead_in: str) -> bool:
	"""Khách đang đứng ở một câu hỏi có lựa chọn (không phải menu chính): gửi lại ĐÚNG câu đó kèm câu dẫn, để khách
	không bị đẩy về menu chính giữa chừng. Không chạy lại lệnh của ô. True nếu đã gửi."""
	code = str(ctx.state.get("script_node") or "")
	node = store.get_node(code)
	if not node or code in TOP_NODES or not node.get("buttons"):
		return False
	if any("{" in (b.get("label") or "") for b in node["buttons"]):
		return False  # nút tính động (vd khung giờ demo): không gửi lại bằng đường này
	values = engine.variables(ctx)
	repeat = engine._repeat_count(ctx, code)
	if engine.missing_vars(node["text"], values) or repeat >= engine.MAX_RESEND:
		return False
	if not send.send_node(ctx.identity, code, values, prefix=_plain(ctx, lead_in) if lead_in else ""):
		return False
	state.update_state(ctx.identity, resend=repeat or None)
	router._mark(ctx.batch_id)
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
	if _resend_current(ctx, NODE_REASK_CHOICE) or _resend_slots(ctx):
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
	thinking(ctx)  # soạn câu từ Kho tri thức mất vài giây
	res = kb_draft(ctx)
	if res and res["action"] == "Reply" and (res["text"] or "").strip():
		if _kb_auto_send(ctx, res):
			pending = _pending_question(ctx)
			lead_in = ctx.state.pop(engine.LEAD_IN_KEY, "")
			if send.send_text(ctx.identity, f"{lead_in} {res['text']}".strip(), code=KB_CODE):
				router._mark(ctx.batch_id)
				if not _back_to_pending(ctx, pending, NODE_BACK):
					engine.run_node(ctx, engine.service_code(ctx, "HOITIEP") or MENU_CODE)
				return
		elif not _shadow_only(res["group"]) and _to_sale_draft(ctx, res["name"]):
			_hold_for_staff(ctx)  # luật 9: nháp chờ nhân viên duyệt thì khách vẫn nhận một câu, không im lặng
			return
	if _understand_on() and store.get_node(NODE_KB_NOTED):
		pending = _pending_question(ctx)
		_log_unanswered(ctx, "", {})
		if send.send_node(ctx.identity, NODE_KB_NOTED, engine.variables(ctx), prefix=ctx.state.pop(engine.LEAD_IN_KEY, "")):
			router._mark(ctx.batch_id)
			_back_to_pending(ctx, pending, NODE_BACK)
			return
	if not engine.run_node(ctx, NODE_KB_NONE, {"question": ctx.text}):
		engine.fallback(ctx, NODE_KB_NONE)


NODE_KB_HOLD = "KB_CHODUYET"


def _hold_for_staff(ctx: RouteContext) -> bool:
	"""Câu trả lời đang chờ nhân viên duyệt: báo khách đã nhận câu hỏi rồi quay lại câu đang hỏi dở (nếu có). Nhân viên
	đã trả lời sau tin này thì thôi. Không bao giờ ném lỗi."""
	try:
		from omni_crm.bot import reply

		last = (reply._batch_messages(ctx.batch_id) or [None])[-1]
		if last and send.staff_replied_after(ctx.lead, last.received_at):
			return False
		pending = _pending_question(ctx)
		prefix = ctx.state.pop(engine.LEAD_IN_KEY, "")
		if not send.send_node(ctx.identity, NODE_KB_HOLD, engine.variables(ctx), prefix=prefix):
			return False
		router._mark(ctx.batch_id)
		_back_to_pending(ctx, pending, NODE_BACK)
		return True
	except Exception as e:
		frappe.logger("omni").info("omni script: không gửi được câu giữ chân (%s)", type(e).__name__)
		return False


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
		if last and send.staff_replied_after(ctx.lead, last.received_at):
			return False  # nhân viên đã trả lời sau tin này (echo của chính tin bot không tính)
	except Exception as e:
		frappe.logger("omni").info("omni script: không kiểm được điều kiện tự gửi (%s)", type(e).__name__)
		return False
	return True


def _shadow_only(group: str | None) -> bool:
	return bool(group) and bool(cint(frappe.db.get_value("Omni Question Group", group, "shadow_only")))


def kb_draft(ctx: RouteContext, question: str | None = None) -> dict | None:
	"""Soạn câu trả lời cho tin hiện tại (hoặc riêng `question`) bằng bộ máy Reply (nháp ngầm, không có đường gửi);
	lỗi hoặc không có → None."""
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
				customer_text=question or ctx.text,
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


engine.on_before_node(_next_todo_before)
engine.on_after_node(_next_todo_after)
engine.register_action("spam", flag_spam)
engine.register_action("no_lead", lambda ctx, arg, node: None)
engine.register_action("log_unanswered", _log_unanswered)
engine.register_state(FLOW_KB, _on_kb_text)
register_handlers()
