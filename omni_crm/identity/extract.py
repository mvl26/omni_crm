"""Trích thông tin liên hệ từ nội dung tin (tín hiệu TRUNG BÌNH, docs/04 mục 1.3)."""

from __future__ import annotations

import re

from omni_crm.identity.normalize import is_system_email, normalize_email, normalize_phone

# Số bắt đầu bằng 0 / 84 / +84 / 0084, 8-10 chữ số tiếp theo, cho phép ngăn bằng 1 dấu cách, chấm, gạch.
# Lookahead (?!\d) khiến regex lùi về độ dài đúng khi hai số đứng cạnh nhau.
_PHONE_RE = re.compile(r"(?<!\d)(?:(?:\+|00)[ ]?)?(?:84|0)(?:[ .\-]?\d){8,10}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def _phone_from_run(run: str) -> str | None:
	digits = re.sub(r"[^\d+]", "", run)
	for cut in range(len(digits), 8, -1):  # cắt bớt chữ số thừa ở cuối nếu cả chuỗi không hợp lệ
		phone = normalize_phone(digits[:cut])
		if phone:
			return phone
	return None


def extract_contacts(text: str | None, system_emails: list[str] | tuple[str, ...] = ()) -> dict:
	"""Trích {"phones": [E.164...], "emails": [...]} đã chuẩn hóa, bỏ trùng, giữ thứ tự xuất hiện."""
	phones: list[str] = []
	emails: list[str] = []
	if not text:
		return {"phones": phones, "emails": emails}
	for m in _EMAIL_RE.finditer(text):
		email = normalize_email(m.group(0))
		if email and not is_system_email(email, system_emails) and email not in emails:
			emails.append(email)
	# Bỏ phần email trước khi tìm số để chữ số trong địa chỉ không bị nhận nhầm.
	rest = _EMAIL_RE.sub(" ", text)
	for m in _PHONE_RE.finditer(rest):
		phone = _phone_from_run(m.group(0))
		if phone and phone not in phones:
			phones.append(phone)
	return {"phones": phones, "emails": emails}
