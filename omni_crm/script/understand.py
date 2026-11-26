"""Bước HIỂU của bot kịch bản (yêu cầu "hiểu ngữ cảnh hội thoại" 20/11, bước 4): một lượt gọi AI đọc CẢ lượt tin của
khách cùng toàn bộ ngữ cảnh và trả về bản đọc hiểu có cấu trúc. Bước này không soạn câu trả lời.

- `build_input(ctx)`: đầu vào đầy đủ — thời gian, hồ sơ lead, việc đang mở (lịch demo, báo giá dở, chờ nhân viên, câu hỏi
  chưa trả lời), trạng thái kịch bản (ô, câu bot vừa hỏi kèm lựa chọn, thông tin đã thu), hội thoại (tới 30 tin), lượt đang
  xử lý. Nội dung khách được che email/SĐT và thoát thẻ; chỉ là DỮ LIỆU.
- `TOOL`: schema bản đọc hiểu (dien_giai, quan_he, cac_y, slots, cau_hoi_kb, chua_ro, mau_thuan, da_co_san, cam_xuc).
- `read(ctx)`: gọi AI; sai schema / lỗi → bản đọc hiểu `KHONG_RO`.
- `clean(raw, text, history)`: kiểm lại bản đọc hiểu bằng code — tên hàng, hãng phải có trong lời khách (lượt này hoặc
  lịch sử); mã ý định, quan hệ phải thuộc danh sách.

Câu lệnh: `bot/prompts/script_understand_v<N>.md`. Mô hình: site_config `omni_understand_model`, không có thì
`Omni Settings.analyze_model`.
"""

from __future__ import annotations

import re

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm.bot import ai_client
from omni_crm.bot.context import escape_tags, mask_pii
from omni_crm.bot.prompts.loader import load_prompt, render

PROMPT_NAME = "script_understand"
PROMPT_VERSION = "v2"  # v2 (25/11): mat_hang_bo, so_luong_chu, mặt hàng chỉ của LƯỢT NÀY khi khách sửa danh sách
MODEL_CONF = "omni_understand_model"
DEFAULT_MODEL = "claude-haiku-5-5"
AI_KIND = "Reply"
MAX_TOKENS = 2000  # bản đọc hiểu có nhiều trường; 900 từng làm mô hình lớn bị cắt mất slots
MAX_MESSAGES = 30
MAX_MESSAGE_CHARS = 500
INBOUND, OUTBOUND, LEAD, IDENTITY = "Omni Inbound Message", "Omni Outbound Message", "CRM Lead", "Omni Identity"
WEEKDAYS = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")

RELATIONS = ("TRA_LOI", "HOI_NGUOC", "Y_KHAC", "XA_GIAO", "KHONG_RO")
INTENTS = (
	"MENU_VATTU", "MENU_PHANMEM", "MENU_SPD", "MENU_KHAC", "BAOGIA", "TAILIEU", "DEMO", "DOI_LICH", "HUY_LICH",
	"HOI_THONG_TIN", "SUA_THONG_TIN", "NHAC_LAI", "CAN_NGUOI", "KHONG_GAP_NGUOI", "PHAN_NAN", "Y_KHOA",
	"KHONG_LIENQUAN", "SPAM", "HUY_DANGKY", "XOA_DULIEU", "MENU", "CHUA_RO",
)  # fmt: skip
SERVICES = ("VATTU", "SUPPLYCORE", "SPD", "KHONG_RO")

