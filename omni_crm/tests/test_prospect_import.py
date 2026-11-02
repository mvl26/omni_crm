# ruff: noqa: RUF001
"""V2-03: nhập danh sách nhà thầu từ mẫu Excel (docs/14 mục 5). Dữ liệu TỔNG HỢP dựng bằng openpyxl theo đúng mẫu.

Mọi bản ghi do test tạo mang tiền tố TD303 (tên) / 09030300xx (mã số thuế) và nằm trong savepoint, được rollback khi xong.
"""

import json
import os
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from openpyxl import load_workbook

from omni_crm.api import prospect as prospect_api
from omni_crm.prospect import contacts as contact_lib
from omni_crm.prospect import fit, importer, parsing
from omni_crm.prospect.sources.base import AwardRecord
from omni_crm.prospect.sources.excel import ExcelSource
from omni_crm.prospect.xlsx_reader import WorkbookError, check_file, read_workbook
from omni_crm.tests.prospect_import_helpers import (
	TEMPLATE,
	TODAY,
	build_workbook,
	expected_fit,
	synth_awards,
	synth_contacts,
	synth_orgs,
	with_calc,
)
from omni_crm.tests.test_prospect_model import make_lead, make_org, make_user

ORG = "CRM Organization"
LEAD = "CRM Lead"
AWARD = "Omni Bid Award"
CONTACT = "Contact"
LOG = "Omni Prospect Import"
SALE1 = "td303_sale1@example.com"
SALE2 = "td303_sale2@example.com"
ADMIN2 = "td303_admin@example.com"


def counts() -> dict[str, int]:
	"""Số bản ghi các DocType mà importer có thể ghi (để kiểm dry run không ghi gì)."""
	names = (ORG, LEAD, AWARD, CONTACT, "CRM Deal", "FCRM Note", "Omni Outbound Message", "Comment", "ToDo")
	return {n: frappe.db.count(n) for n in names}


class ImportCase(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.savepoint("td303_imp")
		self.addCleanup(frappe.db.rollback, save_point="td303_imp")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.clear_document_cache, "Omni Settings", "Omni Settings")
		real_getdate = (
			fit.getdate
		)  # ngày tính cố định = ngày của mẫu (30/10/2026) để điểm không trôi theo đồng hồ
		patcher = mock.patch("omni_crm.prospect.fit.getdate", lambda d=None: real_getdate(d) if d else TODAY)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		make_user(SALE1)
		make_user(SALE2)

	def path(self, name: str = "danh_sach.xlsx") -> Path:
		return Path(self.tmp.name) / name

	def make(self, orgs=None, awards=None, contacts=None, **kw) -> Path:
		return build_workbook(
			self.path(kw.pop("name", "danh_sach.xlsx")),
			synth_orgs() if orgs is None else orgs,
			synth_awards() if awards is None else awards,
			synth_contacts() if contacts is None else contacts,
			**kw,
		)

	def run_import(self, path, dry_run=False, **kw) -> dict:
		"""Chạy importer đồng bộ (không commit) và trả báo cáo."""
		report = importer.Importer(path, dry_run=dry_run, user="Administrator", commit=False, **kw).run()
		self.last = report
		return report

	def tax(self, key: str) -> str:
		return {o["key"]: o["tax_id"] for o in synth_orgs()}[key]

	def org_name(self, key: str) -> str:
		return frappe.db.get_value(ORG, {"omni_tax_id": self.tax(key)}, "name")

	def errors(self, sheet: str) -> list[tuple[int, str]]:
		return self.last["errors"][sheet]


class TestParsing(FrappeTestCase):
	def test_clean_text_strips_formula_lead_and_control_chars(self):
		self.assertEqual(parsing.clean_text("=HYPERLINK(1)"), "HYPERLINK(1)")
		self.assertEqual(parsing.clean_text("@SUM(A1)"), "SUM(A1)")
		self.assertEqual(parsing.clean_text("a\x00b\x07c"), "abc")
		self.assertEqual(parsing.clean_text("'=x"), "x")  # dấu ' do tệp lỗi thêm được gỡ khi nhập lại
		self.assertEqual(parsing.clean_text("'+84901234567"), "+84901234567")
		self.assertEqual(parsing.clean_text("[đường dẫn trang kết quả LCNT]"), "")  # ô mẫu chưa điền

	def test_tax_id_cell_as_number_gets_leading_zero_back(self):
		self.assertEqual(parsing.parse_tax_id(109999999), "0109999999")
		self.assertEqual(parsing.parse_tax_id("0109 999.999"), "0109999999")
		with self.assertRaises(parsing.RowError):
			parsing.parse_tax_id("12345")
		with self.assertRaises(parsing.RowError):
			parsing.parse_tax_id("")

	def test_select_is_case_insensitive_and_rejects_unknown(self):
		self.assertEqual(parsing.parse_select("miền bắc", parsing.REGIONS, "region"), "Miền Bắc")
		with self.assertRaises(parsing.RowError) as ctx:
			parsing.parse_select("Miền Tây", parsing.REGIONS, "region")
		self.assertIn("region", str(ctx.exception))

	def test_dates_numbers_and_names(self):
		self.assertEqual(parsing.parse_date("15/03/2026", "d"), date(2026, 3, 15))
		self.assertEqual(parsing.parse_date("2026-03-15", "d"), date(2026, 3, 15))
		with self.assertRaises(parsing.RowError):
			parsing.parse_date("mai kia", "d")
		self.assertEqual(parsing.parse_number(4500000000.0, "v"), 4500000000.0)
		self.assertEqual(parsing.parse_number("12", "v", integer=True), 12)
		for bad in ("1,5", "-3", float("nan"), "abc", True):
			with self.subTest(bad), self.assertRaises(parsing.RowError):
				parsing.parse_number(bad, "v")
		# họ tên ghép lại đúng thứ tự khi Frappe ghép first + last
		self.assertEqual(parsing.split_name("Nguyễn Văn An"), ("Nguyễn Văn", "An"))
		self.assertEqual(parsing.split_name("An"), ("An", ""))

	def test_option_constants_match_crm_fields(self):
		def opts(dt, field):
			return tuple(o for o in (frappe.get_meta(dt).get_field(field).options or "").split("\n") if o)

		self.assertEqual(opts(ORG, "omni_region"), parsing.REGIONS)
		self.assertEqual(opts(ORG, "omni_accounting_software"), parsing.SOFTWARE)
		self.assertEqual(opts(ORG, "omni_interested_package"), parsing.PACKAGES)
		self.assertEqual(opts(AWARD, "category"), parsing.CATEGORIES)
		self.assertEqual(opts(AWARD, "role"), parsing.AWARD_ROLES)
		self.assertEqual(opts(CONTACT, "omni_contact_role"), parsing.CONTACT_ROLES)
		self.assertEqual(opts(CONTACT, "omni_has_zalo"), parsing.HAS_ZALO)
		self.assertEqual(opts(CONTACT, "omni_consent"), parsing.CONSENT)

	def test_award_requires_hospital_category_and_date(self):
		base = {"tax_id": "0903030011", "hospital": "BV X", "category": "Thuốc", "award_date": "01/02/2026"}
		self.assertEqual(parsing.parse_award(base)["award_date"], date(2026, 2, 1))
		for missing in ("hospital", "category", "award_date"):
			with self.subTest(missing), self.assertRaises(parsing.RowError):
				parsing.parse_award({**base, missing: None})

	def test_contact_requires_info_source(self):
		with self.assertRaises(parsing.RowError) as ctx:
			parsing.parse_contact({"tax_id": "0903030011", "full_name": "A B", "info_source": " "})
		self.assertIn("info_source", str(ctx.exception))


