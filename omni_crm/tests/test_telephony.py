"""D2-01: kênh tổng đài chung chạy hoàn toàn với FakeProvider (docs/07 mục 6)."""

import json
import time
from datetime import datetime
from unittest import mock

import frappe
from crm.api.activities import get_activities
from frappe.utils import get_datetime

from omni_crm.api import inbox, webhooks
from omni_crm.channels.registry import get_adapter
from omni_crm.channels.telephony import calls, lookup
from omni_crm.channels.telephony import registry as tel_registry
from omni_crm.channels.telephony.base import CallEvent, LookupResult
from omni_crm.channels.telephony.fake import SAMPLE_AUDIO, FakeProvider
from omni_crm.crm_bridge import call_log
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.tests.telephony_helpers import (
	AGENT,
	CUSTOMER_E164,
	HOTLINE,
	NUMBERS,
	TOKEN,
	TelephonyTestCase,
	call,
	cleanup,
	fake_request,
	make_channel,
)

CALL = "Omni Call"
INBOUND = "Omni Inbound Message"


def event(**over) -> CallEvent:
	base = dict(
		call_id="td201-x", direction="Inbound", from_number=CUSTOMER_E164, to_number=HOTLINE, status="Ended"
	)
	base.update(over)
	return CallEvent(**base)


class TestCallEventRules(TelephonyTestCase):
	def test_final_and_status_rules(self):
		self.assertFalse(event(status="Ringing").is_final)
		self.assertFalse(event(status="Answered").is_final)  # nhấc máy: sự kiện trung gian
		self.assertTrue(event(status="Answered", ended_at=datetime(2026, 1, 1)).is_final)
		for s in ("Missed", "Busy", "Failed", "Voicemail", "Ended"):
			self.assertTrue(event(status=s).is_final, s)
		self.assertEqual(event(status="Ended", answered_at=datetime(2026, 1, 1)).final_status, "Answered")
		self.assertEqual(event(status="Ended").final_status, "Missed")
		self.assertEqual(event(status="Busy").final_status, "Busy")

	def test_customer_number_follows_direction(self):
		self.assertEqual(event().customer_number, CUSTOMER_E164)
		self.assertEqual(
			event(direction="Outbound", to_number="+84903201002").customer_number, "+84903201002"
		)


class TestFakeProvider(TelephonyTestCase):
	def test_parse_event_normalizes_numbers(self):
		(ev,) = FakeProvider().parse_event(json.dumps(call("p1")).encode(), {}, self.channel)
		self.assertEqual(ev.from_number, CUSTOMER_E164)
		self.assertEqual(ev.duration_seconds, 125)
		self.assertEqual(ev.answered_at, get_datetime("2026-10-30 09:00:05"))

	def test_recording_and_list_calls(self):
		p = FakeProvider(
			[
				event(call_id="a", ended_at=datetime(2026, 10, 29, 10)),
				event(call_id="b", ended_at=datetime(2026, 10, 30, 10)),
			]
		)
		data, mime = p.fetch_recording("ref", self.channel)
		self.assertEqual((data, mime), (SAMPLE_AUDIO, "audio/wav"))
		self.assertLess(len(data), 4096)
		got = p.list_calls(datetime(2026, 10, 29), datetime(2026, 10, 30), self.channel)
		self.assertEqual([c.call_id for c in got], ["a"])
		with self.assertRaises(NotImplementedError):
			p.click_to_call("1", "2", self.channel)

	def test_fake_is_dev_only(self):
		with (
			mock.patch.dict(frappe.flags, {"in_test": False}),
			mock.patch.dict(frappe.conf, {"developer_mode": 0}),
		):
			with self.assertRaises(tel_registry.ProviderNotFoundError):
				tel_registry.get_provider("Fake")
			with self.assertRaises(frappe.ValidationError):
				make_channel("TD201 prod", telephony_provider="Fake")
		self.assertEqual(tel_registry.get_provider("Fake").name, "Fake")

	def test_unknown_or_missing_provider(self):
		for name in (None, "", "Khong co"):
			with self.assertRaises(tel_registry.ProviderNotFoundError):
				tel_registry.get_provider(name)

	def test_adapter_registered_for_telephony(self):
		self.assertEqual(get_adapter("Telephony").channel_type, "Telephony")


