# ruff: noqa: RUF001
"""V2-02: định danh theo nhà thầu (docs/14 mục 2) + R1 (ADR 0008): mã số thuế -> Contact -> tên miền email.

Chỉ gắn Organization khi khớp ĐÚNG MỘT; mơ hồ thì không tự liên kết (ĐQ-4).
"""

import frappe

from omni_crm.tests.identity_helpers import IdentityTestCase, make_msg, phone
from omni_crm.tests.test_resolver import zalo
from omni_crm.tests.zalo_helpers import ZaloTestCase, load

DOMAIN = "td105.example"  # đã nằm trong dải dọn dữ liệu của IdentityTestCase
ORGS = "TD213R%"


def make_org(name: str, tax_id: str | None = None, **kw) -> str:
	doc = frappe.get_doc(
		{"doctype": "CRM Organization", "organization_name": name, "omni_tax_id": tax_id, **kw}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def make_contact(org: str, first: str, *, phone_no: str | None = None, email: str | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first,
			"company_name": org,
			"omni_info_source": "TD213 test",
			"phone_nos": [{"phone": phone_no, "is_primary_mobile_no": 1}] if phone_no else [],
			"email_ids": [{"email_id": email, "is_primary": 1}] if email else [],
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def cleanup_orgs() -> None:
	frappe.db.rollback()
	for c in frappe.get_all("Contact", filters={"first_name": ["like", "TD213R%"]}, pluck="name"):
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True, delete_permanently=True)
	for lead in frappe.get_all("CRM Lead", filters={"organization": ["like", ORGS]}, pluck="name"):
		frappe.db.delete("Omni Duplicate Candidate", {"lead_a": lead})
		frappe.db.delete("Omni Duplicate Candidate", {"lead_b": lead})
		frappe.db.delete("Comment", {"reference_name": lead})
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	for org in frappe.get_all("CRM Organization", filters={"name": ["like", ORGS]}, pluck="name"):
		frappe.db.delete("Omni Bid Award", {"organization": org})
		frappe.delete_doc("CRM Organization", org, force=True, ignore_permissions=True)
	frappe.db.commit()


class OrgResolverCase(IdentityTestCase):
	def setUp(self):
		super().setUp()
		cleanup_orgs()
		self.addCleanup(cleanup_orgs)

	def org_lead(self, org: str, name: str = "TD105 Lead nhà thầu") -> str:
		return self.make_lead(name, organization=org)

	def comments(self, lead: str) -> list[str]:
		return frappe.get_all("Comment", filters={"reference_name": lead}, pluck="content")


class TestTaxId(OrgResolverCase):
	def test_declared_tax_id_attaches_existing_open_lead(self):
		org = make_org("TD213R Công ty A", "0100213401")
		lead = self.org_lead(org)
		before = self.lead_count()
		r = self.resolve(zalo("U1", "xin chào", {"display_name": "Lê Văn Cường", "tax_id": "0100 213 401"}))
		self.assertEqual((r.lead, r.matched_by, r.created_lead), (lead, "org_tax", False))
		self.assertEqual(self.lead_count(), before)

	def test_org_without_lead_gets_a_new_linked_lead(self):
		org = make_org("TD213R Công ty B", "0100213402")
		r = self.resolve(zalo("U1", "xin chào", {"display_name": "Lê Văn Cường", "tax_id": "0100213402"}))
		self.assertTrue(r.created_lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", r.lead, "organization"), org)
		self.assertEqual(r.matched_by, "org_tax")
		# tin sau của cùng khách: đã gắn Lead nên đi theo định danh, không tạo thêm
		r2 = self.resolve(zalo("U1", "ok", {"display_name": "Lê Văn Cường"}))
		self.assertEqual((r2.lead, r2.matched_by), (r.lead, "identity"))

	def test_unknown_or_invalid_tax_id_falls_back_to_old_rules(self):
		for uid, tax in (("U1", "0100213499"), ("U2", "12345"), ("U3", "abc")):
			r = self.resolve(zalo(uid, "hi", {"display_name": "Khách lạ Td", "tax_id": tax}))
			self.assertEqual((r.matched_by, r.created_lead), ("new", True), tax)
			self.assertIsNone(frappe.db.get_value("CRM Lead", r.lead, "organization"))

	def test_tax_id_in_message_text_is_never_used(self):
		org = make_org("TD213R Công ty C", "0100213403")
		self.org_lead(org)
		r = self.resolve(zalo("U1", "MST công ty em là 0100213403 nhé", {"display_name": "Trần Văn Hải"}))
		self.assertEqual((r.matched_by, r.created_lead), ("new", True))

	def test_ten_digit_tax_id_matches_exactly_one_branch(self):
		org = make_org("TD213R Chi nhánh", "0100213404-001")
		lead = self.org_lead(org)
		r = self.resolve(zalo("U1", "hi", {"display_name": "Lê Văn Cường", "tax_id": "0100213404"}))
		self.assertEqual((r.lead, r.matched_by), (lead, "org_tax"))

	def test_ten_digit_tax_id_matching_two_branches_is_ambiguous(self):
		a = make_org("TD213R CN1", "0100213405-001")
		b = make_org("TD213R CN2", "0100213405-002")
		la, lb = self.org_lead(a, "TD105 La"), self.org_lead(b, "TD105 Lb")
		r = self.resolve(zalo("U1", "hi", {"display_name": "Lê Văn Cường", "tax_id": "0100213405"}))
		self.assertTrue(r.created_lead)
		self.assertNotIn(r.lead, (la, lb))
		self.assertIsNone(frappe.db.get_value("CRM Lead", r.lead, "organization"))
		pairs = {frozenset((c["lead_a"], c["lead_b"])) for c in self.candidates(r.lead)}
		self.assertEqual(pairs, {frozenset((r.lead, la)), frozenset((r.lead, lb))})
		self.assertTrue(any("CN1" in c and "CN2" in c for c in self.comments(r.lead)))


class TestContactMatch(OrgResolverCase):
	def test_known_contact_phone_attaches_org_lead(self):
		org = make_org("TD213R Công ty D", "0100213410")
		make_contact(org, "TD213R Hà", phone_no="0903 105 777")
		lead = self.org_lead(org)
		r = self.resolve(
			zalo("U1", "alo", {"display_name": "Hà Thị", "phone": "0903105777"}, event_type="user_info")
		)
		self.assertEqual((r.lead, r.matched_by), (lead, "org_contact"))

	def test_known_contact_email_attaches_org_lead(self):
		org = make_org("TD213R Công ty E", "0100213411")
		make_contact(org, "TD213R Bình", email="Binh@td105.example")
		lead = self.org_lead(org)
		r = self.resolve(make_msg("Email", "binh@td105.example", "Chào anh"))
		self.assertEqual((r.lead, r.matched_by), (lead, "org_contact"))

	def test_contact_shared_by_two_orgs_is_ambiguous_and_not_linked(self):
		a, b = make_org("TD213R F1", "0100213412"), make_org("TD213R F2", "0100213413")
		make_contact(a, "TD213R Dung1", phone_no="0903105778")
		make_contact(b, "TD213R Dung2", phone_no="0903 105 778")
		la, lb = self.org_lead(a, "TD105 La"), self.org_lead(b, "TD105 Lb")
		r = self.resolve(make_msg("Telephony", "0903105778", "cuộc gọi đến"))
		self.assertNotIn(r.lead, (la, lb))
		self.assertEqual(r.matched_by, "new")
		pairs = {frozenset((c["lead_a"], c["lead_b"])) for c in self.candidates(r.lead)}
		self.assertEqual(pairs, {frozenset((r.lead, la)), frozenset((r.lead, lb))})

	def test_phone_mentioned_in_text_is_medium_and_not_used_for_org(self):
		org = make_org("TD213R Công ty G", "0100213414")
		make_contact(org, "TD213R Khoa", phone_no="0903105779")
		self.org_lead(org)
		r = self.resolve(zalo("U1", "gọi giúp số 0903105779", {"display_name": "Trần Văn Hải"}))
		self.assertEqual((r.matched_by, r.created_lead), ("new", True))

	def test_erased_contact_is_not_matched(self):
		org = make_org("TD213R Công ty H", "0100213415")
		c = make_contact(org, "TD213R Lan", phone_no="0903105780")
		frappe.db.set_value("Contact", c, {"omni_phones_e164": "", "omni_emails_norm": ""})
		frappe.db.commit()
		self.org_lead(org)
		r = self.resolve(make_msg("Telephony", "0903105780", "gọi"))
		self.assertEqual(r.matched_by, "new")


class TestDomainMatch(OrgResolverCase):
	def test_company_domain_matches_exactly_one_org(self):
		org = make_org("TD213R Công ty I", "0100213420", website=f"https://www.{DOMAIN}/gioi-thieu")
		lead = self.org_lead(org)
		r = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "Chào Miyano", {"display_name": "Phạm Thu Hà"}))
		self.assertEqual((r.lead, r.matched_by), (lead, "org_domain"))

	def test_company_email_field_also_matches(self):
		org = make_org("TD213R Công ty J", "0100213421", omni_company_email=f"info@{DOMAIN}")
		lead = self.org_lead(org)
		r = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "Chào"))
		self.assertEqual((r.lead, r.matched_by), (lead, "org_domain"))

	def test_domain_matching_two_orgs_is_ambiguous_and_not_linked(self):
		a = make_org("TD213R K1", "0100213422", website=DOMAIN)
		b = make_org("TD213R K2", "0100213423", website=f"www.{DOMAIN}")
		la, lb = self.org_lead(a, "TD105 La"), self.org_lead(b, "TD105 Lb")
		r = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "Chào", {"display_name": "Phạm Thu Hà"}))
		self.assertTrue(r.created_lead)
		self.assertNotIn(r.lead, (la, lb))
		self.assertIsNone(frappe.db.get_value("CRM Lead", r.lead, "organization"))
		pairs = {frozenset((c["lead_a"], c["lead_b"])) for c in self.candidates(r.lead)}
		self.assertEqual(pairs, {frozenset((r.lead, la)), frozenset((r.lead, lb))})

	def test_free_mail_domain_is_never_used(self):
		org = make_org(
			"TD213R Công ty L", "0100213424", website="gmail.com", omni_company_email="x@gmail.com"
		)
		self.org_lead(org)
		r = self.resolve(make_msg("Email", "td105x@gmail.com", "Chào", {"display_name": "Phạm Thu Hà"}))
		self.assertEqual((r.matched_by, r.created_lead), ("new", True))
		self.assertIsNone(frappe.db.get_value("CRM Lead", r.lead, "organization"))

	def test_domain_substring_is_not_a_match(self):
		org = make_org("TD213R Công ty M", "0100213425", website=f"not-{DOMAIN}")
		self.org_lead(org)
		r = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "Chào"))
		self.assertEqual(r.matched_by, "new")

	def test_lost_only_org_gets_new_lead_instead_of_reopening(self):
		org = make_org("TD213R Công ty N", "0100213426", website=DOMAIN)
		old = self.org_lead(org)
		frappe.db.set_value(
			"CRM Lead", old, {"status": "Unqualified", "lost_reason": "Other", "lost_notes": "td"}
		)
		frappe.db.commit()
		r = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "Chào"))
		self.assertTrue(r.created_lead)
		self.assertNotEqual(r.lead, old)
		self.assertEqual(frappe.db.get_value("CRM Lead", r.lead, "organization"), org)

	def test_identity_attached_elsewhere_is_never_repointed_only_flagged(self):
		org = make_org("TD213R Công ty O", "0100213427", website=DOMAIN)
		org_lead = self.org_lead(org)
		own = self.make_lead("TD105 Lead riêng", mobile=phone(60))
		r1 = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "xin chào"))  # lần đầu: gắn Lead của nhà thầu
		self.assertEqual(r1.lead, org_lead)
		frappe.db.set_value("Omni Identity", r1.identity, "lead", own)  # sale đã chuyển khách sang Lead riêng
		frappe.db.commit()
		r2 = self.resolve(make_msg("Email", f"ai-do@{DOMAIN}", "tiếp"))
		self.assertEqual((r2.lead, r2.matched_by), (own, "identity"))
		self.assertEqual(len(self.candidates(own)), 1)


