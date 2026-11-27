"""Đọc NỘI DUNG tệp khách gửi (đơn dự trù, danh mục cần báo giá) để bước đọc hiểu trích mặt hàng từ đó (phản hồi 26/11).

Trước đây bot chỉ giữ tên và đường dẫn tệp cho nhân viên; khách gửi tệp có đủ mặt hàng, số lượng vẫn bị hỏi lại.
Ở đây: tải tệp từ CDN của Zalo (chỉ https, chỉ host trong danh sách, có giới hạn dung lượng và thời gian), rút phần
CHỮ của tệp Word / Excel / PDF / CSV / văn bản, cắt ngắn rồi đưa vào đầu vào của bước đọc hiểu như một phần lời khách.

An toàn: nội dung tệp là DỮ LIỆU của khách, không bao giờ là lệnh; không chạy macro, không mở tệp nhúng; lỗi ở bất kỳ
bước nào → coi như không đọc được (bot làm như trước). Bật bằng site_config `omni_script_read_files`.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from urllib.parse import urlparse

import frappe
from frappe.utils import cint

from omni_crm.utils import media

SWITCH = "omni_script_read_files"
HOST_SUFFIXES = (*media.ZALO_MEDIA_HOST_SUFFIXES, "dlfl.vn")  # dlfl.vn: host tệp của Zalo, thấy trong dữ liệu thật 26/11
MAX_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 6 * 1024 * 1024  # phần chữ sau khi giải nén của một tệp Word (chống bom nén)
TIMEOUT = 10
MAX_CHARS = 4000  # phần chữ đưa cho AI
MAX_ROWS, MAX_COLS, MAX_SHEETS, MAX_PAGES = 200, 12, 3, 10
CACHE_SECONDS = 2 * 3600
TEXT_TYPES = (".docx", ".xlsx", ".xlsm", ".csv", ".txt", ".pdf")
MAX_FILES = 2


def enabled() -> bool:
	return bool(cint(frappe.conf.get(SWITCH) or 0))


def url_ok(url: object) -> bool:
	"""Đường dẫn tải được: https, host là CDN của Zalo, không kèm tài khoản, không cổng lạ, không ký tự lạ."""
	if not isinstance(url, str) or not url.startswith("https://") or any(c.isspace() or c == "\\" or ord(c) < 32 for c in url):
		return False
	try:
		parsed = urlparse(url)
		port = parsed.port
	except ValueError:
		return False
	host = (parsed.hostname or "").lower()
	if parsed.username or parsed.password or port not in (None, 443):
		return False
	return any(host == s or host.endswith("." + s) for s in HOST_SUFFIXES)


def kind_of(name: object) -> str:
	lowered = str(name or "").lower().strip()
	return next((ext for ext in TEXT_TYPES if lowered.endswith(ext)), "")


def _fetch(url: str) -> bytes:
	import requests

	with requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False) as res:
		if res.status_code != 200:
			return b""
		if cint(res.headers.get("Content-Length") or 0) > MAX_BYTES:
			return b""
		chunks, total = [], 0
		for chunk in res.iter_content(65536):
			total += len(chunk)
			if total > MAX_BYTES:
				return b""
			chunks.append(chunk)
		return b"".join(chunks)


# --- Rút chữ (thuần) ---------------------------------------------------------------------------------------------

_W_ROW = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
_W_CELL = re.compile(r"<w:tc[ >].*?</w:tc>", re.S)
_W_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_W_TEXT = re.compile(r"<w:t(?: [^>]*)?>(.*?)</w:t>", re.S)
_W_TABLE = re.compile(r"<w:tbl>.*?</w:tbl>", re.S)
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def _plain(xml: str) -> str:
	text = "".join(_W_TEXT.findall(xml))
	for entity, char in _ENTITIES.items():
		text = text.replace(entity, char)
	return " ".join(text.split())


def docx_text(data: bytes) -> str:
	"""Chữ của tệp Word: từng đoạn một dòng, mỗi hàng của bảng một dòng "ô | ô | ô" (bảng là chỗ hay ghi mặt hàng)."""
	with zipfile.ZipFile(io.BytesIO(data)) as archive:
		info = archive.getinfo("word/document.xml")
		if info.file_size > MAX_XML_BYTES:
			return ""
		xml = archive.read(info).decode("utf-8", "replace")
	lines = []
	position = 0
	for table in _W_TABLE.finditer(xml):
		lines += [_plain(p) for p in _W_PARA.findall(xml[position : table.start()])]
		for row in _W_ROW.findall(table.group(0))[:MAX_ROWS]:
			cells = [_plain(c) for c in _W_CELL.findall(row)[:MAX_COLS]]
			lines.append(" | ".join(c for c in cells if c))
		position = table.end()
	lines += [_plain(p) for p in _W_PARA.findall(xml[position:])]
	return "\n".join(line for line in lines if line)


def xlsx_text(data: bytes) -> str:
	import openpyxl

	book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
	lines = []
	try:
		for sheet in book.worksheets[:MAX_SHEETS]:
			for row in sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True):
				cells = [" ".join(str(c).split()) for c in row if c is not None and str(c).strip()]
				if cells:
					lines.append(" | ".join(cells))
	finally:
		book.close()
	return "\n".join(lines)


def pdf_text(data: bytes) -> str:
	from pypdf import PdfReader

	reader = PdfReader(io.BytesIO(data))
	pages = [(page.extract_text() or "") for page in reader.pages[:MAX_PAGES]]
	return "\n".join(" ".join(line.split()) for text in pages for line in text.splitlines() if line.strip())


def plain_text(data: bytes) -> str:
	for encoding in ("utf-8-sig", "utf-16", "cp1258", "latin-1"):
		try:
			return "\n".join(" ".join(line.split()) for line in data.decode(encoding).splitlines() if line.strip())
		except UnicodeError:
			continue
	return ""


def extract(name: str, data: bytes) -> str:
	"""Phần chữ của tệp theo đuôi tệp (đã cắt `MAX_CHARS`); loại tệp không đọc được hoặc lỗi → ""."""
	kind = kind_of(name)
	if not kind or not data:
		return ""
	try:
		if kind == ".docx":
			text = docx_text(data)
		elif kind in (".xlsx", ".xlsm"):
			text = xlsx_text(data)
		elif kind == ".pdf":
			text = pdf_text(data)
		else:
			text = plain_text(data)
	except Exception as e:
		frappe.logger("omni").info("omni script: không rút được chữ từ tệp khách gửi (%s, %s)", kind, type(e).__name__)
		return ""
	text = "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)
	return text[:MAX_CHARS]


# --- Dùng trong lượt ----------------------------------------------------------------------------------------------


def text_of(attachment: dict) -> str:
	"""Phần chữ của một tệp khách gửi (có bộ đệm theo đường dẫn). Không đọc được → "". Không bao giờ ném lỗi."""
	try:
		url, name = (attachment or {}).get("url"), str((attachment or {}).get("name") or "")
		if not enabled() or not kind_of(name) or not url_ok(url):
			return ""
		key = "omni:script:filedoc:" + hashlib.sha1(url.encode()).hexdigest()
		cached = frappe.cache().get_value(key, expires=True)
		if cached is not None:
			return cached
		text = extract(name, _fetch(url))
		frappe.cache().set_value(key, text, expires_in_sec=CACHE_SECONDS)
		return text
	except Exception as e:
		frappe.logger("omni").info("omni script: không đọc được tệp khách gửi (%s)", type(e).__name__)
		return ""


def for_turn(ctx, st: dict) -> list[tuple[str, str]]:
	"""[(tên tệp, phần chữ)] cần đưa vào bước đọc hiểu của lượt này: tệp khách vừa gửi; không có thì các tệp khách đã
	gửi cho yêu cầu báo giá đang dở (để câu "trong file có ghi rồi" đọc được). Tối đa `MAX_FILES` tệp gần nhất."""
	if not enabled():
		return []
	files = [a for a in (getattr(ctx, "attachments", None) or []) if isinstance(a, dict)]
	if not files and st.get("branch") == "3b" and isinstance(st.get("files"), list):
		files = [f for f in st["files"] if isinstance(f, dict)]
	out, seen = [], set()
	for item in reversed(files):
		name = str(item.get("name") or "")
		if name in seen or not kind_of(name):
			continue
		seen.add(name)
		text = text_of(item)
		if text:
			out.append((name, text))
		if len(out) >= MAX_FILES:
			break
	return out
