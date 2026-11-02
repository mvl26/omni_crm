# ruff: noqa: RUF001
"""Tệp lỗi của lần nhập danh sách nhà thầu: cùng định dạng mẫu để người dùng sửa rồi nhập lại (docs/14 mục 5, D1-14).

Giữ nguyên dòng 1 (nhãn), dòng 2 (mã trường) và dòng 3 (dòng ví dụ, importer luôn bỏ qua) của tệp gốc; các dòng lỗi bắt
đầu từ dòng 4 cùng cột "Lý do lỗi" (mã `error_reason`, importer bỏ qua khi nhập lại). Mọi ô chữ được trung hòa
(CSV/Excel injection): chữ bắt đầu bằng `= + - @` hoặc tab/CR được thêm dấu `'` để Excel không hiểu là công thức.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from omni_crm.prospect.parsing import EXAMPLE_TAX_ID, FORMULA_LEAD
from omni_crm.prospect.xlsx_reader import ERROR_CODE, SheetData

ERROR_LABEL = "Lý do lỗi"
EXAMPLE_NOTE = "DÒNG VÍ DỤ — bị bỏ qua khi nhập; dữ liệu lỗi bắt đầu từ dòng 4"
CONFIRM_SHEET = "CanXacNhan"


def safe_cell(value: Any) -> Any:
	"""Giá trị an toàn để ghi vào ô: chuỗi mở đầu bằng ký tự công thức được thêm `'`; ký tự cấm của XML bị gỡ."""
	if isinstance(value, str):
		value = ILLEGAL_CHARACTERS_RE.sub("", value)
		if value[:1] in tuple(FORMULA_LEAD):
			value = "'" + value
	return value


def _write(ws, row: int, col: int, value: Any) -> None:
	cell = ws.cell(row=row, column=col, value=safe_cell(value))
	if isinstance(cell.value, str):
		cell.data_type = "s"  # ép kiểu chuỗi: không bao giờ là công thức


def build_error_workbook(
	sheets: dict[str, SheetData],
	errors: dict[str, list[tuple[int, str]]],
	confirm: list[dict[str, Any]],
) -> bytes:
	"""Sổ tính chứa các dòng lỗi (đủ ba sheet như mẫu, kèm sheet CanXacNhan); trả nội dung .xlsx."""
	wb = Workbook()
	wb.remove(wb.active)
	for name, sheet in sheets.items():
		ws = wb.create_sheet(name)
		codes = [c for c in sheet.codes if c and c != ERROR_CODE]
		cols = [i for i, c in enumerate(sheet.codes) if c and c != ERROR_CODE]
		for out, i in enumerate(cols, start=1):
			_write(ws, 1, out, sheet.labels[i] if i < len(sheet.labels) else codes[out - 1])
			_write(ws, 2, out, sheet.codes[i])
		extra = len(cols) + 1
		_write(ws, 1, extra, ERROR_LABEL)
		_write(ws, 2, extra, ERROR_CODE)
		if "tax_id" in codes:  # dòng ví dụ cố định: không mang dữ liệu thật của tệp gốc
			_write(ws, 3, codes.index("tax_id") + 1, EXAMPLE_TAX_ID)
			_write(ws, 3, extra, EXAMPLE_NOTE)
		by_number = {r.number: r for r in sheet.rows}
		out_row = 4
		for number, reason in sorted(errors.get(name, [])):
			src = by_number.get(number)
			if not src:
				continue
			for out, i in enumerate(cols, start=1):
				_write(ws, out_row, out, src.values.get(sheet.codes[i]))
			_write(ws, out_row, extra, reason)
			out_row += 1
	ws = wb.create_sheet(CONFIRM_SHEET)
	for out, head in enumerate(("Loại", "Mô tả", "Bản ghi 1", "Bản ghi 2", "Gợi ý xử lý"), start=1):
		_write(ws, 1, out, head)
	for r, item in enumerate(confirm, start=2):
		recs = item.get("records") or []
		_write(ws, r, 1, item.get("kind"))
		_write(ws, r, 2, item.get("message"))
		_write(ws, r, 3, _fmt(recs[0]) if recs else "")
		_write(ws, r, 4, _fmt(recs[1]) if len(recs) > 1 else "")
		_write(ws, r, 5, item.get("hint"))
	buf = BytesIO()
	wb.save(buf)
	return buf.getvalue()


def _fmt(record: Any) -> str:
	if isinstance(record, dict):
		return "; ".join(f"{k}: {v}" for k, v in record.items() if v not in (None, ""))
	return str(record)
