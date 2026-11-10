import json
import threading
import time
from datetime import timedelta
from unittest import mock

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from omni_crm.api import inbox, webhooks
from omni_crm.channels.registry import (
	AdapterNotFoundError,
	get_adapter,
	register_adapter,
	unregister_adapter,
)
from omni_crm.channels.zalo import adapter as adapter_mod
from omni_crm.channels.zalo import errors, signature
from omni_crm.channels.zalo import token as token_mod
from omni_crm.channels.zalo.adapter import ZaloAdapter, parse_event
from omni_crm.channels.zalo.client import ZaloClient, split_text
from omni_crm.ingest.events import OutboundRequest
from omni_crm.tests.zalo_helpers import (
	APP_ID,
	OA_ID,
	OA_SECRET,
	OK_SEND,
	PREFIX,
	TOKEN_ERR,
	USER,
	ZaloTestCase,
	body_of,
	load,
	now_ms,
	response,
	sign,
)

REQ = "omni_crm.channels.zalo.client.requests.request"
POST = "omni_crm.channels.zalo.token.requests.post"


class TestSignature(ZaloTestCase):
	def raw(self, ts=None):
		ts = ts or now_ms()
		return body_of(load("user_send_text", timestamp=ts)), ts

	def test_valid(self):
		raw, ts = self.raw()
		self.assertTrue(signature.verify(raw, sign(raw, ts), APP_ID, OA_SECRET))

	def test_bare_hex_and_lowercase_header_name(self):
		raw, ts = self.raw()
		mac = sign(raw, ts)["X-ZEvent-Signature"][4:]
		self.assertTrue(signature.verify(raw, {"x-zevent-signature": mac}, APP_ID, OA_SECRET))

	def test_wrong_signature_missing_and_wrong_secret(self):
		raw, ts = self.raw()
		self.assertFalse(signature.verify(raw, {"X-ZEvent-Signature": "mac=" + "0" * 64}, APP_ID, OA_SECRET))
		self.assertEqual(signature.check(raw, {}, APP_ID, OA_SECRET)[1], signature.MISSING)
		self.assertFalse(signature.verify(raw, sign(raw, ts), APP_ID, "khac"))
		self.assertFalse(signature.verify(raw, sign(raw, ts), "app-khac", OA_SECRET))

	def test_tampered_body(self):
		raw, ts = self.raw()
		headers = sign(raw, ts)
		self.assertFalse(signature.verify(raw.replace("giá".encode(), b"gia"), headers, APP_ID, OA_SECRET))

	def test_old_and_future_timestamp_rejected(self):
		old = str(int((time.time() - 11 * 60) * 1000))
		raw, _ = self.raw(old)
		ok, reason = signature.check(raw, sign(raw, old), APP_ID, OA_SECRET)
		self.assertEqual((ok, reason), (False, signature.STALE))
		future = str(int((time.time() + 11 * 60) * 1000))
		raw, _ = self.raw(future)
		self.assertFalse(signature.verify(raw, sign(raw, future), APP_ID, OA_SECRET))

	def test_within_ten_minutes_ok_and_now_param(self):
		ts = str(int((time.time() - 9 * 60) * 1000))
		raw, _ = self.raw(ts)
		self.assertTrue(signature.verify(raw, sign(raw, ts), APP_ID, OA_SECRET))
		self.assertTrue(signature.verify(raw, sign(raw, ts), APP_ID, OA_SECRET, now=time.time() - 60))

	def test_body_without_timestamp(self):
		raw = b'{"event_name":"x"}'
		self.assertEqual(signature.check(raw, sign(raw, ""), APP_ID, OA_SECRET)[1], signature.BAD_BODY)


class TestErrors(ZaloTestCase):
	def test_table(self):
		self.assertEqual(errors.classify(-216)[0], errors.TOKEN)
		self.assertEqual(errors.classify(-223)[0], errors.TRANSIENT)
		self.assertEqual(errors.classify(-213)[0], errors.PERMANENT)
		self.assertEqual(errors.classify(-230)[0], errors.WINDOW)
		self.assertEqual(errors.classify(-201)[0], errors.PERMANENT)

	def test_unknown_and_http(self):
		kind, msg = errors.classify(-99999)
		self.assertEqual(kind, errors.PERMANENT)
		self.assertIn("-99999", msg)
		self.assertEqual(errors.classify(None, 503)[0], errors.TRANSIENT)
		self.assertEqual(errors.classify(None, 429)[0], errors.TRANSIENT)
		self.assertEqual(errors.classify(None)[0], errors.PERMANENT)

	def test_messages_are_vietnamese_and_exception_fields(self):
		e = errors.to_error(-216)
		self.assertEqual(e.kind, errors.TOKEN)
		self.assertIn("token", e.message_vi.lower())
		for code in (-216, -223, -213, -230, -201):
			self.assertTrue(errors.classify(code)[1])


