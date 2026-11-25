"""Kiểm tra và chuẩn hóa email / SĐT khách gõ khi bot thu thông tin (kịch bản mục 3.6), che bớt khi ghi log.

Dùng lại bộ chuẩn hóa của `identity.normalize` / `identity.extract` (không sửa các tệp đó).
"""

from __future__ import annotations

from omni_crm.identity.extract import extract_contacts
from omni_crm.identity.normalize import is_system_email, normalize_email, normalize_phone

VN_PREFIX = "+84"
VN_MOBILE_LEN = 12  # "+84" + 9 chữ số = số 10 chữ số bắt đầu bằng 0


def validate_email(raw: str | None) -> str | None:
	"""Email hợp lệ (có @ và tên miền, không phải địa chỉ hệ thống) đã chuẩn hóa chữ thường; sai → None."""
	email = normalize_email((raw or "").strip())
	if not email or is_system_email(email):
		return None
	return email


def validate_phone(raw: str | None) -> str | None:
	"""SĐT Việt Nam 10 số (bắt đầu 0 hoặc +84) dạng E.164 `+84xxxxxxxxx`; số máy bàn, nước ngoài, sai → None."""
	phone = normalize_phone(raw)
	if not phone or not phone.startswith(VN_PREFIX) or len(phone) != VN_MOBILE_LEN:
		return None
	return phone


def phone_local(e164: str | None) -> str:
	"""Đổi `+84912345678` thành `0912345678` để hiển thị cho khách; không phải số VN thì trả nguyên."""
	value = (e164 or "").strip()
	return "0" + value[len(VN_PREFIX) :] if value.startswith(VN_PREFIX) else value


def find_email(text: str | None) -> str | None:
	"""Email hợp lệ đầu tiên trong câu khách gõ (vd "email em là a@b.vn ạ"); không có → None."""
	for email in extract_contacts(text)["emails"]:
		if validate_email(email):
			return email
	return None


def find_phone(text: str | None) -> str | None:
	"""SĐT Việt Nam 10 số đầu tiên trong câu khách gõ, dạng E.164; không có → None."""
	for phone in extract_contacts(text)["phones"]:
		if validate_phone(phone):
			return phone
	return None


def mask_phone(value: str | None) -> str:
	"""Che SĐT khi ghi log: giữ 3 số đầu và 3 số cuối (`091****678`)."""
	digits = "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+")
	if len(digits) <= 6:
		return "*" * len(digits)
	return digits[:3] + "*" * (len(digits) - 6) + digits[-3:]


def mask_email(value: str | None) -> str:
	"""Che email khi ghi log: giữ ký tự đầu của phần tên và toàn bộ tên miền (`a***@miyano.vn`)."""
	text = str(value or "").strip()
	if "@" not in text:
		return "*" * len(text)
	local, domain = text.rsplit("@", 1)
	return (local[:1] + "***" if local else "***") + "@" + domain
