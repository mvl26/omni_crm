"""Chuẩn hóa số điện thoại, email, tên người gửi (docs/04 mục 1)."""

from __future__ import annotations

import re
import unicodedata
from email.utils import parseaddr

import phonenumbers

# Bảng đổi đầu số 11 số cũ sang 10 số (chuyển đổi toàn quốc năm 2018), docs/04 mục 1.1.
# Khóa: 4 chữ số đầu của số cũ (gồm số 0 đầu); giá trị: 3 chữ số đầu của số mới (gồm số 0 đầu).
# Số cũ có 11 chữ số; 7 chữ số cuối giữ nguyên khi đổi.
LEGACY_PREFIXES: dict[str, str] = {
	"0120": "070",
	"0121": "079",
	"0122": "077",
	"0123": "083",
	"0124": "084",
	"0125": "085",
	"0126": "076",
	"0127": "081",
	"0128": "078",
	"0129": "082",
	"0162": "032",
	"0163": "033",
	"0164": "034",
	"0165": "035",
	"0166": "036",
	"0167": "037",
	"0168": "038",
	"0169": "039",
	"0186": "056",
	"0188": "058",
	"0199": "059",
}

# Phần trước @ của địa chỉ hệ thống (không phải người thật): không dùng để khớp khách.
SYSTEM_EMAIL_LOCALPARTS = frozenset(
	{"no-reply", "noreply", "no_reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster"}
)

_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}$")
_PHONE_CHARS_RE = re.compile(r"^\+?[\d\s.\-()]+$")

# Từ chung chung (đã bỏ dấu, chữ thường): tên chỉ gồm các từ này hoặc số là "không có thông tin".
GENERIC_NAME_WORDS = frozenset(
	{
		"anh",
		"chi",
		"em",
		"co",
		"chu",
		"bac",
		"ban",
		"ong",
		"ba",
		"khach",
		"hang",
		"zalo",
		"oa",
		"user",
		"nguoi",
		"dung",
		"customer",
		"guest",
		"unknown",
		"unnamed",
		"lead",
		"mr",
		"mrs",
		"ms",
	}
)


def _legacy_to_new(local: str) -> str:
	"""Đổi số nội địa có số 0 đầu theo bảng đầu số cũ (chỉ khi đúng 11 chữ số)."""
	if len(local) == 11 and local[:4] in LEGACY_PREFIXES:
		return LEGACY_PREFIXES[local[:4]] + local[4:]
	return local


def normalize_phone(raw: str | None) -> str | None:
	"""Chuẩn hóa số điện thoại về E.164 (+84...); None nếu rỗng, lẫn chữ hoặc không hợp lệ."""
	if raw is None:
		return None
	text = str(raw).strip().lstrip("( ")
	if not text or not _PHONE_CHARS_RE.match(text):
		return None  # rỗng hoặc lẫn chữ/ký tự lạ
	international = text.startswith("+")
	digits = re.sub(r"\D", "", text)
	if not digits:
		return None
	if not international and digits.startswith("00"):
		international, digits = True, digits[2:]
	if international:
		if digits.startswith("84"):
			candidate = "+84" + _legacy_to_new("0" + digits[2:])[1:]
		else:
			candidate = "+" + digits
	elif digits.startswith("0"):
		candidate = "+84" + _legacy_to_new(digits)[1:]
	elif digits.startswith("84") and len(digits) in (11, 12):
		candidate = "+84" + _legacy_to_new("0" + digits[2:])[1:]
	else:
		return None  # không đoán số thiếu số 0 đầu/mã nước
	try:
		number = phonenumbers.parse(candidate, None)
	except phonenumbers.NumberParseException:
		return None
	if not phonenumbers.is_valid_number(number):
		return None
	return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def is_mobile_phone(e164: str | None) -> bool:
	"""Số E.164 này có phải di động không."""
	if not e164:
		return False
	try:
		number = phonenumbers.parse(e164, None)
	except phonenumbers.NumberParseException:
		return False
	return phonenumbers.number_type(number) in (
		phonenumbers.PhoneNumberType.MOBILE,
		phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
	)


def normalize_email(raw: str | None) -> str | None:
	"""Chữ thường, bỏ khoảng trắng; giữ dấu chấm và +tag. None nếu rỗng hoặc sai dạng."""
	if not raw:
		return None
	text = parseaddr(str(raw))[1] or str(raw)  # chấp nhận dạng "Tên <a@b.vn>"
	text = re.sub(r"\s+", "", text).lower()
	return text if _EMAIL_RE.match(text) else None


def is_system_email(email: str | None, extra: list[str] | tuple[str, ...] = ()) -> bool:
	"""Địa chỉ hệ thống (no-reply, mailer-daemon...) hoặc nằm trong danh sách cấu hình `extra`."""
	value = normalize_email(email)
	if not value:
		return False
	local = value.split("@", 1)[0]
	if local in SYSTEM_EMAIL_LOCALPARTS:
		return True
	extras = {e.strip().lower() for e in extra if e and e.strip()}
	return value in extras or local in extras


def normalize_sender(channel_type: str, raw: str | None) -> str:
	"""Chuẩn hóa mã người gửi để tạo identity_key (SĐT -> E.164, email -> chữ thường)."""
	value = (raw or "").strip()
	if channel_type == "Email":
		return normalize_email(value) or value.lower()
	if channel_type == "Telephony":
		return normalize_phone(value) or value
	return value


def strip_accents(text: str | None) -> str:
	"""Bỏ dấu tiếng Việt (gồm đ/Đ), chữ thường."""
	if not text:
		return ""
	text = text.replace("đ", "d").replace("Đ", "D")
	text = unicodedata.normalize("NFD", text)
	return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


def _name_tokens(name: str | None) -> list[str]:
	return re.findall(r"[a-z0-9]+", strip_accents(name))


# Tên mặc định do hệ thống đặt ("Khách Zalo OA 4821", "Khách Email .com"): luôn là tên giữ chỗ.
_DEFAULT_NAME_RE = re.compile(r"^khach (zalo oa|zalo|email|telephony)\b")


def informative_tokens(name: str | None) -> list[str]:
	"""Các từ mang thông tin của tên: bỏ từ chung chung (anh, chị, khách...) và số."""
	if _DEFAULT_NAME_RE.match(strip_accents(name).strip()):
		return []
	return [t for t in _name_tokens(name) if t not in GENERIC_NAME_WORDS and not t.isdigit()]


def is_placeholder_name(name: str | None) -> bool:
	"""Tên rỗng hoặc chỉ gồm từ chung chung/số ("Khách", "Zalo user", "Khách Zalo OA 4821")."""
	return not informative_tokens(name)


def is_informative_name(name: str | None) -> bool:
	"""Tên đủ thông tin để so mâu thuẫn: có ít nhất 2 từ riêng (docs/04 mục 3.1)."""
	return len(informative_tokens(name)) >= 2


def name_similarity(a: str | None, b: str | None) -> float:
	"""Độ giống 0..1 giữa hai tên: bỏ dấu, chữ thường, so theo tập từ (không phụ thuộc thứ tự)."""
	ta, tb = set(_name_tokens(a)), set(_name_tokens(b))
	if not ta or not tb:
		return 0.0
	return len(ta & tb) / max(len(ta), len(tb))