class TestParse(ZaloTestCase):
	def parse(self, name, **kw):
		return parse_event(self.channel, load(name, **kw))

	def test_text(self):
		e = self.parse("user_send_text")
		self.assertEqual(
			(e.event_type, e.external_sender_id, e.external_message_id), ("message", USER, "msg-text-001")
		)
		self.assertEqual(e.text, "Chào shop, cho mình hỏi giá máy A")
		self.assertEqual(e.channel_type, "Zalo OA")
		self.assertEqual(e.sender_profile, {})

	def test_attachment_events(self):
		img = self.parse("user_send_image")
		self.assertEqual(img.text, "[Ảnh] ảnh máy")
		self.assertEqual(img.attachments[0]["url"], "https://example.invalid/a.jpg")
		file = self.parse("user_send_file")
		self.assertEqual(file.text, "[Tệp: bao-gia.pdf]")
		self.assertEqual(file.attachments[0]["name"], "bao-gia.pdf")
		self.assertEqual(self.parse("user_send_sticker").text, "[Sticker]")
		loc = self.parse("user_send_location")
		self.assertEqual(loc.text, "[Vị trí]")
		self.assertIn("21.0278", loc.attachments[0]["name"])
		self.assertEqual(self.parse("user_send_audio").text, "[Tin thoại]")
		for n in (
			"user_send_image",
			"user_send_file",
			"user_send_sticker",
			"user_send_location",
			"user_send_audio",
		):
			self.assertEqual(self.parse(n).event_type, "message")

	def test_user_info_is_strong_profile(self):
		e = self.parse("user_submit_info")
		self.assertEqual(e.event_type, "user_info")
		self.assertEqual(e.sender_profile["display_name"], "Nguyễn Văn An")
		self.assertEqual(e.sender_profile["phone"], "+84912345678")  # V3-07: chuẩn hóa E.164 ngay khi nhận
		self.assertNotIn("912345678", e.text)  # SĐT chỉ ở sender_profile, không để trong text
		self.assertEqual(len(e.external_message_id), 64)  # không có msg_id: mã băm ổn định

	def test_follow_unfollow_stable_ids(self):
		f1, f2 = self.parse("follow"), self.parse("follow")
		self.assertEqual((f1.event_type, f1.external_sender_id), ("follow", USER))
		self.assertEqual(f1.external_message_id, f2.external_message_id)
		u = self.parse("unfollow")
		self.assertEqual(u.event_type, "unfollow")
		self.assertNotEqual(u.external_message_id, f1.external_message_id)

	def test_oa_echo_takes_customer_from_recipient(self):
		e = self.parse("oa_send_text")
		self.assertEqual(e.event_type, "oa_echo")
		self.assertEqual(e.external_sender_id, USER)
		self.assertEqual(e.external_message_id, "msg-echo-001")
		self.assertEqual(self.parse("oa_send_image").text, "[Ảnh]")

	def test_other_event_ignored(self):
		e = self.parse("other_event")
		self.assertEqual(e.event_type, "ignored")
		self.assertEqual(e.raw["event_name"], "user_seen_message")

	def test_parse_accepts_list_and_rejects_garbage(self):
		events = self.adapter.parse(body_of([load("user_send_text"), load("follow")]), {}, self.channel)
		self.assertEqual([e.event_type for e in events], ["message", "follow"])
		with self.assertRaises(ValueError):
			self.adapter.parse(b"khong phai json", {}, self.channel)


class TestVerifyRequest(ZaloTestCase):
	def test_signed_ok_and_bad(self):
		ts = now_ms()
		raw = body_of(load("user_send_text", timestamp=ts))
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		self.assertTrue(self.adapter.verify_request(raw, sign(raw, ts), doc))
		self.assertFalse(self.adapter.verify_request(raw, sign(raw, ts, secret="sai"), doc))
		self.assertFalse(self.adapter.verify_request(raw, {}, doc))

	def test_skip_flag_only_on_dev_site(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "zalo_skip_signature", 1)
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		raw = body_of(load("user_send_text"))
		with mock.patch.dict(frappe.conf, {"developer_mode": 1}):
			self.assertTrue(self.adapter.verify_request(raw, {}, doc))
		with (
			mock.patch.dict(frappe.conf, {"developer_mode": 0}),
			mock.patch("omni_crm.channels.zalo.token._alert") as alert,
		):
			self.assertFalse(self.adapter.verify_request(raw, {}, doc))
			alert.assert_called_once()

	def test_rejection_is_counted(self):
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		cache = frappe.cache()
		key = cache.make_key(f"omni:zalo:reject:{doc.name}:{signature.MISSING}")
		cache.delete(key)
		self.adapter.verify_request(b'{"timestamp":"1"}', {}, doc)
		self.adapter.verify_request(b'{"timestamp":"1"}', {}, doc)
		self.assertEqual(int(cache.get(key)), 2)


class TestRegistry(ZaloTestCase):
	def test_adapter_loaded_lazily_for_webhook_and_worker(self):
		unregister_adapter("Zalo OA")
		unregister_adapter("Email")
		self.assertIsInstance(get_adapter("Zalo OA"), ZaloAdapter)
		self.assertEqual(get_adapter("Email").channel_type, "Email")
		self.assertEqual(get_adapter("Telephony").channel_type, "Telephony")  # D2-01
		with self.assertRaises(AdapterNotFoundError):
			get_adapter("Loai kenh khong co")

	def test_fake_overrides_and_lazy_comes_back(self):
		class Fake:
			channel_type = "Zalo OA"

		register_adapter("Zalo OA", Fake())
		self.assertIsInstance(get_adapter("Zalo OA"), Fake)
		unregister_adapter("Zalo OA")
		self.assertIsInstance(get_adapter("Zalo OA"), ZaloAdapter)