_S = {"type": "string"}
TOOL = {
	"name": "doc_hieu",
	"description": "Trả bản đọc hiểu của lượt tin khách vừa gửi.",
	"input_schema": {
		"type": "object",
		"properties": {
			"dien_giai": _S,
			"quan_he": {"type": "string", "enum": list(RELATIONS)},
			"lua_chon": {"type": "integer", "minimum": 0, "maximum": 9},
			"cac_y": {
				"type": "array",
				"maxItems": 4,
				"items": {
					"type": "object",
					"properties": {
						"y_dinh": {"type": "string", "enum": list(INTENTS)},
						"noi_dung": _S,
						"dich_vu": {"type": "string", "enum": list(SERVICES)},
						"doi_tuong": {"type": "string", "enum": ["LICH_DEMO", "BAO_GIA", "YEU_CAU_GAP_NGUOI", "KHONG"]},
						"do_tin_cay": {"type": "number", "minimum": 0, "maximum": 1},
					},
					"required": ["y_dinh", "do_tin_cay"],
					"additionalProperties": False,
				},
			},
			"slots": {
				"type": "object",
				"properties": {
					"mat_hang": {
						"type": "array",
						"maxItems": 10,
						"items": {
							"type": "object",
							"properties": {"ten": _S, "so_luong": {"type": ["number", "null"]}, "so_luong_chu": _S, "don_vi": _S, "hang": _S, "quy_cach": _S},
							"required": ["ten"],
							"additionalProperties": False,
						},
					},
					"mat_hang_bo": {"type": "array", "maxItems": 10, "items": _S},
					"thoi_gian_can_hang": {
						"type": "object",
						"properties": {
							"nguyen_van": _S,
							"ngay": _S,
							"lua_chon": {"type": "string", "enum": ["TRONG_1_TUAN", "TRONG_1_THANG", "1_3_THANG", "THEO_GOI_THAU"]},
						},
						"additionalProperties": False,
					},
					"hang_thay_the": {"type": "string", "enum": ["DUOC", "CHI_DUNG_HANG", "TUY_MAT_HANG"]},
					"hinh_thuc_demo": {"type": "string", "enum": ["ONLINE", "TRUC_TIEP"]},
					"thoi_gian_demo": {
						"type": "object",
						"properties": {
							"nguyen_van": _S,
							"thu": {"type": ["integer", "null"]},
							"tuan": {"type": "string", "enum": ["NAY", "SAU"]},
							"ngay": _S,
							"gio": _S,
						},
						"additionalProperties": False,
					},
					"so_nguoi": {"type": ["integer", "null"]},
					"sdt": _S,
					"email": _S,
					"ten_khach": _S,
					"don_vi": _S,
				},
				"additionalProperties": False,
			},
			"cau_hoi_kb": {"type": "array", "maxItems": 4, "items": _S},
			"chua_ro": {
				"type": "array",
				"maxItems": 4,
				"items": {
					"type": "object",
					"properties": {
						"diem": _S,
						"cach_hieu": {"type": "array", "maxItems": 3, "items": _S},
						"anh_huong": {"type": "string", "enum": ["DOI_HANH_DONG", "CHI_TIET"]},
					},
					"required": ["diem", "anh_huong"],
					"additionalProperties": False,
				},
			},
			"mau_thuan": {"type": "array", "maxItems": 3, "items": _S},
			"da_co_san": {"type": "array", "maxItems": 4, "items": _S},
			"cam_xuc": {"type": "string", "enum": ["BINH_THUONG", "SOT_RUOT", "BUC_BOI"]},
			"ly_do": _S,
		},
		"required": ["dien_giai", "quan_he", "cac_y"],
		"additionalProperties": False,
	},
}
FAILED = "khong_doc_duoc"  # khoá đánh dấu bản đọc hiểu rỗng vì AI không dùng được
UNKNOWN = {"dien_giai": "", "quan_he": "KHONG_RO", "cac_y": [], "slots": {}, "cau_hoi_kb": [], "chua_ro": []}


# --- Đầu vào ----------------------------------------------------------------------------------------------------


def _clip(text: object, limit: int = MAX_MESSAGE_CHARS) -> str:
	clean = escape_tags(mask_pii(" ".join(str(text or "").split())))
	return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _hm(value) -> str:
	try:
		return get_datetime(value).strftime("%d/%m %H:%M")
	except Exception:
		return ""


def now_line(now=None, hours_text: str = "") -> str:
	"""Dòng thời gian cho AI: ngày giờ hiện tại kèm thứ trong tuần, giờ làm việc (thuần khi truyền `now`)."""
	now = now or now_datetime()
	out = f"Bây giờ là {WEEKDAYS[now.weekday()]} ngày {now.strftime('%d/%m/%Y')}, {now.strftime('%H:%M')}."
	return out + (f" Giờ làm việc: {hours_text}." if hours_text else "")


def _hours_text() -> str:
	try:
		raw = frappe.get_cached_doc("Omni Settings").get("business_hours")
		return " ".join(str(raw or "").split())[:200]
	except Exception:
		return ""


