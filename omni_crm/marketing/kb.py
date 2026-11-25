"""Kho tri thức marketing (V3-10, ADR 0029): trích văn bản, cắt đoạn 500-800 token, truy hồi BM25, render cho AI.

Không dùng embedding (MariaDB 10.11 không có VECTOR, Anthropic không có API embedding): BM25 trên văn bản bỏ dấu,
gồm từ đơn và cặp âm tiết liền nhau. Chỉ mục trong bộ nhớ, dựng lại khi kho đổi (khóa = số đoạn + lần tạo cuối).
Mục hỏi/đáp `Omni Knowledge Item` của bot được gộp vào kho để tra cứu, chỉ đọc.
"""

from __future__ import annotations

import hashlib
import html
import io
import math
import os
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm.marketing import s3_media
from omni_crm.pipeline.retry import redact

DOCUMENT = "Omni KB Document"
CHUNK = "Omni KB Chunk"
CATEGORY = "Omni KB Category"
S_PROCESSING, S_INDEXED, S_ERROR = "Đang xử lý", "Đã lập chỉ mục", "Lỗi"
ALLOWED_EXT = (".pdf", ".docx", ".txt", ".md")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024  # chặn tệp nén "bom"
MAX_TEXT_CHARS = 2_000_000
CHARS_PER_TOKEN = 3  # tiếng Việt có dấu: ~3 ký tự / token (ước lượng thô, an toàn)
CHUNK_MIN_TOKENS, CHUNK_TARGET_TOKENS, CHUNK_MAX_TOKENS = 500, 650, 800
BM25_K1, BM25_B = 1.5, 0.75
_index_cache: dict[str, Any] = {}


# --- Trích văn bản -------------------------------------------------------------------------------------------


def _docx_text(data: bytes) -> str:
	"""Văn bản DOCX từ word/document.xml (đoạn <w:p>, ngắt <w:br>/<w:tab>), không cần python-docx."""
	with zipfile.ZipFile(io.BytesIO(data)) as z:
		info = z.getinfo("word/document.xml")
		if info.file_size > MAX_DOCX_XML_BYTES:
			raise ValueError(_("Tệp DOCX quá lớn sau khi giải nén"))
		xml = z.read(info).decode("utf-8", errors="ignore")
	xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
	xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
	xml = re.sub(r"</w:p>", "\n", xml)
	text = re.sub(r"<[^>]+>", "", xml)
	return html.unescape(text)


def _pdf_text(data: bytes) -> str:
	try:
		from pypdf import PdfReader
	except ImportError:
		raise ValueError(_("Máy chủ chưa có thư viện đọc PDF (pypdf)")) from None
	reader = PdfReader(io.BytesIO(data))
	return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(filename: str, data: bytes) -> str:
	"""Văn bản thuần của tệp PDF / DOCX / TXT / MD; ném ValueError (tiếng Việt) nếu không đọc được."""
	ext = os.path.splitext(filename or "")[1].lower()
	if ext not in ALLOWED_EXT:
		raise ValueError(_("Chỉ nhận tệp PDF, DOCX, TXT, MD"))
	if len(data) > MAX_FILE_BYTES:
		raise ValueError(_("Tệp lớn hơn 20 MB"))
	if ext == ".docx":
		text = _docx_text(data)
	elif ext == ".pdf":
		text = _pdf_text(data)
	else:
		text = data.decode("utf-8-sig", errors="ignore")
	text = text.replace("\r\n", "\n").replace("\x00", "")
	return text[:MAX_TEXT_CHARS]


def file_bytes(file_url: str) -> tuple[str, bytes]:
	"""(tên tệp, nội dung) của một File theo file_url; ném ValueError nếu không có."""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		raise ValueError(_("Không tìm thấy tệp {0}").format(file_url))
	f = frappe.get_doc("File", name)
	if f.get(s3_media.KEY_FIELD):  # ADR 0033: tệp đã chuyển sang kho S3, không còn trên đĩa
		if cint(f.file_size) > MAX_FILE_BYTES:
			raise ValueError(_("Tệp lớn hơn 20 MB"))
		try:
			data = s3_media.file_content(f)
		except s3_media.S3Error as e:
			raise ValueError(str(e)) from None
		if len(data) > MAX_FILE_BYTES:
			raise ValueError(_("Tệp lớn hơn 20 MB"))
		return f.file_name or os.path.basename(f.file_url or ""), data
	path = f.get_full_path()
	if not os.path.isfile(path):
		raise ValueError(_("Tệp không còn trên máy chủ"))
	if os.path.getsize(path) > MAX_FILE_BYTES:
		raise ValueError(_("Tệp lớn hơn 20 MB"))
	with open(path, "rb") as fh:
		return f.file_name or os.path.basename(path), fh.read()