class TestWebhookEndToEnd(ZaloTestCase):
	def call(self, raw, headers, ch=None):
		from types import SimpleNamespace

		frappe.local.request = SimpleNamespace(
			method="POST", content_length=len(raw), get_data=lambda: raw, headers=headers
		)
		frappe.local.form_dict = frappe._dict(ch=ch or self.channel.name)
		frappe.local.response = frappe._dict()
		with mock.patch("frappe.enqueue"):
			res = webhooks.zalo()
		return frappe.local.response.get("http_status_code", 200), res

	def count(self):
		return frappe.db.count("Omni Inbound Message", {"channel": self.channel.name})

	def test_signed_event_saved_once_even_if_redelivered(self):
		ts = now_ms()
		raw = body_of(load("user_send_text", timestamp=ts))
		self.assertEqual(self.call(raw, sign(raw, ts))[0], 200)
		self.assertEqual(self.count(), 1)
		self.assertEqual(self.call(raw, sign(raw, ts))[0], 200)  # Zalo gửi lại
		self.assertEqual(self.count(), 1)
		msg = frappe.get_doc("Omni Inbound Message", {"channel": self.channel.name})
		self.assertEqual(
			(msg.event_type, msg.status, msg.text), ("message", "New", "Chào shop, cho mình hỏi giá máy A")
		)

	def test_bad_signature_401_unknown_channel_404(self):
		ts = now_ms()
		raw = body_of(load("user_send_text", timestamp=ts))
		self.assertEqual(self.call(raw, sign(raw, ts, secret="sai"))[0], 401)
		self.assertEqual(
			self.call(
				raw,
				{},
			)[0],
			401,
		)
		self.assertEqual(self.count(), 0)
		self.assertEqual(self.call(raw, sign(raw, ts), ch="khong-co")[0], 404)

	def test_stale_timestamp_401(self):
		ts = str(int((time.time() - 3600) * 1000))
		raw = body_of(load("user_send_text", timestamp=ts))
		self.assertEqual(self.call(raw, sign(raw, ts))[0], 401)

	def test_garbage_body_saved_as_unparsed_200(self):
		raw = b"khong phai json"
		# chữ ký không tính được từ payload hỏng -> 401 (không lưu)
		self.assertEqual(self.call(raw, sign(raw, "1"))[0], 401)

	def test_unknown_event_saved_as_ignored(self):
		ts = now_ms()
		raw = body_of(load("other_event", timestamp=ts))
		self.assertEqual(self.call(raw, sign(raw, ts))[0], 200)
		msg = frappe.get_doc("Omni Inbound Message", {"channel": self.channel.name})
		self.assertEqual(msg.status, "Ignored")


class TestWindow(ZaloTestCase):
	def ident(self, hours):
		return frappe._dict(
			last_inbound_at=add_to_date(now_datetime(), hours=-hours) if hours is not None else None
		)

	def setUp(self):
		super().setUp()
		self.ch = frappe.get_doc("Omni Channel", self.channel.name)

	def test_three_windows_for_sale(self):
		self.assertEqual(self.adapter.can_send_now(self.ident(1), self.ch), (True, ""))
		ok, why = self.adapter.can_send_now(self.ident(60), self.ch)
		self.assertTrue(ok)
		self.assertIn("tính phí", why)
		ok, why = self.adapter.can_send_now(self.ident(24 * 8), self.ch)
		self.assertFalse(ok)
		self.assertIn("7 ngày", why)
		self.assertEqual(self.adapter.send_window(self.ident(60), self.ch), "paid")

	def test_bot_blocked_in_paid_window_but_free_ok(self):
		self.assertEqual(self.adapter.can_send_now(self.ident(1), self.ch, source="Bot"), (True, ""))
		ok, why = self.adapter.can_send_now(self.ident(60), self.ch, source="Bot")
		self.assertFalse(ok)
		self.assertIn("bot", why)

	def test_no_inbound_and_outbound_disabled(self):
		ok, why = self.adapter.can_send_now(self.ident(None), self.ch)
		self.assertFalse(ok)
		self.assertIn("chưa từng nhắn", why)
		self.ch.outbound_enabled = 0
		self.assertFalse(self.adapter.can_send_now(self.ident(1), self.ch)[0])

	def test_thresholds_from_channel_config(self):
		self.ch.zalo_cs_free_hours = 100
		self.assertEqual(self.adapter.can_send_now(self.ident(60), self.ch), (True, ""))