def conversation(identity: str, batch_id: str | None, limit: int = MAX_MESSAGES) -> tuple[list[str], list[str]]:
	"""(các dòng hội thoại cũ trước mới sau, các tin của lượt đang xử lý). Bỏ câu báo chờ và chữ thế chỗ "[Sticker]"."""
	from omni_crm.script import router, send

	rows = []
	for r in frappe.get_all(
		INBOUND,
		filters={"identity": identity, "event_type": "message"},
		fields=["received_at", "text", "batch_id"],
		order_by="received_at desc",
		limit=limit,
	):
		text = (r.text or "").strip()
		if text and not router.NON_TEXT.match(text):
			rows.append((r.received_at, "KH", ("(bấm nút) " + text[1:]) if text.startswith("#") else text, r.batch_id))
	for r in frappe.get_all(
		OUTBOUND,
		filters={"identity": identity},
		fields=["creation", "text", "source", "script_node"],
		order_by="creation desc",
		limit=limit,
	):
		if r.script_node != send.NODE_THINKING and (r.text or "").strip():
			rows.append((r.creation, "NV" if r.source == "Sale" else "BOT", r.text, None))
	rows = sorted((r for r in rows if r[0]), key=lambda r: str(r[0]))[-limit:]
	current = [r[2] for r in rows if r[1] == "KH" and batch_id and r[3] == batch_id]
	history = [f"[{_hm(at)}] {who}: {_clip(text)}" for at, who, text, b in rows if not (who == "KH" and batch_id and b == batch_id)]
	return history, current


def open_items(ctx, st: dict, lead: dict) -> list[str]:
	"""Các việc đang mở của khách, mỗi việc một dòng."""
	out = []
	demo_at = lead.get("omni_demo_at")
	if demo_at and get_datetime(demo_at) > now_datetime():
		at = get_datetime(demo_at)
		line = f"Lịch demo đang có: {WEEKDAYS[at.weekday()]} {at.strftime('%d/%m/%Y %H:%M')}"
		if lead.get("omni_demo_mode"):
			line += f", hình thức {lead['omni_demo_mode']}"
		if lead.get("omni_demo_attendees"):
			line += f", {cint(lead['omni_demo_attendees'])} người"
		out.append(line + ".")
	answers = st.get("answers") if isinstance(st.get("answers"), dict) else {}
	if st.get("branch") == "3b" and (st.get("flow") or answers):
		out.append("Yêu cầu báo giá đang dở" + (f", đã thu: {_clip(answers, 300)}" if answers else "") + ".")
	if isinstance(st.get("handoff"), dict) and st["handoff"].get("at") and ctx.state.get("bot_paused_until"):
		out.append("Khách đang chờ nhân viên trả lời (đã chuyển người).")
	unanswered = " ".join(str(lead.get("omni_unanswered_questions") or "").split())
	if unanswered:
		out.append("Câu hỏi của khách chưa được trả lời: " + _clip(unanswered[-400:], 400))
	return out


