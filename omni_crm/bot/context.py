"""Dựng ngữ cảnh hội thoại đưa cho bot (docs/08 mục 2): chỉ dữ liệu của đúng một Lead, nội dung khách là dữ liệu."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from frappe.utils import get_datetime

from omni_crm.crm_bridge import timeline
from omni_crm.pipeline.retry import EMAIL_PII, PHONE_PII

OPEN_TAG, CLOSE_TAG = "<hoi_thoai>", "</hoi_thoai>"
TRUNCATED = "…"
EMAIL_MASK, PHONE_MASK = "[email]", "[sdt]"
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
# Che chỉ là lớp phụ; lớp chặn dữ liệu khách là cổng Q11 ở ai_client (docs/12). Regex riêng của context, KHÔNG
# dùng chung với redact() của log (retry.py) để không đổi hành vi log.
PHONE_VN = re.compile(r"(?<!\d)(?:\(\s*\+?84\s*\)|\+?84|0)[ .\-]?[35789](?:[ .\-]?\d){8}(?!\d)")
PHONE_BARE = re.compile(r"(?<![\d.,])[35789]\d{8}(?![\d.,])")  # thiếu số 0 đầu, viết liền
EMAIL_OBFUSCATED = re.compile(
	r"[\w.+\-]+\s*(?:\(\s*at\s*\)|\[\s*at\s*\]|\bat\b)\s*[\w\-]+"
	r"(?:\s*(?:\(\s*dot\s*\)|\[\s*dot\s*\]|\bdot\b|\.)\s*[\w\-]+)+",
	re.IGNORECASE,
)


def escape_tags(text: str) -> str:
	"""Thoát mọi ký tự < > trong nội dung khách để không thể giả thẻ (<hoi_thoai>, <kho_tri_thuc>, …)."""
	return text.replace("<", "&lt;").replace(">", "&gt;")


def normalize_text(text: str) -> str:
	"""NFKC (chữ số/ký hiệu full-width về dạng thường) và bỏ ký tự độ rộng 0 chèn giữa số hay thẻ."""
	return unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)


def mask_pii(text: str) -> str:
	"""Che email (kể cả dạng `a (at) b.com`, `a at b dot com`) và số điện thoại VN trong văn bản đã chuẩn hóa."""
	text = normalize_text(text)
	text = EMAIL_PII.sub(EMAIL_MASK, text)
	text = EMAIL_OBFUSCATED.sub(EMAIL_MASK, text)
	for pattern in (PHONE_VN, PHONE_PII, PHONE_BARE):
		text = pattern.sub(PHONE_MASK, text)
	return text


def _role(turn: dict[str, Any]) -> str:
	return "khach" if turn.get("actor") == "customer" else "miyano"


def _line(turn: dict[str, Any], text: str) -> str:
	at = turn.get("at")
	when = get_datetime(at).strftime("%Y-%m-%d %H:%M") if at else "?"
	return f"[{_role(turn)} | {turn.get('channel_type') or '?'} | {when}] {text}"


def build_conversation(
	lead: str,
	until: Any = None,
	max_turns: int = 20,
	max_chars: int = 6000,
	mask: bool = True,
) -> str:
	"""Chuỗi `<hoi_thoai>…</hoi_thoai>` gồm các lượt gần nhất của Lead (cũ trước), cắt từ lượt cũ nhất khi quá dài.

	`until`: chỉ lấy lượt có thời điểm <= until. `mask`: che email/SĐT (mặc định bật cho đến khi chủ dự án chốt Q11
	trong docs/12; phân tích cần SĐT/email khách thì phải được chốt trước khi tắt).
	"""
	return render_turns(timeline.conversation_turns(lead, until=until), max_turns, max_chars, mask)


def render_turns(
	turns: list[dict[str, Any]], max_turns: int = 20, max_chars: int = 6000, mask: bool = True
) -> str:
	"""`<hoi_thoai>…</hoi_thoai>` từ danh sách lượt {at, actor, channel_type, text} (cũ trước), cắt như `build_conversation`.

	Dùng chung cho hội thoại thật (`build_conversation`) và ca của bộ đề (`bot/eval`, không có Lead).
	"""
	turns = [t for t in turns if (t.get("text") or "").strip()]
	lines: list[str] = []
	for turn in turns[-max(max_turns, 0) :]:
		text = escape_tags(
			normalize_text(turn["text"]).strip()
		)  # NFKC trước: không giả thẻ bằng ký tự full-width
		lines.append(_line(turn, mask_pii(text) if mask else text))
	while len(lines) > 1 and sum(len(x) + 1 for x in lines) > max_chars:
		lines.pop(0)
	if lines and len(lines[0]) > max_chars:
		lines[0] = lines[0][: max(max_chars - len(TRUNCATED), 0)] + TRUNCATED
	return "\n".join([OPEN_TAG, *lines, CLOSE_TAG])
