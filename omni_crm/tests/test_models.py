import json

import frappe
import pymysql.err
from frappe.tests.utils import FrappeTestCase

SECRET = "TOP-SECRET-VALUE-123"


def make_lead(first_name: str) -> str:
	"""Tạo CRM Lead tối thiểu cho test."""
	lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": first_name})
	lead.insert(ignore_permissions=True)
	return lead.name


def make_channel(name: str, channel_type: str = "Zalo OA", **kw) -> "frappe.model.document.Document":
	values = {"doctype": "Omni Channel", "channel_name": name, "channel_type": channel_type}
	if channel_type == "Zalo OA":
		values["zalo_app_id"] = "app-1"
	values.update(kw)
	return frappe.get_doc(values).insert()


def inbound_values(dedupe_key: str, **kw) -> dict:
	values = {
		"doctype": "Omni Inbound Message",
		"dedupe_key": dedupe_key,
		"identity_key": "Zalo OA:123",
		"status": "New",
	}
	values.update(kw)
	return values


class TestOmniModels(FrappeTestCase):
	def test_create_and_delete_each_doctype(self):
		channel = make_channel("Test Zalo CRUD")
		identity = frappe.get_doc({"doctype": "Omni Identity", "identity_key": "Zalo OA:crud1"}).insert()
		inbound = frappe.get_doc(inbound_values("crud:1")).insert()
		self.assertTrue(inbound.name.startswith("OIN-"))
		outbound = frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": channel.name,
				"identity": identity.name,
				"text": "x",
			}
		).insert()
		self.assertTrue(outbound.name.startswith("OOUT-"))
		self.assertEqual(outbound.status, "Queued")
		lead_a, lead_b = make_lead("CRUD A"), make_lead("CRUD B")
		dup = frappe.get_doc(
			{"doctype": "Omni Duplicate Candidate", "lead_a": lead_a, "lead_b": lead_b}
		).insert()
		self.assertTrue(dup.name.startswith("ODUP-"))
		for doc in (dup, outbound, inbound, identity, channel):
			doc.delete()
			self.assertFalse(frappe.db.exists(doc.doctype, doc.name))

	def test_settings_defaults(self):
		settings = frappe.get_doc("Omni Settings")
		self.assertEqual(settings.sandbox_mode, 1)
		self.assertEqual(settings.retry_backoff_minutes, "1,5")
		self.assertEqual(settings.raw_payload_retention_days, 180)
		self.assertEqual(settings.recording_retention_days, 365)
		self.assertEqual(settings.max_attempts, 3)

	def test_settings_validation(self):
		settings = frappe.get_doc("Omni Settings")
		for bad in ("1,x", "0,5", "1,-5", "1.5"):
			settings.retry_backoff_minutes = bad
			with self.assertRaises(frappe.ValidationError, msg=bad):
				settings.validate()
		settings.retry_backoff_minutes = "1,5"
		settings.business_hours = "{khong phai json"
		with self.assertRaises(frappe.ValidationError):
			settings.validate()

	def test_doctypes_of_later_phases_absent(self):
		for dt in (
			"Omni Call",
			"Omni Bot Run",
			"Omni Bot Draft",
			"Omni Knowledge Item",
			"Omni Question Group",
		):
			self.assertFalse(frappe.db.exists("DocType", dt), dt)
		settings_fields = {f.fieldname for f in frappe.get_meta("Omni Settings").fields}
		for fn in ("score_rules", "hot_lead_threshold", "kb_version", "bot_active_hours", "holidays"):
			self.assertNotIn(fn, settings_fields)
		for fn in ("assignment_fallback_users", "raw_payload_retention_days", "recording_retention_days"):
			self.assertIn(fn, settings_fields)
		self.assertNotIn("agent_map", {f.fieldname for f in frappe.get_meta("Omni Channel").fields})
		self.assertNotIn("bot_paused_until", {f.fieldname for f in frappe.get_meta("Omni Identity").fields})
		self.assertNotIn("bot_draft", {f.fieldname for f in frappe.get_meta("Omni Outbound Message").fields})

	def test_track_changes(self):
		for dt in ("Omni Settings", "Omni Channel"):
			self.assertEqual(frappe.get_meta(dt).track_changes, 1, dt)

	# ---- Ràng buộc duy nhất ở DB
	def test_dedupe_key_unique_in_db(self):
		frappe.get_doc(inbound_values("dup:1")).insert()
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(inbound_values("dup:1")).insert()

	def test_dedupe_key_unique_bypassing_controller(self):
		frappe.get_doc(inbound_values("raw:1")).insert()
		with self.assertRaises(pymysql.err.IntegrityError) as ctx:
			frappe.db.sql(
				"""insert into `tabOmni Inbound Message` (name, dedupe_key, identity_key, status)
				values ('RAW-X', 'raw:1', 'Zalo OA:1', 'New')"""
			)
		self.assertTrue(frappe.db.is_duplicate_entry(ctx.exception))

	def test_identity_key_unique_in_db(self):
		frappe.get_doc({"doctype": "Omni Identity", "identity_key": "Email:a@b.vn"}).insert()
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc({"doctype": "Omni Identity", "identity_key": "Email:a@b.vn"}).insert()

	def test_composite_indexes_exist(self):
		rows = frappe.db.sql(
			"""select index_name, group_concat(column_name order by seq_in_index)
			from information_schema.statistics
			where table_schema = database() and table_name = 'tabOmni Inbound Message'
			group by index_name""",
		)
		indexes = {name: cols for name, cols in rows}
		self.assertIn("identity_key,status", indexes.values())
		self.assertIn("status,next_attempt_at", indexes.values())

	# ---- Omni Duplicate Candidate
	def test_duplicate_candidate_orders_pair(self):
		lead_1, lead_2 = sorted([make_lead("Dup 1"), make_lead("Dup 2")])
		dup = frappe.get_doc(
			{"doctype": "Omni Duplicate Candidate", "lead_a": lead_2, "lead_b": lead_1}
		).insert()
		self.assertEqual((dup.lead_a, dup.lead_b), (lead_1, lead_2))

	def test_duplicate_candidate_reverse_pair_blocked(self):
		lead_1, lead_2 = sorted([make_lead("Rev 1"), make_lead("Rev 2")])
		frappe.get_doc({"doctype": "Omni Duplicate Candidate", "lead_a": lead_1, "lead_b": lead_2}).insert()
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{"doctype": "Omni Duplicate Candidate", "lead_a": lead_2, "lead_b": lead_1}
			).insert()

	def test_duplicate_candidate_same_lead_rejected(self):
		lead = make_lead("Same")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Omni Duplicate Candidate", "lead_a": lead, "lead_b": lead}).insert()

	# ---- Omni Channel
	def test_zalo_channel_requires_app_id(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Omni Channel", "channel_name": "Zalo thieu app id", "channel_type": "Zalo OA"}
			).insert()
		make_channel("Zalo co app id")

	def test_enabled_email_channel_requires_account(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Omni Channel", "channel_name": "Mail bat", "channel_type": "Email", "enabled": 1}
			).insert()
		make_channel("Mail tat", "Email")

	# ---- Custom field trên CRM Lead
	def test_custom_fields_on_crm_lead(self):
		meta = frappe.get_meta("CRM Lead")
		for fn in (
			"omni_first_channel",
			"omni_last_inbound_at",
			"omni_manual_fields",
			"omni_phone_e164",
			"omni_email_norm",
			"omni_merged_into",
		):
			self.assertTrue(meta.has_field(fn), fn)
		for fn in ("omni_phone_e164", "omni_email_norm"):
			self.assertEqual(meta.get_field(fn).search_index, 1, fn)
			self.assertEqual(meta.get_field(fn).hidden, 1, fn)
		self.assertEqual(meta.get_field("omni_manual_fields").hidden, 1)
		self.assertEqual(meta.get_field("omni_merged_into").options, "CRM Lead")
		for fn in ("omni_need", "omni_score"):
			self.assertFalse(meta.has_field(fn), fn)

	# ---- Bí mật và quyền
	def test_password_fields_never_in_list_view(self):
		for dt in ("Omni Channel", "Omni Settings"):
			for df in frappe.get_meta(dt).fields:
				if df.fieldtype == "Password":
					self.assertFalse(df.in_list_view, df.fieldname)
					self.assertFalse(df.in_standard_filter, df.fieldname)

	def test_password_not_in_as_dict_and_not_for_sales_user(self):
		channel = make_channel(
			"Zalo bi mat", zalo_app_secret=SECRET, zalo_access_token=SECRET, webhook_token=SECRET
		)
		self.assertEqual(channel.get_password("zalo_app_secret"), SECRET)

		user = self._make_sales_user()
		frappe.set_user(user)
		try:
			doc = frappe.get_doc("Omni Channel", channel.name)
			with self.assertRaises(frappe.PermissionError):
				doc.check_permission("read")
			self.assertNotIn(SECRET, json.dumps(doc.as_dict(), default=str))
			self.assertNotIn(SECRET, repr(doc))
			self.assertNotIn(SECRET, str(doc.as_dict()))
			self.assertFalse(frappe.has_permission("Omni Channel", "read"))
			self.assertFalse(frappe.has_permission("Omni Settings", "read"))
			with self.assertRaises(frappe.PermissionError):
				frappe.get_list("Omni Channel", fields=["name"], limit=1)
		finally:
			frappe.set_user("Administrator")

	def test_sales_user_read_only_on_message_doctypes(self):
		frappe.set_user(self._make_sales_user())
		try:
			for dt in (
				"Omni Inbound Message",
				"Omni Outbound Message",
				"Omni Identity",
				"Omni Duplicate Candidate",
			):
				self.assertTrue(frappe.has_permission(dt, "read"), dt)
				for ptype in ("write", "create", "delete"):
					self.assertFalse(frappe.has_permission(dt, ptype), f"{dt} {ptype}")
		finally:
			frappe.set_user("Administrator")

	def _make_sales_user(self) -> str:
		email = "omni_sales_test@example.com"
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Omni Sales",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			)
			user.insert(ignore_permissions=True)
		return email
