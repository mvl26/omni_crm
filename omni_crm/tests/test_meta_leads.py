"""V2-05: kênh Meta Lead Ads (docs/15 mục 2, 10). Payload mẫu theo tài liệu Meta; Graph API được giả: CHƯA THỬ VỚI NỀN TẢNG THẬT."""

import hashlib
import hmac
import json
from unittest import mock

import frappe

from omni_crm.api import webhooks
from omni_crm.channels.meta_leads import adapter as meta_adapter
from omni_crm.channels.meta_leads import reconcile
from omni_crm.channels.meta_leads.client import MetaClient
from omni_crm.tests.ads_helpers import (
	APP_SECRET,
	PAGE_TOKEN,
	PREFIX,
	SALE,
	SALE2,
	VERIFY_TOKEN,
	AdsCase,
	body,
	call_webhook,
	graph_router,
	inbound,
	load,
	make_channel,
	make_form,
	make_users,
	work,
)

LEAD = "CRM Lead"
GET = "omni_crm.channels.meta_leads.client.requests.get"


def sign(raw: bytes, secret: str = APP_SECRET) -> dict:
	return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}


class MetaCase(AdsCase):
	def setUp(self):
		super().setUp()
		make_users()
		self.channel = make_channel("Meta Lead Ads", f"{PREFIX} meta")
		self.form = make_form(
			"Meta",
			f"{PREFIX}FORM",
			"Meta form",
			{
				"phone_number": "phone",
				"so_benh_vien_dang_cung_cap": "hospital_count_text",
				"ghi_chu": "message",
				"dong_y_lien_he": "consent_contact",
				"dong_y_tiep_thi": "consent_marketing",
			},
			default_owner=SALE,
		)
		self.hook = load("meta_leads/webhook_leadgen.json")

	def post(self, data=None, headers=None):
		raw = body(data or self.hook)
		return call_webhook(
			webhooks.meta_leads,
			raw,
			headers if headers is not None else sign(raw),
			args={"ch": self.channel.name},
		)

	def process(self, calls=None, **graph):
		"""Gửi webhook rồi chạy worker với Graph API giả; trả (tin vào, tên Lead)."""
		self.post()
		with mock.patch(GET, side_effect=graph_router(calls=calls, **graph)):
			rows = inbound(self.channel.name)
			work(rows[0].identity_key)
		row = inbound(self.channel.name)[0]
		return row, row.lead


class TestHandshake(MetaCase):
	def hs(self, **over):
		args = {
			"ch": self.channel.name,
			"hub.mode": "subscribe",
			"hub.verify_token": VERIFY_TOKEN,
			"hub.challenge": "1158201444",
		}
		args.update(over)
		return call_webhook(webhooks.meta_leads, b"", method="GET", args=args)

	def test_right_token_returns_challenge_raw(self):
		_res, status = self.hs()
		self.assertEqual(status, 200)
		self.assertEqual(
			frappe.local.response.get("filecontent"), b"1158201444"
		)  # trả nguyên văn, không bọc {"message"}

	def test_wrong_token_mode_or_channel_is_403(self):
		for over in (
			{"hub.verify_token": "sai-token-sai-token-sai"},
			{"hub.mode": "unsubscribe"},
			{"ch": "khong-co"},
			{"hub.challenge": "<script>alert(1)</script>"},
			{"hub.verify_token": ""},
		):
			with self.subTest(over=over):
				_res, status = self.hs(**over)
				self.assertEqual(status, 403)
				self.assertNotIn("filecontent", frappe.local.response)

	def test_channel_without_token_configured_never_verifies(self):
		ch = make_channel("Meta Lead Ads", f"{PREFIX} meta2", meta_verify_token="")
		_res, status = call_webhook(
			webhooks.meta_leads,
			b"",
			method="GET",
			args={"ch": ch.name, "hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"},
		)
		self.assertEqual(status, 403)