class TestReader(ImportCase):
	def test_blank_template_reads_with_zero_rows(self):
		data = read_workbook(TEMPLATE)
		self.assertEqual({n: len(s.rows) for n, s in data.sheets.items()}, {n: 0 for n in data.sheets})
		self.assertEqual(data.today, date(2026, 10, 30))
		self.assertEqual(data.tonghop["total"], 0)
		self.assertEqual(data.params["fit_min_hospitals"], 5)
		self.assertEqual(data.warnings, [])  # dòng 3 là dòng ví dụ chuẩn

	def test_blank_rows_with_calc_zeros_are_not_data(self):
		# mẫu gốc có cột calc_* = 0 ở mọi dòng trống: không được coi là dòng dữ liệu
		self.assertEqual(len(read_workbook(TEMPLATE).sheets["GoiThau"].rows), 0)

	def test_row3_with_real_data_warns_and_is_skipped(self):
		p = self.make(example_tax="0903030099")
		data = read_workbook(p)
		self.assertTrue(any("Dòng 3 có dữ liệu thật" in w for w in data.warnings))
		self.assertEqual(len(data.sheets["NhaThau"].rows), 3)  # dòng 3 không bị nhận làm dữ liệu

	def test_missing_sheet_or_required_code_stops_with_clear_message(self):
		with self.assertRaises(WorkbookError) as ctx:
			read_workbook(self.make(drop_sheet="GoiThau"))
		self.assertIn("GoiThau", str(ctx.exception))
		with self.assertRaises(WorkbookError) as ctx:
			read_workbook(self.make(name="b.xlsx", drop_code=("NguoiLienHe", "info_source")))
		self.assertIn("info_source", str(ctx.exception))

	def test_columns_are_found_by_code_not_position(self):
		a = read_workbook(self.make(name="a.xlsx")).sheets["NhaThau"].rows
		b = read_workbook(self.make(name="b.xlsx", reverse_columns=True)).sheets["NhaThau"].rows
		self.assertEqual([r.values["tax_id"] for r in a], [r.values["tax_id"] for r in b])
		self.assertEqual([r.values["name"] for r in a], [r.values["name"] for r in b])

	def test_rejects_non_xlsx_macro_and_zip_bomb_and_oversize(self):
		p = self.make()
		bad = self.path("x.xlsm")
		bad.write_bytes(p.read_bytes())
		with self.assertRaises(WorkbookError):
			check_file(bad)
		fake = self.path("fake.xlsx")
		fake.write_text("không phải zip")
		with self.assertRaises(WorkbookError):
			check_file(fake)
		macro = self.path("macro.xlsx")
		with zipfile.ZipFile(p) as src, zipfile.ZipFile(macro, "w") as dst:
			for item in src.infolist():
				dst.writestr(item, src.read(item.filename))
			dst.writestr("xl/vbaProject.bin", b"x")
		with self.assertRaisesRegex(WorkbookError, "macro"):
			check_file(macro)
		with (
			mock.patch("omni_crm.prospect.xlsx_reader.MAX_FILE_BYTES", 100),
			self.assertRaises(WorkbookError),
		):
			check_file(p)
		with (
			mock.patch("omni_crm.prospect.xlsx_reader.MAX_UNCOMPRESSED_BYTES", 100),
			self.assertRaisesRegex(WorkbookError, "bom nén"),
		):
			check_file(p)

	def test_row_limit(self):
		p = self.make()
		with (
			mock.patch.dict("omni_crm.prospect.xlsx_reader.MAX_DATA_ROWS", {"NhaThau": 2}),
			self.assertRaisesRegex(WorkbookError, "quá 2 dòng"),
		):
			read_workbook(p)

	def test_formula_cells_are_never_evaluated(self):
		"""Ô công thức không có giá trị đã tính đọc ra rỗng (data_only): công thức không bao giờ chạy."""
		orgs = synth_orgs("A")
		orgs[0]["short_name"] = "=1+1"  # builder ghi chuỗi thật bắt đầu bằng =
		data = read_workbook(self.make(orgs=orgs))
		self.assertEqual(data.sheets["NhaThau"].rows[0].values["short_name"], "=1+1")  # còn là chuỗi
		self.assertEqual(parsing.clean_text("=1+1"), "1+1")  # và bị gỡ ký tự công thức trước khi vào DB


