"""D1-11: kiểm script đo chỉ số nghiệm thu `tests/acceptance/measure_dot1.py` trên dữ liệu dựng sẵn."""

from datetime import datetime, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.tests.acceptance import measure_dot1 as m
from omni_crm.tests.fake_adapter import make_channel

HOURS = {"mon-fri": "08:00-17:30", "sat": "08:00-12:00"}


class TestHelpers(FrappeTestCase):
	def test_business_hours(self):
		self.assertTrue(m._in_business_hours(datetime(2026, 10, 26, 8, 0), HOURS))  # thứ hai
		self.assertTrue(m._in_business_hours(datetime(2026, 10, 31, 11, 59), HOURS))  # thứ bảy
		self.assertFalse(m._in_business_hours(datetime(2026, 10, 26, 17, 31), HOURS))
		self.assertFalse(m._in_business_hours(datetime(2026, 10, 31, 12, 1), HOURS))
		self.assertFalse(m._in_business_hours(datetime(2026, 10, 25, 10, 0), HOURS))  # chủ nhật

	def test_percentile(self):
		self.assertEqual(m._pct([], 0.5), None)
		self.assertEqual(m._pct(list(range(1, 101)), 0.95), 95)
		self.assertEqual(m._pct([5], 0.95), 5)

	def test_table_verdicts(self):
		res = {
			"lost": {"lost_estimate": 1},
			"duplicate_leads": {"ratio": 0.01},
			"message_to_lead": {"p95_s": 30},
			"first_response": {"median_min": 20},
			"manual": {"ratio_manual": None},
			"gateway": {"uptime_pct_external": 99.9},
		}
		t = m._table(res)
		self.assertIn("| Tin bị mất | 0 | 1 | KHÔNG ĐẠT |", t)
		self.assertIn("| Lead trùng / Lead mới | < 3% | 0.01 | ĐẠT |", t)
		self.assertIn("| 20 | KHÔNG ĐẠT |", t)
		self.assertIn("| None | chưa đủ dữ liệu |", t)


class TestOnData(FrappeTestCase):
	"""Dữ liệu dựng trong giao dịch của test (rollback cuối test)."""

	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.channel = make_channel("MD1 kenh", enabled=1)
		self.start = frappe.utils.now_datetime() - timedelta(hours=1)
		self.end = frappe.utils.now_datetime() + timedelta(hours=1)

	def _inbound(self, lead: str, status="Done", attempts=0, minutes_ago=30):
		at = frappe.utils.now_datetime() - timedelta(minutes=minutes_ago)
		return frappe.get_doc(
			{
				"doctype": "Omni Inbound Message",
				"dedupe_key": f"MD1:{frappe.generate_hash(length=10)}",
				"channel": self.channel.name,
				"channel_type": "Zalo OA",
				"event_type": "message",
				"identity_key": "Zalo OA:MD1U",
				"lead": lead,
				"received_at": at,
				"status": status,
				"attempts": attempts,
				"text": "xin chào",
			}
		).insert(ignore_permissions=True)

	def _lead(self, name: str):
		return frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": name, "omni_first_channel": "Zalo OA"}
		).insert(ignore_permissions=True)

	def test_counts_and_ratios(self):
		a, b = self._lead("MD1 A"), self._lead("MD1 B")
		self._inbound(a.name)
		self._inbound(a.name, status="Manual", attempts=3)
		old = self._inbound(b.name, status="Retry", minutes_ago=30)
		frappe.db.set_value(
			"Omni Inbound Message", old.name, "creation", frappe.utils.now_datetime() - timedelta(minutes=30)
		)
		excl = m._excluded_channels(("ZZ",))
		lost = m.lost_messages(self.start, self.end, [c for c in excl if c != self.channel.name], None)
		self.assertGreaterEqual(lost["manual_unresolved"], 1)
		self.assertGreaterEqual(lost["stuck_over_10min"], 1)
		self.assertGreaterEqual(lost["lost_estimate"], 2)
		man = m.manual_handling(self.start, self.end, [])
		self.assertGreaterEqual(man["manual_now"], 1)
		dup = m.duplicate_leads(self.start, self.end)
		self.assertGreaterEqual(dup["leads_created"], 2)
		self.assertIsNotNone(m.message_to_lead(self.start, self.end, [])["p50_s"])

	def test_first_response_counts_sale_outbound_and_unanswered(self):
		lead = self._lead("MD1 C")
		first = self._inbound(lead.name, minutes_ago=30)
		# ép giờ vào giờ hành chính để ca không phụ thuộc giờ chạy test
		inside = datetime(2026, 10, 26, 9, 0)
		frappe.db.set_value("Omni Inbound Message", first.name, "received_at", inside)
		frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": self.channel.name,
				"lead": lead.name,
				"text": "Dạ em chào anh",
				"source": "Sale",
				"status": "Sent",
				"sent_at": inside + timedelta(minutes=7),
				"is_sandbox": 0,
			}
		).insert(ignore_permissions=True)
		lead2 = self._lead("MD1 D")
		second = self._inbound(lead2.name)
		frappe.db.set_value("Omni Inbound Message", second.name, "received_at", inside)
		res = m.first_response(datetime(2026, 10, 1), datetime(2026, 12, 31), [], False)
		self.assertGreaterEqual(res["answered"], 1)
		self.assertGreaterEqual(res["unanswered"], 1)
		self.assertIsNotNone(res["median_min"])
