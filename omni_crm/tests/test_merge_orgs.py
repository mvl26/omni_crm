# ruff: noqa: RUF001
"""V2-02: gộp Lead giữ liên kết Organization; gộp Organization trùng chuyển Contact, gói thầu, Lead, Deal."""

from unittest import mock

import frappe

from omni_crm.api import organization as org_api
from omni_crm.identity import merge
from omni_crm.tests.identity_helpers import IdentityTestCase
from omni_crm.tests.test_resolver_orgs import cleanup_orgs, make_contact, make_org

AWARD = "Omni Bid Award"
ORG = "CRM Organization"


class OrgMergeCase(IdentityTestCase):
	def setUp(self):
		super().setUp()
		cleanup_orgs()
		self.addCleanup(cleanup_orgs)
		enq = mock.patch("frappe.enqueue")  # hook Comment của CRM đẩy job nền
		enq.start()
		self.addCleanup(enq.stop)

	def comments(self, doctype: str, name: str) -> str:
		return " ".join(
			frappe.get_all(
				"Comment", filters={"reference_doctype": doctype, "reference_name": name}, pluck="content"
			)
		)


class TestLeadMergeKeepsOrganization(OrgMergeCase):
	def test_blank_primary_takes_organization_of_secondary(self):
		org = make_org("TD213R Gộp A", "0100213501")
		p = self.make_lead("TD105 Chinh")
		s = self.make_lead("TD105 Phu", organization=org)
		merge.merge_leads(p, s, "Administrator")
		self.assertEqual(frappe.db.get_value("CRM Lead", p, "organization"), org)

	def test_different_organizations_keep_primary_and_report_difference(self):
		a, b = make_org("TD213R Gộp B1", "0100213502"), make_org("TD213R Gộp B2", "0100213503")
		p = self.make_lead("TD105 Chinh", organization=a)
		s = self.make_lead("TD105 Phu", organization=b)
		res = merge.merge_leads(p, s, "Administrator")
		self.assertEqual(frappe.db.get_value("CRM Lead", p, "organization"), a)
		self.assertIn(("organization", a, b), res["differences"])
		self.assertIn(b, self.comments("CRM Lead", p))

	def test_organization_contacts_and_awards_are_untouched(self):
		org = make_org("TD213R Gộp C", "0100213504")
		contact = make_contact(org, "TD213R Chi", phone_no="0903105790")
		frappe.get_doc(
			{
				"doctype": AWARD,
				"organization": org,
				"hospital": "BV X",
				"category": "VTYT tiêu hao",
				"award_date": "2026-03-01",
			}
		).insert(ignore_permissions=True)
		p, s = self.make_lead("TD105 Chinh", organization=org), self.make_lead("TD105 Phu", organization=org)
		merge.merge_leads(p, s, "Administrator")
		self.assertEqual(frappe.db.get_value("Contact", contact, "company_name"), org)
		self.assertEqual(frappe.db.count(AWARD, {"organization": org}), 1)

	def test_care_log_link_is_registered_and_present_since_v2_04(self):
		self.assertIn(("Omni Care Log", "lead"), merge.OMNI_LEAD_LINKS)
		# Đổi có chủ đích ở V2-04: trước đó DocType chưa có nên bị bỏ qua (assertFalse); nay đã có nên được chuyển theo Lead
		self.assertTrue(merge._table_ok("Omni Care Log", "lead"))


