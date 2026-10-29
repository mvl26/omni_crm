"""D2-02: StringeeProvider (JWT, parse, tra cứu, ký webhook, Call log, ghi âm, xác minh). Mọi HTTP đều mock."""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

import frappe
import jwt
import requests

from omni_crm.api import webhooks
from omni_crm.channels.telephony import stringee, worker_hooks
from omni_crm.channels.telephony.base import LookupResult
from omni_crm.channels.telephony.stringee import StringeeError, StringeeProvider
from omni_crm.tests.telephony_helpers import (
	CUSTOMER_E164,
	PREFIX,
	TOKEN,
	TelephonyTestCase,
	fake_request,
	make_channel,
)

FIX = Path(__file__).parent / "fixtures" / "stringee"
SID, SECRET = "SKfakesid", "fake-secret-" + "s" * 30
CALL = "Omni Call"


def load(name: str) -> dict:
	return json.loads((FIX / f"{name}.json").read_text())


def body(name: str) -> bytes:
	return (FIX / f"{name}.json").read_bytes()


def sign(raw: bytes, secret: str = SECRET) -> str:
	return base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha1).digest()).decode()


class FakeResp:
	def __init__(self, status=200, payload=None, chunks=(), headers=None):
		self.status_code = status
		self._payload = payload
		self._chunks = chunks
		self.headers = headers or {}
		self.closed = False

	def json(self):
		if self._payload is None:
			raise ValueError("no json")
		return self._payload

	def iter_content(self, n):
		yield from self._chunks

	def close(self):
		self.closed = True