class TestClientAndSend(ZaloTestCase):
	def test_split_text_keeps_order_and_limit(self):
		text = " ".join(f"tu{i}" for i in range(1000))
		parts = split_text(text, 200)
		self.assertTrue(all(len(p) <= 200 for p in parts))
		self.assertEqual(" ".join(parts), text)
		self.assertEqual(split_text("ngắn"), ["ngắn"])
		self.assertEqual(split_text("   "), [])
		hard = split_text("x" * 450, 200)
		self.assertEqual([len(p) for p in hard], [200, 200, 50])

	def test_sandbox_makes_no_http(self):
		with (
			mock.patch("omni_crm.channels.zalo.phone_request.enabled", return_value=True),
			mock.patch("omni_crm.flags.sandbox", return_value=True),
			mock.patch(REQ) as http,
			mock.patch("omni_crm.channels.zalo.client.requests.get") as g,
		):
			ids = ZaloClient(self.channel.name).send_text(USER, "xin chào")
			rid = ZaloClient(self.channel.name).send_request_user_info(USER, "t", "s")
			req = OutboundRequest(self.channel.name, "x", "hi", None, "Sale")
			frappe.db.set_value("Omni Identity", {"identity_key": "x"}, "external_id", USER)
		http.assert_not_called()
		g.assert_not_called()
		self.assertTrue(ids[0].startswith("sandbox:"))
		self.assertTrue(rid.startswith("sandbox:"))
		self.assertEqual(req.source, "Sale")

	def test_send_text_posts_with_token_header_and_json(self):
		self._not_sandbox()
		with mock.patch(REQ, return_value=response(OK_SEND)) as http:
			ids = ZaloClient(self.channel.name).send_text(USER, "xin chào")
		self.assertEqual(ids, ["zl-sent-1"])
		args, kwargs = http.call_args
		self.assertEqual(args[0], "POST")
		self.assertTrue(args[1].endswith("/v3.0/oa/message/cs"))
		self.assertEqual(kwargs["headers"]["access_token"], "access-OLD-td108")
		self.assertEqual(kwargs["timeout"], 10)
		self.assertEqual(
			json.loads(kwargs["data"]), {"recipient": {"user_id": USER}, "message": {"text": "xin chào"}}
		)

	def test_long_text_split_into_ordered_messages(self):
		self._not_sandbox()
		text = "\n".join(f"dong {i} " + "a" * 100 for i in range(60))
		with mock.patch(REQ, return_value=response(OK_SEND)) as http:
			ZaloClient(self.channel.name).send_text(USER, text)
		sent = [json.loads(c.kwargs["data"])["message"]["text"] for c in http.call_args_list]
		self.assertGreater(len(sent), 1)
		self.assertEqual(" ".join(" ".join(sent).split()), " ".join(text.split()))

	def test_expired_token_refreshes_then_resends(self):
		self._not_sandbox()
		new = {
			"access_token": "access-NEW-td108",
			"refresh_token": "refresh-NEW-td108",
			"expires_in": "90000",
		}
		seen = []

		def fake_request(method, url, **kw):
			seen.append(kw["headers"]["access_token"])
			return response(TOKEN_ERR if kw["headers"]["access_token"] == "access-OLD-td108" else OK_SEND)

		with mock.patch(REQ, side_effect=fake_request), mock.patch(POST, return_value=response(new)) as post:
			ids = ZaloClient(self.channel.name).send_text(USER, "xin chào")
		self.assertEqual(ids, ["zl-sent-1"])
		self.assertEqual(seen, ["access-OLD-td108", "access-NEW-td108"])
		post.assert_called_once()
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		self.assertEqual(doc.get_password("zalo_refresh_token"), "refresh-NEW-td108")

	def test_token_error_after_refresh_gives_up(self):
		self._not_sandbox()
		new = {"access_token": "access-NEW-td108", "refresh_token": "r2", "expires_in": "90000"}
		with (
			mock.patch(REQ, return_value=response(TOKEN_ERR)) as http,
			mock.patch(POST, return_value=response(new)),
		):
			with self.assertRaises(errors.ZaloError) as cm:
				ZaloClient(self.channel.name).send_text(USER, "xin chào")
		self.assertEqual(cm.exception.kind, errors.TOKEN)
		self.assertEqual(http.call_count, 2)  # đúng một lần thử lại

	def test_error_kinds_surface(self):
		self._not_sandbox()
		with mock.patch(REQ, return_value=response({"error": -213, "message": "x"})):
			with self.assertRaises(errors.ZaloError) as cm:
				ZaloClient(self.channel.name).send_text(USER, "hi")
		self.assertEqual(cm.exception.kind, errors.PERMANENT)

	def test_post_not_retried_on_network_error_but_get_is(self):
		self._not_sandbox()
		with mock.patch(REQ, side_effect=requests.ConnectionError("x")) as http:
			with self.assertRaises(errors.ZaloError) as cm:
				ZaloClient(self.channel.name).send_text(USER, "hi")
		self.assertEqual(cm.exception.kind, errors.TRANSIENT)
		self.assertEqual(http.call_count, 1)
		with mock.patch(REQ, side_effect=requests.Timeout("x")) as http, mock.patch("time.sleep"):
			with self.assertRaises(errors.ZaloError):
				ZaloClient(self.channel.name).get_user_profile(USER)
		self.assertEqual(http.call_count, 3)

	def test_partial_failure_reports_sent_ids(self):
		self._not_sandbox()
		text = "\n".join("a" * 900 for _ in range(5))
		replies = [response(OK_SEND), response({"error": -223})]
		with mock.patch(REQ, side_effect=replies):
			with self.assertRaises(errors.ZaloError) as cm:
				ZaloClient(self.channel.name).send_text(USER, text)
		self.assertEqual(cm.exception.sent_ids, ["zl-sent-1"])

	def test_request_user_info_body(self):
		self._not_sandbox()
		with (
			mock.patch("omni_crm.channels.zalo.phone_request.enabled", return_value=True),
			mock.patch(REQ, return_value=response(OK_SEND)) as http,
		):
			ZaloClient(self.channel.name).send_request_user_info(
				USER, "Tiêu đề", "Phụ đề", "https://x.invalid/a.png"
			)
		body = json.loads(http.call_args.kwargs["data"])
		el = body["message"]["attachment"]["payload"]["elements"][0]
		self.assertEqual(body["message"]["attachment"]["payload"]["template_type"], "request_user_info")
		self.assertEqual(el["title"], "Tiêu đề")

	def test_get_user_profile_parses(self):
		data = {
			"error": 0,
			"data": {
				"display_name": "Trần Thị Hoa",
				"avatar": "https://a.invalid/x",
				"shared_info": {"phone": "0987654321"},
			},
		}
		with mock.patch(REQ, return_value=response(data)):
			prof = ZaloClient(self.channel.name).get_user_profile(USER)
		self.assertEqual((prof["display_name"], prof["phone"]), ("Trần Thị Hoa", "0987654321"))

	def test_adapter_send_uses_identity_external_id(self):
		self._not_sandbox()
		ident = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": f"Zalo OA:{USER}",
				"channel_type": "Zalo OA",
				"external_id": USER,
			}
		).insert(ignore_permissions=True)
		req = OutboundRequest(self.channel.name, ident.name, "chào anh", None, "Sale")
		with mock.patch(REQ, return_value=response(OK_SEND)) as http:
			mid = self.adapter.send(req, frappe.get_doc("Omni Channel", self.channel.name))
		self.assertEqual(mid, "zl-sent-1")
		self.assertEqual(json.loads(http.call_args.kwargs["data"])["recipient"]["user_id"], USER)

	def test_secrets_not_in_error_text(self):
		self._not_sandbox()
		with (
			mock.patch(REQ, return_value=response(TOKEN_ERR)),
			mock.patch(
				POST, return_value=response({"error": -118, "error_reason": "refresh-OLD-td108 het han"})
			),
		):
			with self.assertRaises(errors.ZaloError) as cm:
				ZaloClient(self.channel.name).send_text(USER, "hi")
		self.assertNotIn("refresh-OLD-td108", str(cm.exception))
		self.assertNotIn("access-OLD-td108", str(cm.exception))

	def _not_sandbox(self):
		patcher = mock.patch("omni_crm.flags.sandbox", return_value=False)
		patcher.start()
		self.addCleanup(patcher.stop)


