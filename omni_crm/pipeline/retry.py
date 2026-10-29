"""Đánh dấu thử lại và che bí mật khỏi log."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import frappe
from frappe.utils import now_datetime

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = "1,5"
MIN_PASSWORD_LEN = 6
LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=.]{32,}")
# SĐT Việt Nam (0xxxxxxxxx, +84..., 0084...) và email: dữ liệu cá nhân của khách, không được vào log.
EMAIL_PII = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
PHONE_PII = re.compile(r"(?<!\d)(?:(?:\+|00)[ ]?)?(?:84|0)(?:[ .\-]?\d){8,10}(?!\d)")
LAST_ERROR_MAX = 500
INBOUND = "Omni Inbound Message"


class TransientError(Exception):
	"""Lỗi tạm thời (mạng, khóa DB, timeout): thử lại được."""


def is_transient(exc: BaseException) -> bool:
	"""Lỗi này có phải loại tạm thời không (TransientError, mạng, QueryTimeout, TimestampMismatch, deadlock)."""
	if isinstance(
		exc,
		TransientError
		| ConnectionError
		| TimeoutError
		| frappe.QueryTimeoutError
		| frappe.TimestampMismatchError,
	):
		return True
	if isinstance(exc, frappe.QueryDeadlockError):
		return True
	try:
		import requests

		if isinstance(exc, requests.ConnectionError | requests.Timeout):
			return True
	except ImportError:  # pragma: no cover
		pass
	return False


def _password_values() -> list[str]:
	"""Giá trị Password đang cấu hình ở Omni Channel (mọi kênh) và Omni Settings."""
	values: list[str] = []
	for dt in ("Omni Channel", "Omni Settings"):
		fields = [f.fieldname for f in frappe.get_meta(dt).fields if f.fieldtype == "Password"]
		if not fields:
			continue
		names = [None] if frappe.get_meta(dt).issingle else frappe.get_all(dt, pluck="name")
		for name in names:
			doc = frappe.get_doc(dt) if name is None else frappe.get_doc(dt, name)
			for fn in fields:
				try:
					val = doc.get_password(fn, raise_exception=False)
				except Exception:
					val = None
				if val and len(val) >= MIN_PASSWORD_LEN:
					values.append(val)
	return sorted(set(values), key=len, reverse=True)


def redact(text: str | None) -> str:
	"""Che các giá trị Password đang lưu, chuỗi dài giống token/khóa (>= 32 ký tự), email và SĐT."""
	if not text:
		return ""
	out = str(text)
	for val in _password_values():
		out = out.replace(val, "[đã che]")
	out = EMAIL_PII.sub("[đã che email]", out)
	out = PHONE_PII.sub("[đã che SĐT]", out)
	return LONG_TOKEN.sub("[đã che]", out)


def backoff_minutes() -> list[int]:
	"""Danh sách phút chờ sau lỗi thứ n, từ Omni Settings.retry_backoff_minutes."""
	raw = frappe.db.get_single_value("Omni Settings", "retry_backoff_minutes") or DEFAULT_BACKOFF
	out = [int(p) for p in str(raw).split(",") if p.strip().isdigit()]
	return out or [1, 5]


def max_attempts() -> int:
	"""Số lần thử tối đa từ Omni Settings (mặc định 3)."""
	return int(frappe.db.get_single_value("Omni Settings", "max_attempts") or DEFAULT_MAX_ATTEMPTS)


def next_delay(attempts: int, backoff: list[int]) -> timedelta:
	"""Thời gian chờ sau lỗi thứ `attempts` (phần tử n-1; quá dài thì dùng phần tử cuối)."""
	return timedelta(minutes=backoff[min(attempts, len(backoff)) - 1])


def mark_retry(
	docs: list[Any], error: BaseException | str, permanent_hint: bool = False, doctype: str = INBOUND
) -> None:
	"""Tăng attempts; còn lượt -> Retry + next_attempt_at, hết lượt -> Manual. Không commit.

	Dùng chung cho tin vào và tin ra (docs có `name`; `doctype` lấy từ doc nếu có). Số lần thử đọc lại
	từ DB. permanent_hint=True chỉ đánh dấu "[loi du lieu]" ở last_error (lỗi logic/dữ liệu); vẫn thử
	lại theo bảng docs/03 mục 5 vì lỗi đó có thể hết sau khi vá code.
	"""
	limit, backoff, now = max_attempts(), backoff_minutes(), now_datetime()
	message = redact(str(error))
	if permanent_hint:
		message = "[loi du lieu] " + message
	message = message[:LAST_ERROR_MAX]
	for doc in docs:
		dt = (doc.get("doctype") if hasattr(doc, "get") else None) or doctype
		name = doc.get("name") if hasattr(doc, "get") else doc
		attempts = (frappe.db.get_value(dt, name, "attempts") or 0) + 1
		if attempts >= limit:
			values = {"status": "Manual", "next_attempt_at": None}
		else:
			values = {"status": "Retry", "next_attempt_at": now + next_delay(attempts, backoff)}
		values.update({"attempts": attempts, "last_error": message})
		frappe.db.set_value(dt, name, values, update_modified=False)