class TestImport(ImportCase):
	def test_first_import_creates_everything_with_expected_fit(self):
		report = self.run_import(self.make())
		self.assertEqual(report["sheets"]["NhaThau"]["created"], 3)
		self.assertEqual(report["sheets"]["GoiThau"]["created"], 16)  # 9 + 5 + 2
		self.assertEqual(report["sheets"]["NguoiLienHe"]["created"], 3)
		self.assertEqual(
			{s: report["sheets"][s]["errors"] for s in report["sheets"]}, dict.fromkeys(report["sheets"], 0)
		)
		for key in "ABC":
			org = frappe.get_doc(ORG, self.org_name(key))
			for field, want in expected_fit(key).items():
				if field not in fit.RESULT_FIELDS:
					continue
				got = org.get(fit.RESULT_FIELDS[field])
				if isinstance(want, str):
					self.assertEqual(got, want, f"{key}.{field}")
				else:
					self.assertAlmostEqual(float(got), float(want), places=6, msg=f"{key}.{field}")
		a = frappe.get_doc(ORG, self.org_name("A"))
		self.assertEqual(a.omni_short_name, "TD303-A")
		self.assertEqual(a.omni_region, "Miền Bắc")
		self.assertEqual(a.omni_accounting_software, "MISA")
		self.assertEqual(str(a.omni_collected_on), "2026-10-30")
		self.assertEqual(a.omni_data_source, "Tổng hợp")
		self.assertFalse(a.omni_verified)
		c = frappe.get_doc(ORG, self.org_name("C"))
		self.assertTrue(c.omni_data_source)  # luôn có nguồn dữ liệu cho nhà thầu do importer tạo

	def test_default_data_source_is_set_when_file_has_none(self):
		orgs = synth_orgs("C")
		orgs[0].pop("data_source")
		self.run_import(self.make(orgs=orgs, awards=[], contacts=[]))
		self.assertIn("Nhập danh sách", frappe.db.get_value(ORG, self.org_name("C"), "omni_data_source"))

	def test_leads_are_created_once_with_owner_and_status(self):
		self.run_import(self.make())
		a_lead = frappe.get_all(
			LEAD, filters={"organization": self.org_name("A")}, fields=["status", "lead_owner", "source"]
		)
		self.assertEqual(len(a_lead), 1)
		self.assertEqual(a_lead[0].status, "Tiềm năng")
		self.assertEqual(a_lead[0].lead_owner, SALE1)
		self.assertEqual(a_lead[0].source, importer.LEAD_SOURCE)
		b_lead = frappe.get_all(
			LEAD, filters={"organization": self.org_name("B")}, fields=["status", "lead_owner"]
		)
		self.assertEqual(b_lead[0].status, "Đã liên hệ")  # theo cột stage
		self.assertFalse(b_lead[0].lead_owner)  # không có owner_email: để quy tắc phân công
		c_lead = frappe.get_all(LEAD, filters={"organization": self.org_name("C")}, fields=["status"])
		self.assertEqual(c_lead[0].status, "Tiềm năng")  # stage trống = Tiềm năng

	def test_notes_become_one_note_only_when_changed(self):
		self.run_import(self.make())
		lead = frappe.db.get_value(LEAD, {"organization": self.org_name("A")}, "name")
		notes = frappe.get_all("FCRM Note", filters={"reference_docname": lead}, fields=["title", "content"])
		self.assertEqual(len(notes), 1)
		self.assertEqual(notes[0].title, importer.NOTE_TITLE)
		self.assertIn("Ghi chú tổng hợp A", notes[0].content)
		self.run_import(self.make())
		self.assertEqual(frappe.db.count("FCRM Note", {"reference_docname": lead}), 1)
		orgs = synth_orgs()
		orgs[0]["notes"] = "Ghi chú tổng hợp A đã đổi"
		self.run_import(self.make(orgs=orgs))
		self.assertEqual(frappe.db.count("FCRM Note", {"reference_docname": lead}), 2)

	def test_contacts_have_info_source_consent_and_org(self):
		self.run_import(self.make())
		rows = frappe.get_all(CONTACT, filters={"company_name": self.org_name("A")}, fields=["name"])
		self.assertEqual(len(rows), 2)
		thu = next(frappe.get_doc(CONTACT, r.name) for r in rows if "Thử" in r.name)
		self.assertEqual(thu.full_name, "Nguyễn Văn Thử")
		self.assertEqual(thu.omni_info_source, "Website nhà thầu")
		self.assertEqual(thu.omni_consent, "Chưa hỏi")
		self.assertEqual(thu.omni_has_zalo, "Có")
		self.assertEqual(thu.omni_contact_role, "Người quyết định")
		self.assertEqual(thu.designation, "Giám đốc")
		self.assertEqual(thu.omni_phones_e164, "|+84900000001|")
		self.assertEqual(
			frappe.db.count(
				"Comment",
				{"reference_doctype": CONTACT, "reference_name": thu.name, "comment_type": "Comment"},
			),
			1,
		)
		mau = next(frappe.get_doc(CONTACT, r.name) for r in rows if "Mẫu" in r.name)
		self.assertEqual(
			(mau.omni_consent, str(mau.omni_consent_date), mau.omni_consent_method),
			("Có", "2026-10-01", "Email xác nhận"),
		)

	def test_reimport_same_file_creates_and_updates_nothing(self):
		p = self.make()
		self.run_import(p)
		before = counts()
		again = self.run_import(p)
		for sheet, s in again["sheets"].items():
			self.assertEqual((s["created"], s["updated"], s["errors"]), (0, 0, 0), sheet)
		self.assertEqual(counts(), before)

	def test_changing_one_cell_updates_only_that_field(self):
		self.run_import(self.make())
		name = self.org_name("A")
		before = frappe.get_doc(ORG, name).as_dict()
		orgs = synth_orgs()
		orgs[0]["address"] = "Số 2 Đường Mới"
		report = self.run_import(self.make(orgs=orgs))
		self.assertEqual(report["sheets"]["NhaThau"]["updated"], 1)
		self.assertEqual(
			(report["sheets"]["GoiThau"]["updated"], report["sheets"]["NguoiLienHe"]["updated"]), (0, 0)
		)
		after = frappe.get_doc(ORG, name).as_dict()
		changed = {
			k
			for k in after
			if k not in ("modified", "omni_import_snapshot", "omni_fit_computed_at")
			and after[k] != before.get(k)
		}
		self.assertEqual(changed, {"omni_address"})

	def test_blank_cell_does_not_erase_crm_data(self):
		self.run_import(self.make())
		orgs = synth_orgs()
		orgs[0]["address"] = None
		self.run_import(self.make(orgs=orgs))
		self.assertEqual(
			frappe.db.get_value(ORG, self.org_name("A"), "omni_address"), "Số 1 Đường Thử, Hà Nội"
		)

	def test_field_edited_in_crm_is_not_overwritten(self):
		self.run_import(self.make())
		name = self.org_name("A")
		frappe.db.set_value(ORG, name, "omni_province", "Hải Phòng")  # sale sửa tay sau lần nhập đầu
		orgs = synth_orgs()
		orgs[0]["province"] = "Hà Nam"
		orgs[0]["address"] = "Địa chỉ mới"
		report = self.run_import(self.make(orgs=orgs))
		self.assertEqual(frappe.db.get_value(ORG, name, "omni_province"), "Hải Phòng")  # giữ bản sửa tay
		self.assertEqual(
			frappe.db.get_value(ORG, name, "omni_address"), "Địa chỉ mới"
		)  # trường không ai sửa thì cập nhật
		self.assertTrue(any("omni_province" in w for w in report["warnings"]))
		self.assertGreaterEqual(report["counters"]["field_conflicts"], 1)

	def test_column_order_does_not_matter(self):
		self.run_import(self.make(reverse_columns=True))
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": ["like", "09030300%"]}), 3)
		self.assertEqual(
			frappe.db.get_value(ORG, self.org_name("A"), "omni_fit_score"), expected_fit("A")["fit_score"]
		)

	def test_bad_rows_go_to_error_file_and_others_still_import(self):
		orgs = [
			*synth_orgs(),
			{"tax_id": "12345", "name": "TD303 MST sai"},
			{"tax_id": "0903030044", "name": "TD303 Vùng sai", "region": "Miền Tây"},
		]
		awards = [
			*synth_awards(),
			{"tax_id": "0903039999", "hospital": "BV Lạ", "category": "Thuốc", "award_date": "2026-01-01"},
			{
				"tax_id": self.tax("A"),
				"hospital": "BV Loại sai",
				"category": "Rượu",
				"award_date": "2026-01-01",
			},
		]
		contacts = [
			*synth_contacts(),
			{"tax_id": self.tax("A"), "full_name": "Thiếu Nguồn", "phone": "0900000009"},
		]
		report = self.run_import(self.make(orgs=orgs, awards=awards, contacts=contacts))
		self.assertEqual(report["sheets"]["NhaThau"]["errors"], 2)
		self.assertEqual(report["sheets"]["GoiThau"]["errors"], 2)
		self.assertEqual(report["sheets"]["NguoiLienHe"]["errors"], 1)
		self.assertEqual(report["sheets"]["NhaThau"]["created"], 3)  # dòng tốt vẫn vào
		self.assertEqual(report["sheets"]["GoiThau"]["created"], 16)
		reasons = " | ".join(r for sheet in self.last["errors"].values() for _n, r in sheet)
		for fragment in ("sai định dạng", "Miền Tây", "không có trong sheet NhaThau", "Rượu", "info_source"):
			self.assertIn(fragment, reasons)
		self.assertIsNone(frappe.db.get_value(ORG, {"omni_tax_id": "0903030044"}))  # dòng lỗi không được ghi
		self.assertIsNotNone(report["error_file"])

	def test_error_file_has_template_shape_and_can_be_fixed_and_reimported(self):
		awards = synth_awards("A")
		bad = {
			"tax_id": self.tax("A"),
			"hospital": "BV Sửa Được",
			"category": "Rượu",
			"award_date": "2026-01-01",
			"notice_no": "X1",
		}
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[*awards, bad], contacts=[]))
		self.assertEqual(report["sheets"]["GoiThau"]["errors"], 1)
		err = self.path("loi.xlsx")
		err.write_bytes(report["error_file"])
		wb = load_workbook(err)
		self.assertEqual(wb.sheetnames, ["NhaThau", "GoiThau", "NguoiLienHe", "CanXacNhan"])
		ws = wb["GoiThau"]
		codes = [c.value for c in ws[2]]
		self.assertEqual(codes[0], "tax_id")
		self.assertEqual(codes[-1], "error_reason")
		self.assertEqual(ws.cell(row=3, column=1).value, parsing.EXAMPLE_TAX_ID)  # dòng ví dụ giữ chỗ dòng 3
		self.assertEqual(ws.cell(row=4, column=codes.index("hospital") + 1).value, "BV Sửa Được")
		self.assertIn("Rượu", ws.cell(row=4, column=len(codes)).value)
		# sửa loại hàng rồi nhập lại chính tệp lỗi: gói thầu vào, không lỗi, không dòng nào bị nuốt
		ws.cell(row=4, column=codes.index("category") + 1).value = "Thuốc"
		wb.save(err)
		again = self.run_import(err)
		self.assertEqual(
			(again["sheets"]["GoiThau"]["created"], again["sheets"]["GoiThau"]["errors"]), (1, 0)
		)

	def test_error_file_neutralizes_formula_injection_round_trip(self):
		evil = {
			"tax_id": self.tax("A"),
			"hospital": "BV Độc",
			"category": "Rượu",
			"award_date": "2026-01-01",
			"package_name": "=cmd|' /C calc'!A0",
			"source_url": "+84901234567",
			"notice_no": "@SUM(1)",
		}
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[evil], contacts=[]))
		err = self.path("loi.xlsx")
		err.write_bytes(report["error_file"])
		ws = load_workbook(err)["GoiThau"]
		codes = [c.value for c in ws[2]]
		for code in ("package_name", "source_url", "notice_no"):
			cell = ws.cell(row=4, column=codes.index(code) + 1)
			self.assertEqual(cell.data_type, "s", code)
			self.assertEqual(cell.value[:1], "'", code)
		with zipfile.ZipFile(err) as z:  # không có ô công thức nào trong tệp lỗi
			for n in z.namelist():
				if n.startswith("xl/worksheets/"):
					self.assertNotIn(b"<f>", z.read(n), n)
		self.assertEqual(
			parsing.clean_text(ws.cell(row=4, column=codes.index("source_url") + 1).value), "+84901234567"
		)

	def test_dry_run_writes_nothing_and_predicts_the_real_run(self):
		p = self.make()
		before = counts()
		dry = self.run_import(p, dry_run=True)
		self.assertEqual(counts(), before)
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": ["like", "09030300%"]}), 0)
		real = self.run_import(p)
		for sheet in ("NhaThau", "GoiThau", "NguoiLienHe"):
			for k in ("created", "updated", "skipped", "errors"):
				self.assertEqual(dry["sheets"][sheet][k], real["sheets"][sheet][k], f"{sheet}.{k}")
		self.assertEqual(dry["counters"]["leads_created"], real["counters"]["leads_created"])
		# dry run vẫn dự báo được điểm để đối chiếu
		self.assertTrue(dry["reconcile"]["compared"] == 0 or dry["reconcile"]["mismatches"] == [])

	def test_dry_run_reconciles_projected_fit_against_tonghop(self):
		p = self.make(orgs=with_calc(synth_orgs()), tonghop={"total": 3, "meets": 2, "A": 0, "B": 2, "C": 1})
		before = counts()
		dry = self.run_import(p, dry_run=True)
		self.assertEqual(counts(), before)
		rc = dry["reconcile"]
		self.assertEqual((rc["compared"], rc["mismatches"]), (3, []))
		self.assertTrue(rc["tonghop"]["match"])
		self.assertTrue(rc["top10_match"])
		self.assertEqual([r["score"] for r in rc["top10_crm"]], [62, 53, 25])

	def test_reconcile_reports_difference_explained_by_hospital_name_normalization(self):
		awards = synth_awards("A")
		# Excel đếm theo chuỗi thô: "BV Đa khoa H1" và "Bệnh viện Đa khoa H1" là 2 bệnh viện; CRM chuẩn hóa còn 1
		extra = {
			**awards[0],
			"notice_no": "A99",
			"hospital": "BV Đa khoa H1",
			"award_value": 0,
			"item_count": 1,
		}
		orgs = with_calc(synth_orgs("A"))
		orgs[0]["calc_hospitals"] = 7  # tệp đếm 7 bệnh viện (6 + biến thể viết tắt)
		report = self.run_import(self.make(orgs=orgs, awards=[*awards, extra], contacts=[]))
		mism = report["reconcile"]["mismatches"]
		self.assertEqual([m["trường"] for m in mism], ["calc_hospitals"])
		self.assertEqual((mism[0]["tệp"], mism[0]["CRM"]), (7, 6))
		self.assertIn("chuẩn hóa tên bệnh viện", mism[0]["lý do dự kiến"])

	def test_reconcile_flags_parameter_differences_with_settings(self):
		p = self.make(
			orgs=with_calc(synth_orgs("A")),
			awards=[],
			contacts=[],
			thamso={"số bệnh viện đạt điểm tối đa": 8},
		)
		report = self.run_import(p, dry_run=True)
		diffs = report["reconcile"]["param_diffs"]
		self.assertEqual([d["tham số"] for d in diffs], ["fit_max_hospitals"])
		self.assertTrue(any("Tham số chấm điểm" in w for w in report["warnings"]))

	def test_blank_template_imports_zero_rows_and_matches_tonghop(self):
		before = counts()
		report = self.run_import(TEMPLATE, dry_run=True)
		self.assertEqual(counts(), before)
		self.assertEqual(sum(s["rows"] for s in report["sheets"].values()), 0)
		self.assertTrue(report["reconcile"]["tonghop"]["match"])
		self.assertTrue(
			any("thư mục mã nguồn" in w for w in report["warnings"])
		)  # mẫu nằm trong repo: nhắc dữ liệu thật để ngoài

	def test_failure_after_saving_org_rolls_back_row_and_dependents_do_not_trust_it(self):
		real = importer.Importer._create_lead
		tax_a = self.tax("A")

		def fail_for_a(inst, org, *args, **kwargs):
			if org["omni_tax_id"] == tax_a:
				frappe.throw("lỗi giả lập khi tạo Lead")
			return real(inst, org, *args, **kwargs)

		with mock.patch.object(importer.Importer, "_create_lead", fail_for_a):
			report = self.run_import(self.make())
		self.assertEqual(report["sheets"]["NhaThau"]["errors"], 1)
		self.assertIn("lỗi giả lập", self.errors("NhaThau")[0][1])
		self.assertEqual(report["sheets"]["NhaThau"]["created"], 2)
		self.assertIsNone(
			frappe.db.exists(ORG, {"omni_tax_id": tax_a})
		)  # nhà thầu của dòng lỗi được hoàn tác
		self.assertEqual(frappe.db.count(AWARD, {"tax_id": tax_a}), 0)
		self.assertEqual(report["sheets"]["GoiThau"]["errors"], 9)  # gói thầu của A: nhà thầu không tồn tại
		self.assertEqual(report["sheets"]["GoiThau"]["created"], 7)
		self.assertEqual(
			(report["sheets"]["NguoiLienHe"]["errors"], report["sheets"]["NguoiLienHe"]["created"]), (2, 1)
		)

	def test_fit_is_computed_once_per_organization_with_defer_flag(self):
		calls: list[str] = []
		real = fit.apply_fit

		def spy(org, today=None):
			calls.append(org)
			return real(org, today)

		with mock.patch("omni_crm.prospect.fit.apply_fit", spy):
			self.run_import(self.make())
		self.assertEqual(
			sorted(calls), sorted(self.org_name(k) for k in "ABC")
		)  # 16 gói thầu nhưng 3 lần tính
		self.assertFalse(frappe.flags.get("omni_defer_fit"))  # cờ được trả lại

	def test_defer_flag_is_restored_even_when_import_crashes(self):
		with (
			mock.patch.object(importer.Importer, "_convert_deals", side_effect=RuntimeError("x")),
			self.assertRaises(RuntimeError),
		):
			self.run_import(self.make())
		self.assertFalse(frappe.flags.get("omni_defer_fit"))
		self.assertFalse(frappe.flags.get("in_import"))

	def test_fit_switch_off_skips_scoring_without_error(self):
		with mock.patch("omni_crm.prospect.fit.fit_enabled", return_value=False):
			report = self.run_import(self.make())
		self.assertIn("fit_scoring_enabled", report["summary"]["fit"]["skipped"])
		self.assertEqual(report["sheets"]["GoiThau"]["created"], 16)

	def test_importer_creates_no_outbound_message_and_has_no_send_path(self):
		before = frappe.db.count("Omni Outbound Message")
		self.run_import(self.make())
		self.assertEqual(frappe.db.count("Omni Outbound Message"), before)
		root = Path(importer.__file__).parent
		for f in [*root.glob("*.py"), *root.glob("sources/*.py"), root.parent / "api" / "prospect.py"]:
			text = f.read_text(encoding="utf-8")
			for banned in (
				"Omni Outbound Message",
				"sendmail",
				"frappe.sendmail",
				"send_reply",
				"enqueue_outbound",
			):
				self.assertNotIn(banned, text, f"{f.name}: {banned}")

	def test_campaign_fields_only_filled_when_they_exist(self):
		meta = frappe.get_meta(LEAD)
		self.assertFalse(meta.has_field("omni_campaign"))  # V2-05 chưa chạy: importer không được lỗi
		self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=[]))
		self.assertEqual(frappe.db.count(LEAD, {"organization": self.org_name("A")}), 1)

	def test_batch_commit_every_100_rows_in_real_runs_only(self):
		orgs = [
			{"tax_id": f"0903031{n:03d}", "name": f"TD303 Lô {n}", "data_source": "Tổng hợp"}
			for n in range(105)
		]
		p = self.make(orgs=orgs, awards=[], contacts=[])
		with mock.patch("frappe.db.commit") as commit:
			importer.Importer(p, dry_run=False, user="Administrator", commit=True).run()
		self.assertEqual(
			commit.call_count, 3
		)  # sau dòng 100 (nhà thầu), sau 100 lần tính điểm, và lần commit cuối
		with mock.patch("frappe.db.commit") as commit:
			importer.Importer(p, dry_run=True, user="Administrator", commit=True).run()
		self.assertEqual(commit.call_count, 0)


