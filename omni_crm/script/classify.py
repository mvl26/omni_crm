"""Phân loại tin khách gõ tự do thành MỘT mã trong danh sách đóng (seed/CLASSIFY_CODES.md; kịch bản 1.2, 2.5, C.3, C.4).

Hai lớp:
1. Luật cố định (không gọi AI, so trên văn bản đã bỏ dấu, hiểu viết tắt phổ biến): "menu", lời chào, gặp nhân viên,
   dừng nhắn, xoá dữ liệu, "bạn là bot à", báo giá dạng câu ngắn, phàn nàn, y khoa, chiết khấu/công nợ/điều khoản,
   chèn lệnh. Trúng luật trả confidence 1.0.
2. AI (`ai_client.call_tool`): nội dung khách được che email/SĐT và thoát thẻ, đặt trong `<tin_khach>` như dữ liệu.

`classify` không bao giờ ném lỗi: AI lỗi, bị bỏ qua (công tắc, hạn mức), trả mã ngoài danh sách → (UNKNOWN, 0.0).
Hàm trả nguyên confidence của AI; bên gọi (router) so với `LOW_CONFIDENCE` để coi là chưa rõ.
"""

from __future__ import annotations

import re

import frappe

from omni_crm.bot import ai_client, guards
from omni_crm.bot.context import escape_tags, mask_pii, normalize_text
from omni_crm.bot.prompts.loader import load_prompt, render
from omni_crm.identity.normalize import strip_accents

# Thứ tự và tên mã khớp bảng trong omni_crm/script/seed/CLASSIFY_CODES.md (test đối chiếu với tệp đó).
CODES: tuple[str, ...] = (
	"MENU_VATTU",
	"MENU_PHANMEM",
	"MENU_SPD",
	"MENU_KHAC",
	"BAOGIA",
	"TAILIEU",
	"DEMO",
	"GAP_NHANVIEN",
	"BOT_HOI",
	"KB_HOI",
	"CAN_NGUOI",
	"PHAN_NAN",
	"Y_KHOA",
	"KHONG_LIENQUAN",
	"SPAM",
	"HUY_DANGKY",
	"XOA_DULIEU",
	"MENU",
	"CHUA_RO",
)
UNKNOWN = "CHUA_RO"  # mã "không hiểu được ý" của danh sách đóng
INJECTION_CODE = "KHONG_LIENQUAN"  # CLASSIFY_CODES.md: "bỏ qua hướng dẫn", "đóng vai…" thuộc KHONG_LIENQUAN
LOW_CONFIDENCE = 0.6  # dưới ngưỡng này bên gọi coi như CHUA_RO (hỏi lại bằng nút)
RULE_CONFIDENCE = 1.0

AI_KIND = "Reply"  # Omni Bot Run.kind không có loại riêng cho phân loại; lượt này thay cho lượt Reply cũ
DEFAULT_MODEL = "claude-haiku-5-5"
PROMPT_NAME = "script_classify"
PROMPT_VERSION = "v3"  # ghim trong code: câu lệnh này không có trường ghim trong Omni Settings
MAX_TEXT_CHARS = 1000
MAX_TOKENS = 300
DATA_OPEN, DATA_CLOSE = "<tin_khach>", "</tin_khach>"
CONTEXT_OPEN, CONTEXT_CLOSE = "<ngu_canh>", "</ngu_canh>"
TRUNCATED = "…"

TOOL = {
	"name": "classify_message",
	"description": "Trả mã phân loại của tin khách và mức chắc chắn.",
	"input_schema": {
		"type": "object",
		"properties": {
			"code": {"type": "string", "enum": list(CODES)},
			"confidence": {"type": "number", "minimum": 0, "maximum": 1},
			"service": {"type": "string", "enum": ["VATTU", "PHANMEM", "SPD", "KHONG_RO"]},
			"mat_hang": {"type": "string"},
			"so_luong": {"type": "string"},
			"thoi_gian": {"type": "string"},
			"quan_he": {"type": "string", "enum": ["TRA_LOI", "Y_KHAC", "KHONG_HOI"]},
			"lua_chon": {"type": "integer", "minimum": 0, "maximum": 9},
			"dinh_chinh": {"type": "string"},
			"hinh_thuc_demo": {"type": "string", "enum": ["ONLINE", "TRUC_TIEP"]},
			"ngay_gio": {"type": "string"},
			"so_co_so": {"type": "string"},
			"so_nguoi": {"type": "string"},
		},
		"required": ["code", "confidence"],
		"additionalProperties": False,
	},
}

