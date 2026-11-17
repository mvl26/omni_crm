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
PROMPT_VERSION = "v1"  # ghim trong code: câu lệnh này không có trường ghim trong Omni Settings
MAX_TEXT_CHARS = 1000
MAX_TOKENS = 200
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
STOP_EXACT = frozenset({"khong can", "stop", "unsubscribe", "huy dang ky"})
STOP_PATTERNS = (
	r"\b(?:dung|ngung|thoi) (?:co )?(?:nhan|gui|lien he|lien lac|lam phien|spam)\b(?: \w+){0,4}? nua\b",
	r"\b(?:dung|ngung|thoi|khong) (?:nhan|gui) (?:tin nhan|tin)\b(?: \w+){0,3}? nua\b",
	r"\bdung lam phien\b",
	r"\bkhong (?:muon|can) nhan (?:tin nhan|tin|thong tin|quang cao)\b",
	r"\bhuy (?:dang ky|theo doi|nhan tin)\b",
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


def _rule_for_clause(clause: str) -> str | None:
	"""Mã của vế câu đầu tiên có nội dung (ý đầu tiên thắng), None nếu không luật nào trúng."""
	core = _core(clause)
	if core in MENU_EXACT:
		return "MENU"
	if _BOT_RE.search(clause):
		return "BOT_HOI"
	topics = guards.sensitive_topics(_NOT_MEDICAL.sub(" ", clause))
	if _HUMAN_RE.search(clause) or "gap_nguoi" in topics:
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


def classify_by_rules(text: str | None) -> tuple[str, float] | None:
	"""Lớp 1: (mã, confidence) theo luật cố định, hoặc None khi phải hỏi AI. Hàm thuần, không đọc/ghi DB."""
	if not isinstance(text, str) or not _WORD.search(text):
		return UNKNOWN, 0.0  # rỗng, sticker, ghi âm, chỉ có biểu tượng: không có gì để phân loại
	if guards.guard_injection(text)[0]:
		return INJECTION_CODE, RULE_CONFIDENCE  # nội dung khách là dữ liệu: không bao giờ tới AI như một lệnh
	clauses = _clauses(text)
	whole = _core(" ".join(clauses))
	# Yêu cầu dừng nhắn / xoá dữ liệu được nhận ở bất kỳ vế nào của tin (tuân thủ C.4), trước mọi ý khác.
	if any(_ERASE_RE.search(c) for c in clauses):
		return "XOA_DULIEU", RULE_CONFIDENCE
	if whole in STOP_EXACT or any(_STOP_RE.search(c) for c in clauses):
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
	return f"{context_line(state)}\n{DATA_OPEN}\n{clean}\n{DATA_CLOSE}"


def _classify_ai(
	text: str, state: dict | None, lead: str | None, identity: str | None, batch_id: str | None
) -> tuple[str, float]:
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
		return UNKNOWN, 0.0
	confidence = raw.get("confidence")
	if isinstance(confidence, bool) or not isinstance(confidence, int | float):
		return UNKNOWN, 0.0
	return raw["code"], min(max(float(confidence), 0.0), 1.0)


def classify(
	text: str,
	state: dict | None = None,
	*,
	lead: str | None = None,
	identity: str | None = None,
	batch_id: str | None = None,
) -> tuple[str, float]:
	"""Phân loại tin khách gõ thành (mã trong `CODES`, confidence 0..1); không bao giờ ném lỗi, lỗi → (UNKNOWN, 0.0)."""
	try:
		hit = classify_by_rules(text)
		if hit is not None:
			return hit
		return _classify_ai(text, state, lead, identity, batch_id)
	except Exception as e:
		try:  # chỉ ghi loại lỗi: thông điệp lỗi có thể chứa lời khách
			frappe.log_error(
				title="Omni: phân loại tin kịch bản lỗi",
				message=f"batch_id={batch_id or '-'}; loai={type(e).__name__}",
			)
		except Exception:
			pass
		return UNKNOWN, 0.0
