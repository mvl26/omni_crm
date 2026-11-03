"""Các chốt chặn sau sinh cho bot trả lời (docs/08 mục 5.4, mục 7), chạy bằng code, không dựa vào AI.

Mỗi chốt chặn là một hàm thuần trả `(bị_chặn, lý_do)`. `run_guards` gom lý do của mọi chốt chặn bị kích hoạt; có
bất kỳ lý do nào thì bản nháp phải đổi thành chuyển người. Danh sách từ khóa là hằng số ở đầu tệp để dễ chỉnh.
"""

from __future__ import annotations

import functools
import re
import unicodedata

from omni_crm.bot import intents
from omni_crm.bot.context import normalize_text
from omni_crm.bot.guard_text import compact_hit, guard_variants
from omni_crm.bot.guard_text import (
	normalize_for_guard as normalize_for_guard,  # tái xuất: API công khai của F7
)
from omni_crm.identity.normalize import strip_accents

GuardResult = tuple[bool, str]

CONFIDENCE_MIN = 0.5  # chốt chặn 6
DEFAULT_MAX_CHARS = 1200  # chốt chặn 5: giới hạn mặc định khi kênh không khai báo
MAX_PHRASE_SHOWN = 40  # cắt cụm từ ghi vào lý do

# --- Từ khóa (dễ sửa) -------------------------------------------------------------------------------------
# Số viết bằng chữ (một…mười, chục, trăm, rưỡi…) đứng trước đơn vị tiền/thời gian cũng bị coi là con số.
NUM_WORD = r"(?:một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười|mươi|chục|trăm|rưỡi|lăm|vài)"
NUM_WORDS = rf"\b{NUM_WORD}(?:\s+{NUM_WORD})*"
UNIT_MONEY = r"(?:đồng|vnđ|vnd|nghìn|ngàn|triệu|tỷ|tỉ|củ|k|đô|usd|phần trăm|tr|đ|%)"
UNIT_TIME = r"(?:ngày|tuần|tháng|giờ|tiếng|phút|năm|buổi|quý)"
# Tiền: số kèm đơn vị tiền ("1tr5", "2 củ", "20 phần trăm"), chữ số + đơn vị, giá theo ngữ cảnh ("giá 99").
MONEY_PATTERNS = (
	rf"(?<![\w.,])\d[\d.,]*\s*{UNIT_MONEY}(?!\w)",
	r"(?<![\w.,])\d+\s*(?:tr|triệu|k|củ|nghìn|ngàn)\s*\d+",
	r"[$]\s*\d[\d.,]*",
	r"(?<!\w)(?:usd|vnd|vnđ)\s*\d[\d.,]*",
	rf"{NUM_WORDS}\s+{UNIT_MONEY}(?!\w)",
	r"\b(?:trăm|nghìn|ngàn|triệu|tỷ)\s+(?:đồng|vnđ|vnd)(?!\w)",
)
# Số đứng ngay sau từ chỉ giá ("giá 99", "phí: 50"): số đó phải nằm cùng câu với từ giá trong kho tri thức.
PRICE_CONTEXT = r"\b(giá|đơn giá|mức giá|phí|cước|chi phí|chỉ còn|chỉ từ)\s*:?\s*(\d[\d.,]*)"
# Thời hạn có số: chữ số hoặc số bằng chữ + đơn vị thời gian, giờ giấc, ngày tháng.
DEADLINE_PATTERNS = (
	rf"(?<![\w.,/])\d+\s*{UNIT_TIME}(?!\w)",
	r"(?<![\w.,/])\d{1,2}\s*[:h]\s*\d{2}(?!\d)",
	r"(?<![\w.,/])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\w/])",
	r"\bngày\s+\d{1,2}\b",
	rf"{NUM_WORDS}\s+{UNIT_TIME}(?!\w)",
)
# Mốc thời gian tương đối: phải có nguyên văn trong kho tri thức (không kiểm danh từ đi kèm).
RELATIVE_TIME_PATTERNS = (
	r"\b(?:hôm nay|ngày mai|sáng mai|chiều mai|tối mai|ngày kia|ngày mốt|hôm sau|tuần sau|tuần tới|tháng sau"
	r"|tháng tới|trong ngày|trong tuần|trong tháng|cuối tuần|đầu tuần|ngay lập tức|ngay hôm nay|chủ nhật"
	r"|thứ (?:hai|ba|tư|năm|sáu|bảy))\b",
)
# Từ đệm bỏ qua khi tìm danh từ đi trước con số, và từ làm kết thúc cụm cam kết.
FILLERS = frozenset(
	"là chỉ khoảng tầm từ đến trong sau mất tới có cần được của và hoặc vào lúc dưới trên hơn gần đúng mỗi còn "
	"thêm ạ nhé dạ giờ ngày tuần tháng phút tiếng năm buổi quý vẫn sẽ đã đang rất cho với".split()
)
STOP_WORDS = r"(?:ạ|nhé|nha|nhá|thôi|luôn|và|hoặc|với|của|cho|nếu|khi|nên|là|vì|nhưng)"
FOLLOW = rf"(?:\s+(?!{STOP_WORDS}(?!\w))[^\s.,;:!?]+)"
# Từ cam kết: kiểm cụm "từ khóa + tối đa 2 từ theo sau" có nguyên văn trong kho tri thức không.
COMMITMENT_PATTERNS = (
	r"\b(?:cam kết|đảm bảo|chắc chắn|bảo đảm|hoàn lại tiền|hoàn tiền|miễn phí|giảm giá|chiết khấu|khuyến mãi"
	r"|ưu đãi|bảo hành|trọn đời|suốt đời|vĩnh viễn|đổi trả|bồi hoàn|đền bù|bồi thường)" + FOLLOW + "{0,2}",
	r"\bbảo hành(?:\s+[^\s.,;:!?]+){0,4}?\s+(?:năm|tháng|ngày)\b",
)
# Y khoa: không tư vấn chuyên môn ngoài kho tri thức (docs/08 mục 7).
MEDICAL_PATTERNS = (
	r"\b(?:liều dùng|liều lượng|chống chỉ định|chỉ định|chẩn đoán|điều trị|kê đơn|kê toa|đơn thuốc|phác đồ"
	r"|tác dụng phụ|uống thuốc|tiêm cho|chữa khỏi|chữa bệnh|chữa trị|chữa|khỏi bệnh|hết bệnh|dị ứng|kháng sinh"
	r"|trị dứt điểm|mỗi ngày|trước khi ăn|sau khi ăn|sau bữa ăn|an toàn cho (?:bà bầu|trẻ em|phụ nữ))"
	+ FOLLOW
	+ "{0,3}",
	r"\b(?:dùng|uống|tiêm|bôi|nhỏ|xông|liều)\s+\S+\s+(?:viên|ống|lọ|chai|ml|mg|liều|lần|giọt)\b",
)
URL_PATTERN = r"(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:vn|com|net|org|io|info|biz|me)\b(?:/[^\s<>\"']*)?"
EMAIL_PATTERN = r"[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[a-z]{2,}"
DIGIT_RUN_PATTERN = r"\+?\d(?:[\s.\-]?\d){7,}"
# Khách muốn chuyển người / chủ đề nhạy cảm (so khớp trên văn bản đã bỏ dấu, chữ thường). "giá" riêng có dấu.
SENSITIVE_CUSTOMER_PATTERNS = {
	"hoi_gia": (
		r"\bbao nhieu tien\b",
		r"\bbao gia\b",
		r"\bbang gia\b",
		r"\bgia (?:ca|bao nhieu|ban|nhap|si|le|the nao|sao)\b",
		r"\bchiet khau\b",
		r"\bgiam gia\b",
		r"\bthanh toan bao nhieu\b",
		r"\bck (?:bao nhieu|may|nhieu)\b",
		r"\bvat bao nhieu\b",
		r"\bship bao nhieu\b",
		r"\bphi (?:ship|van chuyen)\b",
		r"\bprice\b",
		# B2B (V2-06): phí dịch vụ của Miyano. Chỉ cụm cụ thể; "phí" trơn quá rộng.
		r"\bphi (?:dich vu|quan ly|van hanh|bao nhieu|the nao|ap dung)\b",
		r"\b(?:ty le|muc) phi\b",
		r"\bbao nhieu phan tram\b",
		r"\bphan tram\b",  # V2-08: "phần trăm hoa hồng/chiết khấu"
		r"\bhoa hong\b",
		# V21-07: tỷ lệ kèm % / phần trăm / phí, và tiếng Anh (khách lách bằng ngôn ngữ khác)
		r"\bty le\b.{0,20}(?:%|phan tram|phi|chiet khau|hoa hong|bao nhieu)",
		r"\b(?:discount\w*|commission|service fees?|fees?|pricing|quotation)\b",
	),
	# B2B (V2-06): điều khoản/đàm phán hợp đồng. KHÔNG dùng "hợp đồng" trơn vì dịch vụ chính là vận hành hợp đồng.
	"dieu_khoan_hop_dong": (
		r"\bdieu khoan\b",
		r"\bdam phan\b",
		r"\bthuong luong\b",
		r"\bthoa thuan\b",  # V2-08
		r"\bbao lanh\b",
		r"\bchinh sua (?:hop dong|dieu khoan)\b",
		# V21-07: sửa/đổi/đàm phán hợp đồng (KHÔNG chặn "hợp đồng" trơn)
		r"\b(?:sua|doi|thay doi|dieu chinh|bo sung|soan lai) (?:lai )?(?:hop dong|dieu khoan)\b",
		r"\bnegotiat\w*\b",
		r"\bcontract terms\b|\bterms and conditions\b",
	),
	"khieu_nai": (
		r"\bkhieu nai\b",
		r"\bphan nan\b",
		r"\bkhong hai long\b",
		r"\bboi thuong\b",
		r"\bhoan tien\b",
		r"\bto cao\b",
		r"\bhoan lai tien\b",
		r"\bdoi tra (?:hang|lai)\b",
		r"\btra lai hang\b",
	),
	"phap_ly": (
		r"\bkhoi kien\b",
		r"\bkien tung\b",
		r"\bdi kien\b",
		r"\bluat su\b",
		r"\bphap ly\b",
		r"\bvi pham\b",
		r"\bthanh tra\b",
		r"\bse kien\b",
	),
	"y_khoa": (
		r"\blieu dung\b",
		r"\blieu luong\b",
		r"\bchi dinh\b",
		r"\bchan doan\b",
		r"\btac dung phu\b",
		r"\bchong chi dinh\b",
		r"\bdi ung\b",
		r"\bchua (?:benh|tri)\b",
	),
	"gap_nguoi": (
		r"\bgap nhan vien\b",
		r"\bgap (?:quan ly|sep|nguoi that|truong phong|giam doc)\b",
		r"\bnguoi that\b",
		r"\bnoi chuyen voi (?:nguoi|nhan vien)\b",
		r"\bgoi lai cho toi\b",
	),
}
# V21-07: từ khóa so dạng viết liền / đảo ngược / trong một từ (guard_text.compact_hit), chuẩn hóa cùng cách văn bản.
COMPACT_KEYWORDS = {
	"hoi_gia": (
		"phi dich vu", "phi quan ly", "phi van hanh", "ty le phi", "muc phi", "phan tram", "hoa hong",
		"chiet khau", "giam gia", "bao gia", "bang gia", "discount", "commission",
	),
	"dieu_khoan_hop_dong": (
		"dieu khoan", "dam phan", "thuong luong", "thoa thuan", "bao lanh", "negotiat",
	),
}  # fmt: skip
PRICE_WORD_ACCENTED = r"(?<!\w)giá(?!\w)(?!\s+(?:treo|đỡ|trị|kệ|thể|như))"
# Ý định luôn chuyển người: quyết ở bot/intents.py (F6, V21-06), hiểu cả giá trị cũ của câu lệnh v1 (Hỏi giá, Đặt hàng).
# Chèn lệnh từ khách (so khớp trên văn bản đã bỏ dấu).
INJECTION_PATTERNS = (
	r"\bbo qua (?:moi |tat ca |cac |het )?(?:huong dan|chi dan|quy tac|lenh|cau lenh|chi thi)",
	r"\bquen (?:het|di) (?:huong dan|quy tac|moi thu)",
	r"\bignore (?:all |the |any )?(?:previous|above|prior|earlier)",
	r"\bdisregard\b.{0,30}\b(?:instruction|rule|prompt)",
	r"\bsystem prompt\b",
	r"\bcau lenh he thong\b",
	r"\bdong vai\b",
	r"\bjailbreak\b",
	r"\bbao gia 0\b",
	r"\bgia 0 dong\b",
	r"\b(?:bao )?gia [0o] (?:dong|d|vnd)\b",  # V21-07: sau chuẩn hóa 0 -> o
	r"\b(?:reveal|show|tiet lo)\b.{0,20}\b(?:prompt|huong dan|instruction)",
	r"\byou are now\b",
	r"\bforget (?:everything|all|your)\b",
	r"\bact as\b",
	r"\bpretend (?:to be|you)\b",
	r"\bdeveloper mode\b",
	r"\btu gio ban la\b",
	r"\bliet ke (?:toan bo|tat ca) (?:kho|du lieu|huong dan|noi dung)",
)
INJECTION_COMPACT = (
	"bo qua huong dan", "bo qua chi dan", "bo qua quy tac", "ignore previous", "ignore all previous",
	"ignore the above", "system prompt", "cau lenh he thong", "developer mode",
)  # fmt: skip
# Đầu ra bot không được chứa dấu hiệu rò cấu trúc nội bộ hay thẻ dữ liệu.
UNSAFE_OUTPUT_PATTERNS = (
	r"[<>]",
	r"```",
	r"kho_tri_thuc|hoi_thoai|nhom_cau_hoi|propose_reply",
	r"\bKB-\d{3,}\b",
	r"(?:system prompt|câu lệnh hệ thống|hướng dẫn hệ thống|kho tri thức)",
	r"\bbỏ qua (?:mọi |tất cả )?(?:hướng dẫn|quy tắc)",
)