class TestWebhookSecurity(TelephonyTestCase):
	def test_good_token_and_sign_accepted(self):
		self.assertEqual(self.post(call("s1")), 200)
		self.assertEqual(len(self.inbound()), 1)

	def test_wrong_missing_or_short_token_rejected(self):
		for t in ("sai-token", "", None):
			args = {"ch": self.channel.name, **({"t": t} if t is not None else {})}
			self.assertEqual(self.post(call("s2"), args=args), 401, t)
		self.assertEqual(self.inbound(), [])

	def test_short_stored_token_never_matches(self):
		# kênh có token ngắn (ghi thẳng DB, bỏ qua validate): chuỗi rỗng/ngắn không được khớp
		frappe.db.set_value("Omni Channel", self.channel.name, "webhook_token", "ngan")
		from frappe.utils.password import set_encrypted_password

		set_encrypted_password("Omni Channel", self.channel.name, "ngan", "webhook_token")
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		self.assertEqual(self.post(call("s3"), args={"ch": self.channel.name, "t": "ngan"}), 401)
		self.assertEqual(self.post(call("s3"), args={"ch": self.channel.name, "t": ""}), 401)

	def test_missing_provider_sign_rejected(self):
		self.assertEqual(self.post(call("s4"), headers={}), 401)
		self.assertEqual(self.inbound(), [])

	def test_token_only_in_form_dict_is_still_read(self):
		frappe.local.request = fake_request(call("s5"), {"ch": self.channel.name})
		frappe.local.form_dict = frappe._dict(t=TOKEN)
		frappe.local.response = frappe._dict()
		with mock.patch("frappe.enqueue"):
			webhooks.telephony_event()
		self.assertEqual(frappe.local.response.get("http_status_code", 200), 200)

	def test_get_not_allowed_and_unknown_channel(self):
		frappe.local.request = fake_request(b"", self.args(), method="GET")
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = frappe._dict()
		webhooks.telephony_event()
		self.assertEqual(frappe.local.response["http_status_code"], 405)
		self.assertEqual(self.post(call("s6"), args={"ch": "khong co", "t": TOKEN}), 404)

	def test_token_validation_on_channel_form(self):
		with self.assertRaises(frappe.ValidationError):
			make_channel("TD201 ngan", webhook_token="ngan")


