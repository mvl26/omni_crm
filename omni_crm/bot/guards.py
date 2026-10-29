"""Các chốt chặn sau sinh cho bot trả lời (docs/08 mục 5.4, mục 7), chạy bằng code, không dựa vào AI.

Mỗi chốt chặn là một hàm thuần trả `(bị_chặn, lý_do)`. `run_guards` gom lý do của mọi chốt chặn bị kích hoạt; có
bất kỳ lý do nào thì bản nháp phải đổi thành chuyển người. Danh sách từ khóa là hằng số ở đầu tệp để dễ chỉnh.
"""

from __future__ import annotations

import re
import unicodedata

from omni_crm.identity.normalize import strip_accents

GuardResult = tuple[bool, str]

CONFIDENCE_MIN = 0.5  # chốt chặn 6
DEFAULT_MAX_CHARS = 1200  # chốt chặn 5: giới hạn mặc định khi kênh không khai báo
MAX_PHRASE_SHOWN = 40  # cắt cụm từ ghi vào lý do

# --- Từ khóa (dễ sửa) -------------------------------------------------------------------------------------
# Tiền: số kèm đơn vị tiền, hoặc cụm "<chữ số> triệu/nghìn…"; tỉ lệ phần trăm; ký hiệu đô la.
MONEY_PATTERNS = (
	r"(?<![\w.,])\d[\d.,]*\s*(?:đồng|vnđ|vnd|nghìn|ngàn|triệu|tỷ|tỉ|usd|đô|tr|k|đ|%)(?!\w)",
	r"[$]\s*\d[\d.,]*",
	r"(?<!\w)(?:usd|vnd|vnđ)\s*\d[\d.,]*",
	r"(?:\S+\s+)?(?:triệu|nghìn|ngàn|tỷ)(?!\w)",
	r"(?:\S+\s+)?(?:đồng|vnđ|vnd|usd)(?!\w)",
)
# Ngày/thời hạn giao: số kèm đơn vị thời gian, giờ giấc, ngày tháng, mốc tương đối.
DEADLINE_PATTERNS = (
	r"(?<![\w.,/])\d+\s*(?:ngày|tuần|tháng|giờ|tiếng|phút)(?!\w)",
	r"(?<![\w.,/])\d{1,2}\s*[:h]\s*\d{2}(?!\d)",
	r"(?<![\w.,/])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\w/])",
	r"\bngày\s+\d{1,2}\b",
	r"\b(?:ngày mai|hôm sau|tuần sau|tháng sau|trong ngày|ngay lập tức|cuối tuần này)\b",
)
# Từ cam kết: kiểm cụm "từ khóa + tối đa 3 từ theo sau" có nguyên văn trong kho tri thức không.
COMMITMENT_PATTERNS = (
	r"\b(?:cam kết|đảm bảo|chắc chắn|bảo đảm|hoàn tiền|miễn phí|giảm giá|chiết khấu|khuyến mãi|ưu đãi)"
	r"(?:\s+[^\s.,;:!?]+){0,3}",
	r"\bbảo hành(?:\s+[^\s.,;:!?]+){0,4}?\s+(?:năm|tháng|ngày)\b",
)
# Y khoa: không tư vấn chuyên môn ngoài kho tri thức (docs/08 mục 7).
MEDICAL_PATTERNS = (
	r"\b(?:liều dùng|liều lượng|chống chỉ định|chỉ định|chẩn đoán|điều trị|kê đơn|tác dụng phụ|uống thuốc|tiêm cho)"
	r"(?:\s+[^\s.,;:!?]+){0,3}",
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
	),
	"khieu_nai": (
		r"\bkhieu nai\b",
		r"\bphan nan\b",
		r"\bkhong hai long\b",
		r"\bboi thuong\b",
		r"\bhoan tien\b",
		r"\bto cao\b",
	),
	"phap_ly": (r"\bkien\b", r"\bluat su\b", r"\bphap ly\b", r"\bvi pham\b", r"\bthanh tra\b"),
	"y_khoa": (
		r"\blieu dung\b",
		r"\blieu luong\b",
		r"\bchi dinh\b",
		r"\bchan doan\b",
		r"\btac dung phu\b",
		r"\bchong chi dinh\b",
	),
	"gap_nguoi": (r"\bgap nhan vien\b", r"\bnoi chuyen voi (?:nguoi|nhan vien)\b", r"\bgoi lai cho toi\b"),
}
PRICE_WORD_ACCENTED = r"(?<!\w)giá(?!\w)"
INTENTS_HANDOFF = ("Hỏi giá", "Khiếu nại")
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
	r"\b(?:reveal|show|tiet lo)\b.{0,20}\b(?:prompt|huong dan|instruction)",
)
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