# --- Lớp 1: luật cố định (mọi mẫu viết trên văn bản đã bỏ dấu, chữ thường, đã mở viết tắt) ---------------------

ABBREVIATIONS = {
	"k": "khong",
	"ko": "khong",
	"kh": "khong",
	"khg": "khong",
	"hok": "khong",
	"bg": "bao gia",
	"sp": "san pham",
	"tv": "tu van",
	"nv": "nhan vien",
	"ntn": "nhu the nao",
	"ib": "nhan tin",
	"dc": "duoc",
	"dk": "dang ky",
	"mk": "minh",
	"mik": "minh",
	"sdt": "so dien thoai",
}
_CLAUSE_SPLIT = re.compile(r"[.?!;\n。？！]+")  # noqa: RUF001 (dấu câu full-width của tiếng Nhật)
_WORD = re.compile(r"\w+")
_PERSON = r"(?:toi|minh|em|anh|chi|tui|to|ben (?:toi|minh|em))"
# Từ đệm bỏ ở đầu / cuối câu trước khi so khớp nguyên câu ("dạ mình không cần ạ" -> "khong can").
_LEAD_FILLERS = frozenset(
	"da vang uh um alo hi hello chao xin ad admin shop oi em ban minh toi anh chi miyano thoi cho".split()
)
_TAIL_FILLERS = frozenset(
	"a ah ak nhe nha nhi nua dau oi ad shop em ban di voi giup ho thanks thank cam on miyano".split()
)
_GREET_WORDS = frozenset(
	"da alo hi hii hello helo hey chao xin ad admin shop oi em ban anh chi miyano a".split()
)
_GREET_CORE = frozenset("alo hi hii hello helo hey chao oi".split())
GREETINGS = frozenset(
	{
		"alo",
		"a lo",
		"alo alo",
		"hi",
		"hii",
		"hello",
		"helo",
		"hey",
		"chao",
		"xin chao",
		"chao ban",
		"chao em",
		"chao anh",
		"chao chi",
		"chao shop",
		"chao ad",
		"chao admin",
		"chao miyano",
		"xin chao miyano",
		"ad oi",
		"shop oi",
		"em oi",
		"ban oi",
		"miyano oi",
	}
)
MENU_EXACT = frozenset({"menu", "menu chinh", "ve menu", "quay lai menu", "quay ve menu", "xem menu"})
# "không cần" chỉ nhận khi là CẢ câu (C.4); "không cần gấp, gửi báo giá giúp" không phải yêu cầu dừng.
# 19/11 (người phụ trách quyết): "không cần" là lời từ chối lịch sự, KHÔNG phải yêu cầu dừng nhắn (xem `is_polite_decline`).
STOP_EXACT = frozenset({"stop", "unsubscribe", "huy dang ky"})
DECLINE_EXACT = frozenset(
	{"khong can", "khong can nua", "thoi khong can", "thoi khong can nua", "khong cam on", "thoi cam on", "khong can dau"}
)
# "không cần" trơ trọi khi bot đang đứng ở một ô (đang hỏi dở) có thể chỉ là trả lời câu hỏi: không coi là dừng.
STOP_NEEDS_IDLE = frozenset({"khong can"})
# "hủy đăng ký demo / lịch / đơn ..." là hủy một việc cụ thể, không phải dừng nhận tin.
_NOT_UNSUBSCRIBE = r"(?! (?:buoi |cuoc |cai )?(?:demo|lich|don|hen|cuoc hen|hoi thao|su kien|khoa|lop|goi|dung thu|dat hang|tai khoan)\b)"
STOP_PATTERNS = (
	r"\b(?:dung|ngung|thoi) (?:co )?(?:nhan|gui|lien he|lien lac|lam phien|spam)\b(?: \w+){0,4}? nua\b",
	r"\b(?:dung|ngung|thoi|khong) (?:nhan|gui) (?:tin nhan|tin)\b(?: \w+){0,3}? nua\b",
	r"\bdung lam phien\b",
	r"\bkhong (?:muon|can) nhan (?:tin nhan|tin|thong tin|quang cao)\b",
	r"\bhuy (?:dang ky|theo doi|nhan tin)\b" + _NOT_UNSUBSCRIBE,
	r"\bunsubscribe\b",
	r"\bstop (?:messaging|texting|sending|contacting)\b",
	r"\b(?:do not|dont|don t) (?:contact|message|text) me\b",
)
ERASE_PATTERNS = (
	r"\bxoa (?:\w+ ){0,3}?(?:du lieu|thong tin|so dien thoai|email|lich su(?: chat| tin nhan)?)"
	rf"(?: \w+){{0,2}}? (?:ca nhan|cua {_PERSON})\b",
	rf"\bxoa (?:so dien thoai|email|so) (?:cua )?{_PERSON}\b",
	r"\b(?:delete|erase|remove) (?:all )?(?:of )?my (?:personal )?(?:data|information|info|number|email)\b",
	r"\bquyen duoc quen\b",
)
_BOT = r"(?:chat ?bot|ro ?bot|bot|tro ly ao)"
BOT_PATTERNS = (
	rf"\b(?:la|phai) (?:con |mot )?{_BOT}\b",
	rf"\b{_BOT} (?:a|ah|ha|ho|u|khong|phai khong|dung khong|tra loi)\b",
	r"\b(?:nguoi that|nguoi) hay (?:la )?(?:bot|may|robot|chatbot)\b",
	r"\b(?:bot|may|robot|chatbot) hay (?:la )?nguoi\b",
	r"\b(?:may|tu dong) tra loi (?:a|ha|phai khong|dung khong)\b",
	r"\btra loi tu dong (?:a|ha|phai khong|dung khong)\b",
	r"\b(?:la|phai) nguoi that\b",
	r"\bare you (?:a |an )?(?:bot|robot|chatbot|human|real)\b",
	r"\bis this (?:a |an )?(?:bot|robot|chatbot)\b",
	r"\b(?:em|ban|shop|ad|admin) (?:ten (?:la )?gi|la ai)\b",  # "em tên gì", "bạn là ai": hỏi về chính bot
	r"\bai (?:dang )?tra loi\b",
)
HUMAN_PATTERNS = (
	r"\bgap (?:truc tiep )?(?:nhan vien|tu van vien|nguoi that|nguoi|sale|sales|chuyen vien|quan ly|sep|ai do)\b",
	r"\b(?:noi chuyen|trao doi|chat|lam viec|ket noi) (?:truc tiep )?voi "
	r"(?:nhan vien|tu van vien|nguoi that|nguoi|chuyen vien|sale)\b",
	rf"\bgoi (?:dien thoai |dien )?(?:lai )?cho {_PERSON}\b",
	r"\btu van vien\b",
	r"\b(?:can|muon|cho|xin) (?:\w+ ){0,2}?nhan vien (?:tu van|ho tro|goi|lien he)\b",
	r"\b(?:talk|speak|chat) (?:to|with) (?:a |an |your )?"
	r"(?:human|person|agent|staff|someone|representative|real person|sales)\b",
	r"\bcall me\b",
)
COMPLAINT_PATTERNS = (
	r"\b(?:that vong|buc minh|buc boi|qua te|te qua|lam an kieu gi|thai do (?:kem|te|gi))\b",
)
NEED_HUMAN_PATTERNS = (r"\b(?:chiet khau|cong no|hoa hong|tra cham)\b",)
QUOTE_EXACT = (
	rf"(?:xin |can |gui |cho (?:{_PERSON} )?(?:xin )?)?(?:{_PERSON} )?(?:bao gia|bang gia)"
	rf"(?: (?:giup|cho|ho))?(?: {_PERSON})?",
	r"gia (?:sao|bao nhieu|the nao|nhu the nao|nhu nao|ca the nao|ca sao)",
	r"bao nhieu tien",
	r"(?:price|pricing|quotation|quote)(?: please)?",
)
_NOT_MEDICAL = re.compile(r"\bchi dinh thau\b")  # thuật ngữ đấu thầu, không phải chỉ định y khoa


