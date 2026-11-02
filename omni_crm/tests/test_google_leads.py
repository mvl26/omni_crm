"""V2-05: kênh Google Lead Form (docs/15 mục 3, 10). Payload mẫu theo tài liệu Google: CHƯA THỬ VỚI NỀN TẢNG THẬT."""

import csv
from pathlib import Path
from unittest import mock

import frappe

from omni_crm.api import webhooks
from omni_crm.channels.google_leads import csv_import
from omni_crm.tests.ads_helpers import (
	GOOGLE_KEY,
	PREFIX,
	SALE,
	AdsCase,
	body,
	call_webhook,
	inbound,
	load,
	make_channel,
	make_form,
	make_users,
	work,
)

LEAD = "CRM Lead"
FORM_ID = "TD505GFORM"


class GoogleCase(AdsCase):
	def setUp(self):
		super().setUp()
		make_users()
		self.channel = make_channel("Google Lead Form", f"{PREFIX} google")
		self.form = make_form(
			"Google",
			FORM_ID,
			"Google form",
			{"TD505_TAX": "tax_id", "TD505_CONSENT": "consent_contact"},
			secret=GOOGLE_KEY,
			default_owner=SALE,
		)
		self.payload = load("google_leads/webhook.json")

	def post(self, data=None):
		return call_webhook(webhooks.google_leads, body(data or self.payload), args={"ch": self.channel.name})

	def process(self, data=None):
		self.post(data)
		row = inbound(self.channel.name)[0]
		work(row.identity_key)
		row = inbound(self.channel.name)[0]
		return row, row.lead