# --- Chuẩn hóa để so "nguyên văn" ---------------------------------------------------------------------------


def norm(text: str | None) -> str:
	"""Chuẩn hóa để so khớp: NFKC, chữ thường, bỏ dấu phân cách hàng nghìn và dấu câu, gộp khoảng trắng.

	Giữ chữ, số, `%`, `$`; số dính liền đơn vị đứng sau ("100 k" -> "100k", "8 giờ" -> "8giờ") ở cả hai phía so sánh.
	"""
	t = unicodedata.normalize("NFKC", text or "").lower()
	t = re.sub(r"(?<=\d)[.,](?=\d{3}(?!\d))", "", t)
	t = re.sub(r"[^\w\s%$]", " ", t)
	t = re.sub(r"(?<=\d)\s+(?=[^\W\d_]|[%$])", "", t)
	return re.sub(r"\s+", " ", t).strip()


def _contains(phrase: str, corpus_norm: str) -> bool:
	p = norm(phrase)
	return bool(p) and f" {p} " in f" {corpus_norm} "


def _corpus(kb_texts: list[str]) -> str:
	return norm("\n".join(kb_texts))


def _short(text: str) -> str:
	text = re.sub(r"\s+", " ", text).strip()
	return text[:MAX_PHRASE_SHOWN]