class TestSignatureAndGateway(MetaCase):
	def test_valid_signature_saves_one_event_without_personal_data(self):
		_res, status = self.post()
		self.assertEqual(status, 200)
		rows = inbound(self.channel.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].event_type, rows[0].status), ("form_lead", "New"))
		self.assertEqual(rows[0].external_message_id, "TD505LG1")
		self.assertEqual(
			rows[0].identity_key, "Meta Lead Ads:TD505LG1"
		)  # khóa định danh không chứa SĐT/email

	def test_wrong_missing_or_tampered_signature_saves_nothing(self):
		raw = body(self.hook)
		tampered = raw.replace(b"TD505LG1", b"TD505LG9")
		cases = {
			"sai khóa": sign(raw, "khoa-khac-khoa-khac"),
			"thiếu header": {},
			"sai định dạng": {"X-Hub-Signature-256": "abc"},
			"body bị sửa": sign(raw),
		}
		for name, headers in cases.items():
			with self.subTest(name):
				data = tampered if name == "body bị sửa" else raw
				_res, status = call_webhook(
					webhooks.meta_leads, data, headers, args={"ch": self.channel.name}
				)
				self.assertEqual(status, 401)
		self.assertEqual(inbound(self.channel.name), [])

	def test_channel_without_app_secret_rejects_even_with_a_forged_signature(self):
		ch = make_channel("Meta Lead Ads", f"{PREFIX} meta3", meta_app_secret="")
		raw = body(self.hook)
		_res, status = call_webhook(webhooks.meta_leads, raw, sign(raw, ""), args={"ch": ch.name})
		self.assertEqual(status, 401)

	def test_duplicate_delivery_is_idempotent(self):
		self.post()
		_res, status = self.post()
		self.assertEqual(status, 200)
		self.assertEqual(len(inbound(self.channel.name)), 1)

	def test_page_of_another_channel_is_ignored(self):
		self.hook["entry"][0]["changes"][0]["value"]["page_id"] = "TD505OTHER"
		self.post()
		self.assertEqual(inbound(self.channel.name), [])

	def test_non_leadgen_fields_and_other_objects_are_ignored(self):
		self.hook["entry"][0]["changes"][0]["field"] = "feed"
		self.post()
		self.hook["object"] = "user"
		self.post()
		self.assertEqual(inbound(self.channel.name), [])

	def test_oversize_body_is_413_and_get_on_post_endpoint_path_is_405(self):
		big = b"{" + b" " * (webhooks.MAX_BODY_BYTES + 10) + b"}"
		_res, status = call_webhook(webhooks.meta_leads, big, sign(big), args={"ch": self.channel.name})
		self.assertEqual(status, 413)

	def test_channel_off_stores_but_does_not_enqueue_or_process(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		self.post()
		row = inbound(self.channel.name)[0]
		with mock.patch(GET, side_effect=AssertionError("kênh tắt không được gọi Graph API")):
			work(row.identity_key)
		self.assertEqual(inbound(self.channel.name)[0].status, "New")


class TestWorkerCreatesLead(MetaCase):
	def test_lead_created_with_attribution_organization_and_timeline(self):
		calls: list = []
		row, lead = self.process(calls=calls)
		self.assertEqual(row.status, "Done", row.last_error)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual(
			(d.first_name, d.source, d.omni_first_channel),
			(f"{PREFIX} Nguyễn Văn An", "Quảng cáo Meta", "Meta Lead Ads"),
		)
		self.assertEqual(d.omni_phone_e164, "+84912345601")
		self.assertEqual(d.omni_email_norm, "td505.an@benhvien-td505.vn")
		self.assertEqual(
			(d.omni_ad_platform, d.omni_ad_id, d.omni_adset_id, d.omni_form_id),
			("Meta", "TD505AD1", "TD505ADSET1", f"{PREFIX}FORM"),
		)
		self.assertEqual(d.omni_campaign, f"{PREFIX} Chiến dịch Meta")
		self.assertEqual(d.omni_last_campaign, d.omni_campaign)
		self.assertEqual((d.facebook_lead_id, d.facebook_form_id), ("TD505LG1", f"{PREFIX}FORM"))
		self.assertEqual(d.job_title, "Trưởng khoa Dược")
		self.assertFalse(d.omni_is_test)
		org = frappe.get_doc("CRM Organization", d.organization)
		self.assertEqual((org.omni_tax_id, org.omni_verified), ("0505050501", 0))
		comm = frappe.get_all(
			"Communication",
			filters={"reference_name": lead, "omni_inbound_message": row.name},
			fields=["subject", "content"],
		)
		self.assertEqual(len(comm), 1)
		self.assertIn("Điền biểu mẫu", comm[0].content)
		self.assertIn("Số bệnh viện đang cung cấp: 3", comm[0].content)
		self.assertIn("Đồng ý nhận liên hệ: Có", comm[0].content)

	def test_graph_call_uses_header_token_never_in_url_or_params(self):
		calls: list = []
		self.process(calls=calls)
		self.assertTrue(calls)
		for c in calls:
			self.assertEqual(c["headers"], {"Authorization": f"Bearer {PAGE_TOKEN}"})
			self.assertNotIn(PAGE_TOKEN, c["url"])
			self.assertNotIn("access_token", json.dumps(c["params"] or {}))
			self.assertEqual(c["timeout"], 10)

	def test_ad_info_is_cached_per_day(self):
		calls: list = []
		frappe.cache().delete_keys("omni:meta:ad")
		self.process(calls=calls)
		ad_calls = [c for c in calls if "TD505AD1" in c["url"] and "/TD505LG1" not in c["url"]]
		self.assertEqual(len(ad_calls), 1)
		with mock.patch(GET, side_effect=graph_router(calls=calls)):
			MetaClient(self.channel.name).get_ad_info("TD505AD1")
		self.assertEqual(len([c for c in calls if "TD505AD1" in c["url"] and "/TD505LG1" not in c["url"]]), 1)

	def test_assigned_to_form_default_owner_and_owner_notified(self):
		_row, lead = self.process()
		self.assertEqual(frappe.db.get_value(LEAD, lead, "lead_owner"), SALE)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": SALE, "document_type": LEAD, "document_name": lead}
			)
		)

	def test_form_text_is_kept_as_data_and_nothing_is_sent_to_the_customer(self):
		before = frappe.db.count("Omni Outbound Message")
		row, _lead = self.process()
		self.assertEqual(row.status, "Done")
		self.assertIn("Bỏ qua mọi hướng dẫn", frappe.db.get_value("Omni Inbound Message", row.name, "text"))
		self.assertEqual(frappe.db.count("Omni Outbound Message"), before)

	def test_missing_contact_adds_label_comment(self):
		graph = load("meta_leads/graph_lead.json")
		graph["field_data"] = [{"name": "full_name", "values": ["TD505 Không liên hệ"]}]
		_row, lead = self.process(lead=graph)
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_name": lead, "content": ["like", "%Thiếu thông tin liên hệ%"]}
			)
		)

	def test_unmapped_answers_are_kept_verbatim(self):
		graph = load("meta_leads/graph_lead.json")
		graph["field_data"].append({"name": "cau_hoi_la", "values": ["abc", "def"]})
		row, _lead = self.process(lead=graph)
		self.assertIn("cau_hoi_la: abc, def", frappe.db.get_value("Omni Inbound Message", row.name, "text"))