def _compile(patterns: tuple[str, ...]) -> re.Pattern:
	return re.compile("|".join(f"(?:{p})" for p in patterns))


_STOP_RE = _compile(STOP_PATTERNS)
_ERASE_RE = _compile(ERASE_PATTERNS)
_BOT_RE = _compile(BOT_PATTERNS)
_HUMAN_RE = _compile(HUMAN_PATTERNS)
# phủ định: "anh không muốn gặp nhân viên", "khỏi cần gọi" — KHÔNG phải yêu cầu gặp người
_NO_HUMAN_RE = re.compile(r"\b(?:khong|khoi|dung|thoi)\s+(?:can |muon |phai |can phai )?(?:gap|goi|noi chuyen|chuyen|ket noi)\b")
_COMPLAINT_RE = _compile(COMPLAINT_PATTERNS)
_NEED_HUMAN_RE = _compile(NEED_HUMAN_PATTERNS)
_QUOTE_RE = re.compile("^(?:" + "|".join(f"(?:{p})" for p in QUOTE_EXACT) + ")$")


def fold(text: str | None) -> str:
	"""Dạng so khớp của một câu: NFKC, bỏ dấu, chữ thường, bỏ ký hiệu, mở viết tắt phổ biến ("bg" -> "bao gia")."""
	words = _WORD.findall(strip_accents(normalize_text(text or "")).replace("_", " "))
	return " ".join(ABBREVIATIONS.get(w, w) for w in words)