def _sentences(kb_texts: list[str]) -> list[str]:
	"""Các câu của kho tri thức (đã chuẩn hóa); "danh từ + số + đơn vị" phải nằm trong cùng một câu."""
	parts = re.split(r"[.!?]+(?=\s|$)|[\n;]+", "\n".join(kb_texts))
	return [n for n in (norm(x) for x in parts) if n]


def _noun_before(text: str, start: int) -> str:
	"""Từ đầu tiên không phải từ đệm đứng trước vị trí `start` (tối đa 4 từ chữ cái); rỗng nếu không có."""
	words = re.findall(r"[^\W\d_]+", text[:start].lower())[-4:]
	for w in reversed(words):
		if w not in FILLERS and len(w) > 1:
			return w
	return ""


def _in_same_sentence(phrase: str, noun: str, sentences: list[str]) -> bool:
	p, n = norm(phrase), norm(noun)
	return any(f" {p} " in f" {sent} " and (not n or n in sent) for sent in sentences)


def _unsupported(
	text: str, patterns: tuple[str, ...], kb_texts: list[str], noun_check: bool = False
) -> list[str]:
	"""Các cụm khớp `patterns` trong `text` mà không có nguyên văn (sau chuẩn hóa) trong kho tri thức đã trích.

	`noun_check`: cụm "danh từ + số + đơn vị" phải khớp trong cùng MỘT câu của kho tri thức (số "8 giờ" của giờ làm
	việc không đỡ cho "giao trong 8 giờ").
	"""
	corpus = _corpus(kb_texts)
	sentences = _sentences(kb_texts) if noun_check else []
	found: list[str] = []
	lowered = normalize_text(text)
	for pattern in patterns:
		for m in re.finditer(pattern, lowered, flags=re.IGNORECASE):
			phrase = m.group(0)
			ok = _contains(phrase, corpus)
			if ok and noun_check:
				ok = _in_same_sentence(phrase, _noun_before(lowered, m.start()), sentences)
			if not ok and _short(phrase) not in found:
				found.append(_short(phrase))
	return found