class TestTemplateExpectation(ImportCase):
	"""Số liệu mà Excel đã tính sẵn trong dòng ví dụ của mẫu là kỳ vọng độc lập duy nhất không suy ra từ fit_cases.json."""

	def template_row3(self, sheet: str) -> dict:
		ws = load_workbook(TEMPLATE, data_only=True)[sheet]
		codes = [c.value for c in ws[2]]
		return {code: ws.cell(row=3, column=i + 1).value for i, code in enumerate(codes) if code}

	def test_excel_cached_example_row_matches_crm_fit_after_import(self):
		org, award = self.template_row3("NhaThau"), self.template_row3("GoiThau")
		self.assertEqual(
			(org["calc_hospitals"], org["calc_fit_score"], org["calc_tier"]), (1, 29, "C")
		)  # Excel tính sẵn
		path = self.make(
			orgs=[org], awards=[award], contacts=[]
		)  # đặt dòng ví dụ xuống dòng 4 (dữ liệu thật)
		dry = self.run_import(path, dry_run=True)
		self.assertEqual((dry["reconcile"]["compared"], dry["reconcile"]["mismatches"]), (1, []))
		self.run_import(path)
		self.assertEqual(self.last["reconcile"]["mismatches"], [])
		crm = frappe.db.get_value(
			ORG,
			{"omni_tax_id": parsing.EXAMPLE_TAX_ID},
			[
				"omni_fit_score",
				"omni_fit_tier",
				"omni_hospital_count",
				"omni_items_for_scoring",
				"omni_award_value_bn",
				"omni_active_awards",
			],
			as_dict=True,
		)
		self.assertEqual(
			(crm.omni_fit_score, crm.omni_fit_tier, crm.omni_hospital_count), (29, "C", 1)
		)  # 4 + 10,8 + 4,5 + 10 = 29,3
		self.assertEqual(
			(crm.omni_items_for_scoring, float(crm.omni_award_value_bn), crm.omni_active_awards),
			(180, 4.5, 1),
		)

	def test_cli_report_text_for_a_log(self):
		log = importer.import_workbook(
			str(self.make()), dry_run=True, user="Administrator", now=True, commit=False
		)
		text = importer.format_log_report(log)
		for fragment in (
			"CHẠY THỬ",
			"NhaThau: 3 dòng",
			"Cần xác nhận: 0",
			"Top 10 trong CRM",
			"TD303 Nhà thầu A",
		):
			self.assertIn(fragment, text)


