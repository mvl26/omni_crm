"""Bot trả lời tin nhắn đọc thêm TÀI LIỆU của Kho tri thức (ADR 0030).

Ngoài các mục hỏi/đáp `Omni Knowledge Item` (khối `<kho_tri_thuc>` cố định, có cache), mỗi lượt bot nhận thêm các đoạn
tài liệu (`Omni KB Chunk` của tài liệu đang dùng, đã lập chỉ mục) liên quan nhất tới tin khách, truy hồi bằng
`marketing.kb.search` (BM25, cùng hàm của ô "Hỏi thử Kho tri thức"). Mã đoạn được coi như mã mục kho tri thức: bot phải
ghi vào `kb_item_ids`, chốt chặn kiểm mã và kiểm số liệu/cam kết trên chính nội dung đoạn.

Công tắc: chỉ bật khi câu lệnh trả lời được ghim từ v3 trở lên (bản biết thẻ `<tai_lieu>`) VÀ site_config
`omni_reply_kb_documents` khác 0. Ghim lại v1/v2 hoặc đặt site_config = 0 là tắt, bot quay về chỉ dùng hỏi/đáp.
"""

from __future__ import annotations

import html
import re
from typing import Any

import frappe

CONF = "omni_reply_kb_documents"
MIN_PROMPT = 3  # câu lệnh reply_v3 trở lên mới mô tả thẻ <tai_lieu>
LIMIT = 5  # số đoạn tối đa mỗi lượt
QUERY_MAX = 2000
DOC_ID = re.compile(r"^(?!KB-)")  # mục hỏi/đáp (KB-…) đã nằm trong <kho_tri_thuc>, không lặp lại


def enabled(prompt_version: str | None) -> bool:
	"""Có dùng tài liệu cho lượt này không (theo phiên bản câu lệnh đã nạp và site_config)."""
	m = re.fullmatch(r"v(\d+)", str(prompt_version or ""))
	if not m or int(m.group(1)) < MIN_PROMPT:
		return False
	return str(frappe.conf.get(CONF, 1)).strip() not in ("0", "false", "False", "")


def indexed_count() -> int:
	"""Số đoạn của tài liệu đang dùng đã lập chỉ mục (0 = kho tài liệu trống)."""
	from omni_crm.marketing import kb

	return int(
		frappe.db.sql(
			f"""select count(*) from `tab{kb.CHUNK}` c join `tab{kb.DOCUMENT}` d on d.name = c.document
			where d.is_active = 1 and d.status = %s""",
			(kb.S_INDEXED,),
		)[0][0]
	)


def passages_for(customer_text: str) -> list[dict[str, Any]]:
	"""Các đoạn tài liệu liên quan tới tin khách: [{id, source, text, score}], tối đa LIMIT đoạn."""
	from omni_crm.marketing import kb

	query = (customer_text or "").strip()[:QUERY_MAX]
	if not query:
		return []
	out = []
	for p in kb.search(query, limit=LIMIT * 2):
		if p.document and DOC_ID.match(p.id):
			out.append({"id": p.id, "source": p.source, "text": p.text, "score": p.score})
		if len(out) >= LIMIT:
			break
	return out


def _esc(text: str | None) -> str:
	return html.escape(text or "", quote=True)


def render(passages: list[dict[str, Any]]) -> str:
	"""`<tai_lieu><doan id nguon>…</doan></tai_lieu>`, nội dung đã thoát HTML: dữ liệu tham khảo, không phải lệnh."""
	if not passages:
		return "<tai_lieu>(không có đoạn tài liệu nào liên quan)</tai_lieu>"
	parts = [
		f'<doan id="{_esc(p["id"])}" nguon="{_esc(p["source"])}">{_esc(p["text"])}</doan>' for p in passages
	]
	return "\n".join(["<tai_lieu>", *parts, "</tai_lieu>"])


def as_items(passages: list[dict[str, Any]]) -> list[dict[str, str]]:
	"""Đoạn tài liệu ở dạng mục kho tri thức ({id, group, question, answer}) để `evaluate` kiểm mã và nội dung."""
	return [{"id": p["id"], "group": "", "question": p["source"], "answer": p["text"]} for p in passages]