class TestTokenRefresh(ZaloTestCase):
	NEW = {"access_token": "access-NEW-td108", "refresh_token": "refresh-NEW-td108", "expires_in": "90000"}  # noqa: RUF012

	def soon(self, hours=1):
		frappe.db.set_value(
			"Omni Channel", self.channel.name, "zalo_token_expires_at", add_to_date(None, hours=hours)
		)
		frappe.db.commit()

	def doc(self):
		frappe.db.rollback()
		return frappe.get_doc("Omni Channel", self.channel.name)

	def test_success_saves_both_tokens_and_expiry(self):
		self.soon(1)
		with mock.patch(POST, return_value=response(self.NEW)) as post:
			self.assertTrue(token_mod.refresh_channel(self.channel.name))
		doc = self.doc()
		self.assertEqual(doc.get_password("zalo_access_token"), "access-NEW-td108")
		self.assertEqual(doc.get_password("zalo_refresh_token"), "refresh-NEW-td108")
		self.assertGreater(doc.zalo_token_expires_at, add_to_date(now_datetime(), hours=24))
		kwargs = post.call_args.kwargs
		self.assertEqual(kwargs["headers"], {"secret_key": "app-secret-td108-khong-that"})
		self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
		self.assertEqual(kwargs["data"]["refresh_token"], "refresh-OLD-td108")
		self.assertEqual(kwargs["timeout"], 10)

	def test_not_needed_when_far_from_expiry(self):
		with mock.patch(POST) as post:
			self.assertFalse(token_mod.refresh_channel(self.channel.name))
		post.assert_not_called()

	def test_failure_alerts_and_keeps_old_tokens(self):
		self.soon(1)
		bad = response({"error": -118, "error_reason": "refresh token het han"})
		with mock.patch(POST, return_value=bad), mock.patch.object(token_mod, "_alert") as alert:
			with self.assertRaises(errors.ZaloError):
				token_mod.refresh_channel(self.channel.name)
		alert.assert_called_once()
		doc = self.doc()
		self.assertEqual(doc.get_password("zalo_refresh_token"), "refresh-OLD-td108")
		self.assertIn("-118", doc.last_error)

	def test_alert_sends_mail_once_per_hour_without_secrets(self):
		with (
			mock.patch("frappe.sendmail") as mail,
			mock.patch.object(frappe.db, "get_single_value", return_value="ops@example.invalid"),
		):
			token_mod._alert(self.channel.name, "loi", "chi tiet refresh-OLD-td108")
			token_mod._alert(self.channel.name, "loi", "chi tiet lan hai")
		self.assertEqual(mail.call_count, 1)
		self.assertNotIn("refresh-OLD-td108", mail.call_args.kwargs["message"])

	def test_refresh_if_needed_only_near_expiry(self):
		with mock.patch(POST, return_value=response(self.NEW)) as post:
			token_mod.refresh_if_needed()
			post.assert_not_called()
			self.soon(1)
			token_mod.refresh_if_needed()
			post.assert_called_once()

	def test_lock_held_elsewhere_no_api_call(self):
		self.soon(1)
		cache = frappe.cache()
		lock = cache.lock(cache.make_key(f"omni:zalo:refresh:{self.channel.name}"), timeout=30)
		self.assertTrue(lock.acquire())
		self.addCleanup(lambda: lock.owned() and lock.release())
		with mock.patch(POST) as post, mock.patch.object(token_mod, "LOCK_WAIT", 1):
			self.assertFalse(token_mod.refresh_channel(self.channel.name))
		post.assert_not_called()

	def test_two_threads_refresh_calls_api_once(self):
		self.soon(1)
		site, sites_path, name = frappe.local.site, frappe.local.sites_path, self.channel.name
		calls, results = [], []
		release = threading.Event()

		def slow_post(*a, **kw):
			calls.append(1)
			release.wait(5)
			return response(self.NEW)

		def worker():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			try:
				frappe.set_user("Administrator")
				results.append(token_mod.refresh_channel(name))
			finally:
				frappe.destroy()

		with mock.patch(POST, side_effect=slow_post):
			t1 = threading.Thread(target=worker)
			t1.start()
			for _ in range(100):
				if calls:
					break
				time.sleep(0.05)
			t2 = threading.Thread(target=worker)
			t2.start()
			time.sleep(0.5)  # t2 chờ khóa của t1
			release.set()
			t1.join(20)
			t2.join(20)
		self.assertEqual(len(calls), 1)
		self.assertEqual(sorted(results), [False, True])
		self.assertEqual(self.doc().get_password("zalo_refresh_token"), "refresh-NEW-td108")

	def test_forced_refresh_skipped_if_token_already_replaced(self):
		self.soon(1)
		with mock.patch(POST) as post:
			self.assertFalse(
				token_mod.refresh_channel(self.channel.name, force=True, stale_access_token="token-cu-khac")
			)
		post.assert_not_called()


