# ruff: noqa: RUF001
"""V2-02: mô hình nhà thầu — mã số thuế duy nhất, gói thầu, bảng alias bệnh viện, quyền theo Lead, API."""

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import organization as org_api
from omni_crm.prospect import contacts as contact_lib
from omni_crm.prospect import hospital

ORG = "CRM Organization"
AWARD = "Omni Bid Award"
ALIAS = "Omni Hospital Alias"
SALE1 = "td213_sale1@example.com"
SALE2 = "td213_sale2@example.com"
V = "VTYT tiêu hao"


def make_user(email: str, role: str = "Sales User") -> None:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "TD213",
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)


def make_org(name: str, tax_id: str | None = None, **kw) -> str:
	return (
		frappe.get_doc({"doctype": ORG, "organization_name": name, "omni_tax_id": tax_id, **kw})
		.insert(ignore_permissions=True)
		.name
	)


def make_lead(first_name: str, organization: str | None = None, owner: str | None = None) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first_name,
				"organization": organization,
				"lead_owner": owner,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_award(org: str, hospital_name: str, notice: str = "N1", **kw) -> str:
	values = {
		"doctype": AWARD,
		"organization": org,
		"hospital": hospital_name,
		"notice_no": notice,
		"category": V,
		"award_date": "2026-03-15",
		"award_value": 1_000_000_000,
		"item_count": 10,
		"duration_months": 12,
	}
	values.update(kw)
	return frappe.get_doc(values).insert(ignore_permissions=True).name


