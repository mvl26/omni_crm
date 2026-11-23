"""Xưng hô theo cách khách tự xưng (yêu cầu 20/11, luật 11): khách tự xưng "anh" / "chị" thì các câu của bot dùng đúng
từ đó thay cho "anh/chị". Chỉ nhận khi khách tự xưng RÕ ("anh muốn…", "gửi chị…"); không đoán từ tên hay ảnh.

Công tắc: site_config `omni_script_pronoun` = 1. Lưu ở `script_state.pronoun`.
"""

from __future__ import annotations

import re
import unicodedata

import frappe
from frappe.utils import cint

SWITCH = "omni_script_pronoun"
KEY = "pronoun"
WORDS = {"anh": "anh", "a": "anh", "chị": "chị", "chi": "chị", "c": "chị"}
_SELF_VERB = r"(?:muốn|muon|cần|can|đang|dang|hỏi|xin|nhờ|nho|định|dinh|quên|quen|nhầm|nham|bận|ban|vừa|vua|đã|da|sẽ|se|đặt|dat|lấy|lay|mua|thấy|thay|chưa|chua)"
_TO_VERB = r"(?:cho|giúp|giup|gửi|gui|báo|bao|tư vấn|tu van|gọi|goi|hỗ trợ|ho tro|liên hệ|lien he)"
# khách là chủ ngữ ("anh muốn demo") hoặc là người nhận ("gửi chị báo giá", "tư vấn cho anh")
SUBJECT = re.compile(rf"(?<![\wÀ-ỹ])(anh|chị|chi|a|c)\s+{_SELF_VERB}(?![\wÀ-ỹ])", re.I)
OBJECT = re.compile(rf"(?<![\wÀ-ỹ]){_TO_VERB}\s+(?:cho\s+)?(anh|chị|chi|a|c)(?![\wÀ-ỹ])", re.I)
# khách tự xưng em / mình / tôi: chữ "anh", "chị" trong câu là gọi người của Miyano, không phải tự xưng
OTHER_SELF = re.compile(
	rf"(?<![\wÀ-ỹ])(?:em|e|mình|minh|tôi|toi|cháu|chau)\s+{_SELF_VERB}(?![\wÀ-ỹ])"
	rf"|(?<![\wÀ-ỹ]){_TO_VERB}\s+(?:cho\s+)?(?:em|e|mình|minh|tôi|toi|cháu|chau)(?![\wÀ-ỹ])",
	re.I,
)
PLACEHOLDER = re.compile(r"anh/chị", re.I)


def on() -> bool:
	return bool(cint(frappe.conf.get(SWITCH) or 0))


def detect(text: str | None) -> str | None:
	"""Từ khách dùng để tự xưng trong câu: "anh" | "chị"; không rõ, lẫn cả hai hoặc khách tự xưng em / mình → None. Thuần."""
	raw = unicodedata.normalize("NFC", str(text or "")).lower()
	if not raw.strip() or OTHER_SELF.search(raw):
		return None
	found = {WORDS[m.group(1).lower()] for rx in (SUBJECT, OBJECT) for m in rx.finditer(raw)}
	return found.pop() if len(found) == 1 else None


def remember(identity: str | None, text: str | None, script_state: dict | None) -> str | None:
	"""Ghi cách khách tự xưng (nếu câu này cho biết và khác điều đang lưu). Không bao giờ ném lỗi."""
	try:
		if not identity or not on():
			return None
		word = detect(text)
		if word and (script_state or {}).get(KEY) != word:
			from omni_crm.script import state

			state.update_state(identity, **{KEY: word})
		return word
	except Exception:
		return None


def fit(text: str, word: str | None) -> str:
	"""Thay "anh/chị" bằng từ khách tự xưng, giữ chữ hoa đầu câu. Thuần."""
	if word not in ("anh", "chị") or not text:
		return text
	return PLACEHOLDER.sub(lambda m: word.capitalize() if m.group(0)[0].isupper() else word, text)


def apply(identity: str | None, text: str) -> str:
	"""Câu sắp gửi khách, đã đổi "anh/chị" theo cách khách tự xưng (nếu biết và công tắc bật)."""
	try:
		if not identity or not on() or not PLACEHOLDER.search(text or ""):
			return text
		from omni_crm.script import state

		return fit(text, (state.get(identity).get("script_state") or {}).get(KEY))
	except Exception:
		return text
