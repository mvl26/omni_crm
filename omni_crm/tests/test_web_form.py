"""V2-05: kênh Web Form (docs/15 mục 4, 10): endpoint Guest, honeypot, giới hạn tần suất, UTM, đồng ý, chống bot."""

from unittest import mock

import frappe

from omni_crm.api import webhooks
from omni_crm.tests.ads_helpers import (
	PREFIX,
	AdsCase,
	FakeResponse,
	body,
	call_webhook,
	inbound,
	make_channel,
	make_form,
	make_users,
	work,
)

LEAD = "CRM Lead"
FORM = f"{PREFIX}WEB"


def payload(**over):
	data = {
		"form": FORM,
		"submission_id": "td505-sub-0001",
		"full_name": "TD505 Lê Văn Cường",
		"phone": "0912345611",
		"email": "td505.cuong@cty-td505.vn",
		"company_name": f"Công ty {PREFIX} Y tế",
		"tax_id": "0505050503",
		"message": "Cần tư vấn gói điều phối",
		"consent_contact": "1",
		"utm_source": "google",
		"utm_medium": "cpc",
		"utm_campaign": "td505-utm",
		"utm_content": "banner-a",
		"utm_term": "quan ly vat tu",
		"gclid": "TD505GCLID",
		"landing_page": "https://miyano.example/lien-he",
		"referrer": "https://www.google.com/",
	}
	data.update(over)
	return data


class WebCase(AdsCase):
	def setUp(self):
		super().setUp()
		make_users()
		self.channel = make_channel("Web Form", f"{PREFIX} web")
		self.form = make_form("Website", FORM, "Web form")
		frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PREFIX} Chiến dịch web",
				"platform": "Website",
				"utm_campaign": "td505-utm",
			}
		).insert()
		frappe.db.commit()

	def post(self, data=None, ip="10.9.9.9", raw=None):
		raw = raw if raw is not None else body(data or payload())
		return call_webhook(webhooks.web_form, raw, args={"ch": self.channel.name}, ip=ip)

	def process(self, data=None):
		self.post(data)
		row = inbound(self.channel.name)[0]
		work(row.identity_key)
		row = inbound(self.channel.name)[0]
		return row, row.lead