class TestMergeOrganizations(OrgMergeCase):
	def setUp(self):
		super().setUp()
		self.p = make_org("TD213R Chính", None, website="chinh.example")
		self.s = make_org("TD213R Phụ", "0100213510", omni_est_item_count=300)
		self.contact = make_contact(self.s, "TD213R Nguoi", phone_no="0903105791")
		self.award = frappe.get_doc(
			{
				"doctype": AWARD,
				"organization": self.s,
				"hospital": "BV Gộp",
				"notice_no": "G1",
				"category": "VTYT tiêu hao",
				"award_date": "2026-03-01",
				"award_value": 2_000_000_000,
				"item_count": 10,
			}
		).insert(ignore_permissions=True)
		self.lead_s = self.make_lead("TD105 La", organization=self.s)
		self.lead_p = self.make_lead("TD105 Lb", organization=self.p)
		frappe.db.commit()

	def test_everything_moves_tax_id_goes_to_primary_and_secondary_is_gone(self):
		res = merge.merge_organizations(self.p, self.s, "Administrator")
		self.assertFalse(res["already_merged"])
		self.assertEqual(res["moved"], {"Contact": 1, "Omni Bid Award": 1, "CRM Lead": 1})
		self.assertEqual(frappe.db.get_value(ORG, self.p, "omni_tax_id"), "0100213510")
		self.assertEqual(frappe.db.get_value(ORG, self.p, "omni_est_item_count"), 300)
		self.assertFalse(frappe.db.exists(ORG, self.s))
		self.assertEqual(frappe.db.get_value("Contact", self.contact, "company_name"), self.p)
		self.assertEqual(frappe.db.get_value(AWARD, self.award.name, "organization"), self.p)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead_s, "organization"), self.p)
		self.assertEqual(frappe.db.get_value(ORG, self.p, "omni_hospital_count"), 1)  # điểm tính lại
		self.assertIn(self.s, self.comments(ORG, self.p))

	def test_two_open_leads_become_a_candidate_not_an_automatic_merge(self):
		res = merge.merge_organizations(self.p, self.s, "Administrator")
		self.assertEqual(len(res["candidates"]), 1)
		self.assertEqual(
			{self.lead_p, self.lead_s}, {*(self.candidates(self.lead_p)[0][k] for k in ("lead_a", "lead_b"))}
		)
		for lead in (self.lead_p, self.lead_s):
			self.assertFalse(frappe.db.get_value("CRM Lead", lead, "omni_merged_into"))

	def test_second_run_is_noop(self):
		merge.merge_organizations(self.p, self.s, "Administrator")
		again = merge.merge_organizations(self.p, self.s, "Administrator")
		self.assertTrue(again["already_merged"])

	def test_deal_organization_link_moves(self):
		lead = frappe.get_doc("CRM Lead", self.lead_s)
		frappe.db.set_value("CRM Lead", lead.name, "mobile_no", "0903105792")
		frappe.db.commit()
		deal = frappe.get_doc(
			{"doctype": "CRM Deal", "organization": self.s, "lead": lead.name, "status": "Qualification"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(lambda: (frappe.db.delete("CRM Deal", {"name": deal.name}), frappe.db.commit()))
		merge.merge_organizations(self.p, self.s, "Administrator")
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "organization"), self.p)

	def test_failure_rolls_back_everything(self):
		with mock.patch("omni_crm.prospect.fit.apply_fit", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				merge.merge_organizations(self.p, self.s, "Administrator")
		self.assertTrue(frappe.db.exists(ORG, self.s))
		self.assertEqual(frappe.db.get_value("Contact", self.contact, "company_name"), self.s)
		self.assertEqual(frappe.db.get_value(ORG, self.s, "omni_tax_id"), "0100213510")

	def test_same_org_and_missing_primary_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			merge.merge_organizations(self.p, self.p, "Administrator")
		with self.assertRaises(frappe.ValidationError):
			merge.merge_organizations("TD213R Không có", self.s, "Administrator")

	def test_different_tax_ids_are_not_duplicates(self):
		frappe.db.set_value(ORG, self.p, "omni_tax_id", "0100213599")
		with self.assertRaises(frappe.ValidationError):
			merge.merge_organizations(self.p, self.s, "Administrator")
		self.assertTrue(frappe.db.exists(ORG, self.s))

	def test_api_is_admin_only(self):
		user = "td213_sale_merge@example.com"
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
		self.addCleanup(
			lambda: (
				frappe.set_user("Administrator"),
				frappe.delete_doc("User", user, force=True, ignore_permissions=True),
				frappe.db.commit(),
			)
		)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			org_api.merge_organizations(self.p, self.s)
		frappe.set_user("Administrator")
		self.assertFalse(org_api.merge_organizations(self.p, self.s)["already_merged"])