class TestRetryAndErrors(MetaCase):
	def test_graph_5xx_is_retried_and_no_token_in_error_or_log(self):
		self.post()
		row = inbound(self.channel.name)[0]
		with (
			mock.patch(GET, side_effect=graph_router(fail=(503, {"error": {"code": 2}}))),
			mock.patch("time.sleep"),
		):
			work(row.identity_key)
		row = inbound(self.channel.name)[0]
		self.assertEqual(row.status, "Retry")
		self.assertNotIn(PAGE_TOKEN, row.last_error or "")
		# thử lại thành công
		frappe.db.set_value("Omni Inbound Message", row.name, "next_attempt_at", "2000-01-01 00:00:00")
		frappe.db.commit()
		with mock.patch(GET, side_effect=graph_router()):
			work(row.identity_key)
		self.assertEqual(inbound(self.channel.name)[0].status, "Done")

	def test_network_error_wrapped_without_url_or_token(self):
		import requests

		self.post()
		row = inbound(self.channel.name)[0]
		boom = requests.ConnectionError(
			f"HTTPSConnectionPool url=https://graph.facebook.com/?access_token={PAGE_TOKEN}"
		)
		with mock.patch(GET, side_effect=boom), mock.patch("time.sleep"):
			work(row.identity_key)
		row = inbound(self.channel.name)[0]
		self.assertEqual(row.status, "Retry")
		self.assertNotIn(PAGE_TOKEN, row.last_error or "")
		self.assertFalse(frappe.db.exists("Error Log", {"error": ["like", f"%{PAGE_TOKEN}%"]}))

	def test_bad_token_goes_to_manual_after_max_attempts(self):
		self.post()
		row = inbound(self.channel.name)[0]
		for _i in range(3):
			frappe.db.set_value("Omni Inbound Message", row.name, "next_attempt_at", "2000-01-01 00:00:00")
			frappe.db.commit()
			with mock.patch(
				GET, side_effect=graph_router(fail=(400, {"error": {"code": 190, "message": PAGE_TOKEN}}))
			):
				work(row.identity_key)
		row = inbound(self.channel.name)[0]
		self.assertEqual(row.status, "Manual")
		self.assertNotIn(PAGE_TOKEN, row.last_error or "")
		self.assertEqual(frappe.db.count(LEAD, {"facebook_lead_id": "TD505LG1"}), 0)

	def test_disclaimer_failure_does_not_lose_the_lead(self):
		def fake(url, params=None, headers=None, timeout=None, **kw):
			from omni_crm.tests.ads_helpers import FakeResponse

			if (params or {}).get("fields") == "custom_disclaimer_responses":
				return FakeResponse({"error": {"code": 100}}, 400)
			return graph_router()(url, params=params, headers=headers, timeout=timeout)

		self.post()
		with mock.patch(GET, side_effect=fake):
			work(inbound(self.channel.name)[0].identity_key)
		row = inbound(self.channel.name)[0]
		self.assertEqual(row.status, "Done")
		self.assertTrue(row.lead)