class TestOAuth(ZaloTestCase):
	def as_admin(self):
		frappe.set_user("Administrator")

	def test_start_builds_redirect_with_state_and_pkce(self):
		self.as_admin()
		webhooks.zalo_oauth_start(self.channel.name)
		self.assertEqual(frappe.local.response["type"], "redirect")
		url = frappe.local.response["location"]
		self.assertTrue(url.startswith("https://oauth.zaloapp.com/v4/oa/permission?"))
		self.assertIn(f"app_id={APP_ID}", url)
		self.assertIn("code_challenge_method=S256", url)
		self.assertIn("redirect_uri=", url)
		state = frappe.db.get_value("Omni Channel", self.channel.name, "zalo_oauth_state")
		self.assertIn(f"state={state}", url)

	def test_start_requires_omni_admin(self):
		with mock.patch("frappe.get_roles", return_value=["Sales User"]):
			with self.assertRaises(frappe.PermissionError):
				webhooks.zalo_oauth_start(self.channel.name)

	def test_callback_checks_state_exchanges_code_and_saves(self):
		self.as_admin()
		webhooks.zalo_oauth_start(self.channel.name)
		state = frappe.db.get_value("Omni Channel", self.channel.name, "zalo_oauth_state")
		new = {"access_token": "access-GRANT", "refresh_token": "refresh-GRANT", "expires_in": "90000"}
		with mock.patch(POST, return_value=response(new)) as post:
			webhooks.zalo_oauth_callback(code="ma-cap-quyen", state=state)
		data = post.call_args.kwargs["data"]
		self.assertEqual((data["grant_type"], data["code"]), ("authorization_code", "ma-cap-quyen"))
		self.assertTrue(data["code_verifier"])
		frappe.db.rollback()
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		self.assertEqual(doc.get_password("zalo_access_token"), "access-GRANT")
		self.assertFalse(doc.zalo_oauth_state)
		self.assertTrue(frappe.local.response["location"].endswith(f"/app/omni-channel/{doc.name}"))

	def test_callback_rejects_wrong_or_reused_state(self):
		self.as_admin()
		with mock.patch(POST) as post:
			with self.assertRaises(frappe.PermissionError):
				webhooks.zalo_oauth_callback(code="c", state="gia-mao")
			with self.assertRaises(frappe.PermissionError):
				webhooks.zalo_oauth_callback(code="c", state=None)
			webhooks.zalo_oauth_start(self.channel.name)
			state = frappe.db.get_value("Omni Channel", self.channel.name, "zalo_oauth_state")
			post.return_value = response({"access_token": "a", "refresh_token": "r", "expires_in": "1"})
			webhooks.zalo_oauth_callback(code="c", state=state)
			with self.assertRaises(frappe.PermissionError):  # state dùng một lần
				webhooks.zalo_oauth_callback(code="c", state=state)
		self.assertEqual(post.call_count, 1)


