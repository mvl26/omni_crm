"""V2-01 (07.08): "Xóa dữ liệu khách" phủ Omni Call và CRM Call Log của Lead."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import admin

PHONE = "+84901234567"
LOG_FIELDS = ["from", "to", "recording_url", "status", "duration"]


class TestEraseCalls(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(frappe.db.rollback)
		self.a = self.lead("ER Khach A")
		self.b = self.lead("ER Khach B")
		self.call_a, self.log_a = self.call("ER-A1", self.a)
		self.call_b, self.log_b = self.call("ER-B1", self.b)

	def lead(self, name):
		return (
			frappe.get_doc({"doctype": "CRM Lead", "first_name": name}).insert(ignore_permissions=True).name
		)

	def call(self, call_id, lead, ref_dt="CRM Lead", ref_name=None):
		log = frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": f"stringee-{call_id}",
				"from": PHONE,
				"to": "1900",
				"type": "Incoming",
				"status": "Completed",
				"duration": 61,
				"recording_url": "https://example.invalid/rec.mp3",
				"reference_doctype": ref_dt,
				"reference_docname": ref_name or lead,
			}
		).insert(ignore_permissions=True)
		call = frappe.get_doc(
			{
				"doctype": "Omni Call",
				"provider": "Stringee",
				"call_id": call_id,
				"direction": "Inbound",
				"status": "Answered",
				"from_number": PHONE,
				"to_number": "1900",
				"duration_seconds": 61,
				"lead": lead,
				"crm_call_log": log.name,
				"recording_ref": "rec-1",
				"raw_events": json.dumps([{"from": PHONE}]),
			}
		).insert(ignore_permissions=True)
		return call.name, log.name

	def test_calls_and_call_logs_of_the_lead_are_erased_but_technical_data_stays(self):
		res = admin.erase_customer_data(self.a, "Khách yêu cầu xóa dữ liệu")
		self.assertEqual((res["calls"], res["call_logs"]), (1, 1))
		call = frappe.db.get_value(
			"Omni Call",
			self.call_a,
			["from_number", "to_number", "raw_events", "recording_ref", "status", "duration_seconds", "lead"],
			as_dict=True,
		)
		self.assertEqual((call.from_number, call.to_number), ("[đã xóa]", "[đã xóa]"))
		self.assertFalse(call.raw_events)
		self.assertFalse(call.recording_ref)
		self.assertEqual((call.status, call.duration_seconds, call.lead), ("Answered", 61, self.a))
		log = frappe.db.get_value("CRM Call Log", self.log_a, LOG_FIELDS, as_dict=True)
		self.assertEqual((log["from"], log["to"]), ("[đã xóa]", "[đã xóa]"))
		self.assertFalse(log.recording_url)
		self.assertEqual((log.status, log.duration), ("Completed", 61))

	def test_other_leads_calls_are_untouched(self):
		admin.erase_customer_data(self.a, "Khách yêu cầu xóa dữ liệu")
		call = frappe.db.get_value(
			"Omni Call", self.call_b, ["from_number", "raw_events", "recording_ref"], as_dict=True
		)
		self.assertEqual(call.from_number, PHONE)
		self.assertTrue(call.raw_events and call.recording_ref)
		log = frappe.db.get_value("CRM Call Log", self.log_b, LOG_FIELDS, as_dict=True)
		self.assertEqual(log["from"], PHONE)
		self.assertTrue(log.recording_url)

	def test_call_log_on_the_deal_of_the_lead_is_erased(self):
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": self.a}).insert(ignore_permissions=True).name
		_, deal_log = self.call("ER-A2", self.a, "CRM Deal", deal)
		res = admin.erase_customer_data(self.a, "Khách yêu cầu xóa dữ liệu")
		self.assertEqual(res["call_logs"], 2)
		self.assertEqual(frappe.db.get_value("CRM Call Log", deal_log, "from"), "[đã xóa]")

	def test_rerun_is_harmless_and_comment_counts_calls(self):
		admin.erase_customer_data(self.a, "Khách yêu cầu xóa dữ liệu")
		again = admin.erase_customer_data(self.a, "Chạy lại để chắc chắn")
		self.assertEqual(again["calls"], 1)
		comments = frappe.get_all("Comment", filters={"reference_name": self.a}, pluck="content")
		self.assertTrue(any("1 cuộc gọi" in c for c in comments))
