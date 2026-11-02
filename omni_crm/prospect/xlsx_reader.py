# ruff: noqa: RUF001
"""Đọc an toàn tệp .xlsx danh sách nhà thầu (docs/14 mục 5, mẫu `mau/Mau_DanhSach_NhaThau_TiemNang.xlsx`).

Tệp là dữ liệu do người ngoài mô-đun tạo, coi như không đáng tin: kiểm đuôi, kích thước, cấu trúc zip (chặn bom nén,
macro), mở bằng openpyxl `read_only` + `data_only` (không tính công thức, không theo liên kết ngoài), giới hạn số dòng.
Cột xác định theo MÃ TRƯỜNG ở dòng 2, không theo vị trí.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from omni_crm.prospect.parsing import EXAMPLE_TAX_ID, clean_text

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_ZIP_ENTRIES = 500
MAX_ROWS_SCANNED = 60_000  # tổng số dòng đọc mỗi sheet (kể cả dòng trống do công thức kéo dài)
MAX_DATA_ROWS = {"NhaThau": 5_000, "GoiThau": 25_000, "NguoiLienHe": 15_000}
EXAMPLE_ROW = 3
FIRST_DATA_ROW = 4

# Sheet -> mã trường bắt buộc phải CÓ ở dòng 2 (thiếu thì dừng, docs/14 mục 5).
REQUIRED_CODES = {
	"NhaThau": ("tax_id", "name"),
	"GoiThau": ("tax_id", "hospital", "category", "award_date"),
	"NguoiLienHe": ("tax_id", "full_name", "info_source"),
}
CALC_PREFIX = "calc_"
ERROR_CODE = "error_reason"  # cột lý do lỗi của tệp lỗi: importer bỏ qua khi nhập lại

# Nhãn dòng ThamSo -> tham số fit (Omni Settings) để so với ThamSo của tệp.
THAMSO_LABELS = {
	"khoảng thời gian xét gói thầu": "fit_period_months",
	"tiêu chí bắt buộc: số bệnh viện tối thiểu": "fit_min_hospitals",
	"tiêu chí bắt buộc: số mã hàng tối thiểu": "fit_min_items",
	"số bệnh viện đạt điểm tối đa": "fit_max_hospitals",
	"số mã hàng đạt điểm tối đa": "fit_max_items",
	"tổng giá trị trúng thầu đạt điểm tối đa": "fit_max_value_bn",
	"trọng số: số bệnh viện": "fit_w_hospitals",
	"trọng số: số mã hàng": "fit_w_items",
	"trọng số: tổng giá trị trúng thầu": "fit_w_value",
	"trọng số: còn hợp đồng đang thực hiện": "fit_w_active",
}
THAMSO_THRESHOLD_PREFIX = "ngưỡng hạng a"
TONGHOP_LABELS = {
	"số nhà thầu trong danh sách": "total",
	"đạt tiêu chí bắt buộc": "meets",
	"hạng a": "A",
	"hạng b": "B",
	"hạng c (chưa đạt)": "C",
}


class WorkbookError(Exception):
	"""Tệp không dùng được (sai định dạng, quá lớn, thiếu sheet/mã trường); thông điệp tiếng Việt."""


@dataclass
class SheetRow:
	"""Một dòng dữ liệu: số dòng Excel và giá trị theo mã trường (chỉ cột nhập, không có `calc_*`)."""

	number: int
	values: dict[str, Any]
	calc: dict[str, Any] = field(default_factory=dict)


@dataclass
class SheetData:
	"""Dữ liệu một sheet: nhãn dòng 1, mã dòng 2, dòng ví dụ (3) và các dòng dữ liệu (>= 4, bỏ dòng trống)."""

	name: str
	labels: list[Any]
	codes: list[str]
	example: dict[str, Any]
	rows: list[SheetRow]


@dataclass
class WorkbookData:
	sheets: dict[str, SheetData]
	today: date | None = None  # "Ngày tính" của ThamSo (giá trị đã tính sẵn trong tệp)
	params: dict[str, float] = field(default_factory=dict)
	scored_categories: set[str] | None = (
		None  # loại hàng "Tính điểm = 1" ở sheet DanhMuc (None nếu không đọc được)
	)
	tonghop: dict[str, Any] = field(default_factory=dict)
	warnings: list[str] = field(default_factory=list)


def check_file(path: str | Path) -> None:
	"""Kiểm tệp trước khi mở: đuôi .xlsx, kích thước, đúng là zip OOXML, không macro, không bom nén."""
	p = Path(path)
	if p.suffix.lower() != ".xlsx":
		raise WorkbookError("Chỉ nhận tệp .xlsx (không nhận .xlsm có macro, .xls, .csv)")
	if not p.is_file():
		raise WorkbookError("Không tìm thấy tệp")
	if p.stat().st_size > MAX_FILE_BYTES:
		raise WorkbookError(f"Tệp quá lớn (tối đa {MAX_FILE_BYTES // 1024 // 1024} MB)")
	try:
		with zipfile.ZipFile(p) as z:
			infos = z.infolist()
			if len(infos) > MAX_ZIP_ENTRIES:
				raise WorkbookError("Tệp có cấu trúc bất thường (quá nhiều thành phần)")
			if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
				raise WorkbookError("Tệp giải nén quá lớn (nghi bom nén)")
			names = {i.filename.lower() for i in infos}
			if "[content_types].xml" not in names or "xl/workbook.xml" not in names:
				raise WorkbookError("Tệp không phải sổ tính Excel (.xlsx) hợp lệ")
			if any(n.endswith("vbaproject.bin") or n.startswith("xl/macrosheets/") for n in names):
				raise WorkbookError("Tệp có macro: không nhận")
	except zipfile.BadZipFile:
		raise WorkbookError("Tệp không phải sổ tính Excel (.xlsx) hợp lệ") from None


def _read_sheet(ws, name: str) -> SheetData:
	labels: list[Any] = []
	codes: list[str] = []
	example: dict[str, Any] = {}
	rows: list[SheetRow] = []
	limit = MAX_DATA_ROWS[name]
	for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
		if idx > MAX_ROWS_SCANNED:
			raise WorkbookError(f"Sheet {name} có quá {MAX_ROWS_SCANNED} dòng")
		if idx == 1:
			labels = list(row)
		elif idx == 2:
			codes = [clean_text(c, 60).lower() for c in row]
			if len({c for c in codes if c}) != len([c for c in codes if c]):
				raise WorkbookError(f"Sheet {name}: dòng 2 có mã trường bị lặp")
			missing = [c for c in REQUIRED_CODES[name] if c not in codes]
			if missing:
				raise WorkbookError(f"Sheet {name} thiếu mã trường bắt buộc ở dòng 2: {', '.join(missing)}")
		else:
			values = {c: row[i] for i, c in enumerate(codes) if c and i < len(row)}
			if idx == EXAMPLE_ROW:
				example = values
				continue
			inputs = {c: v for c, v in values.items() if not c.startswith(CALC_PREFIX) and c != ERROR_CODE}
			# Dòng trống xác định theo cột NHẬP; cột calc_* của dòng chưa dùng vẫn có giá trị 0 do công thức.
			if all(clean_text(v, 5) == "" for v in inputs.values()):
				continue
			if len(rows) >= limit:
				raise WorkbookError(f"Sheet {name} có quá {limit} dòng dữ liệu")
			calc = {c: v for c, v in values.items() if c.startswith(CALC_PREFIX)}
			rows.append(SheetRow(idx, inputs, calc))
	if not codes:
		raise WorkbookError(f"Sheet {name} không có dòng mã trường (dòng 2)")
	return SheetData(name, labels, codes, example, rows)


def _num(value: Any) -> float | None:
	return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _read_thamso(ws, data: WorkbookData) -> None:
	for row in ws.iter_rows(min_row=1, max_row=40, values_only=True):
		label = clean_text(row[0], 120).lower() if row and row[0] is not None else ""
		value = row[1] if len(row) > 1 else None
		if label == "ngày tính":
			data.today = (
				value.date() if hasattr(value, "date") else (value if isinstance(value, date) else None)
			)
		elif label in THAMSO_LABELS and _num(value) is not None:
			data.params[THAMSO_LABELS[label]] = _num(value)
		elif label.startswith(THAMSO_THRESHOLD_PREFIX) and _num(value) is not None:
			data.params["fit_tier_a"] = _num(value)


def _read_danhmuc(ws, data: WorkbookData) -> None:
	"""Loại hàng có "Tính điểm = 1" ở sheet DanhMuc (cột "Loại hàng" và cột liền sau)."""
	rows = list(ws.iter_rows(min_row=1, max_row=40, values_only=True))
	for r_idx, row in enumerate(rows):
		heads = [clean_text(c, 60).lower() for c in row]
		if "loại hàng" not in heads:
			continue
		col = heads.index("loại hàng")
		scored: set[str] = set()
		for other in rows[r_idx + 1 :]:
			name = clean_text(other[col], 80) if col < len(other) else ""
			flag = other[col + 1] if col + 1 < len(other) else None
			if name and _num(flag) == 1:
				scored.add(name)
		data.scored_categories = scored
		return


def _read_tonghop(ws, data: WorkbookData) -> None:
	for row in ws.iter_rows(min_row=1, max_row=12, values_only=True):
		label = clean_text(row[0], 80).lower() if row and row[0] is not None else ""
		if label in TONGHOP_LABELS and len(row) > 1 and _num(row[1]) is not None:
			data.tonghop[TONGHOP_LABELS[label]] = int(row[1])


def read_workbook(path: str | Path) -> WorkbookData:
	"""Đọc tệp (đã `check_file`): ba sheet dữ liệu bắt buộc, ThamSo và TongHop nếu có. Ném WorkbookError nếu không dùng được."""
	check_file(path)
	try:
		wb = load_workbook(str(path), read_only=True, data_only=True, keep_links=False)
	except Exception:
		raise WorkbookError("Không mở được tệp Excel (tệp hỏng hoặc không đúng định dạng)") from None
	try:
		missing = [n for n in REQUIRED_CODES if n not in wb.sheetnames]
		if missing:
			raise WorkbookError(
				f"Thiếu sheet bắt buộc: {', '.join(missing)}. Dùng đúng mẫu danh sách nhà thầu."
			)
		data = WorkbookData(sheets={n: _read_sheet(wb[n], n) for n in REQUIRED_CODES})
		if "ThamSo" in wb.sheetnames:
			_read_thamso(wb["ThamSo"], data)
		else:
			data.warnings.append("Tệp không có sheet ThamSo: không đối chiếu được tham số chấm điểm")
		if "DanhMuc" in wb.sheetnames:
			_read_danhmuc(wb["DanhMuc"], data)
		if "TongHop" in wb.sheetnames:
			_read_tonghop(wb["TongHop"], data)
		else:
			data.warnings.append("Tệp không có sheet TongHop: không đối chiếu được số tổng hợp")
		_example_warnings(data)
		return data
	finally:
		wb.close()


def _example_warnings(data: WorkbookData) -> None:
	"""Dòng 3 là dòng ví dụ và luôn bị bỏ qua; nếu có dữ liệu thật thì cảnh báo để chuyển xuống dòng 4."""
	for name, sheet in data.sheets.items():
		tax = clean_text(sheet.example.get("tax_id"), 40)
		if tax and tax != EXAMPLE_TAX_ID:
			data.warnings.append(
				f"Sheet {name}: Dòng 3 có dữ liệu thật — chuyển xuống từ dòng 4 (dòng 3 luôn bị bỏ qua khi nhập)"
			)
