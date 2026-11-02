# ruff: noqa: RUF001
"""V2-02: 'Xóa dữ liệu khách' mở rộng sang Contact (docs/14 mục 6): xóa họ tên/SĐT/email/ghi chú, giữ bản ghi ẩn danh,
nhớ người đã xóa bằng khóa băm, không động tới đồng nghiệp cùng nhà thầu."""

from unittest import mock

import frappe

from omni_crm.api import admin
from omni_crm.prospect import contacts as pc
from omni_crm.tests.identity_helpers import IdentityTestCase, phone
from omni_crm.tests.test_resolver_orgs import cleanup_orgs, make_contact, make_org

CONTACT = "Contact"


class TestEraseContacts(IdentityTestCase):
	def setUp(self):
		super().setUp()
		cleanup_orgs()
		self.addCleanup(cleanup_orgs)
		enq = mock.patch("frappe.enqueue")
		enq.start()
		self.addCleanup(enq.stop)
		self.org = make_org("TD213R Xóa", "0100213701")
		self.person = make_contact(self.org, "TD213R Lan", phone_no="0903 105 801", email="Lan@td105.example")
		self.mate = make_contact(self.org, "TD213R Mai", phone_no="0903105802")
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": CONTACT,
				"reference_name": self.person,
				"content": "Ghi chú riêng về chị Lan",
			}
		).insert(ignore_permissions=True)
		self.lead = self.make_lead(
			"TD105 Lan", mobile="0903 105 801", organization=self.org
		)  # Lead của chính chị Lan
		self.other_lead = self.make_lead("TD105 Mai", mobile=phone(2), organization=self.org)
		frappe.db.commit()

	def test_erase_contact_clears_personal_data_keeps_anonymous_record(self):
		self.assertTrue(pc.erase_contact(self.person, "Khách yêu cầu"))
		frappe.db.commit()
		name = frappe.db.get_value(CONTACT, {"first_name": "[đã xóa]"}, "name")
		self.assertTrue(name and name.startswith("Đã xóa-"), name)  # tên bản ghi không còn họ tên
		self.assertFalse(frappe.db.exists(CONTACT, self.person))
		row = frappe.db.get_value(
			CONTACT,
			name,
			[
				"first_name",
				"last_name",
				"email_id",
				"mobile_no",
				"company_name",
				"omni_consent",
				"omni_info_source",
			],
			as_dict=True,
		)
		self.assertEqual(
			(row.first_name, row.last_name, row.email_id, row.mobile_no), ("[đã xóa]", None, None, None)
		)
		self.assertEqual((row.company_name, row.omni_consent), (self.org, "Từ chối"))
		self.assertEqual(row.omni_info_source, "TD213 test")
		self.assertEqual(frappe.db.count("Contact Phone", {"parent": name}), 0)
		self.assertEqual(frappe.db.count("Contact Email", {"parent": name}), 0)
		self.assertEqual(
			frappe.db.count("Comment", {"reference_doctype": CONTACT, "reference_name": name}), 0
		)
		self.assertEqual(
			frappe.db.count("Version", {"ref_doctype": CONTACT, "docname": ["in", [name, self.person]]}), 0
		)
		# không còn dấu vết họ tên trong cột nào của hàng
		raw = frappe.db.get_value(CONTACT, name, ["*"], as_dict=True)
		raw.pop("omni_erased_keys")  # chỉ chứa khóa băm (hex), có thể trùng chữ số ngẫu nhiên
		dump = " ".join(str(v) for v in raw.values())
		self.assertNotIn("Lan", dump.replace("Lanh", ""))
		self.assertNotIn("801", dump)

	def test_erased_person_is_remembered_by_hash_only(self):
		pc.erase_contact(self.person)
		frappe.db.commit()
		self.assertTrue(pc.is_erased_person(phone="0903105801"))
		self.assertTrue(pc.is_erased_person(email="LAN@td105.example"))
		self.assertTrue(pc.is_erased_person(phone="+84 903 105 801"))
		self.assertFalse(pc.is_erased_person(phone="0903105802"))
		stored = frappe.db.get_value(CONTACT, {"first_name": "[đã xóa]"}, "omni_erased_keys")
		self.assertNotIn("903105801", stored)
		self.assertNotIn("td105", stored)

	def test_erased_contact_is_no_longer_matched_by_resolver_keys(self):
		self.assertEqual([c.name for c in pc.contacts_matching(["+84903105801"], [])], [self.person])
		pc.erase_contact(self.person)
		self.assertEqual(pc.contacts_matching(["+84903105801"], []), [])

	def test_erase_twice_is_noop(self):
		self.assertTrue(pc.erase_contact(self.person))
		name = frappe.db.get_value(CONTACT, {"first_name": "[đã xóa]"}, "name")
		self.assertFalse(pc.erase_contact(name))

	def test_erase_customer_data_erases_only_the_leads_own_contact(self):
		res = admin.erase_customer_data(self.lead, "Khách yêu cầu xóa dữ liệu")
		self.assertEqual(res["contacts"], 1)
		self.assertFalse(frappe.db.exists(CONTACT, self.person))
		self.assertEqual(
			frappe.db.get_value(CONTACT, self.mate, "first_name"), "TD213R Mai"
		)  # đồng nghiệp còn nguyên
		self.assertEqual(frappe.db.get_value("CRM Lead", self.other_lead, "organization"), self.org)
		self.assertTrue(frappe.db.exists("CRM Organization", self.org))
		comment = frappe.get_all(
			"Comment",
			filters={"reference_name": self.lead, "content": ["like", "%người liên hệ%"]},
			pluck="content",
		)
		self.assertEqual(len(comment), 1)

	def test_erase_contact_api_admin_only_and_needs_reason(self):
		user = "td213_sale_erase@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "TD213",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()

		def drop():
			frappe.set_user("Administrator")
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
			frappe.db.commit()

		self.addCleanup(drop)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			admin.erase_contact_data(self.person, "yêu cầu của khách")
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			admin.erase_contact_data(self.person, " ")
		with self.assertRaises(frappe.DoesNotExistError):
			admin.erase_contact_data("Không có", "yêu cầu của khách")
		self.assertEqual(admin.erase_contact_data(self.person, "yêu cầu của khách"), {"erased": True})
		self.assertEqual(
			len(
				frappe.get_all(
					"Comment",
					filters={"reference_name": self.org, "content": ["like", "%xóa dữ liệu cá nhân%"]},
				)
			),
			1,
		)

	def test_contact_in_deal_of_lead_is_found(self):
		deal = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"organization": self.org,
				"lead": self.other_lead,
				"status": "Qualification",
				"contacts": [{"contact": self.mate}],
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.db.delete("CRM Contacts", {"parent": deal.name}),
				frappe.db.delete("CRM Deal", {"name": deal.name}),
				frappe.db.commit(),
			)
		)
		self.assertEqual(pc.contacts_of_lead(self.other_lead), [self.mate])

	def test_secondary_deal_contact_colleague_is_not_erased(self):
		# Review V2-02: sale gắn thêm đồng nghiệp vào Deal (Contact phụ) thì xóa dữ liệu của Lead không được xóa họ.
		deal = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"organization": self.org,
				"lead": self.lead,
				"status": "Qualification",
				"contacts": [{"contact": self.person}, {"contact": self.mate}],
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.db.delete("CRM Contacts", {"parent": deal.name}),
				frappe.db.delete("CRM Deal", {"name": deal.name}),
				frappe.db.commit(),
			)
		)
		self.assertEqual(pc.contacts_of_lead(self.lead), [self.person])