class TestExistingCrmData(ImportCase):
	def test_org_with_same_name_and_no_tax_id_is_not_given_a_tax_id(self):
		existing = make_org("TD303 Nhà thầu A")  # từ dữ liệu v1 (V2-02): chưa có MST
		lead = make_lead("TD303 người v1", existing, SALE2)
		contacts_before = frappe.db.count(CONTACT)
		report = self.run_import(self.make())
		self.assertIsNone(frappe.db.get_value(ORG, existing, "omni_tax_id"))  # KHÔNG tự gán MST
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": self.tax("A")}), 0)
		self.assertEqual(frappe.db.count(LEAD, {"organization": existing}), 1)  # không nhân đôi Lead v1
		self.assertEqual(frappe.db.get_value(LEAD, lead, "lead_owner"), SALE2)
		kinds = [c["kind"] for c in report["confirm"]]
		self.assertIn("org_trung_ten_chua_co_mst", kinds)
		entry = next(c for c in report["confirm"] if c["kind"] == "org_trung_ten_chua_co_mst")
		self.assertEqual(len(entry["records"]), 2)  # hai bản ghi để người duyệt chọn gộp
		self.assertEqual(entry["records"][0]["tên"], existing)
		self.assertEqual(entry["records"][1]["mã số thuế"], self.tax("A"))
		# gói thầu và người liên hệ của nhà thầu bị giữ lại, không lỗi, không nhập
		self.assertEqual(report["sheets"]["NhaThau"]["created"], 2)  # B, C vẫn nhập
		self.assertEqual(report["sheets"]["GoiThau"]["created"], 7)  # chỉ B (5) và C (2)
		self.assertEqual(frappe.db.count(CONTACT) - contacts_before, 1)  # chỉ người của B
		self.assertGreater(report["counters"]["held_rows"], 0)
		self.assertEqual(report["sheets"]["GoiThau"]["errors"], 0)
		wb = load_workbook(self._write(report["error_file"]))
		self.assertEqual(wb["CanXacNhan"].cell(row=2, column=1).value, "org_trung_ten_chua_co_mst")

	def _write(self, content: bytes) -> Path:
		p = self.path("loi.xlsx")
		p.write_bytes(content)
		return p

	def test_org_with_same_name_but_other_tax_id_is_held(self):
		make_org("TD303 Nhà thầu A", "0903030077")
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=[]))
		self.assertEqual([c["kind"] for c in report["confirm"]], ["org_trung_ten_khac_mst"])
		self.assertEqual(frappe.db.get_value(ORG, "TD303 Nhà thầu A", "omni_tax_id"), "0903030077")

	def test_open_lead_is_linked_not_duplicated_and_keeps_owner(self):
		org = make_org("TD303 Nhà thầu có Lead", self.tax("A"))
		lead = make_lead("TD303 khách tự nhắn", org, SALE2)
		orgs = synth_orgs("A")
		orgs[0]["name"] = "TD303 Nhà thầu có Lead"
		orgs[0]["stage"] = "Đã trao đổi nhu cầu"
		orgs[0]["owner_email"] = SALE1
		report = self.run_import(self.make(orgs=orgs, contacts=[]))
		self.assertEqual(frappe.db.count(LEAD, {"organization": org}), 1)
		self.assertEqual(frappe.db.get_value(LEAD, lead, "lead_owner"), SALE2)  # giữ người phụ trách
		self.assertNotEqual(
			frappe.db.get_value(LEAD, lead, "status"), "Đã trao đổi nhu cầu"
		)  # không đổi trạng thái
		self.assertEqual(report["counters"]["leads_linked"], 1)
		self.assertEqual(report["counters"].get("leads_created", 0), 0)
		self.assertEqual(
			frappe.db.get_value(ORG, org, "omni_fit_score"), expected_fit("A")["fit_score"]
		)  # có cập nhật điểm
		self.assertTrue(any("giữ nguyên" in w for w in report["warnings"]))

	def test_org_with_open_deal_gets_no_new_lead(self):
		org = make_org("TD303 Nhà thầu có Deal", self.tax("B"))
		deal = frappe.get_doc({"doctype": "CRM Deal", "organization": org, "status": "Qualification"}).insert(
			ignore_permissions=True
		)
		orgs = synth_orgs("B")
		orgs[0]["name"] = "TD303 Nhà thầu có Deal"
		report = self.run_import(self.make(orgs=orgs, awards=[], contacts=[]))
		self.assertEqual(frappe.db.count(LEAD, {"organization": org}), 0)
		self.assertEqual(report["counters"]["leads_skipped_open_deal"], 1)
		self.assertTrue(deal.name)

	def test_lost_lead_created_by_import_is_not_recreated_on_reimport(self):
		orgs = synth_orgs("A")
		orgs[0]["stage"] = "Không phù hợp"
		p = self.make(orgs=orgs, awards=[], contacts=[])
		self.run_import(p)
		lead = frappe.get_doc(LEAD, {"organization": self.org_name("A")})
		self.assertEqual(
			(lead.status, lead.lost_reason, lead.lost_notes),
			("Không phù hợp", "Other", importer.IMPORT_LOST_NOTE),
		)
		self.run_import(p)
		self.assertEqual(frappe.db.count(LEAD, {"organization": self.org_name("A")}), 1)

	def test_existing_contact_with_same_phone_in_other_format_is_updated(self):
		org = make_org("TD303 Nhà thầu A", self.tax("A"))
		old = frappe.get_doc(
			{
				"doctype": CONTACT,
				"first_name": "Thử Cũ",
				"company_name": org,
				"phone_nos": [{"phone": "+84 900 000 001"}],
			}
		).insert(ignore_permissions=True)
		self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=synth_contacts("A")[:1]))
		self.assertEqual(frappe.db.count(CONTACT, {"company_name": org}), 1)  # cập nhật, không tạo mới
		doc = frappe.get_doc(CONTACT, old.name)
		self.assertEqual(doc.omni_info_source, "Website nhà thầu")
		self.assertEqual(doc.designation, "Giám đốc")
		self.assertEqual(len(doc.phone_nos), 1)  # số cũ cùng chuẩn hóa: không thêm dòng thừa
		self.assertEqual(len(doc.email_ids), 1)  # email mới được thêm

	def test_contact_of_other_organization_is_not_taken_over(self):
		other = make_org("TD303 Nhà thầu khác")
		frappe.get_doc(
			{
				"doctype": CONTACT,
				"first_name": "Người",
				"last_name": "Chung",
				"company_name": other,
				"phone_nos": [{"phone": "0900000001"}],
			}
		).insert(ignore_permissions=True)
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=synth_contacts("A")[:1]))
		self.assertEqual(report["sheets"]["NguoiLienHe"]["created"], 0)
		self.assertEqual([c["kind"] for c in report["confirm"]], ["contact_khac_nha_thau"])
		self.assertEqual(frappe.db.count(CONTACT, {"company_name": self.org_name("A")}), 0)

	def test_refused_consent_is_never_loosened_by_import(self):
		self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=synth_contacts("A")[1:2]))
		name = frappe.db.get_value(CONTACT, {"company_name": self.org_name("A")})
		frappe.db.set_value(CONTACT, name, "omni_consent", "Từ chối")
		report = self.run_import(
			self.make(orgs=synth_orgs("A"), awards=[], contacts=synth_contacts("A")[1:2])
		)  # tệp ghi "Có"
		self.assertEqual(frappe.db.get_value(CONTACT, name, "omni_consent"), "Từ chối")
		self.assertIn("contact_tu_choi", [c["kind"] for c in report["confirm"]])

	def test_erased_contact_is_not_recreated_and_leaves_no_trace_in_reports(self):
		contacts = synth_contacts("A")[:1]
		self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=contacts))
		name = frappe.db.get_value(CONTACT, {"company_name": self.org_name("A")})
		contact_lib.erase_contact(name, "yêu cầu của khách")
		before = frappe.db.count(CONTACT)
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=contacts))
		self.assertEqual(frappe.db.count(CONTACT), before)  # không tạo lại
		self.assertEqual(report["counters"]["contacts_erased_skipped"], 1)
		self.assertEqual(report["sheets"]["NguoiLienHe"]["errors"], 0)
		blob = json.dumps(
			report["warnings"] + [json.dumps(c, ensure_ascii=False) for c in report["confirm"]],
			ensure_ascii=False,
		)
		for secret in ("Nguyễn Văn Thử", "0900 000 001", "0900000001", "thu.a@"):
			self.assertNotIn(secret, blob)
		self.assertNotIn(b"Nguy", report["error_file"] or b"")

	def test_erased_person_is_matched_by_email_only_row_too(self):
		contacts = [{**synth_contacts("A")[0], "phone": None}]
		self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=contacts))
		name = frappe.db.get_value(CONTACT, {"company_name": self.org_name("A")})
		contact_lib.erase_contact(name)
		before = frappe.db.count(CONTACT)
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=contacts))
		self.assertEqual(
			(frappe.db.count(CONTACT), report["counters"]["contacts_erased_skipped"]), (before, 1)
		)

	def test_same_tax_id_with_different_names_in_file_is_held_not_overwritten(self):
		orgs = [*synth_orgs("A"), {"tax_id": self.tax("A"), "name": "TD303 Tên khác hẳn"}]
		report = self.run_import(self.make(orgs=orgs, awards=synth_awards("A"), contacts=[]))
		self.assertEqual(report["sheets"]["NhaThau"]["created"], 0)
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": self.tax("A")}), 0)
		self.assertEqual([c["kind"] for c in report["confirm"]], ["mst_trung_khac_ten_trong_file"])
		self.assertEqual(len(report["confirm"][0]["records"]), 2)
		self.assertEqual(report["sheets"]["GoiThau"]["created"], 0)

	def test_same_tax_id_same_name_twice_is_skipped_as_duplicate(self):
		orgs = synth_orgs("A") * 2
		report = self.run_import(self.make(orgs=orgs, awards=[], contacts=[]))
		self.assertEqual(
			(report["sheets"]["NhaThau"]["created"], report["sheets"]["NhaThau"]["skipped"]), (1, 1)
		)

	def test_org_with_tax_id_but_different_name_keeps_crm_name_and_asks(self):
		make_org("TD303 Tên trong CRM", self.tax("A"))
		report = self.run_import(self.make(orgs=synth_orgs("A"), awards=[], contacts=[]))
		self.assertIn("org_ten_khac_crm", [c["kind"] for c in report["confirm"]])
		self.assertIsNotNone(frappe.db.exists(ORG, "TD303 Tên trong CRM"))
		self.assertEqual(frappe.db.count(ORG, {"organization_name": "TD303 Nhà thầu A"}), 0)

	def test_deal_stage_converts_lead_with_contact_and_without_contact_waits(self):
		orgs = synth_orgs("AB")
		orgs[0]["stage"] = "Đề xuất gói"  # A có người liên hệ trong tệp
		orgs[1]["stage"] = "Đàm phán"  # B có Contact nhưng ta bỏ để thử nhánh chờ
		contacts = synth_contacts("A")[:1]
		report = self.run_import(self.make(orgs=orgs, awards=[], contacts=contacts))
		deal = frappe.get_all(
			"CRM Deal", filters={"organization": self.org_name("A")}, fields=["status", "lead"]
		)
		self.assertEqual(len(deal), 1)
		self.assertEqual(deal[0].status, "Đề xuất gói")
		self.assertEqual(report["counters"]["deals_converted"], 1)
		self.assertEqual(frappe.db.count("CRM Deal", {"organization": self.org_name("B")}), 0)
		self.assertIn("deal_thieu_nguoi_lien_he", [c["kind"] for c in report["confirm"]])
		self.assertEqual(
			frappe.db.get_value(LEAD, {"organization": self.org_name("B")}, "status"), "Đã trao đổi nhu cầu"
		)
		# nhập lại: không tạo Lead mới, không tạo thêm Deal
		self.run_import(self.make(orgs=orgs, awards=[], contacts=contacts))
		self.assertEqual(frappe.db.count("CRM Deal", {"organization": self.org_name("A")}), 1)
		self.assertEqual(frappe.db.count(LEAD, {"organization": self.org_name("A")}), 1)
		self.assertEqual(
			frappe.db.count(CONTACT, {"company_name": self.org_name("A")}), 1
		)  # chuyển Deal không đẻ Contact rác

	def test_invalid_owner_email_leaves_lead_unassigned_with_warning(self):
		orgs = synth_orgs("A")
		orgs[0]["owner_email"] = "khong.co.user@example.com"
		report = self.run_import(self.make(orgs=orgs, awards=[], contacts=[]))
		self.assertFalse(frappe.db.get_value(LEAD, {"organization": self.org_name("A")}, "lead_owner"))
		self.assertTrue(any("không phải User hợp lệ" in w for w in report["warnings"]))

	def test_award_update_by_import_key_and_no_duplicates_without_notice_no(self):
		awards = synth_awards("C")
		for a in awards:
			a["notice_no"] = ""
		self.run_import(self.make(orgs=synth_orgs("C"), awards=awards, contacts=[]))
		self.assertEqual(frappe.db.count(AWARD, {"tax_id": self.tax("C")}), 2)
		self.run_import(self.make(orgs=synth_orgs("C"), awards=awards, contacts=[]))
		self.assertEqual(
			frappe.db.count(AWARD, {"tax_id": self.tax("C")}), 2
		)  # khóa không SBMT: ngày dạng datetime không tạo trùng
		awards[0]["package_name"] = "Tên gói đã sửa"
		report = self.run_import(self.make(orgs=synth_orgs("C"), awards=awards, contacts=[]))
		self.assertEqual(report["sheets"]["GoiThau"]["updated"], 1)

	def test_duplicate_award_row_in_file_is_skipped(self):
		awards = synth_awards("C")
		report = self.run_import(self.make(orgs=synth_orgs("C"), awards=[*awards, awards[0]], contacts=[]))
		self.assertEqual(
			(report["sheets"]["GoiThau"]["created"], report["sheets"]["GoiThau"]["skipped"]), (2, 1)
		)


