"""Khung báo giá của bot kịch bản (K7, kịch bản mục 3.3): công tắc, đầu vào còn thiếu, chốt chặn con số.

Chưa có bảng giá, chưa nối ERP: bot KHÔNG tự báo giá. `can_auto_quote` luôn sai cho đến khi đủ đầu vào ở mục 3.3.5.
Tệp này không chứa con số giá nào; mọi con số sau này phải đọc từ cấu hình hoặc ERP.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.script import switch

SERVICES = ("VATTU", "SUPPLYCORE", "SPD")
SWITCH_FIELDS = {
	"VATTU": "script_quote_enabled_vattu",
	"SUPPLYCORE": "script_quote_enabled_supplycore",
	"SPD": "script_quote_enabled_spd",
}
SERVICE_ALIASES = {"PHANMEM": "SUPPLYCORE"}  # hậu tố mã ô kịch bản -> mã dịch vụ
REVIEW_FIELD = "script_quote_review_required"

# Đầu vào mục 3.3.5: (khóa, nhãn, dịch vụ cần, trường Omni Settings sẽ giữ giá trị khi được bổ sung).
# Các trường này CHƯA có trên Omni Settings, nên mọi đầu vào đang ở trạng thái thiếu.
INPUTS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
	(
		"price_list",
		"Bảng giá bán vật tư bot được dùng (Price List trên ERP)",
		("VATTU",),
		"script_quote_price_list",
	),
	("vat_note", "Cách ghi VAT trong báo giá", SERVICES, "script_quote_vat_note"),
	("shipping_note", "Cách ghi phí vận chuyển", ("VATTU",), "script_quote_shipping_note"),
	("validity_days", "Số ngày hiệu lực của báo giá", SERVICES, "script_quote_validity_days"),
	("auto_threshold", "Ngưỡng giá trị đơn bot được tự báo giá", ("VATTU",), "script_quote_auto_threshold"),
	(
		"supplycore_criteria",
		"Tiêu chí quy mô SupplyCore",
		("SUPPLYCORE",),
		"script_quote_supplycore_criteria",
	),
	("supplycore_tiers", "Bảng giá theo bậc SupplyCore", ("SUPPLYCORE",), "script_quote_supplycore_tiers"),
	("spd_criteria", "Tiêu chí quy mô SPD", ("SPD",), "script_quote_spd_criteria"),
	("spd_formula", "Cách tính phí SPD", ("SPD",), "script_quote_spd_formula"),
	("pdf_policy", "Báo giá PDF: bot tự gửi hay nhân viên duyệt trước", SERVICES, "script_quote_pdf_policy"),
	("erp_account", "Tài khoản kết nối ERP để đọc giá và tạo Báo giá", SERVICES, "script_quote_erp_account"),
)

_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
_VN_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")


def normalize_service(service: str | None) -> str:
	"""Mã dịch vụ chuẩn (`VATTU`/`SUPPLYCORE`/`SPD`); nhận cả hậu tố `PHANMEM`; không hợp lệ trả chuỗi rỗng."""
	code = (service or "").strip().upper()
	code = SERVICE_ALIASES.get(code, code)
	return code if code in SERVICES else ""


def quote_enabled(service: str | None) -> bool:
	"""Công tắc bot tự báo giá của dịch vụ (`script_quote_enabled_<svc>`, mặc định tắt)."""
	field = SWITCH_FIELDS.get(normalize_service(service))
	return bool(field) and bool(cint(switch.setting(field)))


def review_required() -> bool:
	"""Báo giá phải qua nhân viên duyệt trước khi gửi (`script_quote_review_required`, mặc định bật)."""
	value = switch.setting(REVIEW_FIELD)
	return True if value is None else bool(cint(value))


def _configured(fieldname: str) -> bool:
	try:
		if not frappe.get_meta(switch.SETTINGS).has_field(fieldname):
			return False
	except Exception:
		return False
	return bool(str(switch.setting(fieldname) or "").strip())


def inputs_status() -> dict:
	"""Từng đầu vào mục 3.3.5: `{khóa: {label, services, configured}}` (hiện tất cả đều chưa có)."""
	return {
		key: {"label": _(label), "services": list(services), "configured": _configured(field)}
		for key, label, services, field in INPUTS
	}


def missing_inputs(service: str | None) -> list[str]:
	"""Nhãn các đầu vào còn thiếu cho dịch vụ."""
	svc = normalize_service(service)
	return [v["label"] for v in inputs_status().values() if svc in v["services"] and not v["configured"]]


def can_auto_quote(service: str | None) -> tuple[bool, str]:
	"""Bot có được tự báo giá cho dịch vụ không, kèm lý do; luôn sai khi công tắc tắt hoặc còn thiếu đầu vào."""
	svc = normalize_service(service)
	if not svc:
		return False, _("Chưa rõ dịch vụ cần báo giá")
	if not quote_enabled(svc):
		return False, _("Công tắc bot tự báo giá của dịch vụ đang tắt")
	missing = missing_inputs(svc)
	if missing:
		return False, _("Còn thiếu đầu vào báo giá: {0}").format("; ".join(missing))
	# Đủ công tắc và đầu vào nhưng phần đọc giá từ ERP chưa được xây: vẫn để nhân viên báo giá.
	return False, _("Phần đọc giá từ ERP chưa được xây")


def _value(token: str) -> Decimal | None:
	"""Giá trị một cụm số viết kiểu Việt Nam: dấu chấm ngăn nghìn (`1.500.000`), dấu phẩy thập phân (`12,5`)."""
	token = token.strip(".,")
	if _VN_THOUSANDS.match(token) or ("," in token and "." in token):
		token = token.replace(".", "")
	try:
		return Decimal(token.replace(",", ".")).normalize()
	except InvalidOperation:
		return None


def numbers_in(text: str | None) -> list[str]:
	"""Mọi cụm số xuất hiện trong câu (giữ nguyên cách viết)."""
	return [m.group(0).strip(".,") for m in _NUMBER.finditer(text or "")]


def assert_no_ai_numbers(text: str | None, allowed_numbers: Iterable) -> None:
	"""Chặn câu báo giá có con số không đến từ cấu hình/ERP: số lạ thì ném `frappe.ValidationError`.

	`allowed_numbers` gồm số (int/float/Decimal) hoặc chuỗi đã định dạng (`"1.500.000"`, `"17/11/2026"`).
	"""
	allowed: set[Decimal] = set()
	for item in allowed_numbers or ():
		if isinstance(item, bool):
			continue
		if isinstance(item, int | float | Decimal):
			allowed.add(Decimal(str(item)).normalize())
			continue
		allowed |= {_value(token) for token in numbers_in(str(item))}
	strange = [t for t in numbers_in(text) if _value(t) not in allowed]
	if strange:
		# Không ghi nội dung câu vào log; chỉ báo số lượng con số lạ.
		frappe.throw(
			_("Câu báo giá có {0} con số không lấy từ bảng giá đã duyệt").format(len(strange)),
			frappe.ValidationError,
		)