def _clauses(text: str) -> list[str]:
	"""Các vế câu (đã gập) theo thứ tự khách viết; vế không có chữ/số bị bỏ."""
	return [c for c in (fold(part) for part in _CLAUSE_SPLIT.split(text)) if c]


def _core(clause: str) -> str:
	"""Bỏ từ đệm ở đầu và cuối vế câu để so khớp nguyên câu."""
	words = clause.split()
	while len(words) > 1 and words[0] in _LEAD_FILLERS:
		words.pop(0)
	while len(words) > 1 and words[-1] in _TAIL_FILLERS:
		words.pop()
	return " ".join(words)


def _is_greeting(clause: str) -> bool:
	return clause in GREETINGS or all(w in _GREET_WORDS for w in clause.split())


_ACK_CORE = frozenset("ok oki okie okay oke okela vang da u uh uhm uk thanks thank tks".split())
_ACK_WORDS = _ACK_CORE | frozenset(
	"um duoc dung roi cam on you nhe nha a ah em ban anh chi miyano nhieu tot hay lam shop ad".split()
)
ACK_MAX_WORDS = 6


def is_ack_only(text: str | None) -> bool:
	"""Tin chỉ là lời ừ / cảm ơn ("ok em", "vâng", "cảm ơn nhé"), không có ý nào khác — thuần."""
	words = fold(text).split() if isinstance(text, str) else []
	if not words or len(words) > ACK_MAX_WORDS or not all(w in _ACK_WORDS for w in words):
		return False
	joined = " ".join(words)
	return bool(_ACK_CORE.intersection(words)) or any(p in joined for p in ("cam on", "duoc roi", "dung roi"))


def is_polite_decline(text: str | None) -> bool:
	"""Cả tin chỉ là lời từ chối lịch sự ("không cần", "thôi không cần đâu em") — thuần."""
	if not isinstance(text, str):
		return False
	return _core(" ".join(_clauses(text))) in DECLINE_EXACT


def is_greeting_only(text: str | None) -> bool:
	"""Tin chỉ gồm lời chào / gọi ("Alo em ơi", "hi shop"), không có ý nào khác — thuần."""
	clauses = _clauses(text) if isinstance(text, str) else []
	if not clauses or not all(_is_greeting(c) for c in clauses):
		return False
	return any(w in _GREET_CORE for c in clauses for w in c.split())  # "a", "dạ" trơ trọi không phải lời chào


def _rule_for_clause(clause: str) -> str | None:
	"""Mã của vế câu đầu tiên có nội dung (ý đầu tiên thắng), None nếu không luật nào trúng."""
	core = _core(clause)
	if core in MENU_EXACT:
		return "MENU"
	if _BOT_RE.search(clause):
		return "BOT_HOI"
	topics = guards.sensitive_topics(_NOT_MEDICAL.sub(" ", clause))
	if (_HUMAN_RE.search(clause) or "gap_nguoi" in topics) and not _NO_HUMAN_RE.search(clause):
		return "GAP_NHANVIEN"
	if _COMPLAINT_RE.search(clause) or "khieu_nai" in topics:
		return "PHAN_NAN"
	if "y_khoa" in topics:
		return "Y_KHOA"
	if _NEED_HUMAN_RE.search(clause) or "dieu_khoan_hop_dong" in topics:
		return "CAN_NGUOI"
	if _QUOTE_RE.match(core):
		return "BAOGIA"
	return None