def build_input(ctx) -> str:
	"""Toàn bộ đầu vào bước hiểu cho lượt `ctx` (đọc DB). Mọi nội dung do khách viết đã che email/SĐT và thoát thẻ."""
	from omni_crm.script import engine

	st = engine.script_state(ctx)
	lead = {}
	if ctx.lead:
		fields = ["lead_name", "organization", "mobile_no", "email", "omni_demo_at", "omni_demo_mode", "omni_demo_attendees",
			"omni_unanswered_questions", "omni_bot_summary", "omni_services"]  # fmt: skip
		lead = frappe.db.get_value(LEAD, ctx.lead, fields, as_dict=True) or {}
	history, current = conversation(ctx.identity, ctx.batch_id)
	if not current:
		current = [t for t in (ctx.text or "").split("\n") if t.strip()]
	profile = [
		f"Tên: {_clip(st.get('ten') or '', 60) or 'chưa biết'}",
		f"Đơn vị: {_clip(lead.get('organization') or '', 80) or 'chưa biết'}",
		"Số điện thoại: " + ("đã có" if lead.get("mobile_no") else "chưa có"),
		"Email: " + ("đã có" if lead.get("email") else "chưa có"),
	]
	if lead.get("omni_services"):
		profile.append(f"Dịch vụ đã quan tâm: {_clip(lead['omni_services'], 80)}")
	if lead.get("omni_bot_summary"):
		profile.append(f"Tóm tắt các phiên trước: {_clip(lead['omni_bot_summary'], 400)}")
	asked = ""
	last_bot = next((line for line in reversed(history) if "] BOT: " in line), "")
	waiting = bool(st.get("flow")) or bool((st.get("choices") or {}).get("items"))
	if last_bot and history and history[-1] == last_bot and (waiting or "?" in last_bot):
		asked = last_bot.split("] BOT: ", 1)[1]
		items = (st.get("choices") or {}).get("items") or []
		if items and "\n1. " not in asked and " 1. " not in asked:
			asked += " Lựa chọn: " + "; ".join(f"{i}. {_clip(label, 60)}" for i, (label, _t) in enumerate(items, 1))
	answers = st.get("answers") if isinstance(st.get("answers"), dict) else {}
	state_lines = [
		f"Ô kịch bản đang đứng: {re.sub(r'[^A-Za-z0-9_]', '', str(ctx.state.get('script_node') or '')) or 'không'}",
		f"Dịch vụ đang xem: {re.sub(r'[^A-Za-z0-9_]', '', str(st.get('service') or '')) or 'chưa rõ'}",
		"Câu bot vừa hỏi và đang chờ trả lời: " + (asked or "không có"),
		"Thông tin đã thu: " + (_clip(answers, 400) if answers else "chưa có"),
	]
	opened = open_items(ctx, st, lead)
	parts = [
		("thoi_gian", now_line(hours_text=_hours_text())),
		("ho_so", "\n".join(profile)),
		("viec_dang_mo", "\n".join(opened) or "Không có việc nào đang mở."),
		("trang_thai", "\n".join(state_lines)),
		("hoi_thoai", "\n".join(history) or "(chưa có tin nào trước lượt này)"),
		("luot_dang_xu_ly", "\n".join(f"KH: {_clip(t)}" for t in current)),
	]
	return "\n".join(f"<{tag}>\n{body}\n</{tag}>" for tag, body in parts)


# --- Gọi AI và kiểm lại -----------------------------------------------------------------------------------------


def model() -> str:
	return str(frappe.conf.get(MODEL_CONF) or frappe.get_cached_doc("Omni Settings").get("analyze_model") or DEFAULT_MODEL)


def _squash(value: object) -> str:
	"""Dạng so "nguyên văn": gộp khoảng trắng, chữ thường, BỎ DẤU — khách gõ "khau trang", AI chép lại "khẩu trang" vẫn là
	cùng một đoạn (lượt ghi 25/11: tên hàng có dấu bị coi là không có trong lời khách và bị bỏ)."""
	import unicodedata

	text = unicodedata.normalize("NFD", " ".join(str(value or "").split()).casefold().replace("đ", "d"))
	return "".join(c for c in text if unicodedata.category(c) != "Mn")


def clean(raw: dict | None, said: str) -> dict:
	"""Bản đọc hiểu đã kiểm bằng code (thuần). `said` = mọi lời khách đã viết (lượt này + lịch sử): tên hàng, hãng, đơn vị,
	quy cách phải là nguyên văn một đoạn trong đó, không thì bỏ mặt hàng ấy. Mã lạ → KHONG_RO / CHUA_RO."""
	if not isinstance(raw, dict):
		return dict(UNKNOWN)
	source = _squash(said)
	out = {**UNKNOWN, **{k: v for k, v in raw.items() if v is not None}}
	if out.get("quan_he") not in RELATIONS:
		out["quan_he"] = "KHONG_RO"
	ideas = []
	for idea in out.get("cac_y") or []:
		if isinstance(idea, dict) and idea.get("y_dinh") in INTENTS:
			conf = idea.get("do_tin_cay")
			conf = float(conf) if isinstance(conf, int | float) and not isinstance(conf, bool) else 0.0
			ideas.append({**idea, "do_tin_cay": min(max(conf, 0.0), 1.0), "dich_vu": idea.get("dich_vu") if idea.get("dich_vu") in SERVICES else "KHONG_RO"})
	out["cac_y"] = ideas
	slots = out.get("slots") if isinstance(out.get("slots"), dict) else {}
	goods = []
	for g in slots.get("mat_hang") or []:
		name = " ".join(str((g or {}).get("ten") or "").split()) if isinstance(g, dict) else ""
		if name and _squash(name) in source:
			item = {"ten": name}
			qty = g.get("so_luong")
			if isinstance(qty, int | float) and not isinstance(qty, bool) and re.search(rf"(?<!\d){int(qty)}(?!\d)", re.sub(r"[.,](?=\d{{3}})", "", said)):
				item["so_luong"] = int(qty) if float(qty).is_integer() else qty
			for key in ("don_vi", "hang", "quy_cach", "so_luong_chu"):  # đều phải là nguyên văn một đoạn khách đã viết
				value = " ".join(str(g.get(key) or "").split())
				if value and _squash(value) in source:
					item[key] = value
			goods.append(item)
	gone = []
	for name in slots.get("mat_hang_bo") or []:
		name = " ".join(str(name or "").split()) if isinstance(name, str) else ""
		if name and _squash(name) in source:
			gone.append(name)
	slots = {**slots, "mat_hang": goods, "mat_hang_bo": gone}
	for key in ("thoi_gian_can_hang", "thoi_gian_demo"):
		value = slots.get(key)
		if isinstance(value, dict) and value.get("nguyen_van") and _squash(value["nguyen_van"]) not in source:
			slots.pop(key)
	out["slots"] = slots
	choice = out.get("lua_chon")
	out["lua_chon"] = choice if isinstance(choice, int) and not isinstance(choice, bool) and choice > 0 else 0
	for key in ("cau_hoi_kb", "chua_ro", "mau_thuan", "da_co_san"):
		out[key] = out.get(key) if isinstance(out.get(key), list) else []
	return out


