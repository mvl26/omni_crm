"""Che dữ liệu cá nhân trước khi ghi log (V3-07)."""

from __future__ import annotations

import re


def mask_phone(value: str | None) -> str:
	"""Che phần giữa số điện thoại: "+84988806848" -> "+8498***6848"; chuỗi quá ngắn thì "***"."""
	text = re.sub(r"[^\d+]", "", str(value or ""))
	if len(text) < 8:
		return "***"
	return f"{text[:5]}***{text[-4:]}"