class TestWorkerFlows(ZaloTestCase):
	def test_text_creates_lead_timeline_and_last_inbound(self):
		msg = self.deliver(load("user_send_text"), profile={"display_name": "Trần Thị Hoa Td108"})
		lead = self.lead_of(msg)
		self.assertTrue(lead)
		ident = self.identity()
		self.assertEqual(ident.display_name, "Trần Thị Hoa Td108")
		self.assertIsNotNone(ident.last_inbound_at)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_name"), "Trần Thị Hoa Td108")
		comms = frappe.get_all(
			"Communication",
			filters={"reference_name": lead},
			fields=["sent_or_received", "communication_medium"],
		)
		self.assertEqual(
			[(c.sent_or_received, c.communication_medium) for c in comms], [("Received", "Chat")]
		)
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "status"), "Done")

	def test_profile_fetched_once_and_failure_does_not_break(self):
		m1 = self.deliver(load("user_send_text"), profile=None)  # API hồ sơ lỗi
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", m1, "status"), "Done")
		self.assertFalse(self.identity().display_name)
		frappe.cache().delete_keys("omni:zalo:profile_fail")
		self.deliver(
			load("user_send_text", sender={"id": USER}, message={"msg_id": "m2", "text": "tiếp"}),
			profile={"display_name": "Lê Văn Bình Td108"},
		)
		self.assertEqual(self.identity().display_name, "Lê Văn Bình Td108")
		self.deliver(
			load("user_send_text", message={"msg_id": "m3", "text": "nữa"}),
			profile={"display_name": "khac"},
		)
		self.http.assert_not_called()  # đã có tên: không gọi lại API hồ sơ
		self.assertEqual(self.identity().display_name, "Lê Văn Bình Td108")

	def test_no_token_no_profile_call(self):
		frappe.db.delete(
			"__Auth", {"doctype": "Omni Channel", "name": self.channel.name, "fieldname": "zalo_access_token"}
		)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		with mock.patch(REQ) as http, mock.patch("frappe.enqueue"):
			self.deliver(load("user_send_text"), process=False)
			from omni_crm.pipeline.worker import process_identity

			process_identity(f"Zalo OA:{USER}", debounce_seconds=0)
		http.assert_not_called()
		self.assertIsNotNone(self.identity())

	def test_duplicate_event_not_stored_twice(self):
		self.deliver(load("user_send_text"), profile={})
		self.deliver(load("user_send_text"), profile={})
		self.assertEqual(frappe.db.count("Omni Inbound Message", {"channel": self.channel.name}), 1)

	def test_user_info_phone_is_strong_signal_and_joins_lead(self):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": f"{PREFIX} Nguyễn Văn An", "mobile_no": "0912345678"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		msg = self.deliver(load("user_submit_info"), profile={})
		self.assertEqual(self.lead_of(msg), lead.name)
		ident = self.identity()
		self.assertEqual(ident.phone_e164, "+84912345678")
		self.assertEqual(ident.display_name, "Nguyễn Văn An")

	def test_follow_creates_identity_but_no_lead_by_default(self):
		msg = self.deliver(load("follow"), profile={"display_name": "Khách Quan Tâm Td108"})
		self.assertFalse(self.lead_of(msg))
		ident = self.identity()
		self.assertEqual(ident.is_follower, 1)
		self.assertEqual(ident.display_name, "Khách Quan Tâm Td108")
		self.assertFalse(ident.lead)
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "status"), "Done")
		self.assertFalse(ident.last_inbound_at is None)  # follow là tương tác của khách

	def test_follow_with_flag_creates_lead(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "zalo_create_lead_on_follow", 1)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		msg = self.deliver(load("follow"), profile={})
		self.assertTrue(self.lead_of(msg))
		self.assertEqual(self.identity().is_follower, 1)

	def test_unfollow_clears_flag_and_keeps_last_inbound(self):
		self.deliver(load("follow"), profile={})
		before = self.identity().last_inbound_at
		self.deliver(load("unfollow"), profile={})
		ident = self.identity()
		self.assertEqual(ident.is_follower, 0)
		self.assertEqual(ident.last_inbound_at, before)

	def test_unfollow_unknown_user_creates_nothing(self):
		msg = self.deliver(load("unfollow", user=f"{PREFIX}U9"), profile={})
		self.assertIsNone(self.identity(f"{PREFIX}U9"))
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "status"), "Done")

	def test_message_after_follow_gets_lead_and_profile(self):
		self.deliver(load("follow"), profile=None)  # hồ sơ lỗi lúc follow
		frappe.cache().delete_keys("omni:zalo:profile_fail")
		msg = self.deliver(load("user_send_text"), profile={"display_name": "Phạm Quang Huy Td108"})
		self.assertTrue(self.lead_of(msg))
		self.assertEqual(self.identity().display_name, "Phạm Quang Huy Td108")

	def test_oa_echo_becomes_outbound_sale_sent_and_timeline(self):
		m_in = self.deliver(load("user_send_text"), profile={})
		lead = self.lead_of(m_in)
		last_inbound = self.identity().last_inbound_at
		echo = self.deliver(
			load(
				"oa_send_text",
				timestamp=str(int(time.time() * 1000) + 5000),
				sender={
					"id": OA_ID,
					"admin_id": "td108-admin",
				},  # V3-07: admin gõ trên công cụ chat OA = Sale
			),
			profile={},
		)
		self.assertEqual(self.lead_of(echo), lead)
		out = frappe.get_all(
			"Omni Outbound Message",
			filters={"channel": self.channel.name},
			fields=[
				"source",
				"status",
				"external_message_id",
				"lead",
				"text",
				"identity",
				"sent_by",
				"is_sandbox",
			],
		)
		self.assertEqual(len(out), 1)
		self.assertEqual(
			(out[0].source, out[0].status, out[0].external_message_id), ("Sale", "Sent", "msg-echo-001")
		)
		self.assertEqual((out[0].lead, out[0].text), (lead, "Dạ em gửi báo giá ạ"))
		self.assertFalse(out[0].sent_by)
		self.assertEqual(self.identity().last_inbound_at, last_inbound)  # echo không đổi last_inbound_at
		comms = frappe.get_all(
			"Communication",
			filters={"reference_name": lead},
			fields=["sent_or_received"],
			order_by="creation",
		)
		self.assertEqual(sorted(c.sent_or_received for c in comms), ["Received", "Sent"])

	def test_oa_echo_replay_no_duplicate(self):
		self.deliver(load("oa_send_text"), profile={})
		self.deliver(load("oa_send_text"), profile={})
		self.assertEqual(frappe.db.count("Omni Outbound Message", {"channel": self.channel.name}), 1)

	def test_echo_of_own_send_is_not_duplicated(self):
		m_in = self.deliver(load("user_send_text"), profile={})
		lead = self.lead_of(m_in)
		ident = self.identity()
		frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": self.channel.name,
				"identity": ident.name,
				"lead": lead,
				"text": "tin hệ thống",
				"source": "Sale",
				"status": "Sent",
				"external_message_id": "msg-echo-001",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		echo = self.deliver(load("oa_send_text"), profile={})
		self.assertEqual(frappe.db.count("Omni Outbound Message", {"channel": self.channel.name}), 1)
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", echo, "status"), "Done")
		comms = frappe.get_all("Communication", filters={"reference_name": lead, "sent_or_received": "Sent"})
		self.assertEqual(len(comms), 0)

	def test_echo_text_phone_is_not_a_customer_signal(self):
		other = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": f"{PREFIX} Khac", "mobile_no": "0909000111"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		data = load("oa_send_text")
		data["message"]["text"] = "Anh gọi hotline 0909 000 111 nhé"
		msg = self.deliver(data, profile={})
		self.assertNotEqual(self.lead_of(msg), other.name)

	def test_thread_shows_echo_once(self):
		m_in = self.deliver(load("user_send_text"), profile={})
		lead = self.lead_of(m_in)
		self.deliver(load("oa_send_text"), profile={})
		thread = inbox.get_thread(lead)
		texts = [m["text"] for m in thread["messages"]]
		self.assertEqual(texts.count("Dạ em gửi báo giá ạ"), 1)
		self.assertEqual(len(texts), 2)

	def test_channel_disabled_only_stores(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		msg = self.deliver(load("user_send_text"), profile={}, process=False)
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "status"), "New")

	def test_image_event_text_and_link_on_timeline(self):
		msg = self.deliver(load("user_send_image"), profile={})
		lead = self.lead_of(msg)
		comm = frappe.get_all("Communication", filters={"reference_name": lead}, fields=["content"])[0]
		self.assertIn("[Ảnh]", comm.content)
		self.assertIn("https://example.invalid/a.jpg", comm.content)