def call(content: str, *, lead=None, identity=None, batch_id=None, synthetic: bool = False, model_name: str | None = None) -> dict:
	"""Một lượt gọi AI với đầu vào `content`; trả kết quả THÔ (dict) hoặc {} nếu lỗi / bị bỏ qua. Sai schema: gọi lại một lần."""
	prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
	kwargs = dict(lead=lead, identity=identity, batch_id=batch_id, prompt_version=f"{PROMPT_NAME}:{prompt.version}", max_tokens=MAX_TOKENS)
	if synthetic:
		kwargs["synthetic"] = True
	for _attempt in range(2):
		raw = ai_client.call_tool(AI_KIND, model_name or model(), render(prompt), [{"role": "user", "content": content}], TOOL, **kwargs)
		if isinstance(raw, dict) and raw.get("quan_he") in RELATIONS:
			return raw
	return {}


def read(ctx) -> dict:
	"""Bản đọc hiểu (đã kiểm) của lượt `ctx`. Không bao giờ ném lỗi: lỗi → `KHONG_RO`."""
	try:
		content = build_input(ctx)
		history, current = conversation(ctx.identity, ctx.batch_id)
		said = "\n".join([*current, ctx.text or "", *[h.split("] KH: ", 1)[1] for h in history if "] KH: " in h]])
		raw = call(content, lead=ctx.lead, identity=ctx.identity, batch_id=ctx.batch_id)
		if not raw:  # AI không trả kết quả (lỗi mạng, hết ngân sách, sai schema hai lần): khác với "AI đọc mà không rõ"
			return {**UNKNOWN, FAILED: True}
		return clean(raw, said)
	except Exception as e:
		frappe.logger("omni").info("omni script: bước hiểu lỗi (%s) batch=%s", type(e).__name__, getattr(ctx, "batch_id", "-"))
		return {**UNKNOWN, FAILED: True}


def primary(reading: dict) -> dict:
	"""Ý chính của bản đọc hiểu: ý có độ tin cậy cao nhất không phải HOI_THONG_TIN/NHAC_LAI nếu có, không thì ý đầu."""
	ideas = reading.get("cac_y") or []
	acts = [i for i in ideas if i.get("y_dinh") not in ("HOI_THONG_TIN", "NHAC_LAI")]
	pool = acts or ideas
	if not pool:
		return {"y_dinh": "CHUA_RO", "do_tin_cay": 0.0, "dich_vu": "KHONG_RO"}
	best = max(pool, key=lambda i: i.get("do_tin_cay") or 0)
	if ((reading.get("slots") or {}).get("mat_hang")) and best.get("y_dinh") != "BAOGIA":
		quote = next((i for i in pool if i.get("y_dinh") == "BAOGIA"), None)
		if quote and (best.get("do_tin_cay") or 0) - (quote.get("do_tin_cay") or 0) <= 0.1:
			return quote  # khách đã nêu mặt hàng: việc báo giá là ý cần làm trước
	return best


# --- Nối với bộ máy kịch bản ------------------------------------------------------------------------------------