def classify_by_rules(text: str | None, state: dict | None = None) -> tuple[str, float] | None:
	"""Lớp 1: (mã, confidence) theo luật cố định, hoặc None khi phải hỏi AI. Hàm thuần, không đọc/ghi DB.

	`state` có `script_node` (bot đang đứng ở một ô) thì "không cần" trơ trọi không được coi là yêu cầu dừng nhắn.
	"""
	if not isinstance(text, str) or not _WORD.search(text):
		return UNKNOWN, 0.0  # rỗng, sticker, ghi âm, chỉ có biểu tượng: không có gì để phân loại
	if guards.guard_injection(text)[0]:
		return INJECTION_CODE, RULE_CONFIDENCE  # nội dung khách là dữ liệu: không bao giờ tới AI như một lệnh
	clauses = _clauses(text)
	whole = _core(" ".join(clauses))
	# Yêu cầu dừng nhắn / xoá dữ liệu được nhận ở bất kỳ vế nào của tin (tuân thủ C.4), trước mọi ý khác.
	if any(_ERASE_RE.search(c) for c in clauses):
		return "XOA_DULIEU", RULE_CONFIDENCE
	st = state if isinstance(state, dict) else {}
	pending = bool(st.get("script_node") or st.get("node"))
	if any(_STOP_RE.search(c) for c in clauses) or (
		whole in STOP_EXACT and not (pending and whole in STOP_NEEDS_IDLE)
	):
		return "HUY_DANGKY", RULE_CONFIDENCE
	content = [c for c in clauses if not _is_greeting(c)]  # lời chào đầu tin không phải một ý
	if not content:
		return UNKNOWN, RULE_CONFIDENCE  # chỉ chào ("Alo", "hi"): chưa rõ ý, hỏi lại bằng nút
	code = _rule_for_clause(content[0])
	return (code, RULE_CONFIDENCE) if code else None


# --- Lớp 2: AI -------------------------------------------------------------------------------------------------

_SERVICE_HINTS = (
	("SUPPLYCORE", "PHANMEM"),
	("PHANMEM", "PHANMEM"),
	("_SC_", "PHANMEM"),
	("SPD", "SPD"),
	("VATTU", "VATTU"),
	("_VT_", "VATTU"),
)


def _token(value: object) -> str:
	"""Giá trị ngữ cảnh chỉ gồm chữ, số, gạch dưới (không cho nội dung lạ lọt vào câu lệnh)."""
	return re.sub(r"[^A-Za-z0-9_]", "", str(value or ""))[:40]


def _service_of(state: dict) -> str:
	inner = state.get("script_state") if isinstance(state.get("script_state"), dict) else {}
	explicit = _token(state.get("service") or inner.get("service") or inner.get("dich_vu"))
	node = f"_{_token(state.get('script_node') or state.get('node')).upper()}_"
	for source in (f"_{explicit.upper()}_", node):
		for hint, service in _SERVICE_HINTS:
			if hint in source:
				return service
	return ""


def context_line(state: dict | None) -> str:
	"""Dòng `<ngu_canh>` cho AI: bước, ô kịch bản đang đứng, dịch vụ đang xem (lấy từ `state.get(identity)`)."""
	state = state if isinstance(state, dict) else {}
	step = _token(state.get("script_step") or state.get("step"))
	node = _token(state.get("script_node") or state.get("node"))
	parts = f"buoc={step or '?'}; o_dang_dung={node or '?'}; dich_vu={_service_of(state) or '?'}"
	return f"{CONTEXT_OPEN}{parts}{CONTEXT_CLOSE}"


def build_user_message(text: str, state: dict | None = None) -> str:
	"""Nội dung lượt user gửi AI: ngữ cảnh + tin khách đã che email/SĐT, thoát thẻ, cắt độ dài."""
	clean = escape_tags(mask_pii(text).strip())
	if len(clean) > MAX_TEXT_CHARS:
		clean = clean[: MAX_TEXT_CHARS - len(TRUNCATED)] + TRUNCATED
	return f"{context_line(state)}\n{conversation_block(state)}{DATA_OPEN}\n{clean}\n{DATA_CLOSE}"