class TestWebhook(GoogleCase):
	def test_valid_key_saves_and_answers_empty_json_200(self):
		_res, status = self.post()
		self.assertEqual(status, 200)
		self.assertEqual(frappe.local.response.get("filecontent"), b"{}")
		rows = inbound(self.channel.name)
		self.assertEqual(
			(len(rows), rows[0].event_type, rows[0].external_message_id), (1, "form_lead", "TD505GL1")
		)

	def test_google_key_is_never_stored(self):
		self.post()
		name = inbound(self.channel.name)[0].name
		raw = frappe.db.get_value("Omni Inbound Message", name, "raw_payload")
		self.assertNotIn(GOOGLE_KEY, raw)
		self.assertNotIn("google_key", raw)

	def test_wrong_missing_or_other_forms_key_is_4xx_and_saves_nothing(self):
		other = make_form("Google", "TD505GFORM2", "Google form 2", secret="khoa-cua-form-hai-khac")
		cases = {
			"sai khóa": {**self.payload, "google_key": "sai-khoa-sai-khoa"},
			"thiếu khóa": {k: v for k, v in self.payload.items() if k != "google_key"},
			"khóa của form khác": {**self.payload, "google_key": "khoa-cua-form-hai-khac"},
			"form lạ": {**self.payload, "form_id": "TD505KHONGCO"},
			"không có form_id": {k: v for k, v in self.payload.items() if k != "form_id"},
		}
		for name, data in cases.items():
			with self.subTest(name):
				_res, status = self.post(data)
				self.assertEqual(status, 401)
		self.assertEqual(inbound(self.channel.name), [])
		self.assertTrue(other.name)

	def test_form_without_secret_never_authenticates(self):
		bare = make_form("Google", "TD505GFORM3", "Google form 3")
		_res, status = self.post({**self.payload, "form_id": "TD505GFORM3", "google_key": ""})
		self.assertEqual(status, 401)
		self.assertTrue(bare.name)

	def test_non_json_body_is_rejected_and_nothing_saved(self):
		_res, status = call_webhook(webhooks.google_leads, b"khong phai json", args={"ch": self.channel.name})
		self.assertEqual(status, 401)
		self.assertEqual(inbound(self.channel.name), [])

	def test_same_lead_id_twice_is_one_event(self):
		self.post()
		_res, status = self.post()
		self.assertEqual(status, 200)
		self.assertEqual(len(inbound(self.channel.name)), 1)

	def test_disabled_channel_ignores_with_200_and_stores_nothing(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		_res, status = self.post()
		self.assertEqual(status, 200)
		self.assertEqual(inbound(self.channel.name), [])

	def test_get_is_405(self):
		_res, status = call_webhook(webhooks.google_leads, b"", method="GET", args={"ch": self.channel.name})
		self.assertEqual(status, 405)

	def test_lock_trouble_returns_503_so_google_retries(self):
		with mock.patch("omni_crm.ingest.gateway.save_inbound", side_effect=frappe.QueryTimeoutError()):
			_res, status = self.post()
		self.assertEqual(status, 503)


class TestWorker(GoogleCase):
	def test_lead_with_custom_columns_campaign_click_id_and_consent(self):
		row, lead = self.process()
		self.assertEqual(row.status, "Done", row.last_error)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual(
			(d.source, d.omni_ad_platform, d.omni_click_id), ("Quảng cáo Google", "Google", "TD505GCLID")
		)
		self.assertEqual((d.omni_ad_id, d.omni_adset_id, d.omni_form_id), ("TD505GCRE", "TD505GADG", FORM_ID))
		self.assertEqual(d.omni_campaign, "Google TD505GCAMP")
		self.assertEqual(d.omni_phone_e164, "+84912345602")
		org = frappe.get_doc("CRM Organization", d.organization)
		self.assertEqual(
			(org.omni_tax_id, org.omni_verified), ("0505050502", 0)
		)  # TD505_TAX ánh xạ qua field_map
		self.assertEqual(d.lead_owner, SALE)

	def test_consent_goes_to_existing_contact_only(self):
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"{PREFIX} Bích",
				"phone_nos": [{"phone": "+84912345602", "is_primary_mobile_no": 1}],
				"omni_consent": "Chưa hỏi",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		before = frappe.db.count("Contact")
		self.process()
		self.assertEqual(frappe.db.count("Contact"), before)  # không tạo Contact mới
		c = frappe.db.get_value(
			"Contact",
			contact.name,
			["omni_consent", "omni_consent_date", "omni_consent_method"],
			as_dict=True,
		)
		self.assertEqual(c.omni_consent, "Có")
		self.assertTrue(c.omni_consent_date)
		self.assertIn("Google", c.omni_consent_method)

	def test_no_consent_or_refusal_is_not_overwritten(self):
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"{PREFIX} Từ chối",
				"phone_nos": [{"phone": "+84912345602", "is_primary_mobile_no": 1}],
				"omni_consent": "Từ chối",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.process()
		self.assertEqual(frappe.db.get_value("Contact", contact.name, "omni_consent"), "Từ chối")
		# không đánh dấu đồng ý -> không ghi
		other = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"{PREFIX} Khác",
				"phone_nos": [{"phone": "+84912345603", "is_primary_mobile_no": 1}],
				"omni_consent": "Chưa hỏi",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		data = load("google_leads/webhook.json")
		data["lead_id"] = "TD505GL2"
		data["user_column_data"] = [c for c in data["user_column_data"] if c["column_id"] != "TD505_CONSENT"]
		data["user_column_data"][2]["string_value"] = "0912345603"
		self.post(data)
		work("Google Lead Form:TD505GL2")
		self.assertEqual(frappe.db.get_value("Contact", other.name, "omni_consent"), "Chưa hỏi")

	def test_is_test_lead_is_flagged_not_assigned_and_ignores_real_contact_signals(self):
		real = frappe.get_doc(
			{"doctype": LEAD, "first_name": f"{PREFIX} Thật", "mobile_no": "+84912345602"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		row, lead = self.process({**self.payload, "is_test": True})
		self.assertEqual(row.status, "Done", row.last_error)
		self.assertNotEqual(lead, real.name)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual((d.omni_is_test, d.lead_owner), (1, None))
		self.assertEqual(frappe.db.count("Notification Log", {"document_name": lead}), 0)

	def test_disabled_form_only_stores(self):
		frappe.db.set_value("Omni Lead Form", self.form.name, "enabled", 0)
		frappe.db.commit()
		row, lead = self.process()
		self.assertEqual((row.status, lead), ("Done", None))

	def test_reposted_lead_after_processing_is_still_one_lead(self):
		_row, lead = self.process()
		self.post()
		self.assertEqual(frappe.db.count(LEAD, {"omni_form_id": FORM_ID}), 1)
		self.assertEqual(len(inbound(self.channel.name)), 1)
		self.assertTrue(lead)

	def test_missing_column_name_and_extra_unknown_columns_are_fine(self):
		data = self.payload
		data["user_column_data"].append({"column_id": "QUESTION_9", "string_value": "câu trả lời lạ"})
		row, _lead = self.process(data)
		self.assertEqual(row.status, "Done")
		self.assertIn(
			"QUESTION_9: câu trả lời lạ", frappe.db.get_value("Omni Inbound Message", row.name, "text")
		)


class TestCsvImport(GoogleCase):
	def write(self, rows, headers=None):
		path = Path(frappe.get_site_path("private", "files", f"{PREFIX}_google.csv"))
		path.parent.mkdir(parents=True, exist_ok=True)
		headers = headers or [
			"Lead ID",
			"Campaign ID",
			"Full Name",
			"Email",
			"Phone Number",
			"Company Name",
			"Submit Time",
			"TD505_TAX",
		]
		with path.open("w", encoding="utf-8-sig", newline="") as fh:
			w = csv.writer(fh)
			w.writerow(headers)
			w.writerows(rows)
		self.addCleanup(path.unlink, True)
		return str(path)

	def test_import_creates_missing_and_dedupes_against_webhook_by_lead_id(self):
		self.post()  # TD505GL1 đã vào qua webhook
		f = self.write(
			[
				[
					"TD505GL1",
					"TD505GCAMP",
					"TD505 Bích",
					"b@x-td505.vn",
					"0912345602",
					"Cty",
					"2026-11-01T10:00:00Z",
					"",
				],
				[
					"TD505GL5",
					"TD505GCAMP",
					"TD505 Chi",
					"c@x-td505.vn",
					"0912345605",
					"Cty",
					"2026-11-01T11:00:00Z",
					"",
				],
			]
		)
		stats = csv_import.run(self.channel.name, f, FORM_ID)
		self.assertEqual(stats, {"rows": 2, "created": 1, "duplicates": 1, "errors": 0})
		again = csv_import.run(self.channel.name, f, FORM_ID)
		self.assertEqual((again["created"], again["duplicates"]), (0, 2))
		self.assertEqual(
			sorted(r.external_message_id for r in inbound(self.channel.name)), ["TD505GL1", "TD505GL5"]
		)

	def test_dry_run_saves_nothing_and_bad_rows_count_as_errors(self):
		f = self.write(
			[
				["", "x", "TD505 A", "", "", "", "", ""],
				["TD505GL6", "x", "TD505 B", "", "0912345606", "", "", ""],
			]
		)
		stats = csv_import.run(self.channel.name, f, FORM_ID, dry_run=True)
		self.assertEqual((stats["created"], stats["errors"]), (1, 1))
		self.assertEqual(inbound(self.channel.name), [])

	def test_csv_requires_google_channel_and_known_form(self):
		meta = make_channel("Meta Lead Ads", f"{PREFIX} meta")
		with self.assertRaises(frappe.ValidationError):
			csv_import.run(meta.name, self.write([]), FORM_ID)
		stats = csv_import.run(
			self.channel.name, self.write([["TD505GL8", "", "", "", "", "", "", ""]]), "TD505KHONGCO"
		)
		self.assertEqual(stats["errors"], 1)

	def test_csv_import_through_worker_creates_lead(self):
		f = self.write(
			[
				[
					"TD505GL9",
					"TD505GCAMP",
					"TD505 Dũng",
					"d@x-td505.vn",
					"0912345609",
					"Cty",
					"2026-11-01T11:00:00Z",
					"",
				]
			]
		)
		csv_import.run(self.channel.name, f, FORM_ID)
		work("Google Lead Form:TD505GL9")
		row = inbound(self.channel.name)[0]
		self.assertEqual(row.status, "Done", row.last_error)
		self.assertEqual(frappe.db.get_value(LEAD, row.lead, "omni_phone_e164"), "+84912345609")
