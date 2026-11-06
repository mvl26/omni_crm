"""Dựng ngữ cảnh cho trợ lý nội bộ (V3-05) và bọc mọi nội dung do bên ngoài tạo trong <du_lieu_khach_hang>.

Quy tắc 4 và 5 của brief: system prompt là hằng số (loop.py), ngữ cảnh Lead đi trong lượt user. Mọi chuỗi có thể do khách
hoặc hệ thống ngoài viết (tin Zalo, thân email, dữ liệu form, tên, đơn vị) đi qua `clean()` trước khi chèn: chuẩn hóa
NFKC, bỏ ký tự độ rộng 0, xóa mọi biến thể thẻ <du_lieu_khach_hang> / </du_lieu_khach_hang> rồi thoát < >, nên không
chuỗi nào thoát được thẻ hay giả thẻ khác.
"""

from __future__ import annotations

import json
import re
from typing import Any

import frappe
from frappe.utils import cint, get_datetime

from omni_crm.bot.context import escape_tags, mask_pii, normalize_text
from omni_crm.crm_bridge import timeline

DATA_TAG = "du_lieu_khach_hang"
OPEN_DATA, CLOSE_DATA = f"<{DATA_TAG}>", f"</{DATA_TAG}>"
# Mọi dạng thẻ dữ liệu: hoa/thường, khoảng trắng, dấu / thừa, thoát bằng &lt; ... (đã NFKC trước đó)
TAG_RE = re.compile(r"(?:<|&lt;)\s*/?\s*du[\s_]*lieu[\s_]*khach[\s_]*hang\s*/?\s*(?:>|&gt;)", re.IGNORECASE)
MAX_MESSAGES = 10
MAX_MESSAGE_CHARS = 2000
UNMASK_CONF = "omni_ai_unmask_pii"  # như bot phân tích: chỉ bật sau khi chủ dự án chốt Q11
ACTOR_LABELS = {"customer": "Khách", "sale": "Nhân viên", "bot": "Bot", "system": "Hệ thống"}


def unmask_pii() -> bool:
	"""Có gửi SĐT/email nguyên vẹn sang AI không (site_config `omni_ai_unmask_pii`, cùng cờ với bot phân tích)."""
	return bool(cint(frappe.conf.get(UNMASK_CONF)))


def clean(value: Any, limit: int | None = None) -> str:
	"""Chuỗi bên ngoài đã khử thẻ: NFKC, bỏ độ rộng 0, xóa thẻ dữ liệu (mọi biến thể), thoát < >, cắt `limit` ký tự."""
	text = normalize_text(str(value or ""))
	prev = None
	while prev != text:  # xóa lặp: "<du_lieu<du_lieu_khach_hang>_khach_hang>" không ghép lại được thẻ
		prev, text = text, TAG_RE.sub("", text)
	text = escape_tags(text)
	if limit is not None and len(text) > limit:
		text = text[: max(limit - 1, 0)] + "…"
	return text


def wrap(body: str) -> str:
	"""Bọc `body` (đã `clean`) trong thẻ dữ liệu khách hàng."""
	return f"{OPEN_DATA}\n{body}\n{CLOSE_DATA}"


def wrap_json(data: Any) -> str:
	"""Kết quả tool chứa dữ liệu CRM (tên, đơn vị... có thể do khách điền): JSON đã khử thẻ, bọc trong thẻ dữ liệu."""
	return wrap(json.dumps(_clean_tree(data), ensure_ascii=False, default=str))


def _clean_tree(data: Any) -> Any:
	if isinstance(data, dict):
		return {k: _clean_tree(v) for k, v in data.items()}
	if isinstance(data, list | tuple):
		return [_clean_tree(v) for v in data]
	if isinstance(data, str):
		return clean(data)
	return data


def mask_value(value: str | None) -> str:
	"""SĐT/email trong kết quả tool: nguyên vẹn khi đã chốt Q11 bỏ che, ngược lại chỉ giữ 3 ký tự cuối để phân biệt."""
	if not value:
		return ""
	if unmask_pii():
		return str(value)
	text = str(value)
	return "***" + text[-3:] if len(text) > 3 else "***"


def message_lines(lead: str) -> list[str]:
	"""Tối đa 10 tin gần nhất của Lead (cũ trước), mỗi tin cắt 2000 ký tự, đã khử thẻ; che SĐT/email khi chưa chốt Q11."""
	turns = [t for t in timeline.conversation_turns(lead) if (t.get("text") or "").strip()]
	lines = []
	for t in turns[-MAX_MESSAGES:]:
		text = clean(t["text"], MAX_MESSAGE_CHARS)
		if not unmask_pii():
			text = mask_pii(text)
		at = t.get("at")
		when = get_datetime(at).strftime("%d/%m %H:%M") if at else "?"
		who = ACTOR_LABELS.get(t.get("actor") or "", "Khác")
		lines.append(f"[{t.get('channel_type') or '?'} {when} {who}] {text}")
	return lines


def lead_block(lead: str) -> str:
	"""Khối <lead_dang_mo> (mã, tên, đơn vị, trạng thái, người phụ trách) + tin gần nhất trong thẻ dữ liệu khách hàng.

	Tên và đơn vị có thể do khách tự điền qua form nên cũng được khử thẻ như dữ liệu khách. Người gọi đã kiểm quyền đọc.
	"""
	row = (
		frappe.db.get_value(
			"CRM Lead", lead, ["name", "lead_name", "organization", "status", "lead_owner"], as_dict=True
		)
		or {}
	)
	head = "\n".join(
		[
			"<lead_dang_mo>",
			f"Mã: {clean(row.get('name'))}",
			f"Tên: {clean(row.get('lead_name'), 200)}",
			f"Đơn vị: {clean(row.get('organization'), 200)}",
			f"Trạng thái: {clean(row.get('status'))}",
			f"Người phụ trách: {clean(row.get('lead_owner'))}",
			"</lead_dang_mo>",
		]
	)
	lines = message_lines(lead)
	return head + "\n\n" + wrap("\n".join(lines) if lines else "(chưa có tin nhắn)")


def first_user_message(command: str, lead: str | None) -> str:
	"""Nội dung lượt user đầu tiên: ngữ cảnh Lead đang mở (nếu có) rồi câu lệnh của nhân viên."""
	parts = []
	if lead:
		parts.append(lead_block(lead))
	parts.append(f"Câu lệnh của nhân viên: {command}")
	return "\n\n".join(parts)