# --- 6 chốt chặn của docs/08 mục 5.4 ------------------------------------------------------------------------


def _folded(text: str | None) -> str:
	"""Bỏ dấu, chữ thường; trước đó NFKC, bỏ ký tự độ rộng 0 và NBSP để không né được bằng ký tự ẩn."""
	return re.sub(r"\s+", " ", strip_accents(normalize_text(text or "")).lower())


_SEP = r"[-_.·•*~'\u2019`|/\\]+"
_INVISIBLE = re.compile(
	"[\u200b-\u200f\u2060\ufeff\u00ad]"
)  # ký tự không hiển thị chèn giữa chữ (V2-08 review)


def _folded_variants(text: str | None) -> str:
	"""Văn bản đã gập ở nhiều dạng nối bằng " | ": gốc, ký tự chèn giữa chữ thành khoảng trắng / bị xóa, chữ cách từng ký tự được nối lại.

	Để "đàm-phán", "p h í dịch vụ", "phí d.ịch vụ", "đàm" + ký tự rộng bằng không + "phán" không né được so khớp từ khóa (V2-08).
	"""
	raw = text or ""
	parts: list[str] = []
	# ký tự không hiển thị: bị xóa (chèn giữa chữ) hoặc thay bằng khoảng trắng (đứng ở chỗ dấu cách)
	for base in (_folded(_INVISIBLE.sub("", raw)), _folded(_INVISIBLE.sub(" ", raw))):
		spaced = re.sub(rf"(?<=[a-z])\s*{_SEP}\s*(?=[a-z])", " ", base)
		joined = re.sub(rf"(?<=[a-z])\s*{_SEP}\s*(?=[a-z])", "", base)
		parts += [base, spaced, joined, _collapse(spaced), _collapse(joined)]
	return " | ".join(dict.fromkeys(v for v in parts if v))