class ProspectCase(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("td213_model")
		self.addCleanup(frappe.db.rollback, save_point="td213_model")
		self.addCleanup(frappe.set_user, "Administrator")


class TestOrganizationTaxId(ProspectCase):
	def test_tax_id_is_normalized_on_save(self):
		org = make_org("TD213 A", " 0100.109 106 ")
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_tax_id"), "0100109106")

	def test_wrong_length_is_rejected_with_clear_message(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			make_org("TD213 B", "12345")
		self.assertIn("không hợp lệ", str(ctx.exception))

	def test_duplicate_tax_id_is_blocked_with_friendly_message(self):
		a = make_org("TD213 C1", "0100109107")
		with self.assertRaises(frappe.DuplicateEntryError) as ctx:
			make_org("TD213 C2", "0100 109 107")  # cùng số sau khi chuẩn hóa
		self.assertIn(a, str(ctx.exception))

	def test_branch_tax_id_is_distinct_from_head_office(self):
		make_org("TD213 D1", "0100109108")
		make_org("TD213 D2", "0100109108-001")
		self.assertEqual(frappe.db.count(ORG, {"omni_tax_id": ["like", "0100109108%"]}), 2)

	def test_editing_to_existing_tax_id_is_blocked(self):
		make_org("TD213 E1", "0100109109")
		other = frappe.get_doc(ORG, make_org("TD213 E2", "0100109110"))
		other.omni_tax_id = "0100109109"
		with self.assertRaises(frappe.DuplicateEntryError):
			other.save()

	def test_database_unique_index_catches_race_that_skips_validate(self):
		"""Hai lượt tạo đồng thời cùng qua validate: chỉ mục UNIQUE của MariaDB là chốt chặn cuối."""
		make_org("TD213 F1", "0100109111")
		racer = frappe.get_doc({"doctype": ORG, "organization_name": "TD213 F2", "omni_tax_id": "0100109111"})
		racer.name = "TD213 F2"
		with self.assertRaises(frappe.UniqueValidationError):
			racer.db_insert()

	def test_blank_tax_id_is_stored_as_null_and_does_not_collide(self):
		a = make_org("TD213 G1", "")
		b = make_org("TD213 G2", None)
		c = frappe.get_doc(ORG, make_org("TD213 G3"))
		c.omni_tax_id = "  "
		c.save()
		for name in (a, b, c.name):
			self.assertIsNone(frappe.db.get_value(ORG, name, "omni_tax_id"), name)

	def test_crm_own_create_organization_path_stays_collision_free(self):
		"""Chuyển Lead -> Deal của CRM tạo Organization không có mã số thuế: hai lần liên tiếp vẫn phải được."""
		for i in (1, 2):
			lead = frappe.get_doc(
				"CRM Lead", make_lead(f"TD213 Convert {i}", organization=f"TD213 Org tự tạo {i}")
			)
			self.assertEqual(lead.create_organization(), f"TD213 Org tự tạo {i}")
		self.assertIsNone(frappe.db.get_value(ORG, "TD213 Org tự tạo 1", "omni_tax_id"))

	def test_tax_id_is_not_mandatory(self):
		meta = frappe.get_meta(ORG)
		self.assertFalse(meta.get_field("omni_tax_id").reqd)
		self.assertFalse(frappe.get_meta("Contact").get_field("omni_info_source").reqd)


class TestBidAward(ProspectCase):
	def test_hospital_key_merges_spellings_and_counts_one_hospital(self):
		org = make_org("TD213 H1", "0100109120")
		make_award(org, "BV Đa khoa X", "N1")
		make_award(org, "Bệnh viện đa khoa X", "N2")
		keys = frappe.get_all(AWARD, filters={"organization": org}, pluck="hospital_key")
		self.assertEqual(set(keys), {"benh vien da khoa x"})
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_hospital_count"), 1)
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_awards_in_period"), 2)

	def test_end_date_is_award_date_plus_months(self):
		org = make_org("TD213 H2", "0100109121")
		a = make_award(org, "BV A", award_date="2026-01-31", duration_months=1)
		self.assertEqual(str(frappe.db.get_value(AWARD, a, "end_date")), "2026-02-28")  # giống EDATE
		b = make_award(org, "BV B", "N2", duration_months=0)
		self.assertIsNone(frappe.db.get_value(AWARD, b, "end_date"))

	def test_import_key_is_unique_and_ignores_prefix_spelling(self):
		org = make_org("TD213 H3", "0100109122")
		a = make_award(org, "BV Đa khoa X", "N9")
		self.assertEqual(frappe.db.get_value(AWARD, a, "import_key"), "0100109122|N9|benh vien da khoa x")
		with self.assertRaises(frappe.DuplicateEntryError):
			make_award(org, "Bệnh viện Đa khoa X", "N9")

	def test_import_key_without_notice_uses_date_and_value(self):
		org = make_org("TD213 H4", "0100109123")
		a = make_award(org, "BV Y", "", award_date="2026-02-02", award_value=7_000_000_000)
		self.assertEqual(
			frappe.db.get_value(AWARD, a, "import_key"), "0100109123|benh vien y|2026-02-02|7000000000"
		)
		with self.assertRaises(frappe.DuplicateEntryError):
			make_award(org, "BV Y", "", award_date="2026-02-02", award_value=7_000_000_000)

	def test_tax_id_mismatch_with_organization_is_rejected(self):
		org = make_org("TD213 H5", "0100109124")
		with self.assertRaises(frappe.ValidationError):
			make_award(org, "BV Z", tax_id="0100109999")

	def test_alias_merges_hospitals_and_import_key_is_stable(self):
		org = make_org("TD213 H6", "0100109125")
		a = make_award(org, "Bệnh viện Đa khoa Thành phố X", "N1")
		b = make_award(org, "BV ĐK TP X", "N2")
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_hospital_count"), 2)
		key_b = frappe.db.get_value(AWARD, b, "import_key")
		alias = frappe.get_doc(
			{"doctype": ALIAS, "alias_key": "BV ĐK TP X", "canonical_name": "Bệnh viện Đa khoa Thành phố X"}
		).insert(ignore_permissions=True)
		self.assertEqual(alias.name, "benh vien dk tp x")
		self.assertEqual(
			frappe.db.get_value(AWARD, b, "hospital_key"), frappe.db.get_value(AWARD, a, "hospital_key")
		)
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_hospital_count"), 1)
		self.assertEqual(frappe.db.get_value(AWARD, b, "import_key"), key_b)  # sửa alias không đổi khóa nhập
		self.assertEqual(frappe.db.get_value(AWARD, b, "hospital_raw_key"), "benh vien dk tp x")
		alias.delete(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_hospital_count"), 2)

	def test_new_award_after_alias_uses_canonical_key(self):
		org = make_org("TD213 H7", "0100109126")
		frappe.get_doc(
			{"doctype": ALIAS, "alias_key": "TTYT Q1", "canonical_name": "Trung tâm Y tế Quận 1"}
		).insert(ignore_permissions=True)
		a = make_award(org, "TTYT Q1")
		self.assertEqual(frappe.db.get_value(AWARD, a, "hospital_key"), "trung tam y te quan 1")

	def test_alias_validation(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": ALIAS, "alias_key": "BV Nhi", "canonical_name": "Bệnh viện nhi"}
			).insert(ignore_permissions=True)

	def test_similar_hospital_listing(self):
		org = make_org("TD213 H8", "0100109127")
		make_award(org, "Bệnh viện Đa khoa Tỉnh Hà Tĩnh", "N1")
		make_award(org, "Bệnh viện Đa khoa Tỉnh Hà Tiinh", "N2")  # gõ sai một chữ
		pairs = hospital.similar_hospital_names()
		self.assertTrue(
			any(
				{p["a"], p["b"]} == {"benh vien da khoa tinh ha tinh", "benh vien da khoa tinh ha tiinh"}
				for p in pairs
			),
			pairs,
		)

	def test_deleting_award_recomputes(self):
		org = make_org("TD213 H9", "0100109128")
		a = make_award(org, "BV Q")
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_hospital_count"), 1)
		frappe.delete_doc(AWARD, a, ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(ORG, org, "omni_hospital_count"), 0)


class TestSettings(ProspectCase):
	def test_fit_defaults_in_doctype_match_sheet(self):
		meta = frappe.get_meta("Omni Settings")
		got = {
			f: meta.get_field(f).default
			for f in (
				"fit_period_months",
				"fit_min_hospitals",
				"fit_min_items",
				"fit_max_hospitals",
				"fit_max_items",
				"fit_max_value_bn",
				"fit_w_hospitals",
				"fit_w_items",
				"fit_w_value",
				"fit_w_active",
				"fit_tier_a",
			)
		}
		self.assertEqual(
			list(got.values()), ["24", "5", "200", "10", "500", "20", "40", "30", "20", "10", "70"], got
		)
		self.assertEqual(meta.get_field("fit_categories").default, "VTYT tiêu hao\nHóa chất – sinh phẩm")
		self.assertEqual(meta.get_field("fit_scoring_enabled").default, "1")
		self.assertEqual(meta.get_field("prospect_import_enabled").default, "1")

	def test_bad_fit_params_rejected(self):
		doc = frappe.get_doc("Omni Settings")
		doc.fit_max_items = 0
		with self.assertRaises(frappe.ValidationError):
			doc.validate_fit_params()
		doc = frappe.get_doc("Omni Settings")
		doc.fit_categories = "  \n "
		with self.assertRaises(frappe.ValidationError):
			doc.validate_fit_params()


class TestPermissionsAndApi(ProspectCase):
	def setUp(self):
		super().setUp()
		make_user(SALE1)
		make_user(SALE2)
		self.org = make_org("TD213 P1", "0100109130")
		self.other_org = make_org("TD213 P2", "0100109131")
		self.lead1 = make_lead("TD213 Lead1", self.org, SALE1)
		self.lead2 = make_lead("TD213 Lead2", self.other_org, SALE2)
		self.lead_no_org = make_lead("TD213 Lead3", None, SALE1)
		self.award1 = make_award(self.org, "BV P1")
		self.award2 = make_award(self.other_org, "BV P2")
		self.contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "TD213 Nguoi",
				"last_name": "Lienhe",
				"company_name": self.org,
				"omni_contact_role": "Người quyết định",
				"omni_info_source": "Website nhà thầu",
				"omni_consent": "Từ chối",
				"email_ids": [{"email_id": "NguoiLH@TD213-congty.vn", "is_primary": 1}],
				"phone_nos": [{"phone": "0903 213 001", "is_primary_mobile_no": 1}],
			}
		).insert(ignore_permissions=True)

	def test_contact_keys_are_normalized_for_matching(self):
		c = frappe.get_doc("Contact", self.contact.name)
		self.assertEqual(c.omni_emails_norm, "|nguoilh@td213-congty.vn|")
		self.assertEqual(c.omni_phones_e164, "|+84903213001|")
		found = contact_lib.contacts_matching(["+84903213001"], [])
		self.assertEqual([x.name for x in found], [self.contact.name])

	def test_sales_user_sees_only_awards_of_organizations_with_readable_lead(self):
		frappe.set_user(SALE1)
		self.assertEqual(
			frappe.get_list(AWARD, filters={"hospital": ["like", "BV P%"]}, pluck="name"), [self.award1]
		)
		self.assertTrue(frappe.has_permission(AWARD, "read", doc=self.award1))
		self.assertFalse(frappe.has_permission(AWARD, "read", doc=self.award2))
		self.assertFalse(frappe.has_permission(AWARD, "write", doc=self.award1))
		frappe.set_user("Administrator")
		self.assertTrue(frappe.has_permission(AWARD, "read", doc=self.award2))

	def test_api_requires_lead_permission(self):
		frappe.set_user(SALE2)
		for fn in (org_api.get_organization, org_api.get_contacts, org_api.get_awards):
			with self.subTest(fn.__name__), self.assertRaises(frappe.PermissionError):
				fn(self.lead1)
		frappe.set_user("Guest")
		for fn in (org_api.get_organization, org_api.get_contacts, org_api.get_awards):
			with self.subTest("guest " + fn.__name__), self.assertRaises(frappe.PermissionError):
				fn(self.lead1)

	def test_api_returns_data_for_permitted_user(self):
		frappe.set_user(SALE1)
		self.assertEqual(org_api.get_organization(self.lead1)["omni_tax_id"], "0100109130")
		contacts = org_api.get_contacts(self.lead1)
		self.assertEqual([c["name"] for c in contacts], [self.contact.name])
		self.assertEqual(contacts[0]["do_not_contact"], 1)
		self.assertEqual([a["name"] for a in org_api.get_awards(self.lead1)], [self.award1])

	def test_lead_without_organization_returns_empty(self):
		frappe.set_user(SALE1)
		self.assertEqual(org_api.get_organization(self.lead_no_org), {})
		self.assertEqual(org_api.get_contacts(self.lead_no_org), [])
		self.assertEqual(org_api.get_awards(self.lead_no_org), [])

	def test_unknown_lead_is_clean_error(self):
		with self.assertRaises(frappe.DoesNotExistError):
			org_api.get_awards("CRM-LEAD-khong-ton-tai")

	def test_similar_hospitals_api_is_admin_only(self):
		frappe.set_user(SALE1)
		with self.assertRaises(frappe.PermissionError):
			org_api.list_similar_hospitals()
		frappe.set_user("Administrator")
		self.assertIsInstance(org_api.list_similar_hospitals(), list)