HISTORY_KEY = "_history"  # state: các lượt gần nhất, mỗi phần tử (vai, nội dung) với vai "KH" | "BOT"
ASKED_KEY = "_asked"  # state: nguyên văn câu bot vừa hỏi và đang chờ khách trả lời (kèm danh sách lựa chọn)
MAX_HISTORY = 6
MAX_HISTORY_CHARS = 300
MAX_ASKED_CHARS = 900


def _short(value: object, limit: int) -> str:
	clean = escape_tags(mask_pii(" ".join(str(value or "").split())))
	return clean if len(clean) <= limit else clean[: limit - len(TRUNCATED)] + TRUNCATED


def conversation_block(state: dict | None) -> str:
	"""Khối `<lich_su>` và `<bot_vua_hoi>` cho AI (đã che email/SĐT, thoát thẻ, cắt độ dài); không có thì thẻ rỗng."""
	state = state if isinstance(state, dict) else {}
	turns = []
	for item in (state.get(HISTORY_KEY) or [])[-MAX_HISTORY:]:
		if isinstance(item, list | tuple) and len(item) == 2 and item[0] in ("KH", "BOT") and str(item[1]).strip():
			turns.append(f"{item[0]}: {_short(item[1], MAX_HISTORY_CHARS)}")
	asked = str(state.get(ASKED_KEY) or "").strip()
	asked = escape_tags(mask_pii(asked))[:MAX_ASKED_CHARS] if asked else ""
	history = "\n".join(turns)
	return f"<lich_su>\n{history}\n</lich_su>\n<bot_vua_hoi>\n{asked}\n</bot_vua_hoi>\n"


def _classify_ai(
	text: str, state: dict | None, lead: str | None, identity: str | None, batch_id: str | None
) -> dict:
	prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
	model = frappe.get_cached_doc("Omni Settings").get("analyze_model") or DEFAULT_MODEL
	raw = ai_client.call_tool(
		AI_KIND,
		model,
		render(prompt),
		[{"role": "user", "content": build_user_message(text, state)}],
		TOOL,
		lead=lead,
		identity=identity,
		batch_id=batch_id,
		prompt_version=f"{PROMPT_NAME}:{prompt.version}",
		max_tokens=MAX_TOKENS,
	)  # KHÔNG truyền synthetic: tin khách luôn qua cổng Q11
	if not isinstance(raw, dict) or raw.get("code") not in CODES:
		return _detail(UNKNOWN, 0.0)
	confidence = raw.get("confidence")
	if isinstance(confidence, bool) or not isinstance(confidence, int | float):
		return _detail(UNKNOWN, 0.0)
	service = raw.get("service") if raw.get("service") in SERVICES else ""
	out = _detail(raw["code"], min(max(float(confidence), 0.0), 1.0), service, slots_from(raw, text))
	out.update(context_fields(raw, text, bool(str((state or {}).get(ASKED_KEY) or "").strip())))
	return out


DETAIL_FLAG = "omni_script_classify_detail"
SERVICES = ("VATTU", "PHANMEM", "SPD")
SLOT_FIELDS = {"mat_hang": "items", "so_luong": "quantity", "thoi_gian": "need_by"}  # trường AI -> câu hỏi 3b
MAX_SLOT = 200


def _detail(code: str, confidence: float, service: str = "", slots: dict | None = None) -> dict:
	return {"code": code, "confidence": confidence, "service": service, "slots": slots or {}}


RELATIONS = ("TRA_LOI", "Y_KHAC")
EXTRA_FIELDS = {"ngay_gio": "when", "so_co_so": "sites", "so_nguoi": "attendees", "dinh_chinh": "correction"}
DEMO_MODES = ("ONLINE", "TRUC_TIEP")