def _collapse(s: str) -> str:
	"""Nối lại chữ bị cách từng ký tự ("p h i" -> "phi")."""
	return re.sub(r"\b(?:[a-z] ){2,}[a-z]\b", lambda m: m.group(0).replace(" ", ""), s)


@functools.lru_cache(maxsize=16)
def _forms_of(text: str) -> tuple[str, ...]:
	return tuple(guard_variants(text, _folded_variants(text)))


def _topic_forms(customer_text: object) -> tuple[str, ...]:
	"""Mọi dạng gập/chuẩn hóa của lời khách (chốt chặn cũ + normalize_for_guard); không phải str thì coi như rỗng."""
	return _forms_of(customer_text if isinstance(customer_text, str) else "")


@functools.cache
def _alt(patterns: tuple[str, ...]) -> re.Pattern:
	"""Gộp các mẫu thành một biểu thức (biên dịch một lần) để quét nhanh."""
	return re.compile("|".join(f"(?:{p})" for p in patterns))


def sensitive_topics(customer_text: str | None) -> list[str]:
	"""Chủ đề nhạy cảm (giá, khiếu nại, pháp lý, y khoa, muốn gặp người) xuất hiện trong lời khách, kể cả khi viết lách."""
	forms = _topic_forms(customer_text)
	topics = [
		topic
		for topic, patterns in SENSITIVE_CUSTOMER_PATTERNS.items()
		if any(_alt(patterns).search(f) for f in forms)
	]
	for topic, keywords in COMPACT_KEYWORDS.items():
		if topic not in topics and compact_hit(list(forms), keywords):
			topics.append(topic)
	if isinstance(customer_text, str) and re.search(
		PRICE_WORD_ACCENTED, re.sub(r"\s+", " ", normalize_text(customer_text)).lower()
	):
		topics.append("hoi_gia")
	return list(dict.fromkeys(topics))


