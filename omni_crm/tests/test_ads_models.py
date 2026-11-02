"""V2-05: mô hình Omni Campaign, Omni Lead Form, quyền và công tắc (docs/02, docs/09, docs/15 mục 5-6)."""

import frappe

from omni_crm.tests.ads_helpers import PREFIX, AdsCase, make_channel, make_form, make_users

CAMPAIGN = "Omni Campaign"
FORM = "Omni Lead Form"


class TestCampaign(AdsCase):
	def make(self, name, **kw):
		return frappe.get_doc(
			{"doctype": CAMPAIGN, "campaign_name": f"{PREFIX} {name}", "platform": "Meta", **kw}
		).insert()

	def test_unique_per_platform_and_external_id_but_blank_ids_do_not_collide(self):
		self.make("A", external_campaign_id="TD505X1")
		with self.assertRaises(frappe.ValidationError):
			self.make("B", external_campaign_id="TD505X1")
		self.make("C", external_campaign_id="TD505X1", platform="Google")  # nền tảng khác: được
		self.make("D")
		self.make(
			"E", external_campaign_id="  "
		)  # rỗng/khoảng trắng đều thành NULL, không vấp chỉ mục UNIQUE
		self.assertEqual(
			frappe.db.count(
				CAMPAIGN, {"campaign_key": ["is", "not set"], "campaign_name": ["like", f"{PREFIX}%"]}
			),
			2,
		)

	def test_dates_and_money_validated_and_defaults(self):
		with self.assertRaises(frappe.ValidationError):
			self.make("F", start_date="2026-11-10", end_date="2026-11-01")
		with self.assertRaises(frappe.ValidationError):
			self.make("G", spend=-1)
		doc = self.make("H", spend=1000, budget=5000)
		self.assertEqual((doc.status, doc.spend, doc.budget), ("Đang chạy", 1000, 5000))


class TestLeadForm(AdsCase):
	def test_unique_per_platform_and_form_id(self):
		make_form("Meta", "TD505F1", "A")
		with self.assertRaises(frappe.ValidationError):
			make_form("Meta", "TD505F1", "B")
		make_form("Google", "TD505F1", "C")  # nền tảng khác

	def test_field_map_targets_and_duplicates_validated(self):
		with self.assertRaises(frappe.ValidationError):
			make_form("Meta", "TD505F2", "A", {"x": "khong_hop_le"})
		with self.assertRaises(frappe.ValidationError):
			make_form("Meta", "TD505F3", "A", {"x": "custom:"})
		form = make_form(
			"Meta", "TD505F4", "A", {"x": "custom:so_giuong", "y": "tax_id", "z": "consent_contact"}
		)
		self.assertEqual(len(form.field_map), 3)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": FORM,
					"platform": "Meta",
					"external_form_id": "TD505F5",
					"form_name": f"{PREFIX} lặp",
					"field_map": [
						{"source_key": "a", "target": "email"},
						{"source_key": "a", "target": "phone"},
					],
				}
			).insert()

	def test_unknown_assignment_rule_rejected_and_enabled_defaults_on(self):
		with self.assertRaises(frappe.ValidationError):
			make_form("Meta", "TD505F6", "A", assignment_rule="khong-co-rule")
		self.assertEqual(make_form("Website", "TD505F7", "A").enabled, 1)

	def test_secret_is_password_hidden_in_dict_and_readable_with_get_password(self):
		form = make_form("Google", "TD505F8", "A", secret="td505-khoa-bi-mat-khong-that")
		self.assertNotIn("td505-khoa-bi-mat", str(frappe.get_doc(FORM, form.name).as_dict()))
		self.assertEqual(
			frappe.get_doc(FORM, form.name).get_password("secret"), "td505-khoa-bi-mat-khong-that"
		)

	def test_channel_secret_fields_are_password_type_and_new_types_accepted(self):
		meta = frappe.get_meta("Omni Channel")
		for fn in (
			"meta_app_secret",
			"meta_verify_token",
			"meta_page_access_token",
			"webform_captcha_secret",
		):
			self.assertEqual(meta.get_field(fn).fieldtype, "Password")
		for t in ("Meta Lead Ads", "Google Lead Form", "Web Form"):
			self.assertIn(t, meta.get_field("channel_type").options.split("\n"))
			self.assertIn(t, frappe.get_meta("Omni Identity").get_field("channel_type").options.split("\n"))
		ch = make_channel("Web Form", f"{PREFIX} ch")
		self.assertEqual(ch.channel_type, "Web Form")


class TestPermissions(AdsCase):
	def setUp(self):
		super().setUp()
		make_users()
		from omni_crm.tests.ads_helpers import SALE

		self.sale = SALE

	def test_sales_user_reads_campaign_but_not_lead_form_or_writes(self):
		frappe.get_doc({"doctype": CAMPAIGN, "campaign_name": f"{PREFIX} Q", "platform": "Meta"}).insert()
		form = make_form("Meta", "TD505P1", "A", secret="td505-khoa-bi-mat-khong-that")
		self.assertTrue(frappe.has_permission(CAMPAIGN, "read", user=self.sale))
		self.assertFalse(frappe.has_permission(CAMPAIGN, "write", user=self.sale))
		self.assertFalse(frappe.has_permission(CAMPAIGN, "create", user=self.sale))
		self.assertFalse(frappe.has_permission(FORM, "read", user=self.sale))
		self.assertFalse(frappe.has_permission(FORM, "read", doc=form, user=self.sale))

	def test_secret_field_has_permlevel_one_only_omni_admin(self):
		meta = frappe.get_meta(FORM)
		self.assertEqual(meta.get_field("secret").permlevel, 1)
		roles_l1 = {p.role for p in meta.permissions if p.permlevel == 1 and p.read}
		self.assertEqual(roles_l1, {"Omni Admin"})