SANDBOX_ON = "omni_crm.flags.sandbox"


class TestSandboxBlocksAllZaloHttp(ZaloTestCase):
	"""CLAUDE.md điều 6: sandbox_mode=1 thì không có HTTP nào ra Zalo (kể cả OAuth và đọc hồ sơ)."""

	def soon(self):
		frappe.db.set_value(
			"Omni Channel", self.channel.name, "zalo_token_expires_at", add_to_date(None, hours=1)
		)
		frappe.db.commit()

	def test_refresh_if_needed_skips_in_sandbox_but_runs_otherwise(self):
		self.soon()
		new = {"access_token": "a-new", "refresh_token": "r-new", "expires_in": "90000"}
		with mock.patch(SANDBOX_ON, return_value=True), mock.patch(POST) as post, mock.patch(REQ) as req:
			token_mod.refresh_if_needed()
		post.assert_not_called()
		req.assert_not_called()
		with mock.patch(POST, return_value=response(new)) as post:
			token_mod.refresh_if_needed()
		post.assert_called_once()

	def test_refresh_channel_direct_is_blocked_too(self):
		self.soon()
		with mock.patch(SANDBOX_ON, return_value=True), mock.patch(POST) as post:
			with self.assertRaises(errors.ZaloError):
				token_mod.refresh_channel(self.channel.name)
		post.assert_not_called()

	def test_profile_not_fetched_for_message_and_follow(self):
		for data in (load("user_send_text"), load("follow", user=f"{PREFIX}U2")):
			with mock.patch(SANDBOX_ON, return_value=True):
				msg = self.deliver(data, profile={"display_name": "Khong Duoc Goi Td108"})
			self.http.assert_not_called()
			self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "status"), "Done")
		self.assertFalse(self.identity().display_name)

	def test_profile_fetched_when_not_sandbox(self):
		self.deliver(load("user_send_text"), profile={"display_name": "Co Goi Td108"})
		self.http.assert_called()
		self.assertEqual(self.identity().display_name, "Co Goi Td108")

	def test_send_makes_no_http_in_sandbox(self):
		ident = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": f"Zalo OA:{USER}",
				"channel_type": "Zalo OA",
				"external_id": USER,
			}
		).insert(ignore_permissions=True)
		req = OutboundRequest(self.channel.name, ident.name, "chào", None, "Sale")
		with mock.patch(SANDBOX_ON, return_value=True), mock.patch(REQ) as http, mock.patch(POST) as post:
			mid = self.adapter.send(req, frappe.get_doc("Omni Channel", self.channel.name))
		http.assert_not_called()
		post.assert_not_called()
		self.assertTrue(mid.startswith("sandbox:"))

	def test_oauth_code_exchange_blocked_in_sandbox_redirect_still_allowed(self):
		frappe.set_user("Administrator")
		with mock.patch(SANDBOX_ON, return_value=True), mock.patch(POST) as post:
			webhooks.zalo_oauth_start(self.channel.name)
			self.assertEqual(frappe.local.response["type"], "redirect")
			state = frappe.db.get_value("Omni Channel", self.channel.name, "zalo_oauth_state")
			with self.assertRaises(frappe.ValidationError):
				webhooks.zalo_oauth_callback(code="c", state=state)
		post.assert_not_called()


class TestHardening(ZaloTestCase):
	def test_skip_signature_needs_real_developer_mode_value(self):
		for value, allowed in (("0", False), (0, False), ("1", True), (1, True), (None, False)):
			with mock.patch.dict(frappe.conf, {"developer_mode": value}):
				self.assertEqual(adapter_mod._env_allows_skip(), allowed, repr(value))

	def test_refresh_if_needed_error_log_rate_limited(self):
		with (
			mock.patch.object(token_mod, "needs_refresh", return_value=True),
			mock.patch.object(token_mod, "refresh_channel", side_effect=RuntimeError("loi")),
			mock.patch("frappe.log_error") as log,
		):
			token_mod.refresh_if_needed()
			token_mod.refresh_if_needed()
		mine = [c for c in log.call_args_list if self.channel.name in c.kwargs.get("message", "")]
		self.assertEqual(len(mine), 1)  # mỗi kênh tối đa 1 dòng/giờ

	def test_authorization_state_is_consumed_atomically(self):
		frappe.set_user("Administrator")
		webhooks.zalo_oauth_start(self.channel.name)
		state = frappe.db.get_value("Omni Channel", self.channel.name, "zalo_oauth_state")
		info = frappe.cache().get_value(token_mod._state_key(state), expires=True)
		new = {"access_token": "a", "refresh_token": "r", "expires_in": "1"}
		# hai callback cùng đọc được state (đua nhau): chỉ một cái được dùng
		with (
			mock.patch("frappe.utils.redis_wrapper.RedisWrapper.get_value", return_value=info),
			mock.patch(POST, return_value=response(new)) as post,
		):
			token_mod.complete_authorization("c1", state)
			frappe.db.set_value("Omni Channel", self.channel.name, "zalo_oauth_state", state)  # mô phỏng đua
			with self.assertRaises(frappe.PermissionError):
				token_mod.complete_authorization("c2", state)
		self.assertEqual(post.call_count, 1)
