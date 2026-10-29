"""Làm sạch nội dung email: bỏ trích dẫn và chữ ký (docs/05 mục 3)."""

from __future__ import annotations

import re

from frappe.core.utils import html2text

MAX_CHARS = 8000  # giới hạn nội dung đưa cho bot; bản đầy đủ vẫn nằm trong Communication
SIGNATURE_MAX_LINES = 15  # lời chào kết chỉ là chữ ký nếu phần còn lại ngắn hơn ngưỡng này
CLOSING_MAX_LEN = 40  # dòng lời chào kết phải ngắn ("Trân trọng,") để không nhầm với câu trong thư

_HTML_RE = re.compile(r"<\s*(html|body|div|p|br|table|span|blockquote|a|font|b|i|ul|ol|li)\b", re.I)
_BLOCK_END_RE = re.compile(r"</(div|tr|li|h[1-6]|table)\s*>", re.I)
_BLANK_RUN_RE = re.compile(r"\n{3,}")

# Khối trích dẫn: từ dòng này đến hết thư là thư cũ.
_QUOTE_START_RES = [
	re.compile(r"^\s*-{2,}\s*(original message|thư gốc|tin nhắn gốc)\s*-{2,}\s*$", re.I),
	re.compile(r"^\s*_{10,}\s*$"),  # gạch dưới của Outlook, ngay trước khối From:/Từ:
]
_ON_WROTE_RE = re.compile(r"^\s*On\s.{3,300}$", re.I)
_WROTE_RE = re.compile(r"\bwrote:\s*$", re.I)
_VN_WROTE_RE = re.compile(r"^\s*(Vào|Trong|Ngày)\s.{3,300}(đã viết|đã gửi|viết)\s*:\s*$", re.I)
_HEADER_FROM_RE = re.compile(r"^\s*\**\s*(From|Từ)\s*:\s*\**\s*\S", re.I)
_HEADER_FOLLOW_RE = re.compile(r"^\s*\**\s*(Sent|Date|Đã gửi|Ngày gửi|To|Đến|Subject|Chủ đề)\s*:", re.I)

# Dòng bắt đầu chữ ký.
_SIG_DELIM_RE = re.compile(r"^--\s*$")  # "-- " hoặc "--"
_MOBILE_SIG_RE = re.compile(
	r"^\s*(sent from my\s|sent from (outlook|mail|yahoo|gmail)|get outlook for|gửi từ\s|được gửi từ\s)",
	re.I,
)
_CLOSING_RES = [
	re.compile(p, re.I)
	for p in (
		r"^\s*(trân trọng( cảm ơn)?|thân mến|thân ái|trân trọng và cảm ơn|kính thư|kính chào)\b",
		r"^\s*(best regards|kind regards|warm regards|with regards|regards|sincerely|yours (sincerely|faithfully)|"
		r"thanks\s*(&|and)\s*regards|best wishes)\b",
	)
]


def _to_text(content: str | None) -> str:
	text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
	if _HTML_RE.search(text):
		# html2text không xuống dòng sau </div>, </tr>, </li>...: thêm <br> để các khối không dính nhau
		text = html2text(_BLOCK_END_RE.sub(r"</\1><br>", text))
	return text.replace("\xa0", " ")


def _is_quote_start(lines: list[str], i: int) -> bool:
	line = lines[i]
	if any(r.match(line) for r in _QUOTE_START_RES) or _VN_WROTE_RE.match(line):
		return True
	if _ON_WROTE_RE.match(line):  # "On ... wrote:" có thể xuống dòng ở giữa (tối đa 3 dòng)
		for end in range(i + 1, min(i + 4, len(lines) + 1)):
			window = " ".join(x.strip() for x in lines[i:end])
			if _WROTE_RE.search(window):
				return len(window) < 400
	if _HEADER_FROM_RE.match(line):  # khối "From: ... Sent: ..." của Outlook
		return any(_HEADER_FOLLOW_RE.match(x) for x in lines[i + 1 : i + 5])
	return False


def strip_quotes(text: str) -> str:
	"""Bỏ dòng bắt đầu bằng `>` và cắt từ dấu hiệu thư trích dẫn tới hết thư."""
	lines = text.split("\n")
	kept: list[str] = []
	for i, line in enumerate(lines):
		if _is_quote_start(lines, i):
			break
		if line.lstrip().startswith(">"):
			continue
		kept.append(line)
	return "\n".join(kept)


def split_signature(text: str) -> tuple[str, str]:
	"""Tách (nội dung, chữ ký); chữ ký rỗng nếu không thấy dấu hiệu."""
	lines = text.split("\n")
	cut = None
	for i, line in enumerate(lines):
		if _SIG_DELIM_RE.match(line.rstrip("\n")):
			cut = i
			break
	if cut is None:
		tail_from = max(0, len(lines) - SIGNATURE_MAX_LINES)
		for i in range(tail_from, len(lines)):
			line = lines[i].strip()
			if _MOBILE_SIG_RE.match(line) or (
				len(line) <= CLOSING_MAX_LEN and any(r.match(line) for r in _CLOSING_RES)
			):
				cut = i
				break
	if cut is None:
		return text, ""
	return "\n".join(lines[:cut]), "\n".join(lines[cut:])


def _squeeze(text: str) -> str:
	lines = [re.sub(r"[ \t]+", " ", x).strip() for x in text.split("\n")]
	return _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()


def clean_email(html_or_text: str | None) -> tuple[str, str]:
	"""Trả (nội dung đã làm sạch, chữ ký) của một thư; nội dung giới hạn MAX_CHARS ký tự."""
	text = strip_quotes(_to_text(html_or_text))
	body, signature = split_signature(text)
	return _squeeze(body)[:MAX_CHARS], _squeeze(signature)


def clean_body(html_or_text: str | None) -> str:
	"""Nội dung thư đã bỏ HTML, trích dẫn và chữ ký, gọn khoảng trắng, tối đa 8.000 ký tự."""
	return clean_email(html_or_text)[0]
