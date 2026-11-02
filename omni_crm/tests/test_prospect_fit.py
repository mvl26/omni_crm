# ruff: noqa: RUF001
"""V2-02: điểm phù hợp khớp mẫu Excel (docs/14 mục 3.1). Bộ dữ liệu: tests/fixtures/prospect/fit_cases.json."""

import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.prospect import fit

FIXTURE = Path(__file__).parent / "fixtures" / "prospect" / "fit_cases.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
TODAY = date.fromisoformat(DATA["today"])
SETTINGS = "Omni Settings"


def make_org_with_awards(case: dict) -> str:
	"""Tạo CRM Organization + gói thầu của một ca trong fixture; trả tên Organization."""
	org = frappe.get_doc(
		{
			"doctype": "CRM Organization",
			"organization_name": case["name"],
			"omni_tax_id": case["tax_id"],
			"omni_est_item_count": case["est_item_count"],
		}
	).insert(ignore_permissions=True)
	for a in case["awards"]:
		frappe.get_doc({"doctype": "Omni Bid Award", "organization": org.name, **a}).insert(
			ignore_permissions=True
		)
	return org.name


class TestFitPure(unittest.TestCase):
	PARAMS: ClassVar[dict] = {
		"fit_period_months": 24,
		"fit_min_hospitals": 5,
		"fit_min_items": 200,
		"fit_max_hospitals": 10,
		"fit_max_items": 500,
		"fit_max_value_bn": 20,
		"fit_w_hospitals": 40,
		"fit_w_items": 30,
		"fit_w_value": 20,
		"fit_w_active": 10,
		"fit_tier_a": 70,
		"categories": {"VTYT tiêu hao", "Hóa chất – sinh phẩm"},
	}

	def test_excel_round_is_half_up_not_bankers(self):
		self.assertEqual(fit.excel_round(Decimal("4.5")), 5)
		self.assertEqual(fit.excel_round(Decimal("2.5")), 3)
		self.assertEqual(fit.excel_round(Decimal("24.8")), 25)
		self.assertEqual(round(4.5), 4)  # Python làm tròn về chẵn: chính là điều không được dùng

	def test_half_point_score_rounds_up(self):
		# 30 * 75 / 500 = 4.5 điểm -> 5 (ROUND nửa lên của Excel; round() của Python cho 4)
		r = fit.fit_from_awards([], 75, self.PARAMS, TODAY)
		self.assertEqual(r["fit_score"], 5)

	def test_edate_month_end(self):
		# EDATE(31/12/2026; -24 tháng) = 31/12/2024: gói đúng ngày mốc vẫn được tính (so sánh >=)
		params = {**self.PARAMS, "fit_period_months": 24}
		awards = [
			{
				"category": "VTYT tiêu hao",
				"award_date": date(2024, 12, 31),
				"hospital_key": "a",
				"item_count": 1,
			}
		]
		self.assertEqual(fit.fit_from_awards(awards, 0, params, date(2026, 12, 31))["awards_in_period"], 1)
		# EDATE(28/02/2026; -12) = 28/02/2025: gói 27/02/2025 nằm ngoài kỳ
		awards = [
			{
				"category": "VTYT tiêu hao",
				"award_date": date(2025, 2, 27),
				"hospital_key": "a",
				"item_count": 1,
			}
		]
		p12 = {**self.PARAMS, "fit_period_months": 12}
		self.assertEqual(fit.fit_from_awards(awards, 0, p12, date(2026, 2, 28))["awards_in_period"], 0)

	def test_active_awards_ignore_period(self):
		awards = [
			{
				"category": "VTYT tiêu hao",
				"award_date": date(2020, 1, 1),
				"end_date": date(2027, 1, 1),
				"hospital_key": "a",
				"item_count": 1,
			}
		]
		r = fit.fit_from_awards(awards, 0, self.PARAMS, TODAY)
		self.assertEqual((r["awards_in_period"], r["active_awards"]), (0, 1))

	def test_tier_a_needs_criteria_and_threshold(self):
		awards = [
			{
				"category": "VTYT tiêu hao",
				"award_date": date(2026, 1, 1),
				"end_date": date(2027, 1, 1),
				"hospital_key": f"h{i}",
				"item_count": 300,
				"award_value": 3_000_000_000,
			}
			for i in range(10)
		]
		r = fit.fit_from_awards(awards, 0, self.PARAMS, TODAY)
		self.assertEqual((r["fit_score"], r["fit_tier"], r["meets_criteria"]), (88, "A", 1))