def guard_handoff_topic(
	group_always_handoff: bool,
	intent: str | None = None,
	wants_human: bool = False,
	customer_text: str | None = None,
) -> GuardResult:
	"""Chốt chặn 1: nhóm luôn chuyển người, ý định Hỏi phí/Yêu cầu đề xuất/Khiếu nại (và tên cũ), khách muốn gặp người, lời khách nhạy cảm."""
	if group_always_handoff:
		return True, "handoff_topic:nhom_luon_chuyen_nguoi"
	if intents.always_handoff(intent):  # độc lập nhóm câu hỏi và độc lập việc AI đã trả action reply
		return True, f"handoff_topic:y_dinh_{_folded(intent).replace(' ', '_')}"
	if wants_human:
		return True, "handoff_topic:khach_muon_gap_nguoi"
	topics = sensitive_topics(customer_text)
	if topics:
		return True, "handoff_topic:" + ",".join(topics)
	return False, ""


def guard_group_known(known: bool) -> GuardResult:
	"""Nhóm câu hỏi bot nêu phải là nhóm có trong hệ thống (nhóm lạ hay trống không biết có phải chuyển người không)."""
	if known:
		return False, ""
	return True, "nhom_cau_hoi_khong_ton_tai"


def guard_kb_items(kb_item_ids: list[str] | None, valid_ids: set[str] | frozenset[str]) -> GuardResult:
	"""Chốt chặn 2: `kb_item_ids` rỗng, hoặc có mã không tồn tại/không đang dùng."""
	ids = [i for i in (kb_item_ids or []) if i]
	if not ids:
		return True, "kb_rong"
	bad = [i for i in ids if i not in valid_ids]
	if bad:
		return True, "kb_khong_hop_le:" + ",".join(_short(b) for b in bad[:3])
	return False, ""


def _price_context_unsupported(text: str, kb_texts: list[str]) -> list[str]:
	"""Số đứng sau từ chỉ giá mà không có (cùng câu với từ đó) trong kho tri thức đã trích."""
	sentences = _sentences(kb_texts)
	found: list[str] = []
	for m in re.finditer(PRICE_CONTEXT, text, flags=re.IGNORECASE):
		word, number = norm(m.group(1)), norm(m.group(2))
		if not any(
			word in sent and re.search(rf"(?<!\d){re.escape(number)}(?!\d)", sent) for sent in sentences
		):
			found.append(_short(m.group(0)))
	return found