SWITCH = "omni_script_understand"  # site_config: 1 = dùng bước hiểu này thay script_classify (mặc định tắt)
# ý định mới -> mã mà bộ định tuyến (`flows.menu.route`) nhận
INTENT_CODE = {"HOI_THONG_TIN": "KB_HOI", "CAN_NGUOI": "GAP_NHANVIEN"}
SERVICE_CODE = {"VATTU": "VATTU", "SUPPLYCORE": "PHANMEM", "SPD": "SPD"}
SWITCH_CONF = 0.8  # luật 2: ý khác từ 0,8 mới rời luồng đang dở


def enabled() -> bool:
	"""Bước hiểu mới đang bật (site_config `omni_script_understand`)."""
	return bool(cint(frappe.conf.get(SWITCH) or 0))


def quantity_text(good: dict) -> str:
	qty = good.get("so_luong")
	if qty is None:
		return " ".join(str(good.get("so_luong_chu") or "").split())  # "1 triệu cái": giữ nguyên lời khách, không tự đổi ra số
	return f"{qty} {good.get('don_vi') or ''}".strip()


def to_detail(reading: dict) -> dict:
	"""Bản đọc hiểu -> `detail` mà bộ máy kịch bản dùng (thuần): code, confidence, service, slots (items, quantity,
	need_by), relation (TRA_LOI | Y_KHAC | ""), choice, demo_mode, extra, và `social` / `nudge` cho xã giao / khách giục."""
	idea = primary(reading)
	intent = idea.get("y_dinh") or "CHUA_RO"
	relation = reading.get("quan_he") or "KHONG_RO"
	conf = float(idea.get("do_tin_cay") or 0)
	code = INTENT_CODE.get(intent, intent)
	slots_in = reading.get("slots") or {}
	goods = slots_in.get("mat_hang") or []
	slots = {}
	if goods:
		slots["items"] = ", ".join(" ".join(x for x in (g["ten"], g.get("quy_cach") or "", g.get("hang") or "") if x) for g in goods)
		amounts = [quantity_text(g) for g in goods]
		if all(amounts):
			slots["quantity"] = amounts[0] if len(goods) == 1 else "; ".join(f"{g['ten']}: {a}" for g, a in zip(goods, amounts))
	when = slots_in.get("thoi_gian_can_hang") or {}
	if when.get("nguyen_van"):
		slots["need_by"] = when["nguyen_van"]
	rel = ""
	if relation == "TRA_LOI":
		rel = "TRA_LOI"
	elif relation == "HOI_NGUOC" and intent == "NHAC_LAI":
		rel = "TRA_LOI"  # khách GIỤC ("sao k trả lời", "em vẫn k hiểu"): đường xử lý lời giục (xin lỗi, nói lại), không coi là câu hỏi mới
	elif relation == "HOI_NGUOC":
		rel, code, conf = "Y_KHAC", "KB_HOI", max(conf, SWITCH_CONF)  # luật 4: trả lời trước, hỏi tiếp sau
	elif relation == "Y_KHAC":
		rel = "Y_KHAC" if conf >= SWITCH_CONF else ""
	elif relation == "XA_GIAO":
		code = "CHUA_RO"
	extra = {}
	demo_when = slots_in.get("thoi_gian_demo") or {}
	if demo_when.get("nguyen_van"):
		extra["when"] = demo_when["nguyen_van"]
	if slots_in.get("so_nguoi"):
		extra["attendees"] = str(slots_in["so_nguoi"])
	return {
		"code": code,
		"confidence": conf,
		"service": SERVICE_CODE.get(idea.get("dich_vu") or "", ""),
		"slots": slots,
		"relation": rel,
		"choice": reading.get("lua_chon") or 0,
		"demo_mode": slots_in.get("hinh_thuc_demo") or "",
		"extra": extra,
		"social": relation == "XA_GIAO",
		"nudge": any(i.get("y_dinh") == "NHAC_LAI" for i in reading.get("cac_y") or []),
		"questions": [q for q in reading.get("cau_hoi_kb") or [] if isinstance(q, str) and q.strip()],
		"intents": [i.get("y_dinh") for i in reading.get("cac_y") or []],
		"unclear_action": any(c.get("anh_huong") == "DOI_HANH_DONG" for c in reading.get("chua_ro") or [] if isinstance(c, dict)),
		"substitute": slots_in.get("hang_thay_the") or "",
		"failed": bool(reading.get(FAILED)),
		"goods": goods,
		"removed": slots_in.get("mat_hang_bo") or [],
	}