def _unsupported(text: str, patterns: tuple[str, ...], kb_texts: list[str]) -> list[str]:
	"""Các cụm khớp `patterns` trong `text` mà không có nguyên văn (sau chuẩn hóa) trong kho tri thức đã trích."""
	corpus = _corpus(kb_texts)
	found: list[str] = []
	lowered = unicodedata.normalize("NFKC", text or "")
	for pattern in patterns:
		for m in re.finditer(pattern, lowered, flags=re.IGNORECASE):
			phrase = m.group(0)
			if not _contains(phrase, corpus) and _short(phrase) not in found:
				found.append(_short(phrase))
	return found


# --- 6 chốt chặn của docs/08 mục 5.4 ------------------------------------------------------------------------


def _folded(text: str | None) -> str:
	return re.sub(r"\s+", " ", strip_accents(unicodedata.normalize("NFKC", text or "")).lower())


def sensitive_topics(customer_text: str | None) -> list[str]:
	"""Chủ đề nhạy cảm (giá, khiếu nại, pháp lý, y khoa, muốn gặp người) xuất hiện trong lời khách."""
	folded = _folded(customer_text)
	topics = [
		topic
		for topic, patterns in SENSITIVE_CUSTOMER_PATTERNS.items()
		if any(re.search(p, folded) for p in patterns)
	]
	if re.search(PRICE_WORD_ACCENTED, unicodedata.normalize("NFKC", customer_text or "").lower()):
		topics.append("hoi_gia")
	return list(dict.fromkeys(topics))


def guard_handoff_topic(
	group_always_handoff: bool,
	intent: str | None = None,
	wants_human: bool = False,
	customer_text: str | None = None,
) -> GuardResult:
	"""Chốt chặn 1: nhóm luôn chuyển người, ý định Hỏi giá/Khiếu nại, khách muốn gặp người, hoặc lời khách nhạy cảm."""
	if group_always_handoff:
		return True, "handoff_topic:nhom_luon_chuyen_nguoi"
	if intent in INTENTS_HANDOFF:
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


def guard_unsupported_claims(reply_text: str, kb_texts: list[str]) -> GuardResult:
	"""Chốt chặn 3: tiền, ngày/thời hạn, hoặc từ cam kết không có nguyên văn trong các mục kho tri thức đã trích."""
	for label, patterns in (
		("tien", MONEY_PATTERNS),
		("thoi_han", DEADLINE_PATTERNS),
		("cam_ket", COMMITMENT_PATTERNS),
	):
		found = _unsupported(reply_text, patterns, kb_texts)
		if found:
			return True, f"{label}_ngoai_kho:{found[0]}"
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
	text = unicodedata.normalize("NFKC", reply_text or "")
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


def guard_confidence(confidence: float | None) -> GuardResult:
	"""Chốt chặn 6: `confidence` dưới ngưỡng (tín hiệu bổ sung, không phải chốt chặn chính)."""
	try:
		value = float(confidence)
	except (TypeError, ValueError):
		return True, "do_tu_tin_khong_hop_le"
	if value < CONFIDENCE_MIN:
		return True, f"do_tu_tin_thap:{value:.2f}"
	return False, ""


# --- Chốt chặn bổ sung theo docs/08 mục 7 ---------------------------------------------------------------------


def guard_medical(reply_text: str, kb_texts: list[str]) -> GuardResult:
	"""Không tư vấn y khoa (liều dùng, chỉ định, chẩn đoán, điều trị…) ngoài nguyên văn kho tri thức."""
	found = _unsupported(reply_text, MEDICAL_PATTERNS, kb_texts)
	return (True, f"y_khoa_ngoai_kho:{found[0]}") if found else (False, "")


def guard_unsafe_output(reply_text: str) -> GuardResult:
	"""Đầu ra chứa thẻ, mã mục kho, hay dấu hiệu lộ câu lệnh hệ thống thì chặn."""
	for pattern in UNSAFE_OUTPUT_PATTERNS:
		if re.search(pattern, reply_text or "", flags=re.IGNORECASE):
			return True, "dau_ra_khong_an_toan"
	return False, ""


def guard_injection(customer_text: str | None) -> GuardResult:
	"""Lời khách có dấu hiệu chèn lệnh ("bỏ qua hướng dẫn", "báo giá 0 đồng"…) thì bot không tự trả lời."""
	folded = _folded(customer_text)
	for pattern in INJECTION_PATTERNS:
		if re.search(pattern, folded):
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
) -> list[str]:
	"""Chạy mọi chốt chặn; trả danh sách lý do (rỗng = không bị chặn). `kb_texts` là nội dung các mục đã trích."""
	checks = (
		guard_handoff_topic(group_always_handoff, intent, wants_human, customer_text),
		guard_injection(customer_text),
		guard_group_known(group_known),
		guard_kb_items(kb_item_ids, valid_kb_ids),
		guard_unsupported_claims(reply_text, kb_texts),
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
		guard_unsupported_claims(sale_text, all_kb_texts)[0]
		or guard_unsupported_contacts(sale_text, all_kb_texts)[0]
	)