class TestFitCases(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("td213_fit")
		self.addCleanup(frappe.clear_document_cache, "Omni Settings", "Omni Settings")
		self.addCleanup(frappe.db.rollback, save_point="td213_fit")

	def test_cases_match_excel_expectations(self):
		for case in DATA["cases"]:
			with self.subTest(case["key"]):
				org = make_org_with_awards(case)
				got = fit.compute_fit(org, TODAY)
				for key, want in case["expected"].items():
					if isinstance(want, str):
						self.assertEqual(got[key], want, msg=f"{case['key']}.{key}")
					else:
						self.assertAlmostEqual(
							float(got[key]), float(want), places=6, msg=f"{case['key']}.{key}"
						)

	def test_apply_fit_writes_readonly_fields_on_award_change(self):
		case = DATA["cases"][1]
		org = make_org_with_awards(case)
		row = frappe.db.get_value(
			"CRM Organization", org, ["omni_hospital_count", "omni_fit_score", "omni_fit_tier"], as_dict=True
		)
		# điểm tính theo ngày hôm nay: gói tháng 2/2025 còn trong kỳ 24 tháng đến hết 02/2027
		self.assertEqual((row.omni_hospital_count, row.omni_fit_tier), (5, "B"))
		award = frappe.get_all("Omni Bid Award", filters={"organization": org}, pluck="name")[0]
		frappe.delete_doc("Omni Bid Award", award, ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("CRM Organization", org, "omni_hospital_count"), 4)
		self.assertEqual(frappe.db.get_value("CRM Organization", org, "omni_fit_tier"), "C")

	def test_est_item_count_change_recomputes(self):
		org = frappe.get_doc({"doctype": "CRM Organization", "organization_name": "TD213 Est"}).insert()
		self.assertEqual(frappe.db.get_value("CRM Organization", org.name, "omni_items_for_scoring"), 0)
		org.reload()
		org.omni_est_item_count = 400
		org.save()
		self.assertEqual(frappe.db.get_value("CRM Organization", org.name, "omni_items_for_scoring"), 400)

	def test_switch_off_keeps_old_values(self):
		org = make_org_with_awards(DATA["cases"][1])
		before = frappe.db.get_value("CRM Organization", org, ["omni_fit_score", "omni_fit_computed_at"])
		old = frappe.db.get_single_value(SETTINGS, "fit_scoring_enabled")
		self.addCleanup(frappe.db.set_single_value, SETTINGS, "fit_scoring_enabled", old)
		frappe.db.set_single_value(SETTINGS, "fit_scoring_enabled", 0)
		self.assertIsNone(fit.apply_fit(org))
		self.assertEqual(fit.recompute_all_fit(), {"organizations": 0, "skipped": 1})
		frappe.get_doc(
			{
				"doctype": "Omni Bid Award",
				"organization": org,
				"hospital": "BV Mới",
				"category": "VTYT tiêu hao",
				"award_date": "2026-01-01",
				"award_value": 1,
				"item_count": 1,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("CRM Organization", org, ["omni_fit_score", "omni_fit_computed_at"]), before
		)

	def test_nightly_recompute_covers_orgs_with_awards(self):
		org = make_org_with_awards(DATA["cases"][2])
		frappe.db.set_value("CRM Organization", org, "omni_fit_score", 0, update_modified=False)
		with mock.patch.object(
			frappe.db, "commit"
		):  # tác vụ nền commit thật; test giữ trong savepoint để dọn
			result = fit.recompute_all_fit()
		self.assertGreaterEqual(result["organizations"], 1)
		self.assertGreater(frappe.db.get_value("CRM Organization", org, "omni_fit_score"), 0)

	def test_defaults_match_threshold_sheet_when_settings_never_saved(self):
		frappe.db.delete("Singles", {"doctype": SETTINGS, "field": ["like", "fit\\_%"]})
		params = fit.load_params()
		self.assertEqual(
			(params["fit_period_months"], params["fit_min_hospitals"], params["fit_min_items"]), (24, 5, 200)
		)
		self.assertEqual(
			(params["fit_max_hospitals"], params["fit_max_items"], params["fit_max_value_bn"]), (10, 500, 20)
		)
		self.assertEqual(
			[params[k] for k in ("fit_w_hospitals", "fit_w_items", "fit_w_value", "fit_w_active")],
			[40, 30, 20, 10],
		)
		self.assertEqual(params["fit_tier_a"], 70)