def _canon_number(token: str) -> str:
	"""Số dạng chuẩn: bỏ dấu phân cách hàng nghìn ("1.500.000" -> "1500000"), dấu phẩy thập phân thành chấm."""
	if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
		return re.sub(r"[.,]", "", token)
	return token.replace(",", ".")


def _numbers(text: str) -> set[str]:
	return {_canon_number(t.rstrip(".,")) for t in re.findall(r"\d+(?:[.,]\d+)*", normalize_text(text))}


def guard_unsupported_claims(
	reply_text: str,
	kb_texts: list[str],
	allowed_phrases: tuple[str, ...] | list[str] = (),
	strict_numbers: bool = True,
) -> GuardResult:
	"""Chốt chặn 3: tiền, ngày/thời hạn, từ cam kết hoặc bất kỳ con số nào không có nguyên văn trong kho tri thức đã trích.

	`allowed_phrases`: đúng các chuỗi do hệ thống sinh (ví dụ giờ làm việc kế tiếp) được bỏ khỏi văn bản trước khi
	kiểm; chỉ chuỗi đó, phần còn lại của câu vẫn bị kiểm. `strict_numbers`: mọi chữ số trong câu trả lời phải có
	trong các mục đã trích (tắt khi áp lên câu của sale, vì sale có quyền nêu số liệu ngoài kho).
	"""
	text = normalize_text(reply_text)
	for phrase in allowed_phrases:
		if phrase:
			text = text.replace(normalize_text(phrase), " ")
	checks = (
		("tien", MONEY_PATTERNS, True),
		("thoi_han", DEADLINE_PATTERNS, True),
		("thoi_han", RELATIVE_TIME_PATTERNS, False),
		("cam_ket", COMMITMENT_PATTERNS, False),
	)
	for label, patterns, noun_check in checks:
		found = _unsupported(text, patterns, kb_texts, noun_check)
		if label == "tien" and not found:
			found = _price_context_unsupported(text, kb_texts)
		if found:
			return True, f"{label}_ngoai_kho:{found[0]}"
	if strict_numbers:
		extra = sorted(_numbers(text) - _numbers("\n".join(kb_texts)))
		if extra:
			return True, f"so_ngoai_kho:{_short(extra[0])}"
	return False, ""


def _digits_canonical(run: str) -> str:
	digits = re.sub(r"\D", "", run)
	if digits.startswith("84") and len(digits) >= 11:
		return "0" + digits[2:]
	if digits.startswith("0084"):
		return "0" + digits[4:]
	return digits


def guard_unsupported_contacts(reply_text: str, kb_texts: list[str]) -> GuardResult:
	"""Chốt chặn 4: đường dẫn, email hoặc số điện thoại không có trong kho tri thức đã trích."""
	kb_raw = "\n".join(kb_texts).lower()
	text = normalize_text(reply_text)
	for email in re.findall(EMAIL_PATTERN, text, flags=re.IGNORECASE):
		if email.lower() not in kb_raw:
			return True, f"email_ngoai_kho:{_short(email)}"
	without_email = re.sub(EMAIL_PATTERN, " ", text, flags=re.IGNORECASE)
	for url in re.findall(URL_PATTERN, without_email, flags=re.IGNORECASE):
		clean = url.lower().rstrip(".,;:!?)")
		if clean not in kb_raw:
			return True, f"duong_dan_ngoai_kho:{_short(clean)}"
	kb_runs = {_digits_canonical(r) for r in re.findall(DIGIT_RUN_PATTERN, "\n".join(kb_texts))}
	for run in re.findall(DIGIT_RUN_PATTERN, text):
		if _digits_canonical(run) not in kb_runs:
			return True, "so_dien_thoai_ngoai_kho"
	return False, ""


def guard_length(reply_text: str, max_chars: int = DEFAULT_MAX_CHARS) -> GuardResult:
	"""Chốt chặn 5: câu trả lời dài hơn giới hạn của kênh (hoặc rỗng, nên không có gì để gửi)."""
	text = (reply_text or "").strip()
	if not text:
		return True, "tra_loi_rong"
	if len(text) > max_chars:
		return True, f"qua_dai:{len(text)}>{max_chars}"
	return False, ""