class TestEvents(TelephonyTestCase):
	def test_answered_call_one_inbound_and_dedupe_key(self):
		self.post(call("e1"))
		(row,) = self.inbound()
		self.assertEqual(row.event_type, "call_ended")
		self.assertEqual(row.dedupe_key, f"{self.channel.name}:td201-e1")
		self.assertEqual(row.status, "New")

	def test_intermediate_events_make_no_inbound_but_raw_events(self):
		self.post(call("e2", "Ringing", answered_at=None, ended_at=None, duration=None))
		self.post(call("e2", "Answered", ended_at=None, duration=None))
		self.assertEqual(self.inbound(), [])
		doc = frappe.get_doc(CALL, {"call_id": "td201-e2"})
		self.assertEqual(doc.status or "", "")  # bản tạm
		self.assertEqual(len(json.loads(doc.raw_events)), 2)
		self.post(call("e2", "Answered", ended_at=None, duration=None))  # gửi lại nguyên văn: không nhân đôi
		self.assertEqual(len(json.loads(frappe.db.get_value(CALL, doc.name, "raw_events"))), 2)

	def test_duplicate_final_is_one_record(self):
		for _i in range(3):
			self.assertEqual(self.post(call("e3")), 200)
		self.assertEqual(len(self.inbound()), 1)
		self.run_worker()
		self.assertEqual(frappe.db.count(CALL, {"call_id": "td201-e3"}), 1)
		self.assertEqual(frappe.db.count("CRM Call Log", {"id": ["like", "%td201-e3"]}), 1)

	def test_out_of_order_ringing_after_end_keeps_final_fields(self):
		self.post(call("e4"))
		self.run_worker()
		before = frappe.db.get_value(
			CALL, {"call_id": "td201-e4"}, ["status", "ended_at", "duration_seconds", "agent"], as_dict=True
		)
		self.post(call("e4", "Ringing", answered_at=None, ended_at=None, duration=None, agent_ref=None))
		doc = frappe.get_doc(CALL, {"call_id": "td201-e4"})
		self.assertEqual((doc.status, doc.ended_at, doc.duration_seconds, doc.agent), tuple(before.values()))
		self.assertEqual(
			len(json.loads(doc.raw_events)), 2
		)  # cả sự kiện cuối lẫn sự kiện trung gian đến muộn

	def test_final_before_start_events_arrive(self):
		# kết thúc đến trước, nhấc máy đến sau: vẫn một Omni Call, trạng thái cuối không đổi
		self.post(call("e5"))
		self.post(call("e5", "Answered", ended_at=None, duration=None))
		self.run_worker()
		self.assertEqual(frappe.db.count(CALL, {"call_id": "td201-e5"}), 1)
		self.assertEqual(frappe.db.get_value(CALL, {"call_id": "td201-e5"}, "status"), "Answered")

	def test_concurrent_create_race_is_handled(self):
		ev = event(call_id="td201-race")
		calls.get_or_create(self.channel, ev)
		real = calls._locked_name
		seen = iter([None])  # lần tra đầu "chưa thấy" (yêu cầu kia chưa commit), insert đụng khóa duy nhất

		def fake_lock(call_id):
			return next(seen, None) or real(call_id)

		with mock.patch.object(calls, "_locked_name", side_effect=fake_lock):
			name = calls.get_or_create(self.channel, ev)
		self.assertEqual(name, frappe.db.get_value(CALL, {"call_id": "td201-race"}))
		self.assertEqual(frappe.db.count(CALL, {"call_id": "td201-race"}), 1)

	def test_unparsable_body_is_stored_as_manual(self):
		self.assertEqual(self.post(b"khong phai json"), 200)
		(row,) = self.inbound()
		self.assertEqual((row.event_type, row.status), ("unparsed", "Manual"))

	def test_disabled_channel_stores_but_does_not_enqueue(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		self.assertEqual(self.post(call("e6")), 200)
		self.assertEqual(len(self.inbound()), 1)
		self.enqueue.assert_not_called()

	def test_enabled_channel_enqueues_identity_job(self):
		self.post(call("e7"))
		self.enqueue.assert_called()


class TestWorkerFlow(TelephonyTestCase):
	def activities(self, name: str) -> list[dict]:
		return get_activities(name)[1]  # (activities, calls, notes, tasks, attachments)

	def test_answered_inbound_creates_lead_call_and_call_log(self):
		self.post(call("w1", recording_ref="rec-1"))
		self.run_worker()
		(row,) = self.inbound()
		self.assertEqual(row.status, "Done")
		lead = row.lead
		self.assertTrue(lead)
		lead_row = frappe.db.get_value(
			"CRM Lead", lead, ["source", "omni_phone_e164", "omni_first_channel"], as_dict=True
		)
		self.assertEqual(
			(lead_row.source, lead_row.omni_phone_e164, lead_row.omni_first_channel),
			("Điện thoại", CUSTOMER_E164, "Telephony"),
		)
		oc = frappe.get_doc(CALL, {"call_id": "td201-w1"})
		self.assertEqual(
			(
				oc.provider,
				oc.direction,
				oc.status,
				oc.from_number,
				oc.to_number,
				oc.duration_seconds,
				oc.agent,
				oc.lead,
				oc.recording_ref,
			),
			(
				"Fake",
				"Inbound",
				"Answered",
				CUSTOMER_E164,
				"+842873001234",
				125,
				"Administrator",
				lead,
				"rec-1",
			),
		)
		self.assertTrue(oc.crm_call_log)
		log = frappe.get_doc("CRM Call Log", oc.crm_call_log)
		self.assertEqual(
			(
				log.type,
				log.status,
				log.receiver,
				log.caller,
				log.duration,
				log.reference_doctype,
				log.reference_docname,
			),
			("Incoming", "Completed", "Administrator", None, 125, "CRM Lead", lead),
		)
		self.assertEqual(getattr(log, "from"), CUSTOMER_E164)
		self.assertEqual(
			log.telephony_medium, "Manual"
		)  # mặc định của CRM; Stringee/Fake không có trong Select
		self.assertEqual(log.medium, "Fake")
		self.assertFalse(log.recording_url)  # ghi âm là tệp private ở Đợt 3
		# hiện trong tab Calls qua API hoạt động của chính CRM, đúng một lần
		shown = self.activities(lead)
		self.assertEqual([a["name"] for a in shown], [log.name])
		self.assertEqual(shown[0]["activity_type"], "incoming_call")

	def test_missed_call_has_no_agent_and_no_answer_status(self):
		self.post(call("w2", "Missed", answered_at=None, duration=0, agent_ref=None))
		self.run_worker()
		oc = frappe.get_doc(CALL, {"call_id": "td201-w2"})
		self.assertEqual((oc.status, oc.agent), ("Missed", None))
		log = frappe.get_doc("CRM Call Log", oc.crm_call_log)
		self.assertEqual((log.status, log.receiver), ("No Answer", None))

	def test_outbound_call_uses_to_number_and_caller(self):
		self.post(call("w3", direction="Outbound", **{"from": HOTLINE, "to": "0903201002"}))
		self.run_worker("+84903201002")
		oc = frappe.get_doc(CALL, {"call_id": "td201-w3"})
		self.assertEqual(oc.direction, "Outbound")
		log = frappe.get_doc("CRM Call Log", oc.crm_call_log)
		self.assertEqual((log.type, log.caller, log.receiver), ("Outgoing", "Administrator", None))
		self.assertEqual(frappe.db.get_value("CRM Lead", oc.lead, "omni_phone_e164"), "+84903201002")

	def test_same_customer_two_calls_one_lead_two_logs(self):
		self.post(call("w4"))
		self.post(call("w5", started_at="2026-10-30 10:00:00", ended_at="2026-10-30 10:01:00"))
		self.run_worker()
		leads = {frappe.db.get_value(CALL, {"call_id": f"td201-{i}"}, "lead") for i in ("w4", "w5")}
		self.assertEqual(len(leads), 1)
		self.assertEqual(len(self.activities(leads.pop())), 2)

	def test_unmapped_agent_leaves_agent_empty(self):
		self.post(call("w6", agent_ref="Z999"))
		self.run_worker()
		self.assertIsNone(frappe.db.get_value(CALL, {"call_id": "td201-w6"}, "agent"))

	def test_agent_matched_by_extension_case_insensitive(self):
		self.assertEqual(calls.map_agent(self.channel, "101"), "Administrator")
		self.assertEqual(calls.map_agent(self.channel, " a100 "), "Administrator")
		self.assertIsNone(calls.map_agent(self.channel, None))

	def test_reprocessing_is_idempotent(self):
		self.post(call("w7"))
		self.run_worker()
		(row,) = self.inbound()
		frappe.db.set_value(INBOUND, row.name, "status", "New")
		frappe.db.commit()
		self.run_worker()
		self.assertEqual(frappe.db.count(CALL, {"call_id": "td201-w7"}), 1)
		self.assertEqual(frappe.db.count("CRM Call Log", {"id": ["like", "%td201-w7"]}), 1)

	def test_converted_lead_references_deal(self):
		self.post(call("w8"))
		self.run_worker()
		lead = frappe.db.get_value(CALL, {"call_id": "td201-w8"}, "lead")
		frappe.get_doc({"doctype": "CRM Deal", "lead": lead, "organization": None, "first_name": "x"}).insert(
			ignore_permissions=True, ignore_mandatory=True
		)
		frappe.db.set_value("CRM Lead", lead, "converted", 1)
		frappe.db.commit()
		deal = frappe.db.get_value("CRM Deal", {"lead": lead}, "name")
		self.post(call("w9", started_at="2026-10-30 11:00:00", ended_at="2026-10-30 11:01:00"))
		self.run_worker()
		log = frappe.db.get_value(CALL, {"call_id": "td201-w9"}, "crm_call_log")
		self.assertEqual(
			frappe.db.get_value("CRM Call Log", log, ["reference_doctype", "reference_docname"]),
			("CRM Deal", deal),
		)
		self.assertIn(log, [a["name"] for a in self.activities(deal)])

	def test_status_and_log_id_mapping(self):
		self.assertEqual(call_log.STATUS_MAP["Voicemail"], "No Answer")
		self.assertEqual(call_log.call_log_id("Fake", "abc"), "fake-abc")
		self.assertLessEqual(len(call_log.call_log_id("Fake", "x" * 500)), 140)

	def test_payload_cleaned_before_processing_goes_manual(self):
		self.post(call("w10"))
		(row,) = self.inbound()
		frappe.db.set_value(INBOUND, row.name, "raw_payload", None)
		frappe.db.commit()
		self.run_worker()
		(row,) = self.inbound()
		self.assertNotEqual(row.status, "Done")
		self.assertEqual(frappe.db.count(CALL, {"call_id": "td201-w10"}), 0)


class TestLookup(TelephonyTestCase):
	def make_lead(self):
		self.post(call("l0"))
		self.run_worker()
		lead = frappe.db.get_value(CALL, {"call_id": "td201-l0"}, "lead")
		frappe.db.set_value("CRM Lead", lead, "lead_owner", "Administrator")
		frappe.db.commit()
		return lead

	def snapshot_writes(self):
		return (
			frappe.db.count("Error Log"),
			frappe.db.count(CALL),
			frappe.db.count(INBOUND),
			frappe.db.count("CRM Lead"),
		)

	def test_number_with_lead(self):
		lead = self.make_lead()
		before = self.snapshot_writes()
		frappe.db.rollback()
		status, body, elapsed = self.lookup()
		self.assertEqual(status, 200)
		self.assertTrue(body["found"])
		self.assertEqual((body["lead"], body["owner"]), (lead, "Administrator"))
		self.assertTrue(body["customer_name"])
		self.assertIn(f"/crm/leads/{lead}", body["lead_url"])
		self.assertEqual(frappe.db.transaction_writes, 0, "tra cứu không được ghi DB")
		self.assertEqual(self.snapshot_writes(), before)
		self.assertLess(elapsed, 0.3)

	def test_lookup_also_by_post_with_number_formats(self):
		self.make_lead()
		for number in ("0903 201 001", "+84903201001", "84903201001"):
			status, body, _t = self.lookup({"from": number}, method="POST")
			self.assertEqual(status, 200)
			self.assertTrue(body["found"], number)

	def test_new_customer(self):
		status, body, _t = self.lookup({"from": "0903201003"})
		self.assertEqual((status, body["found"]), (200, False))
		self.assertEqual(frappe.db.count("CRM Lead", {"omni_phone_e164": "+84903201003"}), 0)  # không ghi

	def test_malformed_numbers_return_valid_empty(self):
		for number in ("abc", "", "12", "0903xx1001"):
			status, body, _t = self.lookup({"from": number})
			self.assertEqual((status, body["found"]), (200, False), number)

	def test_wrong_token_does_not_leak_customer(self):
		self.make_lead()
		for args in ({"t": "sai"}, {"t": ""}, {"ch": "khong co"}):
			status, body, _t = self.lookup(args)
			self.assertEqual(status, 200)
			self.assertNotIn("lead", body)
			self.assertNotIn("owner", body)
			self.assertNotIn("customer_name", body if not body.get("found") else {"x": 1})

	def test_disabled_channel_returns_empty_valid(self):
		self.make_lead()
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		status, body, _t = self.lookup()
		self.assertEqual(status, 200)
		self.assertNotIn("lead", body)

	def test_db_error_returns_valid_response(self):
		self.make_lead()
		boom = RuntimeError("1969 Query execution was interrupted (max_statement_time exceeded)")
		with mock.patch.object(lookup, "_lead_of", side_effect=boom):
			status, body, elapsed = self.lookup()
		self.assertEqual((status, body["found"]), (200, False))
		self.assertLess(elapsed, 0.3)

	def test_slow_step_hits_budget_and_returns_in_time(self):
		self.make_lead()
		real = lookup._lead_of

		def slow(phone, deadline):
			time.sleep(0.35)  # bước chậm; bước sau phải thấy hết hạn và bỏ
			return real(phone, deadline)

		with mock.patch.object(lookup, "_lead_of", side_effect=slow):
			status, body, elapsed = self.lookup()
		self.assertEqual((status, body["found"]), (200, False))
		self.assertLess(elapsed, 0.6)

	def test_database_enforces_statement_time_limit(self):
		started = time.monotonic()
		with lookup.statement_limit(0.1):
			try:
				frappe.db.sql("select sleep(2)")
			except Exception:
				pass
		elapsed = time.monotonic() - started
		self.assertLess(elapsed, 1.0, "MariaDB phải hủy truy vấn chậm theo max_statement_time")
		self.assertEqual(frappe.db.sql("select @@session.max_statement_time")[0][0], 0)  # đã khôi phục

	def test_provider_error_in_response_building_is_empty(self):
		with mock.patch.object(
			FakeProvider, "lookup_response", side_effect=[ValueError("x"), {"found": False}]
		):
			status, body, _t = self.lookup()
		self.assertEqual((status, body), (200, {"found": False}))

	def test_string_response_is_sent_raw(self):
		with mock.patch.object(FakeProvider, "lookup_response", return_value="khach moi"):
			status, _body, _t = self.lookup()
		self.assertEqual(status, 200)
		self.assertEqual(frappe.local.response["filecontent"], b"khach moi")

	def test_lookup_result_dataclass_defaults(self):
		r = LookupResult()
		self.assertTrue(r.is_new)
		self.assertIsNone(r.lead)


class TestInboxPermissionsMonitor(TelephonyTestCase):
	def test_thread_shows_call_rows(self):
		self.post(call("i1", recording_ref="r"))
		self.run_worker()
		lead = frappe.db.get_value(CALL, {"call_id": "td201-i1"}, "lead")
		data = inbox.get_thread(lead)
		rows = [m for m in data["messages"] if m["doctype"] == CALL]
		self.assertEqual(len(rows), 1)
		m = rows[0]
		self.assertEqual(m["channel_type"], "Telephony")
		self.assertEqual(m["direction"], "in")
		self.assertIn("2 phút 5 giây", m["text"])
		self.assertIn("Administrator", m["text"])
		self.assertNotIn("recording", json.dumps(m, default=str))

	def test_call_not_in_conversation_turns(self):
		from omni_crm.crm_bridge import timeline

		self.post(call("i2"))
		self.run_worker()
		lead = frappe.db.get_value(CALL, {"call_id": "td201-i2"}, "lead")
		self.assertEqual(timeline.conversation_turns(lead), [])  # cuộc gọi không phải lượt hội thoại cho bot
		self.assertEqual(
			frappe.db.count("Communication", {"reference_name": lead, "communication_medium": "Chat"}), 0
		)

	def test_permission_hooks_registered(self):
		self.assertIn("Omni Call", frappe.get_hooks("permission_query_conditions"))
		self.assertIn("Omni Call", frappe.get_hooks("has_permission"))
		self.assertEqual(
			frappe.get_hooks("permission_query_conditions")["Omni Call"],
			["omni_crm.permissions.call_conditions"],
		)

	def test_sales_user_reads_only_visible_lead_calls(self):
		from omni_crm import permissions

		self.post(call("i3"))
		self.run_worker()
		doc = frappe.get_doc(CALL, {"call_id": "td201-i3"})
		self.assertEqual(permissions.call_conditions("Administrator"), "")
		with mock.patch.object(permissions, "_lead_condition", return_value="1=0"):
			self.assertIn("`tabOmni Call`.`lead`", permissions.call_conditions("someone@example.com"))
		with mock.patch.object(frappe, "has_permission", return_value=False):
			self.assertFalse(permissions.has_lead_scoped_permission(doc, "read", "someone@example.com"))

	def test_call_doctype_permissions_and_unique(self):
		meta = frappe.get_meta(CALL)
		roles = {(p.role, p.permlevel): (p.read, p.write) for p in meta.permissions}
		self.assertEqual(roles[("Omni Admin", 0)], (1, 1))
		self.assertEqual(roles[("Sales User", 0)], (1, 0))
		self.assertTrue(meta.get_field("call_id").unique)
		self.assertEqual(meta.get_field("raw_events").permlevel, 1)
		with self.assertRaises(frappe.UniqueValidationError):
			for _i in range(2):
				frappe.get_doc({"doctype": CALL, "call_id": "td201-uniq"}).insert(ignore_permissions=True)

	def test_agent_map_is_child_table_and_settings_fields(self):
		self.assertTrue(frappe.get_meta("Omni Agent Map").istable)
		self.assertEqual(frappe.get_meta("Omni Channel").get_field("agent_map").options, "Omni Agent Map")
		s = frappe.get_meta("Omni Settings")
		self.assertEqual(s.get_field("missed_call_callback_minutes").default, "30")
		self.assertEqual(s.get_field("callback_fallback_user").options, "User")

	def test_monitor_snapshot_telephony_block(self):
		self.post(call("m1"))
		self.post(call("m2", "Missed", answered_at=None, duration=0, agent_ref=None))
		self.run_worker()
		base = build_snapshot()["telephony"]
		self.assertGreaterEqual(base["calls_today"], 2)
		self.assertGreaterEqual(base["missed_today"], 1)
		self.assertGreaterEqual(base["answered_today"], 1)
		self.assertTrue(any(c["name"] == self.channel.name for c in base["channels"]))
		self.assertNotIn(TOKEN, json.dumps(build_snapshot(), default=str))

	def test_cleanup_leaves_nothing(self):
		self.post(call("c1"))
		self.run_worker()
		cleanup()
		self.assertEqual(frappe.db.count(CALL, {"call_id": ["like", "td201-%"]}), 0)
		self.assertEqual(frappe.db.count("CRM Lead", {"omni_phone_e164": ["in", list(NUMBERS)]}), 0)
