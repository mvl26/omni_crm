"""K7 (bot kịch bản v3, mục 3.3): khung báo giá. Công tắc tắt, thiếu đầu vào thì nhân viên báo giá, không có con số."""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.script import engine, pricing
from omni_crm.script.flows import quote
from omni_crm.tests.test_k6_postdemo import FlowCase

SWITCHES = (*pricing.SWITCH_FIELDS.values(), pricing.REVIEW_FIELD)


class TestPricing(FlowCase):
	def setUp(self):
		super().setUp()
		# Đọc dòng thô trong tabSingles: trường chưa lưu lần nào thì KHÔNG có dòng, khôi phục phải giữ nguyên điều đó
		# (ghi 0 cho `script_quote_review_required` sẽ biến "chưa đặt" thành "đã tắt duyệt").
		self._quote_orig = {f: self._raw_single(f) for f in SWITCHES}
		self.addCleanup(self._restore_quote)

	@staticmethod
	def _raw_single(field: str):
		rows = frappe.db.sql("select value from tabSingles where doctype='Omni Settings' and field=%s", field)
		return rows[0][0] if rows else None

	def _restore_quote(self):
		for f, v in self._quote_orig.items():
			frappe.db.sql("delete from tabSingles where doctype='Omni Settings' and field=%s", f)
			if v is not None:
				frappe.db.set_single_value("Omni Settings", f, v)
		frappe.db.commit()

	def test_switches_default_off_and_review_default_on(self):
		meta = frappe.get_meta("Omni Settings")
		for field in pricing.SWITCH_FIELDS.values():
			self.assertEqual(str(meta.get_field(field).default or "0"), "0")
		self.assertEqual(str(meta.get_field(pricing.REVIEW_FIELD).default), "1")
		for field in pricing.SWITCH_FIELDS.values():
			frappe.db.set_single_value("Omni Settings", field, 0)
		for svc in (*pricing.SERVICES, "PHANMEM", "", None, "KHAC"):
			self.assertFalse(pricing.quote_enabled(svc))
			self.assertFalse(pricing.can_auto_quote(svc)[0])
		frappe.db.sql(
			"delete from tabSingles where doctype='Omni Settings' and field=%s", pricing.REVIEW_FIELD
		)
		self.assertTrue(pricing.review_required())  # chưa có dòng trong tabSingles = bắt buộc duyệt

	def test_all_inputs_missing(self):
		status = pricing.inputs_status()
		self.assertEqual(len(status), len(pricing.INPUTS))
		self.assertTrue(all(not v["configured"] for v in status.values()))
		for key in (
			"price_list",
			"auto_threshold",
			"supplycore_tiers",
			"spd_formula",
			"pdf_policy",
			"erp_account",
		):
			self.assertIn(key, status)
		self.assertTrue(pricing.missing_inputs("PHANMEM"))

	def test_switch_on_but_inputs_missing_still_no_auto_quote(self):
		for svc, field in pricing.SWITCH_FIELDS.items():
			frappe.db.set_single_value("Omni Settings", field, 1)
			self.assertTrue(pricing.quote_enabled(svc))
			ok, why = pricing.can_auto_quote(svc)
			self.assertFalse(ok)
			self.assertIn("thiếu", why)


class TestQuoteFallback(FlowCase):
	NODES = ("BG_XACNHAN_TRONGGIO", "BG_XACNHAN_NGOAIGIO", "BG_VT_KETQUA")

	def test_registered(self):
		for name in ("quote_show", "quote", "quote_match"):
			self.assertIn(name, engine._actions)

	def test_quote_show_falls_back_to_staff_task_without_numbers(self):
		frappe.db.set_value("CRM Lead", self.lead1, {"omni_services": "VATTU", "email": "k7@example.com"})
		quote.state.update_state(self.ident1, items="găng tay y tế", quantity="vài thùng")
		node = {"code": "BG_VT_KETQUA", "service": "VATTU"}
		self.assertTrue(quote._act_quote_show(self.ctx("BG_VT_KETQUA"), "", node))  # dừng chuỗi lệnh của ô
		self.assertEqual(len(self.tasks), 1)
		self.assertEqual(self.tasks[0]["service"], "VATTU")
		self.assertIn("găng tay y tế", self.tasks[0]["description"])
		sent = frappe.get_all(
			"Omni Outbound Message", filters={"identity": self.ident1}, fields=["script_node", "text"]
		)
		self.assertEqual(len(sent), 1)
		self.assertIn(sent[0].script_node, (quote.NODE_PROMISE_IN_HOURS, quote.NODE_PROMISE_AFTER_HOURS))
		self.assertNotIn("BG_VT_KETQUA", [s.script_node for s in sent])
		# câu hẹn chỉ được có con số của ngày hẹn (do hệ thống tính), không có giá
		allowed = [quote.timeutil.next_working_day_text(frappe.utils.now_datetime())]
		pricing.assert_no_ai_numbers(sent[0].text.replace("k7@example.com", ""), allowed)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_journey_status"), "Đang tư vấn")
		# cùng lượt tin: không tạo việc lần hai
		self.assertFalse(quote.show_or_fallback(self.ctx(), "VATTU"))
		self.assertEqual(len(self.tasks), 1)

	def test_switch_on_still_falls_back(self):
		field = pricing.SWITCH_FIELDS["SPD"]
		orig = frappe.db.get_single_value("Omni Settings", field)
		self.addCleanup(
			lambda: (frappe.db.set_single_value("Omni Settings", field, orig), frappe.db.commit())
		)
		frappe.db.set_single_value("Omni Settings", field, 1)
		self.assertFalse(quote.show_or_fallback(self.ctx(), "SPD"))
		self.assertEqual(len(self.tasks), 1)
		self.assertIn("thiếu", self.tasks[0]["description"])

	def test_manual_flag(self):
		quote._act_quote(self.ctx(), "manual", {})
		self.assertEqual(self.sstate()["quote_manual"], 1)


class TestNumberGuard(FrappeTestCase):
	def test_allowed_numbers_pass(self):
		text = "Tổng cộng: 1.500.000 đ cho 10 hộp, VAT 8%. Hiệu lực đến 17/12/2026."
		pricing.assert_no_ai_numbers(text, [1500000, 10, "8%", "17/12/2026"])
		pricing.assert_no_ai_numbers("Dạ, chuyên viên sẽ liên hệ anh/chị ạ.", [])
		pricing.assert_no_ai_numbers("Phí 12,5 triệu", ["12,5"])

	def test_unknown_number_raises(self):
		for text, allowed in (
			("Giá 1.500.000 đ", [1400000]),
			("Giảm thêm 5% cho anh/chị", []),
			("Đơn giá 12,5 nghìn", [125]),
			("Tổng 2.000.000, đã giảm 100.000", ["2.000.000"]),
		):
			with self.assertRaises(frappe.ValidationError):
				pricing.assert_no_ai_numbers(text, allowed)

	def test_numbers_in(self):
		self.assertEqual(pricing.numbers_in("10 hộp x 1.200,5 = 12.005."), ["10", "1.200,5", "12.005"])
