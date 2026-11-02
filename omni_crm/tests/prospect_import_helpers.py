# ruff: noqa: RUF001
"""Dựng tệp .xlsx danh sách nhà thầu cho test V2-03 từ đúng mẫu `mau/` (không đọc tệp khách thật) + dữ liệu tổng hợp."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import frappe
from openpyxl import Workbook, load_workbook

FIXTURES = Path(__file__).parent / "fixtures" / "prospect"
TEMPLATE = Path(frappe.get_app_path("omni_crm")).parent / "mau" / "Mau_DanhSach_NhaThau_TiemNang.xlsx"
SYNTH = json.loads((FIXTURES / "import_synthetic.json").read_text(encoding="utf-8"))
FIT = json.loads((FIXTURES / "fit_cases.json").read_text(encoding="utf-8"))
FIT_BY_KEY = {c["key"]: c for c in FIT["cases"]}
TODAY = date.fromisoformat(SYNTH["today"])
DATA_SHEETS = ("NhaThau", "GoiThau", "NguoiLienHe")
CALC_FOR_ORG = {
	"awards_in_period": "calc_awards",
	"hospital_count": "calc_hospitals",
	"max_items": "calc_max_items",
	"items_for_scoring": "calc_items",
	"award_value_bn": "calc_value_bn",
	"active_awards": "calc_active",
	"fit_score": "calc_fit_score",
	"fit_tier": "calc_tier",
}


def synth_orgs(keys: str = "ABC") -> list[dict[str, Any]]:
	return [dict(o) for o in SYNTH["orgs"] if o["key"] in keys]


def synth_awards(keys: str = "ABC") -> list[dict[str, Any]]:
	"""Gói thầu của các ca fit_cases, gắn vào mã số thuế tổng hợp tương ứng."""
	out = []
	for o in SYNTH["orgs"]:
		if o["key"] not in keys:
			continue
		for a in FIT_BY_KEY[o["fit_case"]]["awards"]:
			out.append({**a, "tax_id": o["tax_id"]})
	return out


def synth_contacts(keys: str = "ABC") -> list[dict[str, Any]]:
	tax = {o["key"]: o["tax_id"] for o in SYNTH["orgs"]}
	return [{**c, "tax_id": tax[c["org"]]} for c in SYNTH["contacts"] if c["org"] in keys]


def expected_fit(key: str) -> dict[str, Any]:
	return FIT_BY_KEY[SYNTH["orgs"][ord(key) - ord("A")]["fit_case"]]["expected"]


def with_calc(orgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Gắn cột calc_* bằng giá trị kỳ vọng của fit_cases (tệp dựng bằng openpyxl không có giá trị công thức đã tính)."""
	out = []
	for o in orgs:
		exp = FIT_BY_KEY[o["fit_case"]]["expected"]
		calc = {col: exp[k] for k, col in CALC_FOR_ORG.items()}
		out.append({**o, **calc})
	return out


def _plain(value: Any) -> Any:
	return None if isinstance(value, str) and value.startswith("=") else value


def build_workbook(
	path: str | Path,
	orgs: list[dict[str, Any]] | None = None,
	awards: list[dict[str, Any]] | None = None,
	contacts: list[dict[str, Any]] | None = None,
	*,
	reverse_columns: bool = False,
	tonghop: dict[str, int] | None = None,
	today: date | None = TODAY,
	example_tax: str | None = None,
	drop_sheet: str | None = None,
	drop_code: tuple[str, str] | None = None,
	thamso: dict[str, float] | None = None,
) -> Path:
	"""Ghi tệp theo mẫu: dòng 1-3 giữ nguyên từ mẫu, dữ liệu từ dòng 4; công thức đổi thành giá trị chữ."""
	tpl = load_workbook(TEMPLATE)
	wb = Workbook()
	wb.remove(wb.active)
	rows_by_sheet = {"NhaThau": orgs or [], "GoiThau": awards or [], "NguoiLienHe": contacts or []}
	for name in tpl.sheetnames:
		if name == drop_sheet:
			continue
		src, ws = tpl[name], wb.create_sheet(name)
		if name not in DATA_SHEETS:
			for row in src.iter_rows():
				for c in row:
					if c.value is not None:
						ws.cell(row=c.row, column=c.column, value=_plain(c.value))
			continue
		width = src.max_column
		codes = {
			str(src.cell(row=2, column=i).value): i
			for i in range(1, width + 1)
			if src.cell(row=2, column=i).value
		}
		columns = list(range(1, width + 1))
		if reverse_columns:
			columns.reverse()
		position = {col: pos for pos, col in enumerate(columns, start=1)}
		for r in (1, 2, 3):
			for i in range(1, width + 1):
				value = _plain(src.cell(row=r, column=i).value)
				if drop_code and drop_code[0] == name and r == 2 and value == drop_code[1]:
					value = None
				if r == 3 and example_tax and src.cell(row=2, column=i).value == "tax_id":
					value = example_tax
				if value is not None:
					ws.cell(row=r, column=position[i], value=value)
		for n, record in enumerate(rows_by_sheet[name], start=4):
			for code, value in record.items():
				if code in codes and value is not None:
					cell = ws.cell(row=n, column=position[codes[code]])
					if isinstance(value, str) and value[:1] in "=+-@":
						cell.value = value
						cell.data_type = "s"  # chuỗi thật, không phải công thức (giả lập tệp độc hại)
					elif code in ("collected_on", "award_date", "consent_date") and isinstance(value, str):
						cell.value = datetime.fromisoformat(value)
					else:
						cell.value = value
	ts = wb["ThamSo"] if "ThamSo" in wb.sheetnames else None
	if ts:
		for row in ts.iter_rows(min_col=1, max_col=2):
			label = str(row[0].value or "").lower()
			if label == "ngày tính":
				row[1].value = datetime.combine(today, datetime.min.time()) if today else None
			for key, value in (thamso or {}).items():
				if key == label:
					row[1].value = value
	th = wb["TongHop"] if "TongHop" in wb.sheetnames else None
	if th and tonghop is not None:
		for r, key in zip((5, 6, 7, 8, 9), ("total", "meets", "A", "B", "C"), strict=True):
			th.cell(row=r, column=2, value=tonghop.get(key, 0))
	wb.save(str(path))
	return Path(path)