class TestFormSwitchAndUnknownForm(MetaCase):
	def test_disabled_form_stores_but_creates_no_lead_and_no_graph_call(self):
		frappe.db.set_value("Omni Lead Form", self.form.name, "enabled", 0)
		frappe.db.commit()
		self.post()
		row = inbound(self.channel.name)[0]
		with mock.patch(GET, side_effect=AssertionError("biểu mẫu tắt không được gọi Graph API")):
			work(row.identity_key)
		row = inbound(self.channel.name)[0]
		self.assertEqual((row.status, row.lead), ("Done", None))

	def test_unknown_form_still_creates_lead_with_default_mapping(self):
		frappe.db.delete("Omni Lead Form", {"name": self.form.name})
		frappe.db.commit()
		row, lead = self.process()
		self.assertEqual(row.status, "Done")
		self.assertEqual(
			frappe.db.get_value(LEAD, lead, "omni_phone_e164"), "+84912345601"
		)  # phone_number có trong khóa mặc định


class TestDedupeAndTouches(MetaCase):
	def second_lead(self, lead_id, campaign_id="TD505CAMP1", name="TD505 Chiến dịch Meta"):
		hook = load("meta_leads/webhook_leadgen.json")
		hook["entry"][0]["changes"][0]["value"]["leadgen_id"] = lead_id
		self.post(hook)
		graph = load("meta_leads/graph_lead.json")
		graph["id"] = lead_id
		graph["ad_id"] = f"TD505AD-{campaign_id}"
		ad = load("meta_leads/graph_ad.json")
		ad["id"] = graph["ad_id"]
		ad["campaign_id"], ad["campaign"] = campaign_id, {"name": name}
		with mock.patch(GET, side_effect=graph_router(lead=graph, ad=ad)):
			work(f"Meta Lead Ads:{lead_id}")
		return frappe.db.get_value(
			"Omni Inbound Message", {"external_message_id": lead_id}, ["lead", "status"], as_dict=True
		)

	def test_same_person_new_leadgen_same_campaign_keeps_one_lead_and_owner(self):
		_row, lead = self.process()
		frappe.db.set_value(LEAD, lead, "lead_owner", SALE2)
		second = self.second_lead("TD505LG2")
		self.assertEqual(second.lead, lead)
		self.assertEqual(frappe.db.count(LEAD, {"omni_phone_e164": "+84912345601"}), 1)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual(d.lead_owner, SALE2)  # không giao lại
		self.assertEqual(
			frappe.db.count("Communication", {"reference_name": lead}), 2
		)  # thêm hoạt động "Điền biểu mẫu"

	def test_return_through_other_campaign_keeps_first_updates_last_and_comments(self):
		_row, lead = self.process()
		first = frappe.db.get_value(LEAD, lead, "omni_campaign")
		second = self.second_lead("TD505LG3", "TD505CAMP2", "TD505 Chiến dịch Meta thứ hai")
		self.assertEqual(second.lead, lead)
		d = frappe.get_doc(LEAD, lead)
		self.assertEqual(d.omni_campaign, first)
		self.assertEqual(d.omni_last_campaign, f"{PREFIX} Chiến dịch Meta thứ hai")
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_name": lead, "content": ["like", "%Quay lại qua chiến dịch%"]}
			)
		)

	def test_existing_facebook_lead_id_from_crm_sync_is_attached_not_duplicated(self):
		# ĐQ-11: CRM Lead đã được đồng bộ Facebook có sẵn của crm tạo ra
		pre = frappe.get_doc(
			{
				"doctype": LEAD,
				"first_name": f"{PREFIX} Có sẵn",
				"facebook_lead_id": "TD505LG1",
				"mobile_no": "+84900000505",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		row, lead = self.process()
		self.assertEqual(lead, pre.name)
		self.assertEqual(frappe.db.count(LEAD, {"facebook_lead_id": "TD505LG1"}), 1)
		self.assertEqual(frappe.db.get_value(LEAD, lead, "omni_ad_platform"), "Meta")


class TestReconcile(MetaCase):
	def test_missing_leads_are_created_once_and_idempotent_with_webhook(self):
		a = load("meta_leads/graph_lead.json")
		b = {**a, "id": "TD505LG7"}
		self.post()  # webhook đã nhận TD505LG1
		with mock.patch(GET, side_effect=graph_router(lead=[a, b])):
			stats = reconcile.reconcile_channel(self.channel.name)
			again = reconcile.reconcile_channel(self.channel.name)
		self.assertEqual((stats["seen"], stats["created"]), (2, 1))  # chỉ TD505LG7 là lead thiếu
		self.assertEqual(again["created"], 0)
		ids = sorted(r.external_message_id for r in inbound(self.channel.name))
		self.assertEqual(ids, ["TD505LG1", "TD505LG7"])
		self.assertIsNotNone(frappe.db.get_value("Omni Lead Form", self.form.name, "last_reconciled_at"))
		with mock.patch(GET, side_effect=graph_router(lead=[a, b])):
			work("Meta Lead Ads:TD505LG7")  # lead đã tải sẵn: không gọi lại Graph lấy lead
		self.assertEqual(
			frappe.db.get_value("Omni Inbound Message", {"external_message_id": "TD505LG7"}, "status"), "Done"
		)

	def test_graph_error_for_one_form_does_not_raise(self):
		with mock.patch(GET, side_effect=graph_router(fail=(401, {"error": {"code": 190}}))):
			stats = reconcile.reconcile_channel(self.channel.name)
		self.assertEqual((stats["created"], stats["errors"]), (0, 1))

	def test_run_skips_channel_without_token(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		with mock.patch(GET, side_effect=AssertionError("kênh tắt không đối soát")):
			self.assertEqual(reconcile.run()["created"], 0)


class TestTestLead(MetaCase):
	def test_testing_tool_lead_is_flagged_unassigned_and_not_matched_to_real_contact(self):
		real = frappe.get_doc(
			{"doctype": LEAD, "first_name": f"{PREFIX} Khách thật", "mobile_no": "+84912345601"}
		).insert(ignore_permissions=True)
		graph = load("meta_leads/graph_lead.json")
		graph["field_data"] = [
			{"name": "full_name", "values": ["<test lead: dummy data for full_name>"]},
			{"name": "phone_number", "values": ["+84912345601"]},
			{"name": "email", "values": ["test@fb.com"]},
		]
		frappe.db.commit()
		row, lead = self.process(lead=graph)
		self.assertEqual(row.status, "Done", row.last_error)
		self.assertNotEqual(lead, real.name)
		d = frappe.get_doc(LEAD, lead)
		self.assertTrue(d.omni_is_test)
		self.assertFalse(d.lead_owner)
		self.assertEqual(frappe.db.count("ToDo", {"reference_name": lead, "status": "Open"}), 0)
		self.assertFalse(d.omni_phone_e164)
		self.assertEqual(frappe.db.count("Notification Log", {"document_name": lead}), 0)


class TestHostileInput(MetaCase):
	def test_hostile_name_and_html_do_not_break_the_batch_or_render_as_html(self):
		graph = load("meta_leads/graph_lead.json")
		graph["field_data"][0]["values"] = ["TD505 \"><script>alert(1)</script> O'Brien & {{7*7}}"]
		row, lead = self.process(lead=graph)
		self.assertEqual(row.status, "Done", row.last_error)
		self.assertNotIn("<", frappe.db.get_value(LEAD, lead, "first_name"))
		content = frappe.get_all("Communication", filters={"reference_name": lead}, pluck="content")[0]
		self.assertNotIn("<script>", content)