class TestEndpoint(WebCase):
	def test_guest_endpoint_is_whitelisted_for_guest(self):
		self.assertIn(webhooks.web_form, frappe.guest_methods)

	def test_success_returns_submission_id_only(self):
		res, status = self.post()
		self.assertEqual((status, res), (200, {"message": "ok", "submission_id": "td505-sub-0001"}))
		self.assertEqual(len(inbound(self.channel.name)), 1)

	def test_generated_submission_id_when_page_sends_none(self):
		data = payload()
		data.pop("submission_id")
		res, _status = self.post(data)
		self.assertEqual(len(res["submission_id"]), 32)

	def test_same_submission_twice_is_one_event(self):
		self.post()
		self.post()
		self.assertEqual(len(inbound(self.channel.name)), 1)

	def test_honeypot_looks_like_success_and_saves_nothing(self):
		res, status = self.post(payload(website_url="http://spam.example"))
		self.assertEqual(status, 200)
		self.assertEqual(res["message"], "ok")
		self.assertEqual(inbound(self.channel.name), [])

	def test_rate_limit_per_ip_returns_429_then_other_ip_still_ok(self):
		for i in range(5):
			_res, status = self.post(payload(submission_id=f"td505-sub-rl{i:03d}"))
			self.assertEqual(status, 200)
		_res, status = self.post(payload(submission_id="td505-sub-rl999"))
		self.assertEqual(status, 429)
		self.assertEqual(len(inbound(self.channel.name)), 5)
		_res, status = self.post(payload(submission_id="td505-sub-other"), ip="10.9.9.10")
		self.assertEqual(status, 200)

	def test_honeypot_hits_also_count_toward_rate_limit(self):
		for _i in range(5):
			self.post(payload(hp_field="x"))
		_res, status = self.post(payload())
		self.assertEqual(status, 429)

	def test_too_large_body_is_413_and_junk_body_is_rejected_without_saving(self):
		_res, status = self.post(raw=b"{" + b" " * (65 * 1024) + b"}")
		self.assertEqual(status, 413)
		_res, status = self.post(raw=b"\xff\xfe khong phai utf8")
		self.assertEqual(status, 401)
		_res, status = self.post(raw=b"[1,2,3]")
		self.assertEqual(status, 401)
		self.assertEqual(inbound(self.channel.name), [])

	def test_empty_submission_saves_nothing(self):
		self.post(
			payload(
				full_name="",
				phone="",
				email="",
				company_name="",
				tax_id="",
				message="",
				consent_contact="",
				utm_source="",
			)
		)
		self.assertEqual(inbound(self.channel.name), [])

	def test_disabled_or_wrong_type_channel_is_404(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		_res, status = self.post()
		self.assertEqual(status, 404)
		g = make_channel("Google Lead Form", f"{PREFIX} g")
		_res, status = call_webhook(webhooks.web_form, body(payload()), args={"ch": g.name})
		self.assertEqual(status, 404)

	def test_form_data_body_is_accepted(self):
		args = {"ch": self.channel.name, **{k: v for k, v in payload().items()}}
		_res, status = call_webhook(webhooks.web_form, b"", args=args)
		self.assertEqual(status, 200)
		self.assertEqual(len(inbound(self.channel.name)), 1)

	def test_very_long_values_are_truncated(self):
		self.post(payload(message="x" * 30000))
		text = inbound(self.channel.name)[0].text
		self.assertLess(len(text), 7000)

	def test_get_is_405(self):
		_res, status = call_webhook(webhooks.web_form, b"", method="GET", args={"ch": self.channel.name})
		self.assertEqual(status, 405)


class TestCaptcha(WebCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_value("Omni Channel", self.channel.name, "webform_captcha_provider", "Turnstile")
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		doc.webform_captcha_secret = "td505-captcha-secret-khong-that"
		doc.save()
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)

	def test_off_by_default_and_on_when_configured(self):
		_res, status = self.post()  # có provider mà không có token
		self.assertEqual(status, 401)
		with mock.patch(
			"omni_crm.channels.web_form.captcha.requests.post", return_value=FakeResponse({"success": True})
		) as post:
			_res, status = self.post(payload(captcha_token="tok"))
		self.assertEqual(status, 200)
		self.assertEqual(post.call_args.kwargs["timeout"], 10)

	def test_provider_failure_or_network_error_rejects(self):
		import requests

		with mock.patch(
			"omni_crm.channels.web_form.captcha.requests.post", return_value=FakeResponse({"success": False})
		):
			self.assertEqual(self.post(payload(captcha_token="tok"))[1], 401)
		with mock.patch("omni_crm.channels.web_form.captcha.requests.post", side_effect=requests.Timeout()):
			self.assertEqual(self.post(payload(captcha_token="tok"))[1], 401)
		self.assertEqual(inbound(self.channel.name), [])


class TestWorker(WebCase):
	def test_lead_with_utm_click_landing_page_and_campaign_by_utm(self):
		row, lead = self.process()
		self.assertEqual(row.status, "Done", row.last_error)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual(
			(d.source, d.omni_ad_platform, d.omni_first_channel), ("Website", "Website", "Web Form")
		)
		self.assertEqual(
			(d.omni_utm_source, d.omni_utm_medium, d.omni_utm_campaign, d.omni_utm_content, d.omni_utm_term),
			("google", "cpc", "td505-utm", "banner-a", "quan ly vat tu"),
		)
		self.assertEqual(
			(d.omni_click_id, d.omni_landing_page), ("TD505GCLID", "https://miyano.example/lien-he")
		)
		self.assertEqual(d.omni_campaign, f"{PREFIX} Chiến dịch web")  # khớp theo utm_campaign
		self.assertEqual(d.omni_form_id, FORM)

	def test_missing_consent_records_nothing_and_sends_nothing(self):
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"{PREFIX} Cường",
				"phone_nos": [{"phone": "+84912345611", "is_primary_mobile_no": 1}],
				"omni_consent": "Chưa hỏi",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		before = (frappe.db.count("Omni Outbound Message"), frappe.db.count("Email Queue"))
		row, _lead = self.process(payload(consent_contact=""))
		self.assertEqual(row.status, "Done", row.last_error)
		self.assertEqual(frappe.db.get_value("Contact", contact.name, "omni_consent"), "Chưa hỏi")
		self.assertEqual((frappe.db.count("Omni Outbound Message"), frappe.db.count("Email Queue")), before)

	def test_web_form_never_raises_consent_of_existing_contact(self):
		# V2-05 (review): đồng ý từ form ẩn danh không nâng Contact "Chưa hỏi" -> "Có"; chỉ nằm trên dòng thời gian của Lead mới
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"{PREFIX} Cường",
				"phone_nos": [{"phone": "+84912345611", "is_primary_mobile_no": 1}],
				"omni_consent": "Chưa hỏi",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		n = frappe.db.count("Contact")
		_row, lead = self.process()
		self.assertEqual(frappe.db.count("Contact"), n)
		c = frappe.db.get_value(
			"Contact",
			contact.name,
			["omni_consent", "omni_consent_date", "omni_consent_method"],
			as_dict=True,
		)
		self.assertEqual(
			(c.omni_consent, c.omni_consent_date, c.omni_consent_method), ("Chưa hỏi", None, None)
		)
		content = frappe.get_all("Communication", filters={"reference_name": lead}, pluck="content")[0]
		self.assertIn("Đồng ý nhận liên hệ: Có", content)

	def test_unverified_tax_id_creates_organization_with_verified_zero(self):
		_row, lead = self.process()
		org = frappe.get_doc("CRM Organization", frappe.db.get_value(LEAD, lead, "organization"))
		self.assertEqual((org.omni_tax_id, org.omni_verified), ("0505050503", 0))

	def test_bad_tax_id_and_empty_tax_id_never_store_blank_or_garbage(self):
		self.process(payload(tax_id="khong-phai-mst"))
		d = frappe.get_all("CRM Organization", filters={"omni_tax_id": ["like", "%khong%"]})
		self.assertEqual(d, [])
		lead = inbound(self.channel.name)[0].lead
		self.assertEqual(
			frappe.db.get_value(LEAD, lead, "organization"), f"Công ty {PREFIX} Y tế"
		)  # chỉ chuỗi, không tạo Organization

	def make_real_org(self):
		org = frappe.get_doc(
			{
				"doctype": "CRM Organization",
				"organization_name": f"{PREFIX} Nhà thầu thật",
				"omni_tax_id": "0505050503",
				"omni_verified": 1,
			}
		).insert(ignore_permissions=True)
		real = frappe.get_doc(
			{
				"doctype": LEAD,
				"first_name": f"{PREFIX} Đầu mối thật",
				"organization": org.name,
				"lead_owner": "Administrator",
				"mobile_no": "+84900000999",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return org, real

	def test_anonymous_tax_id_of_real_contractor_is_weak_new_lead_is_created_and_flagged(self):
		# V2-05 (review): người lạ khai MST của nhà thầu thật không được gắn vào Lead của nhà thầu và không để lại SĐT/email ở đó
		org, real = self.make_real_org()
		n = frappe.db.count(LEAD)
		_row, lead = self.process()
		self.assertNotEqual(lead, real.name)
		self.assertEqual(frappe.db.count(LEAD), n + 1)
		stranger = frappe.get_doc(LEAD, lead)
		self.assertNotEqual(stranger.organization, org.name)
		self.assertEqual(stranger.omni_phone_e164, "+84912345611")
		r = frappe.get_doc(LEAD, real.name)
		self.assertFalse(r.email)
		self.assertNotEqual(r.omni_phone_e164, "+84912345611")
		self.assertEqual(frappe.db.get_value("CRM Organization", org.name, "omni_verified"), 1)
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_name": lead, "content": ["like", f"%trùng Organization {org.name}%"]}
			)
		)
		self.assertEqual(
			frappe.db.count("Comment", {"reference_name": real.name, "content": ["like", "%MST khai%"]}), 0
		)
		self.assertEqual(
			frappe.db.count("Communication", {"reference_name": real.name}), 0
		)  # không thêm hoạt động vào Lead thật

	def test_unverified_new_org_for_unknown_tax_id_still_created(self):
		_row, lead = self.process()
		org = frappe.get_doc("CRM Organization", frappe.db.get_value(LEAD, lead, "organization"))
		self.assertEqual((org.omni_tax_id, org.omni_verified), ("0505050503", 0))

	def test_form_off_switch(self):
		frappe.db.set_value("Omni Lead Form", self.form.name, "enabled", 0)
		frappe.db.commit()
		row, lead = self.process()
		self.assertEqual((row.status, lead), ("Done", None))


class TestPublicInputHardening(WebCase):
	"""Review V2-05: giá trị dài/ký tự lạ từ người lạ không được làm hỏng lô, và tên đơn vị không gắn nhầm vào Organization thật."""

	def test_long_utm_and_click_id_are_clipped_not_fatal(self):
		row, lead = self.process(
			payload(utm_source="s" * 200, utm_content="c" * 200, gclid="g" * 200, utm_campaign="m" * 200)
		)
		self.assertEqual(row.status, "Done", row.last_error)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual((len(d.omni_utm_source), len(d.omni_utm_content), len(d.omni_click_id)), (140,) * 3)

	def test_angle_brackets_in_company_name_do_not_break_the_batch(self):
		row, lead = self.process(
			payload(company_name=f"<img src=x onerror=alert(1)> {PREFIX} Công ty", tax_id="0505050597")
		)
		self.assertEqual(row.status, "Done", row.last_error)
		org = frappe.db.get_value(LEAD, lead, "organization")
		self.assertTrue(org)
		self.assertNotIn("<", org)
		self.assertNotIn(">", org)

	def test_company_name_equal_to_real_organization_does_not_attach_lead(self):
		org = frappe.get_doc(
			{
				"doctype": "CRM Organization",
				"organization_name": f"{PREFIX} Nhà thầu thật",
				"omni_tax_id": "0505050599",
				"omni_verified": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		_row, lead = self.process(payload(tax_id="", company_name=org.name))
		self.assertNotEqual(frappe.db.get_value(LEAD, lead, "organization"), org.name)
		self.assertNotIn(lead, resolver_organization_leads(org.name))


def resolver_organization_leads(org):
	from omni_crm.identity import resolver

	return resolver.organization_leads(org)
