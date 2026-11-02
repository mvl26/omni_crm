# ruff: noqa: RUF001
"""Hàm thuần chuẩn hóa cho mô hình nhà thầu (docs/14 mục 2): mã số thuế, tên bệnh viện, tên miền email, tên công ty."""

from __future__ import annotations

import re

from omni_crm.identity.normalize import normalize_email, strip_accents

# Tên miền thư miễn phí / nhà cung cấp chung: không phải tên miền công ty, không dùng để khớp Organization
# (docs/14 mục 2 bước 3). Hằng số theo yêu cầu D1-13 bước 6.
FREE_EMAIL_DOMAINS = frozenset(
	{
		"gmail.com",
		"googlemail.com",
		"yahoo.com",
		"yahoo.com.vn",
		"ymail.com",
		"outlook.com",
		"outlook.com.vn",
		"hotmail.com",
		"hotmail.com.vn",
		"live.com",
		"msn.com",
		"icloud.com",
		"me.com",
		"aol.com",
		"proton.me",
		"protonmail.com",
		"zoho.com",
		"mail.com",
		"gmx.com",
		"yandex.com",
		"qq.com",
		"163.com",
		"vnn.vn",
		"hanoi.vnn.vn",
		"hcm.vnn.vn",
		"email.vn",
		"mail.ru",
		"fpt.vn",
		"vnpt.vn",
		"viettel.com.vn",
		"viettel.vn",
		"yahoo.vn",
		"live.com.vn",
		"hotmail.co.uk",
		"gmail.com.vn",
		"zing.vn",
		"mail.vn",
	}
)

# Website của Organization là mạng xã hội/nền tảng chung: không phải tên miền công ty, không dùng để khớp email.
SOCIAL_DOMAINS = frozenset(
	{
		"facebook.com",
		"fb.com",
		"m.facebook.com",
		"zalo.me",
		"chat.zalo.me",
		"linkedin.com",
		"youtube.com",
		"youtu.be",
		"tiktok.com",
		"twitter.com",
		"x.com",
		"instagram.com",
		"shopee.vn",
		"lazada.vn",
		"sites.google.com",
		"wordpress.com",
		"blogspot.com",
		"wixsite.com",
	}
)

_TAX_DASHES = re.compile(r"[‐-―−‒–—]")
_TAX_DROP = re.compile(r"[\s. ]")
_TAX_RE = re.compile(r"^(\d{10})(?:-?(\d{3}))?$")

# Tiền tố đồng nghĩa của tên bệnh viện/bên mời thầu, áp lên từ ĐẦU TIÊN sau khi bỏ dấu (docs/14 mục 2.2).
_HOSPITAL_PREFIXES = {
	"bv": "benh vien",
	"bvdk": "benh vien da khoa",
	"ttyt": "trung tam y te",
}
_NON_WORD = re.compile(r"[^0-9a-z]+")


class InvalidTaxId(ValueError):
	"""Mã số thuế sai định dạng (không phải 10 số hoặc 10 số-3 số)."""


def normalize_tax_id(raw: str | None) -> str | None:
	"""Mã số thuế chuẩn `0123456789` hoặc `0123456789-001`; chuỗi rỗng -> None; sai định dạng -> InvalidTaxId.

	Bỏ khoảng trắng và dấu chấm; chấp nhận 13 số liền (đưa về dạng có gạch).
	"""
	if raw is None:
		return None
	text = _TAX_DROP.sub("", _TAX_DASHES.sub("-", str(raw)))
	if not text:
		return None
	m = _TAX_RE.match(text)
	if not m:
		raise InvalidTaxId(raw)
	return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)


def tax_id_base(tax_id: str | None) -> str | None:
	"""10 số đầu của mã số thuế (bỏ đuôi chi nhánh); None nếu rỗng."""
	return tax_id.split("-", 1)[0] if tax_id else None


def hospital_key(raw: str | None) -> str:
	"""Khóa tên bệnh viện: chữ thường, bỏ dấu, bỏ dấu câu và khoảng trắng thừa, mở rộng tiền tố (docs/14 mục 2.2)."""
	text = _NON_WORD.sub(" ", strip_accents(raw or "").lower()).strip()
	if not text:
		return ""
	first, _sep, rest = text.partition(" ")
	first = _HOSPITAL_PREFIXES.get(first, first)
	return f"{first} {rest}".strip()


def email_domain(email: str | None) -> str | None:
	"""Tên miền của email đã chuẩn hóa; None nếu không hợp lệ."""
	norm = normalize_email(email)
	return norm.rsplit("@", 1)[1] if norm and "@" in norm else None


def corporate_domain(email: str | None) -> str | None:
	"""Tên miền công ty của email (None nếu là tên miền thư miễn phí/chung hoặc email không hợp lệ)."""
	domain = email_domain(email)
	return None if not domain or domain in FREE_EMAIL_DOMAINS else domain


def website_domain(website: str | None) -> str | None:
	"""Tên miền từ trường website (bỏ giao thức, `www.`, đường dẫn, cổng); None nếu rỗng/không phải tên miền."""
	text = (website or "").strip().lower()
	if not text:
		return None
	text = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", text)
	text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
	text = text.removeprefix("www.").strip(".")
	return text if re.fullmatch(r"[a-z0-9\-]+(\.[a-z0-9\-]+)+", text) else None


def company_name_key(name: str | None) -> str:
	"""Khóa so sánh tên công ty: bỏ dấu, chữ thường, bỏ dấu câu, gộp khoảng trắng (không đổi từ)."""
	return _NON_WORD.sub(" ", strip_accents(name or "").lower()).strip()
