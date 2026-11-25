"""Bước 3 kịch bản: thu thông tin 3a (tài liệu) / 3b (báo giá), kiểm email/SĐT (3.6), giao việc cho nhân viên (3.5).

Mỗi lượt chỉ hỏi một câu. Trạng thái nằm trong `script_state` của Omni Identity để khách quay lại vẫn hỏi tiếp đúng chỗ:
`flow="collect"`, `field` (câu đang hỏi), `node` (ô vừa hỏi), `pending` (phần chuỗi action còn lại), `wrong` (số lần sai
định dạng), `branch` (3a/3b), `answers` (câu trả lời của nhánh đang thu), `files` (tệp khách gửi).

Bot KHÔNG nêu giá: hết câu hỏi thì tạo việc cho nhân viên (`tasks.create_task`) và gửi ô hẹn trong giờ / ngoài giờ.
Câu gửi khách đều lấy từ Omni Script Node; chuỗi trong tệp này chỉ là nội dung việc cho nhân viên.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.script import engine, router, send, state, store, timeutil, validate
from omni_crm.script.router import RouteContext

FLOW = "collect"
LEAD = "CRM Lead"
IDENTITY = "Omni Identity"
CONTACT_FIELDS = ("email", "phone")
BRANCHES = ("3a", "3b")
CONFIRM_NODES = {  # (trong giờ, ngoài giờ) theo mốc 16:30 (3.5)
	"3a": ("TL_XACNHAN_TRONGGIO", "TL_XACNHAN_NGOAIGIO"),
	"3b": ("BG_XACNHAN_TRONGGIO", "BG_XACNHAN_NGOAIGIO"),
}
CONFIRM_NO_EMAIL = "XACNHAN_KHONG_EMAIL"  # ô bổ sung (nodes_k2flow.json): khách không để lại email hợp lệ
NODE_PRIVACY = "BAOMAT"
NODE_BAD = {"email": "SAI_EMAIL", "phone": "SAI_SDT"}
NODE_NO_PHONE = "KHONG_SDT_CAMON"
NODE_NO_CONTACT = "KHONG_THONGTIN"
NODE_DISCOUNT = "BG_CHIETKHAU"
JOURNEY_AFTER = "Đang tư vấn"
TASK_KINDS = {"3a": "docs", "3b": "quote"}
MAX_FILES = 10
MAX_ANSWER = 1000
TASK_FLAG = "_task_made"
SKIP_COMMANDS = ("answer", "quote")  # không lặp khi suy chuỗi tiếp theo từ ô đích của nút trả lời
# Khách từ chối để lại thông tin (3.4): câu ngắn mở đầu bằng từ phủ định
DECLINE = re.compile(
	r"^\s*(dạ[\s,]+)?(không|khong|ko|hông|hong|k|thôi|thoi|bỏ qua|bo qua|để sau|de sau|no)(\s|[.,!]|$)", re.I
)
DECLINE_MAX_LEN = 60
EMAIL_LIKE = re.compile(r"@|[\w-]\.[a-z]{2,}\b", re.I)  # khách có vẻ đang gõ một địa chỉ email (kể cả gõ thiếu @)
# Khách đề nghị giảm giá / chiết khấu / giá dự thầu (3.3.1): luôn chuyển nhân viên
DISCOUNT = re.compile(
	r"giảm giá|giam gia|chiết khấu|chiet khau|giá dự thầu|gia du thau|bớt giá|discount", re.I
)
_NAME_SPLIT = re.compile(r"\s*[,;\n]\s*|\s+[-–]\s+")  # noqa: RUF001 (gạch ngang dài khách hay gõ)


FORCE_MARK = "!"
PREFILL_KEY = "_prefill"  # ctx.state: thông tin khách đã nêu trong tin (items, quantity, need_by)
PREFILL_FIELDS = ("items", "quantity", "need_by", "sc_sites", "spd_sites")
FILE_FIELD = "file"  # ô chờ khách gửi tệp danh mục
NODE_TYPE_ITEMS = "BAOGIA_VATTU"  # khách không có tệp: hỏi mặt hàng bằng chữ
EXTRACT_FROM = "items"  # câu trả lời mặt hàng hay kèm luôn số lượng / thời gian
FORCED_NODES = ("NL_DOIEMAIL", "NL_BG_DOIEMAIL")  # "Đổi email khác": luôn hỏi email mới dù Lead đã có
OWN_TASK_KINDS = ("docs", "quote", "resend_docs", "resend_quote")
LEAVE_CODES = (
	"GAP_NHANVIEN",
	"CAN_NGUOI",
	"HUY_DANGKY",
	"XOA_DULIEU",
	"PHAN_NAN",
)  # ý rõ ràng: rời câu đang hỏi
MIRROR_KEYS = (
	"items",
	"quantity",
	"need_by",
	"substitute",
	"sc_items",
	"sc_users",
	"sc_sites",
	"spd_sites",
	"spd_items",
	"spd_value",
	"adjust",
)


def reset_answers(identity: str, **values) -> None:
	"""Xoá câu trả lời của nhánh thu thông tin trước (kèm bản sao cấp trên) và ghi thêm `values`."""
	state.update_state(identity, **{**dict.fromkeys(MIRROR_KEYS), "answers": {}, **values})


# --- Tra cứu ---------------------------------------------------------------------------------------------------


def known(ctx: RouteContext, field: str, lead_only: bool = False) -> bool:
	"""Thông tin `field` đã có (trên Lead hoặc đã trả lời trong nhánh đang thu) nên không hỏi lại (3.1).

	`field` kết thúc bằng `!` (vd `email!`) = luôn hỏi. `lead_only`: không tính câu trả lời tạm của nhánh.
	"""
	field = (field or "").strip().lower()
	if field.endswith(FORCE_MARK):
		return False
	st = engine.script_state(ctx)
	answers = {} if lead_only else store.as_dict(st.get("answers"))
	if field in answers or f"{field}_raw" in answers or f"{field}_declined" in answers:
		return True
	lead = _lead(ctx.lead)
	if field == "email":
		return bool(lead.get("email"))
	if field == "phone":
		return bool(lead.get("mobile_no") or lead.get("phone"))
	if field == "name_org":
		return bool(st.get("name_org") or lead.get("organization"))
	return False


def _lead(lead: str | None) -> dict:
	if not lead:
		return {}
	fields = ["email", "mobile_no", "phone", "organization", "lead_name", "omni_data_consent_at"]
	return frappe.db.get_value(LEAD, lead, fields, as_dict=True) or {}


# --- Lệnh `collect` / `answer` (gọi từ engine) ------------------------------------------------------------------


def start(ctx: RouteContext, field: str, node: dict, rest: list) -> bool:
	"""Ô vừa gửi câu hỏi: ghi câu đang hỏi và phần chuỗi còn lại, dừng lượt chờ khách trả lời."""
	state.update_state(
		ctx.identity,
		flow=FLOW,
		field=(field or "").strip().lower().rstrip(FORCE_MARK),
		force=1 if forced(field, node) else None,
		node=node.get("code"),
		pending=engine.join_actions(rest),
		wrong=0,
	)
	return True


def forced(field: str, node: dict | None) -> bool:
	"""Câu hỏi phải hỏi dù thông tin đã có: `collect:email!` hoặc ô "Đổi email khác"."""
	return (field or "").strip().endswith(FORCE_MARK) or (node or {}).get("code") in FORCED_NODES


def after_skip(ctx: RouteContext, field: str, node: dict, rest: list) -> bool | None:
	"""Câu hỏi được bỏ qua vì đã có thông tin: để chuỗi chạy tiếp, hoặc suy bước kế / kết thúc nếu chuỗi hết."""
	if rest:
		return None
	_continue(ctx, node, [])
	return True


ACK_KEY = "_ack"  # ctx.state: [(field, value)] khách vừa trả lời trong lượt này, chờ xác nhận lại ở tin kế tiếp
ACK_NODE = "XACNHAN_THONGTIN"
ACK_LABELS = {
	"items": "mặt hàng",
	"quantity": "số lượng",
	"need_by": "thời gian cần hàng",
	"substitute": "hãng thay thế",
	"sc_items": "số mặt hàng đang quản lý",
	"sc_users": "số người dùng",
	"sc_sites": "số cơ sở/kho",
	"spd_sites": "số cơ sở y tế",
	"spd_items": "số mặt hàng",
	"tai_lieu": "tài liệu cần nhận",
	"name_org": "tên và đơn vị",
	"adjust": "nội dung cần điều chỉnh",
}  # trường không có ở đây (vd spd_value: khoảng giá trị tiền) thì không xác nhận lại
ACK_MAX_VALUE = 80
ACK_UNSAFE = re.compile(r"https?://|www\.|[<>{}\[\]|\\`$#@]", re.I)
ACK_MONEY = re.compile(r"\d[\d.,]*\s*(đ|₫|vnd|vnđ|đồng|dong|nghìn|ngàn|k|triệu|trieu|tr|tỷ|tỉ|usd|\$)(?![a-zà-ỹ])", re.I)


# đuôi hỏi lại / lời gọi ở cuối câu trả lời: không thuộc thông tin cần xác nhận
ACK_TAIL = re.compile(
	r"(?:\s+(?:có\s+)?(?:được|kịp|ok|oke)\s*(?:không|ko|k|hông|hok|chưa)?)?(?:\s+(?:em|ạ|nhé|nha|nhá|bạn|shop|ơi|với))*\s*\??$",
	re.I,
)
NOTE_KEY = "_note"  # ctx.state: mã ô ghép thêm sau câu xác nhận của lượt
DELIVERY_FIELD = "need_by"
NODE_DELIVERY_NOTE = "BG_GIAOHANG_XACNHAN"
# khách vừa nêu thời gian cần hàng vừa hỏi Miyano có giao kịp không ("thứ 3 tuần sau được k em")
DELIVERY_ASK = re.compile(r"(?:được|kịp|ok|oke|nổi)\s*(?:không|ko|k|hông|hok|chưa)(?![\wÀ-ỹ])|\?")
ACK_QUESTION = re.compile(r"\?|khi nào|bao giờ|bao lâu|thế nào|như nào|ra sao|thì sao|được không|có thể|hay không|mấy ngày")


def ack_value(value: str | None) -> str:
	"""Câu trả lời của khách đủ an toàn để nhắc lại trong tin bot (thuần): ngắn, một dòng, không link, không ký tự
	lạ, không số tiền; không đạt thì trả chuỗi rỗng (bot không nhắc lại phần đó)."""
	text = " ".join(str(value or "").split()).strip(" .;,")
	text = ACK_TAIL.sub("", text).strip(" .;,")  # "thứ 3 tuần sau được k em" -> "thứ 3 tuần sau"
	if not text or len(text) > ACK_MAX_VALUE or ACK_UNSAFE.search(text) or ACK_MONEY.search(text):
		return ""
	if ACK_QUESTION.search(text.lower()):  # khách hỏi ngược lại ("giao sớm nhất khi nào"): không phải thông tin để xác nhận
		return ""
	try:
		from omni_crm.bot import guards

		if guards.guard_injection(text)[0]:  # câu giống lệnh chèn ("bỏ qua hướng dẫn…"): không nhắc lại
			return ""
	except Exception:
		return ""
	return text


def ack_text(pairs: list) -> str:
	"""Phần `{thong_tin}` của câu xác nhận: "mặt hàng bơm kim tiêm; số lượng 30 cái" (thuần); không có gì thì rỗng."""
	seen: dict = {}
	for field, value in pairs or []:
		label, safe = ACK_LABELS.get(field), ack_value(value)
		if label and safe:
			seen[field] = f"{_(label)} {safe}"
	return "; ".join(seen.values())


def take_ack(ctx: RouteContext, values: dict) -> str:
	"""Câu xác nhận thông tin khách vừa trả lời trong lượt (lấy từ ô `XACNHAN_THONGTIN`) rồi xoá hàng chờ; không có thì rỗng."""
	pairs = ctx.state.pop(ACK_KEY, None)
	summary = ack_text(pairs)
	parts = []
	if summary:
		rendered = store.render(
			store.get_node(ACK_NODE), {**values, "identity": ctx.identity, "thong_tin": summary}
		)
		parts.append(rendered[0] if rendered else "")
	note = ctx.state.pop(NOTE_KEY, None)
	if note:  # khách hỏi "giao kịp không": bot không tự cam kết, báo nhân viên sẽ xác nhận
		rendered = store.render(store.get_node(note), {**values, "identity": ctx.identity})
		parts.append(rendered[0] if rendered else "")
	return " ".join(p for p in parts if p)


def apply_prefill(ctx: RouteContext) -> None:
	"""Ghi thông tin khách đã nêu trong tin (`ctx.state[PREFILL_KEY]`) thành câu trả lời của nhánh vừa mở."""
	for field, value in (ctx.state.get(PREFILL_KEY) or {}).items():
		if field in PREFILL_FIELDS and str(value or "").strip():
			save_answer(ctx, field, str(value), typed=True)


def _more_from_answer(ctx: RouteContext, text: str) -> dict:
	"""Khách trả lời câu hỏi mặt hàng kèm luôn số lượng / thời gian ("bơm 5ml 10 hộp"): lấy phần đã nêu. Lỗi → {}."""
	try:
		from omni_crm.script import classify

		got = ctx.state.get("_classify_detail") or {}  # lượt này đã hỏi AI (menu._side_talk): không hỏi lần hai
		if got.get("batch_id") != ctx.batch_id:
			got = classify.classify_detail(
				text, ctx.state, lead=ctx.lead, identity=ctx.identity, batch_id=ctx.batch_id
			)
		slots = got.get("slots") or {}
		return {f: v for f, v in slots.items() if f in PREFILL_FIELDS and f != EXTRACT_FROM}
	except Exception:
		return {}


_FILLERS = frozenset(
	"anh chi em toi minh ben can muon lay mua dat khoang tam la cho them nhe nha a da vang thi cai".split()
)


# --- Luật 1 và 3 (yêu cầu 20/11): ô thông tin chỉ nhận GIÁ TRỊ đã trích và đúng kiểu, không ghi nguyên văn câu nói ---
# Câu nói / câu hỏi của khách chứ không phải một giá trị: "Anh vừa nói bên trên thây", "Em vẫn k hiểu", "sao không trả lời"
TALK = re.compile(
	r"(?:^|\s)(?:anh|chị|em|tôi|mình|tui|a|e|c)\s+(?:vừa|đã|vẫn|nói|hỏi|bảo|không|k|ko|chưa|đang|thấy|muốn hỏi|muốn biết)(?![\wÀ-ỹ])"
	r"|(?:không|k|ko|chưa|chẳng)\s+hiểu|bên trên|ở trên|như trên|lúc nãy|sao\s+(?:không|k|ko|chưa)"
	r"|khi nào|bao giờ|bao lâu|thế nào|như nào|\?",
	re.I,
)
# mốc thời gian: ngày/tháng, "3 tuần", "thứ 5"… Một con số trơ trọi ("300 đôi") KHÔNG phải mốc thời gian.
TIME_VALUE = re.compile(
	r"\d{1,2}\s*[/-]\s*\d{1,2}|\d+\s*(?:ngày|tuần|tháng|năm|giờ|h\b)|(?:ngày|thứ|tháng|tuần|quý|mùng|mồng)\s*\d+"
	r"|tuần|tháng|ngày|thứ|mai|nay|gấp|quý|cuối|đầu|sớm|gói thầu|càng",
	re.I,
)
NUMBER_WORD = re.compile(r"(?<![\wÀ-ỹ])(?:một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|chục|trăm|nghìn|ngàn|vài|dăm)(?![\wÀ-ỹ])", re.I)
SKIP_VALUE = re.compile(r"chưa (?:biết|rõ|chắc|có|tính)|không (?:rõ|biết|nhớ)|tính sau|để sau|tuỳ|tùy", re.I)
LOW_ANSWER_CONF = 0.7  # luật 3: AI coi là câu trả lời nhưng chưa chắc thì không ghi
MAX_NOT_RECORDED = 2  # hỏi lại tối đa 2 lần, lần thứ ba mời gặp nhân viên
NODE_NOT_RECORDED = "CHUA_GHI_DUOC"
NODE_HAVE = "DANG_GHI_NHAN"  # "Miyano đang ghi nhận: …" (đọc lại điều đã thu)
FREE_FIELDS = ("name_org",)  # tên – đơn vị: chữ tự do, không kiểm kiểu


SEE_FILE = re.compile(r"(?:theo|như|trong|xem)\s+(?:file|tệp|danh mục|bảng|ảnh)", re.I)
_QTY_FILLERS = frozenset(
	"anh chi em toi minh ben can lay mua dat khoang tam chung la cho them nhe nha a da vang thi cai va voi moi loai thoi".split()
)


def _only_quantities(text: str) -> bool:
	"""Tin chỉ gồm số lượng, đơn vị và từ nối (không kèm nội dung khác) — thuần."""
	rest = QUANTITY.sub(" ", text)
	try:
		from omni_crm.script import classify

		words = classify.fold(rest).split()
	except Exception:
		return False
	left = [w for w in words if w not in _QTY_FILLERS and not w.isdigit() and not NUMBER_WORD.search(w)]
	return len(left) <= 2


def not_recorded(ctx: RouteContext) -> bool:
	"""Như `_not_recorded` nhưng tự đọc trạng thái đang hỏi (dùng từ `menu._side_talk`). False nếu không có câu đang hỏi."""
	st = engine.script_state(ctx)
	node = store.get_node(st.get("node")) or {}
	if (st.get("flow") or "") != FLOW or not st.get("field") or not node:
		return False
	return _not_recorded(ctx, st, node)


def slot_value(ctx: RouteContext, field: str, text: str) -> str | None:
	"""Giá trị hợp lệ để ghi vào ô `field` từ tin khách gõ, hoặc None nếu tin không phải một giá trị của ô đó.

	Ưu tiên giá trị AI đã trích (nguyên văn một đoạn của tin). Không có thì kiểm kiểu: mặt hàng không được là một câu
	nói / câu hỏi; số lượng phải có con số; thời gian phải có mốc thời gian. AI coi là câu trả lời mà độ chắc chắn
	dưới 0,7 thì chỉ nhận khi kiểu rõ ràng (có số, có mốc thời gian)."""
	detail = ctx.state.get("_classify_detail") or {}
	detail = detail if detail.get("batch_id") == ctx.batch_id else {}
	extracted = str((detail.get("slots") or {}).get(field) or "").strip()
	if extracted:
		return extracted
	if field in FREE_FIELDS:
		return text
	if len(text.split()) <= 5 and (DECLINE.search(text) or SKIP_VALUE.search(text)):
		return text  # khách chủ động bỏ qua câu này ("bỏ qua", "chưa biết", "để sau"): ghi đúng như vậy cho nhân viên
	if "intents" in detail and not detail.get("failed") and detail.get("relation") != "TRA_LOI":
		return None  # bước hiểu mới đã đọc lượt này và KHÔNG coi là câu trả lời cho câu đang hỏi
	# AI KHÔNG DÙNG ĐƯỢC (lỗi mạng / hết ngân sách): không coi là "không phải câu trả lời", kiểm kiểu như đường cũ
	# để bot vẫn ghi được câu trả lời của khách
	talk = bool(TALK.search(text))
	unsure = detail.get("relation") == "TRA_LOI" and float(detail.get("confidence") or 0) < LOW_ANSWER_CONF
	if field in ("items", "quantity") and SEE_FILE.search(text) and not talk:
		return text  # "theo file", "như danh mục đã gửi": khách chỉ tới tệp đã gửi
	if field == "quantity":
		found = [m.group(0).strip() for m in QUANTITY.finditer(text)]
		numeric = any(ch.isdigit() for ch in text) or bool(NUMBER_WORD.search(text))
		if numeric and not talk and _only_quantities(text):
			return text  # cả tin chỉ nói về số lượng ("200 hộp và 5000 cái", "khoảng vài chục hộp")
		if found:
			return ", ".join(found)  # câu dài có nêu số lượng: chỉ lấy phần số lượng
		return text if numeric and not talk else None
	if field == DELIVERY_FIELD:
		return text if TIME_VALUE.search(text) and not TALK.search(ACK_TAIL.sub("", text)) else None
	if talk or unsure:
		return None
	return text


def _not_recorded(ctx: RouteContext, st: dict, node: dict) -> bool:
	"""Tin khách không phải giá trị của ô đang hỏi: KHÔNG ghi, hỏi lại đúng câu đó (tối đa 2 lần), lần thứ ba mời
	gặp nhân viên. Luôn có một tin gửi khách."""
	from omni_crm.script.flows import menu

	tries = cint(st.get("not_recorded")) + 1
	if tries <= MAX_NOT_RECORDED:
		lead_in = store.render(store.get_node(NODE_NOT_RECORDED), {**engine.variables(ctx), "identity": ctx.identity})
		prefix = lead_in[0] if lead_in else ""
		have = ack_text(list(store.as_dict(st.get("answers")).items()))
		if prefix and have:  # đọc lại điều đã ghi được để khách thấy bot vẫn giữ thông tin
			noted = store.render(store.get_node(NODE_HAVE), {**engine.variables(ctx), "identity": ctx.identity, "thong_tin": have})
			prefix = f"{prefix} {noted[0]}" if noted else prefix
		sent = send.send_node(ctx.identity, node.get("code") or "", engine.variables(ctx), prefix=prefix)
		if sent:
			router._mark(ctx.batch_id)
			_restore_flow(ctx, st, wrong=cint(st.get("wrong")))
			state.update_state(ctx.identity, not_recorded=tries)
			return True
	_clear_flow(ctx)
	state.update_state(ctx.identity, not_recorded=None)
	menu.auto_handoff(ctx, "REASON_MISUNDERSTOOD")
	return True


QUANTITY = re.compile(
	r"\d[\d.,]*\s*(?:hộp|cái|chiếc|thùng|bộ|gói|lọ|chai|túi|cuộn|đôi|kg|lít|tấm|vỉ|ống|bịch|cây|tuýp|kiện|can|bao)(?![\wÀ-ỹ])",
	re.I,
)
CORRECTION = re.compile(
	r"(?:đổi|sửa|thay)\s+(?P<old>.+?)\s+(?:thành|sang|bằng)\s+(?P<new>.+?)(?:\s+(?:nhé|nha|nhá|ạ|em|giúp anh|giúp chị|giúp mình))*\s*[.!]?$",
	re.I,
)


def _corrected_items(ctx: RouteContext, st: dict, node: dict, text: str) -> bool:
	"""Khách sửa mặt hàng đã nêu ("à anh nhầm, đổi găng tay thành khẩu trang nhé") trong lúc đang được hỏi câu khác:
	sửa câu trả lời mặt hàng rồi hỏi lại câu đang dở, KHÔNG ghi câu sửa làm câu trả lời của câu đang hỏi."""
	m = CORRECTION.search(text)
	items = str(store.as_dict(st.get("answers")).get(EXTRACT_FROM) or "")
	if not m or not items:
		return False
	old, new = m["old"].strip(), m["new"].strip()
	if not new or len(new) > MAX_ANSWER:
		return False
	at = items.lower().find(old.lower())
	fixed = items[:at] + new + items[at + len(old) :] if at >= 0 else new
	save_answer(ctx, EXTRACT_FROM, fixed, typed=True)
	engine.run_node(ctx, node.get("code") or "")
	_restore_flow(ctx, st, wrong=cint(st.get("wrong")))
	return True


def _only_extras(text: str, extras: dict) -> bool:
	"""Câu trả lời cho ô "mặt hàng" chỉ gồm số lượng / thời gian ("anh cần 100 hộp"), không nêu mặt hàng nào — thuần."""
	rest = text.lower()
	for value in extras.values():
		rest = rest.replace(str(value).lower(), " ")
	try:
		from omni_crm.script import classify

		words = classify.fold(rest).split()
	except Exception:
		return False
	return not [w for w in words if w not in _FILLERS]


def save_answer(ctx: RouteContext, field: str, value: str, typed: bool = False) -> None:
	"""Lưu câu trả lời (nút bấm hoặc khách gõ) vào `script_state.answers`; khách đã trả lời nên thôi chờ câu đó."""
	field = field.strip().lower()
	st = engine.script_state(ctx)
	answers = store.as_dict(st.get("answers"))
	answers[field] = (value or "").strip()[:MAX_ANSWER]
	skip_ack = (typed and field == "tai_lieu") or bool(ctx.attachments)  # tên tài liệu tự gõ / tệp: không nhắc lại
	if typed and len(answers[field]) <= DECLINE_MAX_LEN and DECLINE.search(answers[field]):
		skip_ack = True  # "bỏ qua", "không": không phải thông tin để xác nhận
	if not skip_ack:
		ctx.state.setdefault(ACK_KEY, []).append((field, answers[field]))
	if typed and field == DELIVERY_FIELD and DELIVERY_ASK.search(answers[field].lower()):
		ctx.state[NOTE_KEY] = NODE_DELIVERY_NOTE
	values: dict = {"answers": answers}
	if field in MIRROR_KEYS:  # flows/quote.py (K7) đọc câu trả lời báo giá ở khóa cấp trên của script_state
		values[field] = answers[field]
	if field == "tai_lieu":
		values["tai_lieu"] = answers[field]
		values["tai_lieu_typed"] = 1 if typed else None  # khách tự gõ: không chèn nguyên văn vào tin bot
	if field == "name_org":
		values.update(_name_org(ctx, answers[field]))
	if st.get("flow") == FLOW and st.get("field") == field:
		values.update(flow=None, field=None, node=None, pending=None, wrong=None, force=None, not_recorded=None)
	state.update_state(ctx.identity, **values)


def _name_org(ctx: RouteContext, raw: str) -> dict:
	"""Tách "tên, đơn vị" khi khách viết có dấu ngăn; không tách được thì giữ nguyên văn cho nhân viên."""
	values: dict = {"name_org": raw}
	parts = [p.strip() for p in _NAME_SPLIT.split(raw, maxsplit=1) if p.strip()]
	if len(parts) == 2 and 0 < len(parts[0].split()) <= 4 and not any(ch.isdigit() for ch in parts[0]):
		values["ten"] = parts[0].split()[-1][:40]
		if ctx.lead and not frappe.db.get_value(LEAD, ctx.lead, "organization"):
			with flags.omni_bot():
				frappe.db.set_value(LEAD, ctx.lead, "organization", parts[1][:140])
	return values


def remember_files(ctx: RouteContext) -> list[dict]:
	"""Ghi tham chiếu tệp khách gửi trong lượt vào `script_state.files` (tệp đã nằm trên dòng thời gian của Lead)."""
	files = [f for f in engine.script_state(ctx).get("files") or [] if isinstance(f, dict)]
	seen = {f.get("url") for f in files}
	for att in ctx.attachments or []:
		url = str((att or {}).get("url") or "") if isinstance(att, dict) else ""
		if not url.lower().startswith(("http://", "https://")) or url in seen:
			continue
		seen.add(url)
		files.append({"name": str(att.get("name") or "")[:140], "url": url[:500]})
	files = files[-MAX_FILES:]
	if files:
		state.update_state(ctx.identity, files=files)
	return files


# --- Khách gõ khi đang được hỏi ---------------------------------------------------------------------------------


def on_text(ctx: RouteContext) -> bool:
	"""Bộ xử lý tin gõ khi `flow == "collect"`: tách thông tin của câu đang hỏi; False để phân loại bình thường."""
	st = engine.script_state(ctx)
	field = (st.get("field") or "").lower()
	text = (ctx.text or "").strip()
	if not field or (not text and not ctx.attachments):
		return False
	node = store.get_node(st.get("node")) or {}
	declining = field in CONTACT_FIELDS and len(text) <= DECLINE_MAX_LEN and bool(DECLINE.search(text))
	if text and not declining and _leave_for_intent(ctx, text):
		return False
	before = _bot_mark(ctx)
	if field in CONTACT_FIELDS:
		if not _contact(ctx, st, field, node, text):
			return False
	else:
		if st.get("branch") == "3b" and DISCOUNT.search(text):
			_clear_flow(ctx)
			engine.run_button(ctx, NODE_DISCOUNT)
			return True
		if field == FILE_FIELD and not ctx.attachments:  # chưa có tệp nào: không báo "đã nhận danh mục"
			_clear_flow(ctx)
			said = (ctx.state.get("_classify_detail") or {}).get("slots") or {}
			if said:  # khách gõ luôn mặt hàng / số lượng: mang theo, không hỏi lại (luật 2)
				ctx.state[PREFILL_KEY] = {k: v for k, v in said.items() if k in PREFILL_FIELDS}
			return bool(engine.run_button(ctx, NODE_TYPE_ITEMS))
		value = text or _("(khách gửi tệp)")
		if ctx.attachments:
			remember_files(ctx)
		if st.get("branch") == "3b" and text and field != EXTRACT_FROM and _corrected_items(ctx, st, node, text):
			return True
		extras = _more_from_answer(ctx, text) if field == EXTRACT_FROM and st.get("branch") == "3b" and text else {}
		if not extras and field == EXTRACT_FROM and st.get("branch") == "3b" and text:
			m = QUANTITY.search(text)
			extras = {"quantity": m.group(0).strip()} if m else {}
			if not _only_extras(text, extras):
				extras = {}
		if extras and _only_extras(text, extras) and not ctx.attachments and not st.get("wrong"):
			# khách mới nêu số lượng / thời gian ("anh cần 100 hộp"): ghi vào đúng ô rồi hỏi lại mặt hàng một lần
			for extra, extra_value in extras.items():
				save_answer(ctx, extra, extra_value, typed=True)
			engine.run_node(ctx, node.get("code") or "")
			_restore_flow(ctx, st, wrong=1)
			return True
		if field == DELIVERY_FIELD and text and ((ctx.state.get("_classify_detail") or {}).get("questions") or DELIVERY_ASK.search(text.lower())):
			ctx.state[NOTE_KEY] = NODE_DELIVERY_NOTE
		if text and not ctx.attachments:
			value = slot_value(ctx, field, text)
			if value is None:  # không phải giá trị của ô này (câu nói, câu hỏi, AI chưa chắc): KHÔNG ghi, hỏi lại
				return _not_recorded(ctx, st, node)
		save_answer(ctx, field, value, typed=True)
		for extra, extra_value in extras.items():
			save_answer(ctx, extra, extra_value, typed=True)
		_advance(ctx, st, node)
	state.reset_reask(ctx.identity)
	if _bot_mark(ctx) == before and not engine.script_state(ctx).get("flow"):
		engine.fallback(ctx)  # không bao giờ im lặng
	return True


def _leave_for_intent(ctx: RouteContext, text: str) -> bool:
	"""Khách đang được hỏi mà nói rõ ý khác (gặp người, phàn nàn, dừng nhắn…): rời câu hỏi, định tuyến ý đó."""
	try:
		from omni_crm.script import classify

		hit = classify.classify_by_rules(text)
	except Exception:
		return False
	if not hit or hit[0] not in LEAVE_CODES:
		return False
	from omni_crm.script.flows import menu

	_clear_flow(ctx)
	ctx.state[menu.CLASSIFIED_KEY] = (hit[0], float(hit[1] or 1.0))
	return True


def _contact(ctx: RouteContext, st: dict, field: str, node: dict, text: str) -> bool:
	email, phone = validate.find_email(text), validate.find_phone(text)
	if email or phone:
		_save_contact(ctx, email, phone, force=bool(st.get("force")))
	if {"email": email, "phone": phone}[field]:
		_advance(ctx, st, node)
		return True
	if len(text) <= DECLINE_MAX_LEN and DECLINE.search(text):
		_declined(ctx, st, field, node)
		return True
	if _is_other_request(ctx):
		return False  # khách hỏi việc khác: để bộ phân loại xử lý, câu đang hỏi vẫn giữ để hỏi tiếp sau
	if cint(st.get("wrong")) < 1:  # 3.4: hỏi lại nhẹ nhàng đúng 1 lần
		state.update_state(ctx.identity, wrong=1)
		attempt = bool(EMAIL_LIKE.search(text)) if field == "email" else any(ch.isdigit() for ch in text)
		if not (attempt and engine.run_node(ctx, NODE_BAD[field])):  # không phải gõ sai: hỏi lại chính câu đó
			engine.run_node(ctx, node.get("code") or "")
		_restore_flow(ctx, st, wrong=1)
		return True
	_set_answer(ctx, f"{field}_raw", text)  # sai lần 2: ghi nguyên văn để nhân viên kiểm tra
	_advance(ctx, st, node)
	return True


def _is_other_request(ctx: RouteContext) -> bool:
	from omni_crm.script.flows import menu

	code, _conf = menu.classify_text(ctx)
	return code not in (menu.UNCLEAR, "KHONG_LIENQUAN")


def _restore_flow(ctx: RouteContext, st: dict, wrong: int) -> None:
	"""Giữ nguyên câu đang hỏi sau khi gửi ô báo sai (ô đó không được đổi `script_state` thu thông tin)."""
	state.update_state(
		ctx.identity,
		flow=FLOW,
		field=st.get("field"),
		node=st.get("node"),
		pending=st.get("pending") or None,
		wrong=wrong,
	)


def _set_answer(ctx: RouteContext, key: str, value) -> None:
	answers = store.as_dict(engine.script_state(ctx).get("answers"))
	answers[key] = value[:MAX_ANSWER] if isinstance(value, str) else value
	state.update_state(ctx.identity, answers=answers)


def _save_contact(ctx: RouteContext, email: str | None, phone: str | None, force: bool = False) -> None:
	"""Ghi email/SĐT hợp lệ vào Lead (không ghi đè, trừ "Đổi email khác"); lần đầu thì gửi câu chính sách bảo mật."""
	if ctx.lead and force and email:
		_replace_email(ctx, email)
	if ctx.lead:
		with flags.omni_bot():
			lead_bridge.fill_blank_contact(ctx.lead, phone, email)
	for key, value in (("email", email), ("phone", phone)):
		if value:
			_set_answer(ctx, key, value)
	frappe.logger("omni").info(
		"omni script: khách để lại liên hệ identity=%s email=%s sdt=%s",
		ctx.identity,
		validate.mask_email(email) if email else "-",
		validate.mask_phone(phone) if phone else "-",
	)
	note_consent(ctx)


def note_consent(ctx: RouteContext) -> bool:
	"""Lần đầu khách để lại email/SĐT: gửi câu chính sách bảo mật (3.4), action `consent` ghi `omni_data_consent_at`."""
	if not ctx.lead or _lead(ctx.lead).get("omni_data_consent_at"):
		return False
	return engine.run_node(ctx, NODE_PRIVACY)


def _replace_email(ctx: RouteContext, email: str) -> None:
	"""Khách đổi email: ghi email mới (đã kiểm định dạng) lên Lead, giữ email cũ trong câu trả lời cho nhân viên."""
	old = frappe.db.get_value(LEAD, ctx.lead, "email") or ""
	if old.strip().lower() == email.strip().lower():
		return
	row = frappe.db.get_value(LEAD, ctx.lead, ["mobile_no", "phone"], as_dict=True) or {}
	_phone_norm, email_norm = lead_bridge.normalized_fields(row.get("mobile_no"), row.get("phone"), email)
	with flags.omni_bot():
		frappe.db.set_value(LEAD, ctx.lead, {"email": email, "omni_email_norm": email_norm})
	if old:
		_set_answer(ctx, "email_old", old)


def _declined(ctx: RouteContext, st: dict, field: str, node: dict) -> None:
	"""Bảng 3.4: không ép. Thiếu SĐT thì cảm ơn và đi tiếp; không cho cả email lẫn SĐT thì kết thúc nhẹ nhàng."""
	_set_answer(ctx, f"{field}_declined", 1)
	if field == "phone":
		answers = store.as_dict(engine.script_state(ctx).get("answers"))
		lead = _lead(ctx.lead)
		if lead.get("email") or answers.get("email_raw"):
			engine.run_node(ctx, NODE_NO_PHONE)
		elif not (lead.get("mobile_no") or lead.get("phone")):
			_clear_flow(ctx)
			engine.run_node(ctx, NODE_NO_CONTACT)
			ctx.state[TASK_FLAG] = False
			_close_without_confirm(ctx)
			return
	_advance(ctx, st, node)


def _clear_flow(ctx: RouteContext) -> None:
	state.update_state(ctx.identity, flow=None, field=None, node=None, pending=None, wrong=None, force=None)


def _advance(ctx: RouteContext, st: dict, node: dict) -> None:
	"""Đã có câu trả lời: thôi chờ, chạy phần chuỗi còn lại của ô vừa hỏi (hoặc bước kế / kết thúc nhánh)."""
	_clear_flow(ctx)
	_continue(ctx, node, engine.split_actions(st.get("pending")))


def _continue(ctx: RouteContext, node: dict, rest: list) -> None:
	chain = rest or _chain_from_buttons(node)
	if chain:
		engine.run_actions(ctx, chain, node)
	else:
		finish(ctx)


def _chain_from_buttons(node: dict) -> list:
	"""Ô hỏi có nút trả lời mà khách lại gõ: bước kế là phần `then:`… của ô đích nút đầu tiên."""
	for b in node.get("buttons") or []:
		target = store.get_node(b.get("target"))
		if target:
			return [c for c in engine.split_actions(target["action"]) if c[0] not in SKIP_COMMANDS]
	return []


def _bot_mark(ctx: RouteContext):
	return frappe.db.get_value(IDENTITY, ctx.identity, "script_last_bot_at")


# --- Kết thúc nhánh: hẹn với khách + giao việc -------------------------------------------------------------------


def finish(ctx: RouteContext) -> bool:
	"""Hết câu hỏi của nhánh 3a/3b: gửi ô hẹn (trong giờ / ngoài giờ), tạo việc cho nhân viên, ghi hành trình."""
	branch = engine.script_state(ctx).get("branch")
	if branch not in BRANCHES:
		return False
	ctx.state[TASK_FLAG] = False
	if branch == "3b" and _read_back_quote(ctx):
		return True  # luật 7: gửi yêu cầu báo giá cho nhân viên là việc khó hoàn tác, đọc lại cho khách xác nhận trước
	if branch == "3b" and _quote_flow(ctx):
		return True
	late = timeutil.cutoff_1630(now_datetime())
	if not engine.run_node(ctx, CONFIRM_NODES[branch][1 if late else 0]):
		engine.run_node(ctx, CONFIRM_NO_EMAIL)
	_close_without_confirm(ctx)
	return True


FLOW_READBACK = "bg_readback"
NODE_QUOTE_READBACK = "BG_DOCLAI"
NODE_QUOTE_EDIT = "BG_SUA_GI"
READBACK_FIELDS = ("items", "quantity", "need_by", "substitute", "sc_items", "sc_users", "sc_sites", "spd_sites", "spd_items", "spd_value")


def _readback_on() -> bool:
	return bool(cint(frappe.conf.get("omni_script_readback") or 0))


def quote_summary(answers: dict) -> str:
	"""Tóm tắt mọi thông tin báo giá đã thu để đọc lại cho khách (thuần; qua cùng bộ lọc với câu xác nhận từng bước)."""
	return ack_text([(f, answers.get(f)) for f in READBACK_FIELDS if answers.get(f)])


def _read_back_quote(ctx: RouteContext) -> bool:
	"""Đọc lại toàn bộ yêu cầu báo giá và chờ khách xác nhận; CHƯA tạo việc cho nhân viên. True nếu đã gửi."""
	st = engine.script_state(ctx)
	if not _readback_on() or st.get("quote_confirmed"):
		return False
	summary = quote_summary(store.as_dict(st.get("answers")))
	if not summary:
		return False
	ctx.state.pop(ACK_KEY, None)  # tin đọc lại đã gồm mọi thứ: không ghép thêm câu xác nhận từng bước
	if not engine.run_node(ctx, NODE_QUOTE_READBACK, {"thong_tin": summary}):
		return False
	state.update_state(ctx.identity, flow=FLOW_READBACK)
	return True


def _quote_confirmed(ctx: RouteContext, arg: str = "", node: dict | None = None) -> bool:
	"""Khách xác nhận tin đọc lại (nút "Đúng rồi" hoặc gõ): gửi yêu cầu cho nhân viên như cũ."""
	state.update_state(ctx.identity, flow=None, quote_confirmed=1)
	done = finish(ctx)
	state.update_state(ctx.identity, quote_confirmed=None)
	return done


_YES_QUOTE = re.compile(r"^(?:dạ[\s,]*)?(?:đúng|chuẩn|chính xác|ok|oke|okay|được|đồng ý|xác nhận|vâng|ừ|uh)(?![\wÀ-ỹ])", re.I)


def on_readback_text(ctx: RouteContext) -> bool:
	"""Khách GÕ ở bước đọc lại báo giá: "đúng rồi" → gửi; nêu mục cần sửa (bước hiểu trích được mặt hàng / số lượng /
	thời gian) → sửa đúng mục đó rồi đọc lại. Không đọc được → hỏi sửa mục nào."""
	text = (ctx.text or "").strip()
	if not text:
		return False
	if _YES_QUOTE.match(text) and len(text.split()) <= 5:
		_quote_confirmed(ctx)
		return True
	from omni_crm.script.flows import menu

	code, _conf = menu.classify_text(ctx)
	slots = (ctx.state.get("_classify_detail") or {}).get("slots") or {}
	changed = False
	for field, value in slots.items():
		if field in PREFILL_FIELDS and str(value or "").strip():
			save_answer(ctx, field, str(value), typed=True)
			changed = True
	if changed:
		ctx.state.pop(ACK_KEY, None)
		state.update_state(ctx.identity, flow=None)
		return _read_back_quote(ctx)
	if code not in (menu.UNCLEAR, "BAOGIA", "MENU_VATTU", "SUA_THONG_TIN"):
		state.update_state(ctx.identity, flow=None)
		return False  # khách nói sang việc khác: để bộ định tuyến xử lý
	sent = engine.run_node(ctx, NODE_QUOTE_EDIT)
	state.update_state(ctx.identity, flow=FLOW_READBACK)
	return bool(sent)


def _quote_edit(ctx: RouteContext, arg: str = "", node: dict | None = None) -> None:
	state.update_state(ctx.identity, flow=FLOW_READBACK)


def _quote_flow(ctx: RouteContext) -> bool:
	"""Nhánh 3b: giao cho `flows.quote` (K7) báo giá hoặc hẹn + tạo việc; False nếu phần đó chưa có (tự tạo việc)."""
	try:
		from omni_crm.script.flows import quote
	except ImportError:
		return False
	service = engine.current_service(ctx)
	if not quote.show_or_fallback(ctx, service):
		quote.fallback_to_staff(ctx, service)  # tự chống trùng trong cùng lượt
	ctx.state[TASK_FLAG] = True
	reset_answers(ctx.identity, branch=None, last_branch="3b", files=None)
	return True


def _close_without_confirm(ctx: RouteContext) -> None:
	"""Bảo đảm việc cho nhân viên đã được tạo dù ô hẹn không gửi được (ô tắt, thiếu email…)."""
	if ctx.state.get(TASK_FLAG):
		return
	if engine.script_state(ctx).get("branch") not in BRANCHES:
		return
	task_action(ctx, "", {})
	state.set_journey(ctx.lead, JOURNEY_AFTER)


def task_action(ctx: RouteContext, arg: str, node: dict) -> None:
	"""Lệnh `task:<loại>`: `docs`/`quote` (hoặc để trống = theo nhánh) do K4 tạo kèm mọi câu trả lời."""
	st = engine.script_state(ctx)
	branch = st.get("branch")
	kind = (arg or "").strip().lower() or TASK_KINDS.get(branch, "")
	if kind not in OWN_TASK_KINDS:
		other = engine._actions.get(
			"task"
		)  # loại khác (purchasing, urgent…) thuộc phần việc khác nếu đã đăng ký
		if other is not None:
			other(ctx, arg, node)
			return
	service = engine.current_service(ctx) or "KHAC"
	name = _create_task(
		ctx.lead,
		service,
		_task_title(kind, ctx),
		build_description(ctx, kind),
		timeutil.task_due(now_datetime()),
	)
	ctx.state[TASK_FLAG] = True
	if kind in TASK_KINDS.values():  # nhánh đã xong: dọn câu trả lời, giữ `last_branch` cho tin nhắc sau này
		reset_answers(ctx.identity, branch=None, last_branch=branch, files=None, last_task=name)


def _create_task(lead: str | None, service: str, title: str, description: str, due) -> str | None:
	if not lead:
		return None
	try:
		from omni_crm.script import tasks
	except ImportError:
		frappe.logger("omni").info("omni script: chưa có tasks.create_task, bỏ qua tạo việc lead=%s", lead)
		return None
	return tasks.create_task(lead, service, title, description, due=due, priority="Medium")


def _task_title(kind: str, ctx: RouteContext) -> str:
	titles = {
		"docs": _("Gửi tài liệu cho khách (bot đã hẹn)"),
		"quote": _("Báo giá cho khách (bot đã hẹn, bot chưa nêu giá)"),
		"resend_docs": _("Gửi lại tài liệu cho khách"),
		"resend_quote": _("Gửi lại báo giá cho khách"),
		"purchasing": _("Kiểm tra nguồn hàng và bổ sung báo giá"),
		"urgent": _("Xác nhận khả năng giao hàng gấp"),
	}
	title = titles.get(kind) or _("Việc từ bot kịch bản: {0}").format(kind or "-")
	svc = engine.current_service(
		ctx
	)  # mỗi dịch vụ một việc riêng (người nhận theo dịch vụ), không gộp vào việc của dịch vụ khác
	return f"{title} - {engine.service_label(svc)}" if svc else title


ANSWER_LABELS = {
	"tai_lieu": "Tài liệu cần",
	"items": "Mặt hàng cần báo giá",
	"quantity": "Số lượng",
	"need_by": "Thời gian cần hàng",
	"substitute": "Chấp nhận hãng thay thế",
	"file": "Danh mục khách gửi",
	"sc_items": "Số mặt hàng đang quản lý",
	"sc_users": "Số người dùng phần mềm",
	"sc_sites": "Số cơ sở y tế / kho",
	"spd_sites": "Số cơ sở y tế",
	"spd_items": "Số mặt hàng trong danh mục",
	"spd_value": "Giá trị vật tư mỗi tháng",
	"adjust": "Khách muốn điều chỉnh",
	"order_info": "Số đơn / nội dung cần hỗ trợ",
	"email_old": "Email cũ (khách vừa đổi)",
	"email_raw": "Email khách gõ (sai định dạng, cần kiểm tra)",
	"phone_raw": "SĐT khách gõ (sai định dạng, cần kiểm tra)",
	"email_declined": "Khách không để lại email",
	"phone_declined": "Khách không để lại SĐT",
}
HIDDEN_ANSWERS = ("email", "phone", "name_org")  # đã có dòng riêng trong nội dung việc


def build_description(ctx: RouteContext, kind: str) -> str:
	"""Nội dung việc cho nhân viên (3.5): khách là ai, cần gì, mọi câu trả lời, email, SĐT, tệp, hạn xử lý."""
	st = engine.script_state(ctx)
	answers = store.as_dict(st.get("answers"))
	lead = _lead(ctx.lead)
	need = {"docs": _("Gửi tài liệu qua email"), "quote": _("Báo giá (bot chưa nêu giá nào)")}.get(kind, kind)
	lines = [
		_("Khách: {0}").format(
			st.get("name_org") or lead.get("organization") or lead.get("lead_name") or "-"
		),
		_("Dịch vụ: {0}").format(engine.service_label(engine.current_service(ctx)) or "-"),
		_("Cần làm: {0}").format(need),
	]
	for key, value in answers.items():
		if key in HIDDEN_ANSWERS:
			continue
		label = _(ANSWER_LABELS.get(key, key))
		lines.append(label if key.endswith("_declined") else f"{label}: {value}")
	lines.append(_("Email: {0}").format(lead.get("email") or "-"))
	phone = validate.phone_local(lead.get("mobile_no") or lead.get("phone") or "")
	lines.append(_("SĐT: {0}").format(phone or "-"))
	for f in st.get("files") or []:
		lines.append(_("Tệp khách gửi: {0} {1}").format(f.get("name") or "", f.get("url") or "").strip())
	due = timeutil.task_due(now_datetime())
	lines.append(_("Hạn xử lý: {0}").format(due.strftime("%H:%M %d/%m/%Y")))
	return "\n".join(lines)


# --- Lệnh phụ ---------------------------------------------------------------------------------------------------


def _consent(ctx: RouteContext, arg: str, node: dict) -> None:
	"""Lệnh `consent`: đã gửi câu chính sách bảo mật -> ghi thời điểm đồng ý xử lý dữ liệu (3.7), chỉ ghi lần đầu."""
	if ctx.lead and not frappe.db.get_value(LEAD, ctx.lead, "omni_data_consent_at"):
		frappe.db.set_value(LEAD, ctx.lead, "omni_data_consent_at", now_datetime(), update_modified=False)


def _attach_file(ctx: RouteContext, arg: str, node: dict) -> None:
	"""Lệnh `attach_file`: giữ tham chiếu tệp khách gửi để đưa vào việc của nhân viên."""
	if remember_files(ctx):
		_set_answer(ctx, "file", _("(khách gửi tệp)"))


engine.register_action("consent", _consent)
engine.register_action("attach_file", _attach_file)
engine.register_state(FLOW, on_text)
engine.register_state(FLOW_READBACK, on_readback_text)
engine.register_action("quote_confirmed", _quote_confirmed)
engine.register_action("quote_edit", _quote_edit)
