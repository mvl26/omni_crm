"""V3-06: nút "Tạo liên hệ" trên Lead (omni_crm.api.lead_contact). Dữ liệu commit thật nên tự dọn (tiền tố TEST-LC)."""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import lead_contact

PREFIX = "TEST-LC"
SALE1, SALE2 = "lc_sale1@example.com", "lc_sale2@example.com"


def cleanup() -> None:
	frappe.db.rollback()
	frappe.set_user("Administrator")
	leads = frappe.get_all("CRM Lead", filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name")
	contacts = set(frappe.get_all("Contact", filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name"))
	contacts.update(
		frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Contact", "link_doctype": "CRM Lead", "link_name": ["in", leads or [""]]},
			pluck="parent",
		)
	)
	for c in contacts:
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
	refs = leads or [""]
	frappe.db.delete("Comment", {"reference_name": ["in", refs]})
	frappe.db.delete("CRM Notification", {"reference_name": ["in", refs]})
	frappe.db.delete("ToDo", {"reference_name": ["in", refs]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("ToDo", {"allocated_to": ["in", [SALE1, SALE2]]})
	frappe.db.delete("CRM Notification", {"to_user": ["in", [SALE1, SALE2]]})
	for u in (SALE1, SALE2):
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.commit()


class TestLeadContact(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		with mock.patch("frappe.enqueue"):
			for i, email in enumerate((SALE1, SALE2), 1):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": f"LC Sale{i}",
						"send_welcome_email": 0,
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)
		frappe.db.commit()

	def make_lead(self, name, owner=SALE1, **kw):
		doc = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": f"{PREFIX} {name}", "lead_owner": owner, **kw}
		)
		doc.flags.ignore_permissions = True
		with mock.patch("frappe.enqueue"):
			doc.insert()
		frappe.db.commit()
		return doc.name

	def sync(self, user, lead):
		frappe.set_user(user)
		try:
			with mock.patch("frappe.enqueue"):
				res = lead_contact.sync_from_lead(lead)
			frappe.db.commit()
		finally:
			frappe.set_user("Administrator")
		return res

	def test_creates_contact_without_deal(self):
		lead = self.make_lead(
			"Bùi Việt", mobile_no="0399688241", email="lc.viet@example.com", organization="Công ty ABC"
		)
		res = self.sync(SALE1, lead)
		self.assertTrue(res["created"])
		c = frappe.get_doc("Contact", res["contact"])
		self.assertEqual(c.first_name, f"{PREFIX} Bùi Việt")
		self.assertEqual(c.company_name, "Công ty ABC")
		self.assertEqual([e.email_id for e in c.email_ids], ["lc.viet@example.com"])
		self.assertEqual([p.phone for p in c.phone_nos], ["0399688241"])
		self.assertIn(("CRM Lead", lead), [(x.link_doctype, x.link_name) for x in c.links])
		self.assertFalse(frappe.db.exists("CRM Deal", {"lead": lead}))
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": lead, "content": ["like", "%liên hệ%"]})
		)

	def test_second_click_fetches_new_data_into_same_contact_without_overwriting(self):
		lead = self.make_lead("Cập Nhật", mobile_no="0399688242")
		first = self.sync(SALE1, lead)["contact"]
		frappe.db.set_value("Contact", first, "company_name", "Đơn vị gõ tay")
		frappe.db.set_value(
			"CRM Lead",
			lead,
			{"email": "lc.moi@example.com", "organization": "Khác", "job_title": "Trưởng khoa"},
		)
		frappe.db.commit()
		res = self.sync(SALE1, lead)
		self.assertEqual((res["contact"], res["created"], res["matched_by"]), (first, False, "link"))
		c = frappe.get_doc("Contact", first)
		self.assertEqual(c.company_name, "Đơn vị gõ tay")  # không ghi đè
		self.assertEqual(c.designation, "Trưởng khoa")
		self.assertEqual([e.email_id for e in c.email_ids], ["lc.moi@example.com"])
		self.assertEqual(len(c.phone_nos), 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "organization"), "Khác")  # Lead không bị sửa

	def test_matches_existing_contact_by_phone_tail(self):
		existing = frappe.get_doc(
			{"doctype": "Contact", "first_name": f"{PREFIX} Có Sẵn", "phone_nos": [{"phone": "+84988806848"}]}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		lead = self.make_lead("Trùng Số", mobile_no="0988806848")
		res = self.sync(SALE1, lead)
		self.assertEqual((res["contact"], res["created"], res["matched_by"]), (existing.name, False, "phone"))
		self.assertEqual(frappe.db.count("Contact Phone", {"parent": existing.name}), 1)

	def test_needs_lead_write_and_switch(self):
		lead = self.make_lead("Của Sale1", owner=SALE1, mobile_no="0399688243")
		frappe.set_user(SALE2)
		can = frappe.has_permission("CRM Lead", "write", doc=lead)
		frappe.set_user("Administrator")
		if not can:
			with self.assertRaises(frappe.PermissionError):
				self.sync(SALE2, lead)
		frappe.db.set_single_value("Omni Settings", "lead_contact_button_disabled", 1)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.db.set_single_value("Omni Settings", "lead_contact_button_disabled", 0),
				frappe.db.commit(),
			)
		)
		frappe.set_user(SALE1)
		self.assertFalse(lead_contact.state()["enabled"])
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			self.sync(SALE1, lead)
		self.assertFalse(frappe.db.exists("Dynamic Link", {"parenttype": "Contact", "link_name": lead}))
