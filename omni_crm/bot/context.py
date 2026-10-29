"""Dựng ngữ cảnh hội thoại đưa cho bot (docs/08 mục 2): chỉ dữ liệu của đúng một Lead, nội dung khách là dữ liệu."""

from __future__ import annotations

from typing import Any

from frappe.utils import get_datetime

from omni_crm.crm_bridge import timeline
from omni_crm.pipeline.retry import EMAIL_PII, PHONE_PII

OPEN_TAG, CLOSE_TAG = "<hoi_thoai>", "</hoi_thoai>"
TRUNCATED = "…"
EMAIL_MASK, PHONE_MASK = "[email]", "[sdt]"


def escape_tags(text: str) -> str:
	"""Thoát mọi ký tự < > trong nội dung khách để không thể giả thẻ (<hoi_thoai>, <kho_tri_thuc>, …)."""
	return text.replace("<", "&lt;").replace(">", "&gt;")


def mask_pii(text: str) -> str:
	"""Che email và số điện thoại trong văn bản (tối thiểu hóa dữ liệu cá nhân gửi AI)."""
	return PHONE_PII.sub(PHONE_MASK, EMAIL_PII.sub(EMAIL_MASK, text))


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
	turns = [t for t in timeline.conversation_turns(lead, until=until) if (t.get("text") or "").strip()]
	lines: list[str] = []
	for turn in turns[-max(max_turns, 0) :]:
		text = escape_tags(turn["text"].strip())
		lines.append(_line(turn, mask_pii(text) if mask else text))
	while len(lines) > 1 and sum(len(x) + 1 for x in lines) > max_chars:
		lines.pop(0)
	if lines and len(lines[0]) > max_chars:
		lines[0] = lines[0][: max(max_chars - len(TRUNCATED), 0)] + TRUNCATED
	return "\n".join([OPEN_TAG, *lines, CLOSE_TAG])