class TestMediumSignalR1(OrgResolverCase):
	def test_unknown_name_with_medium_phone_creates_candidate_with_unknown_name_reason(self):
		nam = self.make_lead("Phạm Quốc Nam", mobile=phone(61))
		r = self.resolve(zalo("U1", f"liên hệ {phone(61)}"))
		self.assertEqual((r.created_lead, r.matched_by), (True, "new"))
		cands = self.candidates(nam)
		self.assertEqual(len(cands), 1)
		self.assertIn("chưa đủ giống", cands[0]["reason"])


class TestEchoFirst(ZaloTestCase):
	def test_echo_before_customer_creates_no_lead(self):
		before = frappe.db.count("CRM Lead")
		echo = self.deliver(load("oa_send_text"), profile={})
		self.assertIsNone(self.lead_of(echo))
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", echo, "status"), "Done")
		self.assertEqual(frappe.db.count("CRM Lead"), before)
		ident = self.identity()
		self.assertIsNotNone(ident)
		self.assertFalse(ident.lead)
		# khách nhắn sau đó: tạo đúng một Lead
		msg = self.deliver(load("user_send_text"), profile={"display_name": "Phạm Quang Huy Td213"})
		lead = self.lead_of(msg)
		self.assertTrue(lead)
		self.assertEqual(frappe.db.count("CRM Lead"), before + 1)