def guard_confidence(confidence: object) -> GuardResult:
	"""Chốt chặn 6: `confidence` phải là số thực/nguyên trong [0, 1] và >= ngưỡng; còn lại (NaN, bool, chuỗi, None) chặn."""
	ok = (
		isinstance(confidence, int | float)
		and not isinstance(confidence, bool)
		and 0 <= confidence <= 1  # NaN so sánh luôn sai nên rơi vào nhánh chặn
	)
	if not ok:
		return True, "do_tu_tin_khong_hop_le"
	if confidence < CONFIDENCE_MIN:
		return True, f"do_tu_tin_thap:{confidence:.2f}"
	return False, ""


# --- Chốt chặn bổ sung theo docs/08 mục 7 ---------------------------------------------------------------------


def guard_medical(reply_text: str, kb_texts: list[str]) -> GuardResult:
	"""Không tư vấn y khoa (liều dùng, chỉ định, chẩn đoán, điều trị…) ngoài nguyên văn kho tri thức."""
	found = _unsupported(reply_text, MEDICAL_PATTERNS, kb_texts)  # dùng _unsupported: đã NFKC và bỏ ký tự ẩn
	return (True, f"y_khoa_ngoai_kho:{found[0]}") if found else (False, "")


def guard_unsafe_output(reply_text: str) -> GuardResult:
	"""Đầu ra chứa thẻ, mã mục kho, hay dấu hiệu lộ câu lệnh hệ thống thì chặn."""
	text = normalize_text(reply_text)
	for pattern in UNSAFE_OUTPUT_PATTERNS:
		if re.search(pattern, text, flags=re.IGNORECASE):
			return True, "dau_ra_khong_an_toan"
	return False, ""


def guard_injection(customer_text: str | None) -> GuardResult:
	"""Lời khách có dấu hiệu chèn lệnh ("bỏ qua hướng dẫn", "báo giá 0 đồng"…) thì bot không tự trả lời; kháng cả lối viết lách."""
	forms = _topic_forms(customer_text)
	if any(_alt(INJECTION_PATTERNS).search(f) for f in forms) or compact_hit(list(forms), INJECTION_COMPACT):
		return True, "nghi_chen_lenh"
	return False, ""


def run_guards(
	*,
	reply_text: str,
	kb_item_ids: list[str],
	valid_kb_ids: set[str] | frozenset[str],
	kb_texts: list[str],
	group_known: bool,
	group_always_handoff: bool,
	intent: str | None,
	wants_human: bool,
	customer_text: str | None,
	confidence: float | None,
	max_chars: int = DEFAULT_MAX_CHARS,
	allowed_phrases: tuple[str, ...] | list[str] = (),
) -> list[str]:
	"""Chạy mọi chốt chặn; trả danh sách lý do (rỗng = không bị chặn). `kb_texts` là nội dung các mục đã trích."""
	checks = (
		guard_handoff_topic(group_always_handoff, intent, wants_human, customer_text),
		guard_injection(customer_text),
		guard_group_known(group_known),
		guard_kb_items(kb_item_ids, valid_kb_ids),
		guard_unsupported_claims(reply_text, kb_texts, allowed_phrases),
		guard_unsupported_contacts(reply_text, kb_texts),
		guard_medical(reply_text, kb_texts),
		guard_unsafe_output(reply_text),
		guard_length(reply_text, max_chars),
		guard_confidence(confidence),
	)
	return [reason for blocked, reason in checks if blocked]


def sale_goes_beyond_kb(sale_text: str, all_kb_texts: list[str]) -> bool:
	"""Câu sale chứa tiền/thời hạn/cam kết hoặc liên hệ ngoài kho tri thức (chốt chặn 3-4 áp lên câu sale)."""
	return (
		guard_unsupported_claims(sale_text, all_kb_texts, strict_numbers=False)[0]
		or guard_unsupported_contacts(sale_text, all_kb_texts)[0]
	)