class StringeeCase(TelephonyTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
		frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
		self.channel = make_channel(
			f"{PREFIX} stringee",
			telephony_provider="Stringee",
			telephony_api_key_sid=SID,
			telephony_api_key_secret=SECRET,
			agent_map=[{"agent_ref": "agent101", "user": "Administrator", "extension": "101"}],
		)
		frappe.db.commit()
		self.p = StringeeProvider()
		stringee._jwt_cache.clear()
		self.addCleanup(self._cleanup_calls)

	def _cleanup_calls(self):
		frappe.db.rollback()
		logs = frappe.get_all("CRM Call Log", filters={"id": ["like", "%FAKE%"]}, pluck="name")
		frappe.db.delete("Dynamic Link", {"parenttype": "CRM Call Log", "parent": ["in", logs or [""]]})
		frappe.db.delete("CRM Call Log", {"name": ["in", logs or [""]]})
		frappe.db.delete("Omni Call", {"call_id": ["like", "call-vn-1-%"]})
		frappe.db.commit()


class TestJwt(StringeeCase):
	def test_structure_and_signature(self):
		token = self.p._jwt(self.channel)
		self.assertEqual(
			jwt.get_unverified_header(token), {"typ": "JWT", "alg": "HS256", "cty": "stringee-api;v=1"}
		)
		payload = jwt.decode(token, SECRET, algorithms=["HS256"])  # chữ ký đúng
		self.assertEqual((payload["iss"], payload["rest_api"]), (SID, True))
		self.assertTrue(payload["jti"].startswith(SID + "-"))
		self.assertAlmostEqual(payload["exp"], time.time() + 3600, delta=5)
		with self.assertRaises(jwt.InvalidSignatureError):
			jwt.decode(token, "khac", algorithms=["HS256"])

	def test_cached_and_refreshed_near_expiry(self):
		first = self.p._jwt(self.channel)
		self.assertEqual(self.p._jwt(self.channel), first)
		with mock.patch.object(stringee.time, "time", return_value=time.time() + 3600 - 30):
			second = self.p._jwt(self.channel)
		self.assertNotEqual(first, second)

	def test_missing_credentials(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "telephony_api_key_sid", "")
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		with self.assertRaises(StringeeError):
			self.p._jwt(frappe.get_doc("Omni Channel", self.channel.name))


class TestParse(StringeeCase):
	def parse(self, name):
		return self.p.parse_event(body(name), {}, self.channel)

	def test_ringing_and_answered_are_intermediate(self):
		(r,) = self.parse("event_ringing")
		self.assertEqual((r.status, r.direction, r.is_final), ("Ringing", "Inbound", False))
		self.assertEqual((r.from_number, r.agent_ref), (CUSTOMER_E164, "agent101"))
		(a,) = self.parse("event_answered")
		self.assertEqual((a.status, a.is_final), ("Answered", False))
		self.assertIsNotNone(a.answered_at)

	def test_ended_answered(self):
		(e,) = self.parse("event_ended_answered")
		self.assertTrue(e.is_final)
		self.assertEqual((e.final_status, e.duration_seconds), ("Answered", 125))
		self.assertEqual((e.ended_at - e.answered_at).total_seconds(), 125)
		self.assertEqual((e.ended_at - e.started_at).total_seconds(), 130)

	def test_ended_missed_busy(self):
		(m,) = self.parse("event_ended_missed")
		self.assertEqual((m.final_status, m.duration_seconds), ("Missed", 0))
		(b,) = self.parse("event_ended_busy")
		self.assertEqual(b.final_status, "Busy")

	def test_outbound(self):
		(o,) = self.parse("event_ended_outbound")
		self.assertEqual(o.direction, "Outbound")
		self.assertEqual((o.agent_ref, o.customer_number), ("agent101", "+84903201002"))

	def test_recording_event(self):
		(r,) = self.parse("event_recording")
		self.assertFalse(r.is_final)
		self.assertTrue(r.recording_ref.endswith("rec-fake-0001"))

	def test_bad_payloads_raise(self):
		for raw in (b"not json", b'{"call_status":"ended"}', b'{"call_id":"x","call_status":"weird"}'):
			with self.assertRaises((ValueError, KeyError)):
				self.p.parse_event(raw, {}, self.channel)

	def test_status_mapping_table(self):
		table = {
			("USER_END_CALL", False): "Missed",
			("USER_END_CALL", True): "Answered",
			("CAN_NOT_MAKE_CALL", False): "Failed",
			("486 Busy Here", False): "Busy",
			("486", False): "Busy",
			("480 Temporarily Unavailable", False): "Missed",
			("MAX_CONNECT_TIME", True): "Answered",
			("MAX_CONNECT_TIME", False): "Missed",
			(None, False): "Missed",
			("la hoac moi", False): "Missed",
		}
		for (cause, answered), expected in table.items():
			self.assertEqual(stringee.cause_status(cause, answered), expected, (cause, answered))
		self.assertEqual(set(stringee.STATUS_MAP.values()), {"Ringing", "Answered"})


class TestLookup(StringeeCase):
	def test_request_and_response_format(self):
		self.assertEqual(
			self.p.parse_lookup_request({"from": "0903201001", "to": "x", "project": "1"}, b""),
			("0903201001", "x"),
		)
		self.assertEqual(self.p.lookup_response(LookupResult(), self.channel), {"found": False})
		known = LookupResult(False, "An", "CRM-LEAD-1", "a@x.vn", "Sale A", "https://x/crm/leads/1")
		out = self.p.lookup_response(known, self.channel)
		self.assertEqual((out["found"], out["lead"], out["owner_name"]), (True, "CRM-LEAD-1", "Sale A"))
		self.assertNotIn("owner", out)  # không lộ email

	def test_wrong_token_same_as_new_customer(self):
		_s, new_body, _t = self.lookup({"from": "0903201003"})
		for args in ({"t": "sai"}, {"t": ""}):
			status, wrong, _t = self.lookup({**args, "from": "0903201001"})
			self.assertEqual((status, wrong), (200, new_body))
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		self.assertEqual(self.lookup({"from": "0903201003"})[1], new_body)

	def test_provider_keys_cannot_override_frappe_response(self):
		evil = {
			"type": "redirect",
			"location": "https://evil.example",
			"message": "x",
			"http_status_code": 500,
		}
		with mock.patch.object(StringeeProvider, "lookup_response", return_value=evil):
			status, body_, _t = self.lookup()
		self.assertEqual((status, body_), (200, evil))
		self.assertEqual(frappe.local.response["type"], "download")
		self.assertNotIn("location", frappe.local.response)


class TestSignature(StringeeCase):
	def verify(self, raw, headers):
		return self.p.verify_request(raw, headers, {}, self.channel)

	def test_signature_rules(self):
		raw = body("event_ended_missed")
		self.assertTrue(self.verify(raw, {"X-Stringee-Signature": sign(raw)}))
		self.assertTrue(self.verify(raw, {"x-stringee-signature": sign(raw)}))
		self.assertFalse(self.verify(raw, {"X-Stringee-Signature": sign(raw, "khac")}))
		self.assertFalse(self.verify(raw + b" ", {"X-Stringee-Signature": sign(raw)}))
		self.assertTrue(self.verify(raw, {}))  # không có chữ ký: dựa vào token t (đã kiểm ở adapter)

	def webhook(self, raw, headers):
		frappe.local.request = fake_request(raw, self.args(), headers)
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = frappe._dict()
		with mock.patch("frappe.enqueue"):
			webhooks.telephony_event()
			frappe.db.after_commit.run()
		frappe.db.commit()
		return frappe.local.response.get("http_status_code", 200)

	def test_webhook_bad_signature_401_good_200_and_flow(self):
		raw = body("event_ended_missed")
		self.assertEqual(self.webhook(raw, {"X-Stringee-Signature": "xxxx"}), 401)
		self.assertEqual(frappe.db.count(CALL, {"call_id": "call-vn-1-FAKE0002"}), 0)
		self.assertEqual(self.webhook(raw, {"X-Stringee-Signature": sign(raw)}), 200)
		self.assertEqual(self.webhook(raw, {"X-Stringee-Signature": sign(raw)}), 200)  # gửi trùng
		self.assertEqual(len(self.inbound()), 1)
		self.run_worker()
		row = frappe.db.get_value(
			CALL, {"call_id": "call-vn-1-FAKE0002"}, ["status", "direction", "agent"], as_dict=True
		)
		self.assertEqual((row.status, row.direction), ("Missed", "Inbound"))
		self.assertEqual(row.agent, "Administrator")  # agent101 -> Omni Agent Map

	def test_token_still_required(self):
		raw = body("event_ended_missed")
		frappe.local.request = fake_request(
			raw, {"ch": self.channel.name, "t": "sai"}, {"X-Stringee-Signature": sign(raw)}
		)
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = frappe._dict()
		webhooks.telephony_event()
		self.assertEqual(frappe.local.response.get("http_status_code"), 401)

	def test_recording_event_before_end_does_not_break(self):
		raw = body("event_recording")
		self.assertEqual(self.webhook(raw, {"X-Stringee-Signature": sign(raw)}), 200)
		self.assertEqual(len(self.inbound()), 0)
		raw = body("event_ended_answered")
		self.assertEqual(self.webhook(raw, {}), 200)
		self.run_worker()
		row = frappe.db.get_value(
			CALL, {"call_id": "call-vn-1-FAKE0001"}, ["status", "from_number"], as_dict=True
		)
		self.assertEqual((row.status, row.from_number), ("Answered", CUSTOMER_E164))

	def test_intermediate_then_final_one_call(self):
		for n in ("event_ringing", "event_answered", "event_ended_answered"):
			raw = body(n)
			self.assertEqual(self.webhook(raw, {"X-Stringee-Signature": sign(raw)}), 200)
		self.assertEqual(len(self.inbound()), 1)
		self.run_worker()
		self.assertEqual(frappe.db.count(CALL, {"call_id": "call-vn-1-FAKE0001"}), 1)
		self.assertEqual(frappe.db.get_value(CALL, {"call_id": "call-vn-1-FAKE0001"}, "status"), "Answered")


class TestCallLog(StringeeCase):
	def test_pagination_to_end_and_request_shape(self):
		pages = [load("call_log_page1"), load("call_log_page2"), load("call_log_page3")]
		seen = []

		def fake_get(url, params=None, headers=None, timeout=None, stream=False, allow_redirects=True):
			seen.append((url, dict(params), headers, timeout, allow_redirects))
			return FakeResp(200, pages[len(seen) - 1])

		with (
			mock.patch.object(stringee.requests, "get", side_effect=fake_get),
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			calls = self.p.list_calls(datetime(2026, 10, 29), datetime(2026, 10, 30), self.channel)
		self.assertEqual(
			[c.call_id for c in calls], ["call-vn-1-LOG0001", "call-vn-1-LOG0002", "call-vn-1-LOG0003"]
		)
		self.assertEqual(len(seen), 3)
		self.assertNotIn("search_after[]", seen[0][1])
		self.assertEqual(seen[1][1]["search_after[]"], ["1790000002000", "cursor-fake-1"])
		self.assertEqual(
			(seen[0][0], seen[0][3], seen[0][4]), ("https://api.stringee.com/v1/call/log", 10, False)
		)
		self.assertTrue(jwt.decode(seen[0][2]["X-STRINGEE-AUTH"], SECRET, algorithms=["HS256"]))
		self.assertEqual((calls[0].final_status, calls[1].final_status), ("Answered", "Missed"))
		self.assertEqual(calls[2].recording_ref, "rec-fake-0003")

	def test_ongoing_calls_skipped_and_errors(self):
		page = load("call_log_page3")
		page["data"]["calls"] = [{"id": "x", "stop_time": 0, "start_time": 1}]
		with (
			mock.patch.object(stringee.requests, "get", return_value=FakeResp(200, page)),
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			self.assertEqual(self.p.list_calls(datetime(2026, 1, 1), datetime(2026, 1, 2), self.channel), [])
		with (
			mock.patch.object(stringee.requests, "get", return_value=FakeResp(401)),
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			with self.assertRaises(StringeeError):
				self.p.list_calls(datetime(2026, 1, 1), datetime(2026, 1, 2), self.channel)

	def test_retry_on_network_error_then_give_up_without_secret_in_message(self):
		with (
			mock.patch.object(stringee.requests, "get", side_effect=requests.ConnectionError(SECRET)) as g,
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
			mock.patch.object(stringee.time, "sleep"),
		):
			with self.assertRaises(StringeeError) as cm:
				self.p.list_calls(datetime(2026, 1, 1), datetime(2026, 1, 2), self.channel)
		self.assertEqual(g.call_count, 1 + stringee.GET_RETRIES)
		self.assertNotIn(SECRET, str(cm.exception))

	def test_stuck_cursor_stops_without_looping(self):
		page = load("call_log_page1")  # trang trả lại y nguyên: cursor không tiến
		with (
			mock.patch.object(stringee.requests, "get", return_value=FakeResp(200, page)) as g,
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			calls = self.p.list_calls(datetime(2026, 1, 1), datetime(2026, 1, 2), self.channel)
		self.assertEqual(g.call_count, 2)
		self.assertEqual(len(calls), 2)

	def test_max_pages_raises_instead_of_truncating(self):
		counter = iter(range(10**6))

		def page(*a, **k):
			n = next(counter)
			p = load("call_log_page1")
			p["data"]["calls"] = [dict(c, id=f"call-vn-1-N{n}-{i}") for i, c in enumerate(p["data"]["calls"])]
			p["data"]["search_after"] = [n, "c"]
			return FakeResp(200, p)

		with (
			mock.patch.object(stringee, "MAX_PAGES", 3),
			mock.patch.object(stringee.requests, "get", side_effect=page),
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			with self.assertRaises(StringeeError):
				self.p.list_calls(datetime(2026, 1, 1), datetime(2026, 1, 2), self.channel)

	def test_sandbox_blocks_http(self):
		with (
			mock.patch.object(stringee.requests, "get") as g,
			mock.patch.object(stringee.flags, "sandbox", return_value=True),
		):
			with self.assertRaises(StringeeError):
				self.p.list_calls(datetime(2026, 1, 1), datetime(2026, 1, 2), self.channel)
		g.assert_not_called()


class TestRecording(StringeeCase):
	def fetch(self, resp, ref="rec-fake-0001"):
		with (
			mock.patch.object(stringee.requests, "get", return_value=resp) as g,
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			try:
				return self.p.fetch_recording(ref, self.channel), g
			finally:
				self.get_mock = g

	def test_ok_with_id_and_url(self):
		(data, mime), g = self.fetch(
			FakeResp(200, chunks=[b"ab", b"cd"], headers={"Content-Type": "audio/mpeg"})
		)
		self.assertEqual((data, mime), (b"abcd", "audio/mpeg"))
		self.assertEqual(g.call_args[0][0], "https://api.stringee.com/v1/call/recording/rec-fake-0001")
		(data, _m), g = self.fetch(
			FakeResp(200, chunks=[b"x"]), "https://api.stringee.com/v1/call/recording/abc"
		)
		self.assertEqual(data, b"x")

	def test_too_large_rejected(self):
		with self.assertRaises(StringeeError):
			self.fetch(FakeResp(200, headers={"Content-Length": str(stringee.MAX_RECORDING_BYTES + 1)}))
		big = [b"x" * (1024 * 1024)] * 101
		resp = FakeResp(200, chunks=big)
		with self.assertRaises(StringeeError):
			self.fetch(resp)
		self.assertTrue(resp.closed)

	def test_foreign_host_and_bad_refs_rejected_without_http(self):
		for ref in (
			"https://evil.example/x",
			"http://api.stringee.com/x",
			"https://stringee.com.evil.io/x",
			"a b",
			"../x",
			"",
		):
			with mock.patch.object(stringee.requests, "get") as g:
				with self.assertRaises(StringeeError):
					self.p.fetch_recording(ref, self.channel)
			g.assert_not_called()

	def test_total_deadline(self):
		resp = FakeResp(200, chunks=[b"x", b"y"])
		with mock.patch.object(stringee, "RECORDING_TOTAL_SECONDS", -1):
			with self.assertRaises(StringeeError):
				self.fetch(resp)

	def test_non_audio_and_empty_rejected(self):
		with self.assertRaises(StringeeError):
			self.fetch(FakeResp(200, chunks=[b"<html>"], headers={"Content-Type": "text/html"}))
		with self.assertRaises(StringeeError):
			self.fetch(FakeResp(200, chunks=[]))

	def test_click_to_call_not_implemented(self):
		with self.assertRaises(NotImplementedError):
			self.p.click_to_call("agent101", "0903201001", self.channel)


class TestVerifyMissedCall(StringeeCase):
	def make_call(self):
		raw = body("event_ended_missed")
		self.assertEqual(TestSignature.webhook(self, raw, {}), 200)
		self.run_worker()
		return frappe.db.get_value(
			CALL, {"call_id": "call-vn-1-FAKE0002"}, ["name", "ended_at"], as_dict=True
		)

	def check(self, exists):
		c = self.make_call()
		with (
			mock.patch.object(StringeeProvider, "call_exists", **exists),
			mock.patch("omni_crm.flags.sandbox", return_value=False),  # D2-03: sandbox bật thì không xác minh
		):
			result = worker_hooks.verify_missed_call(c.name, self.channel)
		return result, frappe.db.get_value(CALL, c.name, "unverified")

	def test_true_leaves_flag_clear(self):
		self.assertEqual(self.check({"return_value": True}), (True, 0))

	def test_not_found_or_api_error_sets_unverified_and_counter(self):
		cache = frappe.cache()
		key = cache.make_key(worker_hooks.UNVERIFIED_COUNTER_KEY.format(channel=self.channel.name))
		cache.delete(key)
		self.assertEqual(self.check({"return_value": False}), (False, 1))
		self.assertEqual(int(cache.get(key)), 1)
		frappe.db.set_value(CALL, {"call_id": "call-vn-1-FAKE0002"}, "unverified", 0)
		self.assertEqual(self.check({"side_effect": StringeeError("x")}), (None, 1))
		self.assertEqual(int(cache.get(key)), 2)
		cache.delete(key)

	def test_call_exists_matches_by_id(self):
		pages = [load("call_log_page1"), load("call_log_page3")]
		with (
			mock.patch.object(stringee.requests, "get", side_effect=[FakeResp(200, p) for p in pages]),
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			self.assertTrue(self.p.call_exists("call-vn-1-LOG0002", datetime(2026, 10, 1), self.channel))
		with (
			mock.patch.object(stringee.requests, "get", return_value=FakeResp(200, load("call_log_page3"))),
			mock.patch.object(stringee.flags, "sandbox", return_value=False),
		):
			self.assertFalse(self.p.call_exists("nope", datetime(2026, 10, 1), self.channel))


class TestRegistry(StringeeCase):
	def test_registry_returns_stringee(self):
		from omni_crm.channels.telephony.registry import get_provider

		self.assertIsInstance(get_provider("Stringee"), StringeeProvider)
