"""Chuẩn hóa văn bản khách cho chốt chặn chủ đề và chèn lệnh (V21-07, F7): thuần, không đụng DB.

Mục tiêu: khách không né được bằng cách viết ("đ à m p h á n", "ph1 dich vu", "damphan", chữ Cyrillic giống Latin,
ký tự ẩn, emoji xen giữa, chữ kéo dài, đảo ngược). Chỉ dùng để QUYẾT ĐỊNH CHUYỂN NGƯỜI, không bao giờ là lý do gửi tin.
"""

from __future__ import annotations

import functools
import re
import unicodedata

from omni_crm.identity.normalize import strip_accents

# Cyrillic/Hy Lạp/chữ lạ trông giống Latin (đã hạ chữ thường trước khi tra).
_HOMOGLYPHS = str.maketrans(
	{
		"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",  # noqa: RUF001
		"ѕ": "s", "ԁ": "d", "һ": "h", "ԛ": "q", "ԝ": "w", "к": "k", "м": "m", "н": "h", "т": "t",  # noqa: RUF001
		"в": "b", "ɡ": "g", "ο": "o", "α": "a", "ε": "e", "ρ": "p", "ν": "v", "ι": "i", "κ": "k",  # noqa: RUF001
		"τ": "t", "υ": "u", "χ": "x", "ı": "i", "ø": "o", "ł": "l",  # noqa: RUF001
	}
)  # fmt: skip
# Số/ký hiệu hay dùng thay chữ (F7 mục 1).
_LEET = str.maketrans({"1": "i", "0": "o", "3": "e", "4": "a", "@": "a", "$": "s"})
_SINGLE_RUN = re.compile(r"\b(?:[a-z] ){2,}[a-z]\b")


def _clean(text: object) -> str:
	return _clean_str(text) if isinstance(text, str) else ""


@functools.lru_cache(maxsize=16)
def _clean_str(text: str) -> str:
	"""Chỉ nhận str; NFKC, bỏ ký tự điều khiển/định dạng ẩn (độ rộng 0, gạch nối mềm, bidi), chữ thường, homoglyph, bỏ dấu."""
	if not text:
		return ""
	t = unicodedata.normalize("NFKC", text)
	t = "".join(c for c in t if unicodedata.category(c) not in ("Cf", "Cc") or c in "\n\t ")
	t = strip_accents(t.casefold().translate(_HOMOGLYPHS))  # NFD, bỏ dấu kể cả đ -> d, chữ thường
	return t


def normalize_for_guard(text: str | None, sep: str = "") -> str:
	"""Dạng chuẩn để so khớp chủ đề: bỏ dấu (đ->d), 1->i 0->o 3->e 4->a @->a $->s, bỏ ký hiệu/emoji (thay bằng `sep`).

	Gộp chữ đơn cách nhau ("d a m" -> "dam") và rút chuỗi ký tự lặp quá 2 lần về 2. `sep=" "` giữ ranh giới từ nơi
	ký hiệu đứng giữa. Không nhận str thì trả "".
	"""
	t = _clean(text).translate(_LEET)
	t = re.sub(r"[^a-z0-9%\s]", sep, t)
	t = re.sub(r"\s+", " ", t).strip()
	t = _SINGLE_RUN.sub(lambda m: m.group(0).replace(" ", ""), t)
	return re.sub(r"(.)\1{2,}", r"\1\1", t)


def _squash(s: str) -> str:
	"""Rút mọi chuỗi ký tự lặp về 1 ("phii" -> "phi"); dùng song song với dạng gốc để bắt kéo dài chữ."""
	return re.sub(r"(.)\1+", r"\1", s)


# Cụm lành tính bị gỡ khỏi biến thể trước khi so từ khóa (giảm chặn nhầm): viết trên dạng đã bỏ dấu, `\s*` để dùng được
# cho cả dạng viết liền. Phần còn lại của câu vẫn bị kiểm nên "thỏa thuận lịch hẹn và đàm phán" vẫn bị chặn.
BENIGN = re.compile(
	r"phan\s*tram\s*(?:hai\s*long|chinh\s*xac|chac\s*chan|an\s*tam|tin\s*tuong|ung\s*ho|dong\s*y)"
	r"|thoa\s*thuan\s*(?:lich|hen|gio\s*giac|thoi\s*gian\s*(?:hop|gap|lam\s*viec)|cuoc\s*hop|buoi\s*(?:hop|gap|demo))"
)


def guard_variants(text: object, extra_folded: str = "") -> list[str]:
	"""Các dạng chuẩn hóa của lời khách để so khớp (khử trùng, giữ thứ tự): ký hiệu xóa / thành khoảng trắng, rút lặp.

	`extra_folded`: chuỗi các dạng gập có sẵn của chốt chặn cũ (nối bằng " | ") được thêm vào để "một bên khớp là chặn".
	"""
	forms: list[str] = []
	for sep in ("", " "):
		n = normalize_for_guard(text, sep)
		forms += [n, _squash(n)]
	forms += [p.strip() for p in extra_folded.split("|")]
	return [BENIGN.sub(" ", f) for f in dict.fromkeys(f for f in forms if f)]


def compact(s: str) -> str:
	"""Chỉ giữ chữ-số (bỏ mọi khoảng trắng) để so dạng viết liền."""
	return re.sub(r"[^a-z0-9]", "", s)


def _candidates(kw: str) -> tuple[str, str]:
	k = compact(kw)
	return k, _squash(k)


_CAND_CACHE: dict[str, tuple[str, str]] = {}


def compact_hit(forms: list[str] | tuple[str, ...], keywords: tuple[str, ...]) -> str | None:
	"""Từ khóa (dạng viết liền) xuất hiện trong dạng viết liền của văn bản, trong một từ, hoặc đảo ngược; None nếu không có.

	Từ >= 7 ký tự: tìm trong cả văn bản viết liền (bắt tách chữ kỳ quặc) và đảo ngược; từ 5-6 ký tự: chỉ khi nằm trong
	MỘT từ của văn bản (tránh dính chữ giữa hai từ như "khoa hong" ~ "hoahong").
	"""
	for form in forms:
		whole = compact(form)
		squashed = form == _squash(form)  # dạng đã rút lặp thì so với từ khóa cũng rút lặp
		for kw in keywords:
			k, ks = _CAND_CACHE.get(kw) or _CAND_CACHE.setdefault(kw, _candidates(kw))
			for cand in (k, ks) if squashed and ks != k else (k,):
				if len(cand) >= 7 and (cand in whole or cand[::-1] in whole):
					return kw
				if len(cand) >= 5 and cand in form:  # không chứa khoảng trắng nên nằm trong MỘT từ
					return kw
	return None
