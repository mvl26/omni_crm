"""API lịch demo cho trang Lịch demo."""

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import script as api


class TestDemoCalendar(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "K10 Lịch", "organization": "K10 Công ty A"}
		).insert(ignore_permissions=True)
		frappe.db.set_value("CRM Lead", self.lead.name, {"omni_demo_at": "2031-03-05 10:00:00", "omni_demo_mode": "Online qua Teams", "omni_demo_attendees": 5})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_week_lists_booking_and_busy_slot(self):
		got = api.demo_calendar("2031-03-03", 7)
		self.assertEqual((got["start"], got["end"]), ("2031-03-03", "2031-03-10"))
		mine = [i for i in got["items"] if i["lead"] == self.lead.name]
		self.assertEqual(len(mine), 1)
		self.assertEqual((mine[0]["start"], mine[0]["end"]), ("2031-03-05 10:00", "2031-03-05 10:30"))
		self.assertEqual((mine[0]["title"], mine[0]["attendees"]), ("K10 Công ty A", 5))
		self.assertIn("2031-03-05 10:00", got["busy"])

	def test_other_week_is_empty_and_days_are_capped(self):
		got = api.demo_calendar("2031-03-10", 7)
		self.assertFalse([i for i in got["items"] if i["lead"] == self.lead.name])
		self.assertEqual(api.demo_calendar("2031-03-01", 999)["end"], "2031-04-15")

	def test_default_start_is_monday(self):
		from frappe.utils import getdate

		self.assertEqual(getdate(api.demo_calendar()["start"]).weekday(), 0)

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, api.demo_calendar, "2031-03-03", 7)