def context_fields(raw: dict, text: str, asked: bool) -> dict:
	"""Phần "hiểu theo ngữ cảnh" của kết quả AI (thuần): `relation` (TRA_LOI | Y_KHAC | ""), `choice` (số thứ tự lựa
	chọn khách chọn, 0 = không rõ), `demo_mode` và `extra` (ngày giờ, số cơ sở, số người, đính chính — chỉ nhận
	giá trị là nguyên văn một đoạn của tin khách). Bot không hỏi gì thì không có `relation` / `choice`."""
	source = _squash(text)
	relation = raw.get("quan_he") if asked and raw.get("quan_he") in RELATIONS else ""
	choice = raw.get("lua_chon")
	choice = choice if relation == "TRA_LOI" and isinstance(choice, int) and not isinstance(choice, bool) else 0
	extra = {}
	for key, field in EXTRA_FIELDS.items():
		value = " ".join(str(raw.get(key) or "").split())
		if value and len(value) <= MAX_SLOT and _squash(value) in source:
			extra[field] = value
	mode = raw.get("hinh_thuc_demo") if raw.get("hinh_thuc_demo") in DEMO_MODES else ""
	return {"relation": relation, "choice": max(0, choice), "demo_mode": mode, "extra": extra}


def _squash(value: object) -> str:
	return " ".join(str(value or "").split()).casefold()


def slots_from(raw: dict, text: str) -> dict:
	"""Thông tin khách ĐÃ nêu trong tin (mặt hàng, số lượng, thời gian) — thuần. Chỉ nhận giá trị là nguyên văn
	một đoạn của tin khách: AI không được thêm, đổi hay suy ra con số."""
	source = _squash(text)
	out: dict = {}
	for key, field in SLOT_FIELDS.items():
		value = " ".join(str(raw.get(key) or "").split())
		if value and len(value) <= MAX_SLOT and _squash(value) in source:
			out[field] = value
	return out


RULE_NEEDS_DETAIL = ("BAOGIA", "MENU_VATTU")  # luật trúng nhưng tin dài: vẫn hỏi AI để lấy thông tin khách đã nêu
RULE_DETAIL_MIN_WORDS = 4


def _learned(text: str) -> str | None:
	try:
		from omni_crm.script import brain

		return brain.learned_code(text)
	except Exception:
		return None


def classify_detail(
	text: str,
	state: dict | None = None,
	*,
	lead: str | None = None,
	identity: str | None = None,
	batch_id: str | None = None,
) -> dict:
	"""Như `classify` nhưng trả dict: code, confidence, service (dịch vụ khách nhắc tới) và slots (thông tin khách
	đã nêu trong tin: items, quantity, need_by). Không bao giờ ném lỗi."""
	hit = None
	try:
		hit = classify_by_rules(text, state)
		wants_detail = (
			bool(hit)
			and hit[0] in RULE_NEEDS_DETAIL
			and len(text.split()) >= RULE_DETAIL_MIN_WORDS
			and any(ch.isdigit() for ch in text)  # có con số: nhiều khả năng khách đã nêu số lượng
		)
		if hit is not None and not wants_detail:
			return _detail(*hit)
		if hit is None:
			learned = _learned(text)  # cách nói đã được người phụ trách duyệt (ADR 0034): không cần hỏi AI
			if learned:
				return _detail(learned, RULE_CONFIDENCE)
		got = _classify_ai(text, state, lead, identity, batch_id)
		if hit is not None:  # luật đã quyết mã: AI chỉ bổ sung dịch vụ và thông tin đã nêu
			return _detail(hit[0], hit[1], got.get("service", ""), got.get("slots"))
		return got
	except Exception as e:
		try:  # chỉ ghi loại lỗi: thông điệp lỗi có thể chứa lời khách
			frappe.log_error(
				title="Omni: phân loại tin kịch bản lỗi",
				message=f"batch_id={batch_id or '-'}; loai={type(e).__name__}",
			)
		except Exception:
			pass
		return _detail(*hit) if hit else _detail(UNKNOWN, 0.0)


def classify(
	text: str,
	state: dict | None = None,
	*,
	lead: str | None = None,
	identity: str | None = None,
	batch_id: str | None = None,
) -> tuple[str, float]:
	"""Phân loại tin khách gõ thành (mã trong `CODES`, confidence 0..1); không bao giờ ném lỗi, lỗi → (UNKNOWN, 0.0)."""
	got = classify_detail(text, state, lead=lead, identity=identity, batch_id=batch_id)
	frappe.flags[DETAIL_FLAG] = {**got, "batch_id": batch_id}  # bên gọi (flows/menu) đọc dịch vụ và thông tin đã nêu
	return got["code"], got["confidence"]