class TestJobAndAccess(ImportCase):
	def setUp(self):
		super().setUp()
		make_user(ADMIN2, "Omni Admin")
		self.set_switch(1)
		self.addCleanup(self.set_switch, 1)

	def set_switch(self, value: int) -> None:
		frappe.db.sql(
			"delete from `tabSingles` where doctype='Omni Settings' and field='prospect_import_enabled'"
		)
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','prospect_import_enabled', %s)",
			(str(value),),
		)

	def test_sales_user_cannot_run_importer(self):
		p = self.make()
		for user in (SALE1, "Guest"):
			with self.subTest(user), self.assertRaises(frappe.PermissionError):
				importer.import_workbook(str(p), dry_run=True, user=user, now=True, commit=False)
		frappe.set_user(SALE1)
		with self.assertRaises(frappe.PermissionError):
			prospect_api.download_template()
		with self.assertRaises(frappe.PermissionError):
			prospect_api.run_import("khong-co", 1)
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": ["like", "09030300%"]}), 0)

	def test_switch_off_refuses_to_run(self):
		self.set_switch(0)
		with self.assertRaises(frappe.ValidationError):
			importer.import_workbook(str(self.make()), user=ADMIN2, now=True, commit=False)
		self.set_switch(1)
		log = importer.create_log(source_name="x.xlsx", user=ADMIN2)
		frappe.db.set_value(LOG, log, "status", "Queued")
		self.set_switch(0)
		self.assertEqual(
			importer.run_job(log, commit=False, path=str(self.make())), "Refused"
		)  # job đã xếp hàng cũng từ chối

	def test_import_workbook_creates_log_with_counts_and_private_error_file(self):
		orgs = [*synth_orgs(), {"tax_id": "12345", "name": "TD303 lỗi"}]
		p = self.make(orgs=orgs)
		log = importer.import_workbook(str(p), dry_run=False, user=ADMIN2, now=True, commit=False)
		doc = frappe.get_doc(LOG, log)
		self.assertEqual(doc.status, "Done")
		self.assertEqual((doc.nt_created, doc.nt_errors, doc.gt_created, doc.lh_created), (3, 1, 16, 3))
		self.assertEqual(doc.run_by, ADMIN2)
		self.assertTrue(doc.file_hash)
		self.assertTrue(doc.error_file.startswith("/private/files/"))
		self.addCleanup(os.remove, frappe.get_site_path(doc.error_file.lstrip("/")))
		self.assertFalse(doc.dry_run)
		self.assertIn("NhaThau", doc.render_report())

	def test_dry_run_log_has_no_crm_writes(self):
		before = counts()
		log = importer.import_workbook(str(self.make()), dry_run=True, user=ADMIN2, now=True, commit=False)
		after = counts()
		self.assertEqual(frappe.db.get_value(LOG, log, "status"), "Done")
		self.assertEqual(after, before)

	def test_background_job_runs_as_guest_like_a_real_worker(self):
		log = importer.create_log(source_name="x.xlsx", dry_run=False, user=ADMIN2)
		frappe.db.set_value(LOG, log, "status", "Queued")
		frappe.set_user("Guest")
		self.assertEqual(
			importer.run_job(log, commit=False, path=str(self.make())), "Done"
		)  # quyền theo run_by, ghi bằng ignore_permissions
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": ["like", "09030300%"]}), 3)

	def test_job_refuses_when_run_by_is_not_admin(self):
		log = importer.create_log(source_name="x.xlsx", dry_run=True, user=SALE1)
		frappe.db.set_value(LOG, log, "status", "Queued")
		self.assertEqual(importer.run_job(log, commit=False, path=str(self.make())), "Refused")

	def test_workbook_error_marks_log_failed_with_message(self):
		bad = self.path("bad.xlsx")
		bad.write_text("không phải xlsx")
		log = importer.import_workbook(str(bad), user=ADMIN2, now=True, commit=False)
		doc = frappe.get_doc(LOG, log)
		self.assertEqual(doc.status, "Failed")
		self.assertIn("xlsx", doc.message)

	def test_start_without_attachment_is_refused(self):
		log = importer.create_log(source_name="x.xlsx", user=ADMIN2)
		with self.assertRaises(frappe.ValidationError):
			importer.start_log(log, True, ADMIN2)

	def test_private_upload_is_enqueued_on_omni_queue_and_runs_from_the_log(self):
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "td303-upload.xlsx",
				"content": self.make().read_bytes(),
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(os.remove, frappe.get_site_path(doc.file_url.lstrip("/")))
		with mock.patch("frappe.enqueue") as enqueue:
			log = importer.import_workbook(doc.file_url, dry_run=True, user=ADMIN2)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.args[0], importer.JOB_METHOD)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "omni")
		self.assertEqual(enqueue.call_args.kwargs["log"], log)
		self.assertEqual(frappe.db.get_value(LOG, log, "status"), "Queued")
		self.assertEqual(
			importer.run_job(log, commit=False), "Done"
		)  # việc nền đọc tệp từ nhật ký, không nhận đường dẫn

	def test_file_references_are_confined_to_private_files(self):
		for ref in ("/files/x.xlsx", "https://example.com/x.xlsx", "/private/files/../../site_config.json"):
			with self.subTest(ref), self.assertRaises(frappe.ValidationError):
				importer.resolve_file(ref)
		with (
			mock.patch.object(frappe.local, "request", object(), create=True),
			self.assertRaises(frappe.PermissionError),
		):
			importer.resolve_file(str(self.make()))  # đường dẫn tuyệt đối không nhận qua yêu cầu web

	def test_log_rejects_public_attachment(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": LOG, "file": "/files/x.xlsx"}).insert(ignore_permissions=True)

	def test_download_template_serves_original_file_to_admin(self):
		frappe.set_user(ADMIN2)
		prospect_api.download_template()
		self.assertEqual(frappe.response["filecontent"], TEMPLATE.read_bytes())
		self.assertEqual(frappe.response["type"], "download")


class TestSources(ImportCase):
	def test_excel_source_returns_award_records(self):
		src = ExcelSource(self.make(awards=[*synth_awards("C"), {"tax_id": "bad", "hospital": "x"}]))
		got = src.fetch_awards()
		self.assertEqual(len(got), 2)
		self.assertIsInstance(got[0], AwardRecord)
		self.assertEqual(len(src.skipped), 1)
		self.assertEqual(len(src.fetch_awards(since=date(2100, 1, 1))), 0)
