# ruff: noqa: RUF001
"""Phân tích giá trị ô của tệp danh sách nhà thầu (docs/14 mục 5): hàm thuần, không đụng DB.

Mọi hàm `parse_*` nhận dict {mã trường: giá trị ô} và trả dict đã chuẩn hóa, hoặc ném `RowError` với lý do tiếng
Việt để ghi vào tệp lỗi. Giá trị ô là DỮ LIỆU: không bao giờ được hiểu là công thức hay lệnh.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from omni_crm.identity.normalize import normalize_email, normalize_phone
from omni_crm.prospect.normalize import InvalidTaxId, normalize_tax_id

EXAMPLE_TAX_ID = "0109999999"  # mã số thuế của dòng ví dụ (dòng 3) trong mẫu

# Danh mục của các ô chọn. Phải khớp Custom Field / DocType (có test đối chiếu: test_prospect_import::TestOptions).
REGIONS = ("Miền Bắc", "Miền Trung", "Miền Nam")
SOFTWARE = ("MISA", "Fast", "Bravo", "Excel", "Phần mềm khác", "Chưa rõ")
PACKAGES = ("Chưa rõ", "Gói Điều phối", "Gói Trọn gói")
CATEGORIES = ("VTYT tiêu hao", "Hóa chất – sinh phẩm", "Thuốc", "Trang thiết bị", "Khác")
AWARD_ROLES = ("Nhà thầu độc lập", "Liên danh – đứng đầu", "Liên danh – thành viên")
CONTACT_ROLES = ("Người quyết định", "Phụ trách đấu thầu", "Kế toán – công nợ", "Mua hàng", "Khác")
HAS_ZALO = ("Có", "Không", "Chưa rõ")
CONSENT = ("Có", "Chưa hỏi", "Từ chối")
LEAD_STAGES = ("Tiềm năng", "Đã liên hệ", "Đã trao đổi nhu cầu")  # trạng thái của CRM Lead
DEAL_STAGES = ("Đề xuất gói", "Đàm phán", "Ký hợp đồng")  # trạng thái của CRM Deal (cần chuyển Lead -> Deal)
NOT_FIT = "Không phù hợp"
STAGES = (*LEAD_STAGES, *DEAL_STAGES, NOT_FIT)

# Cột nhập của NhaThau -> (trường CRM Organization, kiểu, ràng buộc). `tax_id`, `name` xử lý riêng.
ORG_COLUMNS: dict[str, tuple[str, str, Any]] = {
	"short_name": ("omni_short_name", "text", 140),
	"address": ("omni_address", "text", 500),
	"province": ("omni_province", "text", 140),
	"region": ("omni_region", "select", REGIONS),
	"website": ("website", "text", 140),
	"company_phone": ("omni_company_phone", "text", 50),
	"company_email": ("omni_company_email", "email", None),
	"annual_revenue_bn": ("omni_revenue_bn", "float", None),
	"revenue_year": ("omni_revenue_year", "year", None),
	"accounting_software": ("omni_accounting_software", "select", SOFTWARE),
	"est_item_count": ("omni_est_item_count", "int", None),
	"data_source": ("omni_data_source", "text", 140),
	"collected_on": ("omni_collected_on", "date", None),
	"interested_package": ("omni_interested_package", "select", PACKAGES),
}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PLACEHOLDER = re.compile(r"^\[[^\]]*\]$")  # ô mẫu còn "[đường dẫn ...]" coi như trống
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y")
_NUMBER = re.compile(r"^\d+(\.\d+)?$")
FORMULA_LEAD = "=+-@\t\r"


class RowError(Exception):
	"""Dòng không nhập được; thông điệp là lý do tiếng Việt hiển thị cho người sửa file."""


def clean_text(value: Any, limit: int = 255, *, multiline: bool = False) -> str:
	"""Văn bản sạch từ ô: bỏ ký tự điều khiển, gộp khoảng trắng, cắt độ dài, gỡ ký tự mở công thức ở đầu.

	Dấu `'` do tệp lỗi thêm để chặn công thức (xem `import_errors.safe_cell`) được gỡ để nhập lại được.
	"""
	if value is None or isinstance(value, bool):
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	if isinstance(value, datetime | date):
		value = value.strftime("%Y-%m-%d")
	text = _CONTROL.sub("", str(value))
	if multiline:
		text = "\n".join(line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
		text = re.sub(r"\n{3,}", "\n\n", text).strip()
	else:
		text = re.sub(r"\s+", " ", text).strip()
	if text[:1] == "'" and text[1:2] and text[1:2] in FORMULA_LEAD:
		text = text[1:]
	text = (
		text.lstrip("=@ \t") if text[:1] in "=@" else text
	)  # ô bắt đầu bằng = hoặc @: không giữ làm công thức
	if _PLACEHOLDER.match(text):
		return ""
	return text[:limit]


def parse_tax_id(value: Any, *, required: bool = True) -> str | None:
	"""Mã số thuế chuẩn hóa. Ô số (Excel làm mất số 0 đầu) được đệm 0 cho đủ 10 số."""
	if isinstance(value, int | float) and not isinstance(value, bool) and float(value).is_integer():
		value = str(int(value)).zfill(10) if int(value) < 10**10 else str(int(value))
	try:
		tax = normalize_tax_id(clean_text(value, 40))
	except InvalidTaxId:
		raise RowError(
			f"Mã số thuế «{clean_text(value, 40)}» sai định dạng: phải gồm 10 số, hoặc 10 số-3 số (chi nhánh)"
		) from None
	if required and not tax:
		raise RowError("Thiếu mã số thuế")
	return tax


def _label(label: str) -> str:
	return f"«{label}»"


def parse_select(value: Any, options: tuple[str, ...], label: str) -> str | None:
	"""Giá trị thuộc danh mục (so không phân biệt hoa thường, khoảng trắng); trống -> None; lạ -> RowError."""
	text = clean_text(value, 100)
	if not text:
		return None
	for option in options:
		if option.casefold() == text.casefold():
			return option
	raise RowError(f"Cột {_label(label)} có giá trị «{text}» không có trong danh mục: {', '.join(options)}")


def parse_date(value: Any, label: str) -> date | None:
	"""Ngày từ ô ngày hoặc chuỗi (yyyy-mm-dd, dd/mm/yyyy, dd-mm-yyyy); trống -> None; sai -> RowError."""
	if value is None or (isinstance(value, str) and not value.strip()):
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = clean_text(value, 30)
	for fmt in _DATE_FORMATS:
		try:
			return datetime.strptime(text, fmt).date()
		except ValueError:
			continue
	raise RowError(f"Cột {_label(label)} có ngày «{text}» không đọc được (dùng dd/mm/yyyy)")


def parse_number(value: Any, label: str, *, integer: bool = False) -> float | int | None:
	"""Số không âm từ ô số hoặc chuỗi chỉ gồm chữ số; trống -> None; sai -> RowError."""
	if value is None or (isinstance(value, str) and not value.strip()):
		return None
	if isinstance(value, bool):
		raise RowError(f"Cột {_label(label)} không phải số")
	if isinstance(value, int | float):
		number = float(value)
	else:
		text = clean_text(value, 40).replace(" ", "")
		if not _NUMBER.match(text):
			raise RowError(f"Cột {_label(label)} có giá trị «{text}» không phải số")
		number = float(text)
	if number != number or number in (float("inf"), float("-inf")) or number < 0:  # NaN, vô cực, âm
		raise RowError(f"Cột {_label(label)} phải là số không âm")
	if integer:
		if not number.is_integer():
			raise RowError(f"Cột {_label(label)} phải là số nguyên")
		return int(number)
	return number


def parse_email(value: Any, label: str) -> str | None:
	"""Email chuẩn hóa (chữ thường); trống -> None; sai -> RowError."""
	text = clean_text(value, 140)
	if not text:
		return None
	norm = normalize_email(text)
	if not norm:
		raise RowError(f"Cột {_label(label)} có email «{text}» không hợp lệ")
	return norm


def parse_phone(value: Any, label: str) -> tuple[str, str] | None:
	"""(SĐT như người nhập, SĐT E.164); trống -> None; sai -> RowError."""
	text = clean_text(value, 40)
	if not text:
		return None
	norm = normalize_phone(text)
	if not norm:
		raise RowError(f"Cột {_label(label)} có số điện thoại «{text}» không hợp lệ")
	return text, norm


def split_name(full_name: str) -> tuple[str, str]:
	"""(first_name, last_name) sao cho họ tên ghép lại đúng thứ tự: từ cuối là last_name, phần đầu là first_name.

	Frappe ghép `full_name` = first + middle + last; đặt phần đầu vào `first_name` và từ cuối vào `last_name` giữ đúng
	cách đọc tiếng Việt "Nguyễn Văn An" (docs/12 mục D, V2-03).
	"""
	words = full_name.split()
	if len(words) <= 1:
		return full_name, ""
	return " ".join(words[:-1]), words[-1]


def parse_organization(values: dict[str, Any]) -> dict[str, Any]:
	"""Dòng NhaThau -> {tax_id, name, fields: {trường CRM: giá trị}, stage, owner_email, notes}."""
	tax = parse_tax_id(values.get("tax_id"))
	name = clean_text(values.get("name"), 140)
	if not name:
		raise RowError("Thiếu tên nhà thầu")
	fields: dict[str, Any] = {}
	for code, (fieldname, kind, extra) in ORG_COLUMNS.items():
		raw = values.get(code)
		if kind == "text":
			value: Any = clean_text(raw, extra) or None
		elif kind == "select":
			value = parse_select(raw, extra, code)
		elif kind == "email":
			value = parse_email(raw, code)
		elif kind == "float":
			value = parse_number(raw, code)
		elif kind == "year":
			value = parse_number(raw, code, integer=True)
			if value is not None and not 1990 <= value <= 2100:
				raise RowError(f"Cột «{code}» có năm «{value}» ngoài khoảng 1990-2100")
		elif kind == "int":
			value = parse_number(raw, code, integer=True)
		else:
			value = parse_date(raw, code)
		if value is not None:
			fields[fieldname] = value
	stage = parse_select(values.get("stage"), STAGES, "stage")
	owner = clean_text(values.get("owner_email"), 140).lower() or None
	return {
		"tax_id": tax,
		"name": name,
		"fields": fields,
		"stage": stage,
		"owner_email": owner,
		"notes": clean_text(values.get("notes"), 2000, multiline=True),
	}


def parse_award(values: dict[str, Any]) -> dict[str, Any]:
	"""Dòng GoiThau -> dict gói thầu đã chuẩn hóa (tax_id, hospital, category, award_date bắt buộc)."""
	tax = parse_tax_id(values.get("tax_id"))
	hospital = clean_text(values.get("hospital"), 140)
	if not hospital:
		raise RowError("Thiếu tên bệnh viện / bên mời thầu")
	category = parse_select(values.get("category"), CATEGORIES, "category")
	if not category:
		raise RowError("Thiếu loại hàng")
	award_date = parse_date(values.get("award_date"), "award_date")
	if not award_date:
		raise RowError("Thiếu ngày phê duyệt KQLCNT")
	return {
		"tax_id": tax,
		"notice_no": clean_text(values.get("notice_no"), 140),
		"package_name": clean_text(values.get("package_name"), 500) or None,
		"hospital": hospital,
		"hospital_province": clean_text(values.get("hospital_province"), 140) or None,
		"category": category,
		"award_date": award_date,
		"award_value": parse_number(values.get("award_value"), "award_value"),
		"item_count": parse_number(values.get("item_count"), "item_count", integer=True),
		"duration_months": parse_number(values.get("duration_months"), "duration_months", integer=True),
		"role": parse_select(values.get("role"), AWARD_ROLES, "role"),
		"source_url": clean_text(values.get("source_url"), 140) or None,
	}


def parse_contact(values: dict[str, Any]) -> dict[str, Any]:
	"""Dòng NguoiLienHe -> dict người liên hệ đã chuẩn hóa (tax_id, full_name, info_source bắt buộc)."""
	tax = parse_tax_id(values.get("tax_id"))
	full_name = clean_text(values.get("full_name"), 140)
	if not full_name:
		raise RowError("Thiếu họ tên")
	info_source = clean_text(values.get("info_source"), 140)
	if not info_source:
		raise RowError("Thiếu nguồn thông tin (info_source): bắt buộc để biết lấy dữ liệu cá nhân từ đâu")
	return {
		"tax_id": tax,
		"full_name": full_name,
		"title": clean_text(values.get("title"), 140) or None,
		"contact_role": parse_select(values.get("contact_role"), CONTACT_ROLES, "contact_role"),
		"phone": parse_phone(values.get("phone"), "phone"),
		"email": parse_email(values.get("email"), "email"),
		"has_zalo": parse_select(values.get("has_zalo"), HAS_ZALO, "has_zalo"),
		"info_source": info_source,
		"consent": parse_select(values.get("consent"), CONSENT, "consent"),
		"consent_date": parse_date(values.get("consent_date"), "consent_date"),
		"consent_method": clean_text(values.get("consent_method"), 140) or None,
		"notes": clean_text(values.get("notes"), 2000, multiline=True),
	}