# --- Cắt đoạn ------------------------------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
	return -(-len(text or "") // CHARS_PER_TOKEN)


def _pieces(paragraph: str, max_chars: int) -> list[str]:
	"""Tách đoạn văn quá dài theo câu, câu quá dài theo từ."""
	if len(paragraph) <= max_chars:
		return [paragraph]
	out: list[str] = []
	buf = ""
	for sentence in re.split(r"(?<=[.!?…;])\s+", paragraph):
		while len(sentence) > max_chars:
			cut = sentence.rfind(" ", 0, max_chars)
			cut = cut if cut > max_chars // 2 else max_chars
			out.append(sentence[:cut].strip())
			sentence = sentence[cut:].strip()
		if buf and len(buf) + 1 + len(sentence) > max_chars:
			out.append(buf)
			buf = sentence
		else:
			buf = f"{buf} {sentence}".strip()
	if buf:
		out.append(buf)
	return out


def chunk_text(text: str) -> list[str]:
	"""Cắt văn bản thành đoạn ~500-800 token theo ranh giới đoạn văn (đoạn cuối có thể ngắn hơn)."""
	min_c = CHUNK_MIN_TOKENS * CHARS_PER_TOKEN
	target_c = CHUNK_TARGET_TOKENS * CHARS_PER_TOKEN
	max_c = CHUNK_MAX_TOKENS * CHARS_PER_TOKEN
	paragraphs = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n|\n", text or "")]
	parts: list[str] = []
	for p in paragraphs:
		if p:
			parts.extend(_pieces(p, max_c))
	chunks: list[str] = []
	buf = ""
	for p in parts:
		if buf and len(buf) + 1 + len(p) > max_c:
			chunks.append(buf)
			buf = p
		else:
			buf = f"{buf}\n{p}" if buf else p
		if len(buf) >= target_c:
			chunks.append(buf)
			buf = ""
	if buf:
		if chunks and len(buf) < min_c and len(chunks[-1]) + 1 + len(buf) <= max_c:
			chunks[-1] = f"{chunks[-1]}\n{buf}"
		else:
			chunks.append(buf)
	return chunks


# --- Vòng đời tài liệu ---------------------------------------------------------------------------------------


def signature(doc) -> str:
	return hashlib.sha256(f"{doc.source_file or ''}\n{doc.content or ''}".encode()).hexdigest()


def validate_document(doc) -> None:
	"""Cần tệp hoặc nội dung; tệp đúng định dạng; danh mục đang dùng."""
	if not doc.source_file and not (doc.content or "").strip():
		frappe.throw(_("Chọn tệp hoặc dán nội dung văn bản"))
	if doc.source_file and os.path.splitext(doc.source_file)[1].lower() not in ALLOWED_EXT:
		frappe.throw(_("Chỉ nhận tệp PDF, DOCX, TXT, MD"))
	if doc.category and not cint(frappe.db.get_value(CATEGORY, doc.category, "is_active")):
		frappe.throw(_("Danh mục {0} đang tắt").format(doc.category))


def needs_reindex(doc) -> bool:
	return doc.content_hash != signature(doc) or doc.status == S_PROCESSING


def bump_version() -> None:
	_index_cache.clear()


def queue_index(name: str) -> None:
	"""Đặt Đang xử lý và xếp job lập chỉ mục (hàng đợi long) sau khi giao dịch hiện tại commit."""
	frappe.db.set_value(DOCUMENT, name, {"status": S_PROCESSING, "index_error": ""}, update_modified=False)
	frappe.enqueue(
		"omni_crm.marketing.kb.index_document",
		queue="long",
		job_id=f"oc-kb-index-{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		name=name,
	)


def index_document(name: str) -> str:
	"""Job nền: đọc tệp/nội dung, cắt đoạn, thay toàn bộ đoạn cũ; trả trạng thái mới. Lỗi -> "Lỗi" kèm lý do."""
	doc = frappe.get_doc(DOCUMENT, name)
	try:
		parts = []
		if doc.source_file:
			filename, data = file_bytes(doc.source_file)
			parts.append(extract_text(filename, data))
		if (doc.content or "").strip():
			parts.append(html.unescape(doc.content))  # Frappe thoát HTML khi lưu: đưa về văn bản gốc
		text = "\n\n".join(p for p in parts if p.strip())
		chunks = chunk_text(text)
		if not chunks:
			raise ValueError(_("Không đọc được chữ nào từ tài liệu (PDF ảnh scan cần OCR trước)"))
	except Exception as e:
		frappe.db.rollback()
		message = str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"
		frappe.db.set_value(
			DOCUMENT, name, {"status": S_ERROR, "index_error": redact(message)[:500]}, update_modified=False
		)
		frappe.db.commit()
		return S_ERROR
	frappe.db.delete(CHUNK, {"document": name})
	for i, content in enumerate(chunks, 1):
		frappe.get_doc(
			{
				"doctype": CHUNK,
				"document": name,
				"category": doc.category,
				"seq": i,
				"token_estimate": estimate_tokens(content),
				"content": content,
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value(
		DOCUMENT,
		name,
		{
			"status": S_INDEXED,
			"chunk_count": len(chunks),
			"char_count": len(text),
			"indexed_at": now_datetime(),
			"content_hash": signature(doc),
			"index_error": "",
		},
		update_modified=False,
	)
	frappe.db.commit()
	bump_version()
	return S_INDEXED


# --- Truy hồi BM25 -------------------------------------------------------------------------------------------


def normalize(text: str) -> str:
	"""Chữ thường, bỏ dấu tiếng Việt (đ -> d)."""
	from unidecode import unidecode

	return unidecode((text or "").lower().replace("đ", "d"))


def terms(text: str) -> list[str]:
	"""Từ đơn (bỏ từ 1 ký tự) và cặp âm tiết liền nhau, trên văn bản đã bỏ dấu."""
	words = [w for w in re.findall(r"[a-z0-9]+", normalize(text))]
	uni = [w for w in words if len(w) > 1]
	bi = [f"{a}_{b}" for a, b in pairwise(words)]
	return uni + bi


@dataclass
class Passage:
	"""Một đoạn nguồn để AI trích: `id` là tên Omni KB Chunk hoặc mã KB-… của mục hỏi/đáp."""

	id: str
	source: str
	text: str
	document: str | None = None
	score: float = 0.0


def _corpus_key() -> str:
	row = frappe.db.sql(
		f"select count(*), coalesce(max(creation), '') from `tab{CHUNK}`"
	)  # đổi khi lập chỉ mục lại
	items = frappe.db.sql("select count(*), coalesce(max(modified), '') from `tabOmni Knowledge Item`")
	return f"{row[0][0]}|{row[0][1]}|{items[0][0]}|{items[0][1]}"


def _load_passages() -> list[Passage]:
	rows = frappe.db.sql(
		f"""select c.name, c.content, c.document, d.title
		from `tab{CHUNK}` c join `tab{DOCUMENT}` d on d.name = c.document
		where d.is_active = 1 and d.status = %s
		order by d.title, c.seq""",
		(S_INDEXED,),
		as_dict=True,
	)
	out = [
		Passage(r.name, html.unescape(r.title or ""), html.unescape(r.content or ""), r.document)
		for r in rows
	]
	from omni_crm.bot import knowledge

	for item in knowledge.kb_items():
		out.append(Passage(item["id"], _("Hỏi đáp: {0}").format(item["question"]), item["answer"]))
	return out


def _index() -> dict[str, Any]:
	key = _corpus_key()
	cached = _index_cache.get("index")
	if cached and cached["key"] == key:
		return cached
	passages = _load_passages()
	docs = [Counter(terms(f"{p.source}\n{p.text}")) for p in passages]
	df: Counter = Counter()
	for d in docs:
		df.update(d.keys())
	n = len(docs)
	avg = (sum(sum(d.values()) for d in docs) / n) if n else 0
	idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
	built = {"key": key, "passages": passages, "docs": docs, "idf": idf, "avg": avg}
	_index_cache["index"] = built
	return built


def search(query: str, limit: int = 6) -> list[Passage]:
	"""Các đoạn liên quan nhất tới `query` theo BM25 (điểm > 0), nhiều nhất `limit` đoạn."""
	q = set(terms(query))
	if not q:
		return []
	idx = _index()
	scored: list[Passage] = []
	for p, d in zip(idx["passages"], idx["docs"], strict=False):
		length = sum(d.values()) or 1
		s = 0.0
		for t in q:
			f = d.get(t)
			if not f:
				continue
			s += (
				idx["idf"][t]
				* f
				* (BM25_K1 + 1)
				/ (f + BM25_K1 * (1 - BM25_B + BM25_B * length / (idx["avg"] or 1)))
			)
		if s > 0:
			scored.append(Passage(p.id, p.source, p.text, p.document, round(s, 4)))
	scored.sort(key=lambda x: x.score, reverse=True)
	return scored[:limit]


def esc(text: str | None) -> str:
	return html.escape(text or "", quote=True)


def render_passages(passages: list[Passage]) -> str:
	"""`<kho_tri_thuc>` gồm các `<doan id nguon>` đã thoát HTML: dữ liệu tham khảo, không phải lệnh."""
	if not passages:
		return "<kho_tri_thuc>(không có đoạn nào liên quan)</kho_tri_thuc>"
	parts = [f'<doan id="{esc(p.id)}" nguon="{esc(p.source)}">{esc(p.text)}</doan>' for p in passages]
	return "\n".join(["<kho_tri_thuc>", *parts, "</kho_tri_thuc>"])
