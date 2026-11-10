"""V3-07: xin số điện thoại qua Zalo (A), chọn loại tin + quỹ tin ngoài 48 giờ (B), móc nối chatbot (C).

Mọi lời gọi Zalo đều giả (mock `requests`/adapter). Dữ liệu commit thật nên các lớp cha tự dọn.
"""

from __future__ import annotations

import json
import time
from unittest import mock

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from omni_crm.api import inbox
from omni_crm.channels.zalo import phone_request, quota, quota_watch, routing
from omni_crm.channels.zalo.adapter import ZaloAdapter, parse_event
from omni_crm.channels.zalo.errors import ZaloError
from omni_crm.crm_bridge import first_response
from omni_crm.identity.normalize import normalize_phone
from omni_crm.ingest.events import OutboundRequest
from omni_crm.pipeline import outbound
from omni_crm.tests import test_outbound
from omni_crm.tests.zalo_helpers import USER, ZaloTestCase, load, now_ms, response

REQ = "omni_crm.channels.zalo.client.requests.request"
OUT = "Omni Outbound Message"
IDENTITY = "Omni Identity"
SALE1 = test_outbound.SALE1
SHARED = "84988806848"
SHARED_E164 = "+84988806848"
SEND_OK = {"error": 0, "data": {"message_id": "zl-card-1", "quota": {"quota_type": "reply"}}}


def _switch(on: bool):
	return mock.patch("omni_crm.channels.zalo.phone_request.enabled", return_value=on)


# --- A. Xin số điện thoại: gửi -----------------------------------------------------------------------------------


class _PhoneBase(ZaloTestCase):
	def setUp(self):
		super().setUp()
		p = _switch(True)
		p.start()
		self.addCleanup(p.stop)
		frappe.cache().delete_value(phone_request._due_key())
		self.addCleanup(lambda: frappe.cache().delete_value(phone_request._due_key()))

	def customer_writes(self, seconds_ago: int = 120) -> str:
		ts = str(int((time.time() - seconds_ago) * 1000))
		self.deliver(
			load(
				"user_send_text",
				timestamp=ts,
				message={"msg_id": frappe.generate_hash(length=10), "text": "cho hỏi giá"},
			),
			profile={"display_name": "Khách Td108"},
		)
		return frappe.db.get_value(IDENTITY, {"identity_key": f"Zalo OA:{USER}"}, "name")

	def run_due(self, *, later_seconds: int = 0, reply: dict | None = None):
		at = add_to_date(now_datetime(), seconds=later_seconds)
		with (
			mock.patch("omni_crm.channels.zalo.phone_request.now_datetime", return_value=at),
			mock.patch(REQ, return_value=response(reply or SEND_OK)) as http,
			mock.patch("frappe.enqueue"),
		):
			stats = phone_request.run_due()
		return stats, http

	def cards(self, ident: str) -> list:
		return frappe.get_all(
			OUT,
			filters={"identity": ident, "purpose": "request_info"},
			fields=["status", "source", "message_kind", "quota_consumed"],
		)


class TestPhoneRequestAuto(_PhoneBase):
	def test_sends_card_60s_after_customer_message_and_marks_time(self):
		ident = self.customer_writes(seconds_ago=10)
		stats, http = self.run_due()  # mới 10 giây: chưa đến hẹn
		self.assertEqual(stats["sent"], 0)
		http.assert_not_called()
		stats, http = self.run_due(later_seconds=60)
		self.assertEqual(stats["sent"], 1)
		body = json.loads(http.call_args.kwargs["data"])
		self.assertEqual(body["message"]["attachment"]["payload"]["template_type"], "request_user_info")
		element = body["message"]["attachment"]["payload"]["elements"][0]
		self.assertTrue(element["image_url"].startswith("https://"))
		self.assertLessEqual(len(element["title"]), 100)
		self.assertTrue(frappe.db.get_value(IDENTITY, ident, "phone_requested_at"))
		self.assertEqual(
			[(c.status, c.source, c.message_kind, c.quota_consumed) for c in self.cards(ident)],
			[("Sent", "System", routing.CS_48H, 0)],
		)
		stats, http = self.run_due(later_seconds=120)  # đã nhận: không gửi lại
		http.assert_not_called()

	def test_identity_with_phone_not_asked(self):
		ident = self.customer_writes()
		frappe.db.set_value(IDENTITY, ident, "phone_e164", SHARED_E164)
		frappe.db.commit()
		stats, http = self.run_due(later_seconds=60)
		http.assert_not_called()
		self.assertFalse(self.cards(ident))

	def test_cooldown_10_days_blocks_31_days_allows(self):
		ident = self.customer_writes()
		for days, expect in ((10, 0), (31, 1)):
			frappe.db.set_value(
				IDENTITY, ident, "phone_requested_at", add_to_date(now_datetime(), days=-days)
			)
			frappe.db.commit()
			phone_request._schedule(ident, now_datetime().timestamp() - 1)
			stats, _http = self.run_due(later_seconds=60)
			self.assertEqual(stats["sent"], expect, days)

	def test_outside_48h_not_sent(self):
		ident = self.customer_writes()
		frappe.db.set_value(IDENTITY, ident, "last_inbound_at", add_to_date(now_datetime(), hours=-49))
		frappe.db.commit()
		stats, http = self.run_due(later_seconds=60)
		http.assert_not_called()
		self.assertIsNone(frappe.db.get_value(IDENTITY, ident, "phone_requested_at"))

	def test_failed_send_does_not_mark_time(self):
		ident = self.customer_writes()
		stats, _http = self.run_due(later_seconds=60, reply={"error": -213, "message": "not follower"})
		self.assertEqual(stats["failed"], 1)
		self.assertIsNone(frappe.db.get_value(IDENTITY, ident, "phone_requested_at"))
		self.assertEqual([c.status for c in self.cards(ident)], ["Failed"])

	def test_ambiguous_network_error_keeps_mark_to_avoid_resend(self):
		import requests

		ident = self.customer_writes()
		with mock.patch(REQ, side_effect=requests.ReadTimeout("x")):
			with mock.patch(
				"omni_crm.channels.zalo.phone_request.now_datetime",
				return_value=add_to_date(now_datetime(), seconds=60),
			):
				phone_request.run_due()
		self.assertTrue(frappe.db.get_value(IDENTITY, ident, "phone_requested_at"))

	def test_switch_off_only_whitelisted_user_receives(self):
		ident = self.customer_writes()
		with _switch(False):
			stats, http = self.run_due(later_seconds=60)
			http.assert_not_called()
			doc = frappe.get_doc(IDENTITY, ident)
			self.assertIn(
				"tắt", phone_request.ineligible_reason(doc, phone_request._channel_of(ident), manual=True)
			)
			# chốt chặn ở tầng gửi, kể cả khi có ai gọi thẳng client
			from omni_crm.channels.zalo.client import ZaloClient

			with mock.patch(REQ) as raw_http, self.assertRaises(ZaloError):
				ZaloClient(self.channel.name).send_request_user_info(USER, "t", "s", "https://x/a.png")
			raw_http.assert_not_called()
			self.assertTrue(phone_request.recipient_allowed(next(iter(phone_request.WHITELIST_USER_IDS))))

	def test_paused_bot_blocks_auto_card(self):
		from omni_crm.bot import handoff

		ident = self.customer_writes()
		handoff.pause_identity_for_sale(ident)
		frappe.db.commit()
		stats, http = self.run_due(later_seconds=60)
		http.assert_not_called()
		self.customer_writes(seconds_ago=5)  # khách nhắn lại: tạm dừng vẫn còn
		self.assertTrue(handoff.is_bot_paused(ident))


class TestPhoneRequestManual(test_outbound.OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.sandbox(True)
		p = _switch(True)
		p.start()
		self.addCleanup(p.stop)

	def call(self):
		with mock.patch("frappe.enqueue"):
			res = inbox.request_phone(self.lead1)
			frappe.db.commit()
		return res

	def test_manual_ignores_30_days_but_not_session_or_window(self):
		frappe.db.set_value(IDENTITY, self.ident1, "phone_requested_at", add_to_date(now_datetime(), days=-3))
		frappe.db.commit()
		self.as_user(SALE1)
		res = self.call()
		self.assertEqual(res["status"], "Sent")
		r = frappe.db.get_value(OUT, res["outbound"], ["source", "sent_by", "purpose"], as_dict=True)
		self.assertEqual((r.source, r.sent_by, r.purpose), ("Sale", SALE1, "request_info"))
		with self.assertRaises(frappe.ValidationError):  # cùng phiên chat
			self.call()
		frappe.db.set_value(IDENTITY, self.ident1, "phone_requested_at", None)
		self.set_last_inbound(hours_ago=72)
		with self.assertRaises(frappe.ValidationError):  # ngoài 48 giờ: không gửi, không hỏi tiêu quỹ
			self.call()

	def test_manual_needs_blank_identity_phone(self):
		frappe.db.set_value(IDENTITY, self.ident1, "phone_e164", "+84912345678")
		frappe.db.commit()
		self.as_user(SALE1)
		with self.assertRaises(frappe.ValidationError):
			self.call()


# --- A. Xin số điện thoại: nhận ----------------------------------------------------------------------------------


class TestSharedPhone(ZaloTestCase):
	def share(self, phone=SHARED, **over):
		data = load("user_submit_info", timestamp=over.pop("timestamp", "1762700000000"))
		data["info"] = {"name": "Khách Chia Sẻ", "phone": phone, "full_address": {"user_city": "Hà Nội"}}
		return self.deliver(data, profile={})

	def lead_with_identity(self, mobile=None) -> str:
		self.deliver(load("user_send_text", timestamp=now_ms()), profile={"display_name": "Khách Td108"})
		lead = frappe.db.get_value(IDENTITY, {"identity_key": f"Zalo OA:{USER}"}, "lead")
		if mobile:
			frappe.db.set_value(
				"CRM Lead", lead, {"mobile_no": mobile, "omni_phone_e164": normalize_phone(mobile)}
			)
			frappe.db.commit()
		return lead

	def test_normalizes_84_prefix_and_parses_full_address(self):
		e = parse_event(
			self.channel,
			{
				**load("user_submit_info"),
				"info": {
					"phone": SHARED,
					"name": "A",
					"full_address": {"user_city": "Hà Nội", "user_ward": "P1"},
				},
			},
		)
		self.assertEqual(e.sender_profile["phone"], SHARED_E164)
		self.assertIn("Hà Nội", e.sender_profile["address"])

	def test_invalid_number_dropped_and_logged_masked(self):
		e = parse_event(self.channel, {**load("user_submit_info"), "info": {"phone": "8412", "name": "A"}})
		self.assertNotIn("phone", e.sender_profile)
		log = frappe.get_all(
			"Error Log",
			filters={"method": ["like", "%không hợp lệ%"]},
			fields=["error"],
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(log)
		self.assertNotIn("8412", log[0].error.replace("***", ""))

	def test_blank_lead_mobile_is_filled(self):
		lead = self.lead_with_identity()
		self.share()
		self.assertEqual(
			frappe.db.get_value(IDENTITY, {"identity_key": f"Zalo OA:{USER}"}, "phone_e164"), SHARED_E164
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "mobile_no"), SHARED_E164)

	def test_lead_with_other_number_kept_and_commented_once(self):
		lead = self.lead_with_identity(mobile="0912345678")
		self.share()
		self.share()  # Zalo gửi lại đúng sự kiện
		self.share(timestamp="1762700009999")  # khách bấm chia sẻ lần nữa
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "mobile_no"), "0912345678")
		comments = frappe.get_all(
			"Comment", filters={"reference_name": lead, "content": ["like", "%chia sẻ số%"]}
		)
		self.assertEqual(len(comments), 1)

	def test_zalo_and_email_identity_same_number_one_open_candidate(self):
		lead = self.lead_with_identity()
		other = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD108 Email Khách"}).insert(
			ignore_permissions=True
		)
		frappe.get_doc(
			{
				"doctype": IDENTITY,
				"identity_key": "Email:td108-mail@example.com",
				"channel_type": "Email",
				"external_id": "td108-mail@example.com",
				"phone_e164": SHARED_E164,
				"lead": other.name,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.db.delete(IDENTITY, {"identity_key": "Email:td108-mail@example.com"}),
				frappe.db.commit(),
			)
		)
		self.share()
		self.share()
		rows = frappe.get_all(
			"Omni Duplicate Candidate",
			filters={"lead_a": ["in", [lead, other.name]], "lead_b": ["in", [lead, other.name]]},
			fields=["status", "reason"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].status, "Open")
		self.assertIn("Zalo", rows[0].reason)

	def test_no_full_number_in_error_log(self):
		self.lead_with_identity(mobile="0912345678")
		self.share()
		self.assertFalse(frappe.db.exists("Error Log", {"error": ["like", "%988806848%"]}))


# --- B. Loại tin và quỹ ----------------------------------------------------------------------------------------


class TestRouting(test_outbound.OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.sandbox(True)

	def send(self, confirm=0):
		self.as_user(SALE1)
		with mock.patch("frappe.enqueue"):
			res = inbox.send_zalo(self.lead1, "Dạ em chào anh", confirm)
			frappe.db.commit()
		frappe.set_user("Administrator")
		return res

	def kind(self, name):
		return frappe.db.get_value(
			OUT, name, ["message_kind", "purpose", "quota_consumed", "approved_by"], as_dict=True
		)

	def test_reply_inside_48h_is_free(self):
		res = self.send()
		r = self.kind(res["outbound"])
		self.assertEqual((r.message_kind, r.purpose, r.quota_consumed), (routing.CS_48H, "reply", 0))

	def test_reply_outside_48h_needs_human_then_consumes_quota(self):
		self.set_last_inbound(hours_ago=72)
		res = self.send()
		self.assertEqual(res["status"], "confirm_required")
		self.assertIn("/500", res["warning"])
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1, "status": "Queued"}), 0)
		res = self.send(confirm=1)
		r = self.kind(res["outbound"])
		self.assertEqual((r.message_kind, r.quota_consumed, r.approved_by), (routing.CS_PAID, 1, SALE1))

	def test_bot_never_falls_back_to_paid(self):
		self.set_last_inbound(hours_ago=72)
		ident = frappe.get_doc(IDENTITY, self.ident1)
		with self.assertRaises(routing.RoutingError):
			routing.pick_message_kind(ident, "reply", channel_doc=self.channel, user=None, confirmed=True)

	def test_transaction_without_zbs_template_refused_never_cs(self):
		ident = frappe.get_doc(IDENTITY, self.ident1)
		before = frappe.db.count(OUT)
		with self.assertRaises(routing.RoutingError) as ctx:
			routing.pick_message_kind(
				ident, "transaction", channel_doc=self.channel, user=SALE1, confirmed=True
			)
		self.assertEqual(ctx.exception.code, "no_zbs_template")
		with self.assertRaises(frappe.ValidationError):
			with mock.patch("frappe.enqueue"):
				outbound.queue_outbound(
					OutboundRequest(
						self.channel.name,
						self.ident1,
						"Đơn hàng đã giao",
						None,
						"Sale",
						purpose="transaction",
					),
					user=SALE1,
					confirmed_paid=True,
				)
		self.assertEqual(frappe.db.count(OUT), before)

	def test_quota_levels(self):
		self.set_last_inbound(hours_ago=72)
		ident = frappe.get_doc(IDENTITY, self.ident1)

		def pick(user):
			return routing.pick_message_kind(
				ident, "followup", channel_doc=self.channel, user=user, confirmed=True
			)

		with mock.patch.object(quota, "count_used", return_value=400):
			self.assertEqual(quota.usage().level, quota.WARN)
			self.assertEqual(pick(SALE1).kind, routing.CS_PAID)
			with mock.patch.object(quota_watch, "_deliver", return_value=True) as alert:
				frappe.cache().delete_keys(quota_watch.LEVEL_KEY)
				quota_watch.check_level()
				quota_watch.check_level()  # mỗi mức một lần
			self.assertEqual(alert.call_count, 1)
		with mock.patch.object(quota, "count_used", return_value=475):
			with self.assertRaises(routing.RoutingError):
				pick(SALE1)
			self.assertEqual(pick("Administrator").kind, routing.CS_PAID)  # Omni Admin
		with mock.patch.object(quota, "count_used", return_value=500):
			for user in (SALE1, "Administrator"):
				with self.assertRaises(routing.RoutingError) as ctx:
					pick(user)
				self.assertIn("tính phí", ctx.exception.message_vi)

	def test_quota_line_only_outside_48h(self):
		self.assertIsNone(inbox.compose_state(self.lead1)["zalo"]["quota"])
		self.set_last_inbound(hours_ago=72)
		q = inbox.compose_state(self.lead1)["zalo"]["quota"]
		self.assertRegex(q["text"], r"^Còn \d+/500 tin ngoài khung 48h$")

	def test_counts_only_sent_consumed_in_cycle(self):
		base = quota.count_used()
		start, end = quota.cycle_bounds()
		self.assertEqual((start.date().isoformat(), end.date().isoformat()), ("2026-11-10", "2027-11-10"))
		for status, consumed, at in (
			("Sent", 1, now_datetime()),
			("Failed", 1, now_datetime()),
			("Sent", 0, now_datetime()),
			("Sent", 1, get_datetime("2026-11-09 23:00:00")),
		):
			frappe.get_doc(
				{
					"doctype": OUT,
					"channel": self.channel.name,
					"identity": self.ident1,
					"lead": self.lead1,
					"text": "x",
					"source": "Sale",
					"status": status,
					"quota_consumed": consumed,
					"sent_at": at,
					"message_kind": routing.CS_PAID,
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
		self.assertEqual(quota.count_used(), base + 1)

	def test_zalo_reported_quota_type_wins(self):
		self.sandbox(False)
		name = self.send()["outbound"]

		def fake_send(req, channel_doc):
			req.meta["quota_type"] = "sub_quota"
			return "zl-q-1"

		with mock.patch.object(ZaloAdapter, "send", side_effect=fake_send):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		self.assertEqual(frappe.db.get_value(OUT, name, "quota_consumed"), 1)

	def test_daily_job_flags_message_that_bypassed_routing(self):
		frappe.get_doc(
			{
				"doctype": OUT,
				"channel": self.channel.name,
				"identity": self.ident1,
				"lead": self.lead1,
				"text": "tạo tay",
				"source": "Sale",
				"status": "Sent",
				"sent_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		with (
			mock.patch.object(quota_watch, "_deliver", return_value=True) as alert,
			mock.patch.object(quota_watch, "zalo_used", return_value=None),
		):
			out = quota_watch.daily()
		self.assertGreaterEqual(out["unrouted"], 1)
		self.assertIn("unrouted", out["alerted"])
		self.assertTrue(any("không qua bộ chọn" in c.args[1] for c in alert.call_args_list))

	def test_daily_job_alerts_drift_over_5(self):
		with (
			mock.patch.object(quota_watch, "_deliver", return_value=True),
			mock.patch.object(quota, "count_used", return_value=10),
			mock.patch.object(quota_watch, "unrouted_count", return_value=0),
			mock.patch.object(quota_watch, "zalo_used", return_value={"used": 17, "total": 500, "rows": []}),
		):
			self.assertIn("drift", quota_watch.daily()["alerted"])
		with (
			mock.patch.object(quota_watch, "_deliver", return_value=True),
			mock.patch.object(quota, "count_used", return_value=12),
			mock.patch.object(quota_watch, "unrouted_count", return_value=0),
			mock.patch.object(quota_watch, "zalo_used", return_value={"used": 17, "total": 500, "rows": []}),
		):
			self.assertNotIn("drift", quota_watch.daily()["alerted"])


# --- C. Móc nối chatbot ------------------------------------------------------------------------------------------


class TestSalePause(test_outbound.OutboundTestCase):
	def test_sale_message_pauses_bot_24h(self):
		self.sandbox(True)
		name = self.queue()
		outbound.send_outbound(name)
		until = get_datetime(frappe.db.get_value(IDENTITY, self.ident1, "bot_paused_until"))
		delta = (until - now_datetime()).total_seconds()
		self.assertTrue(23.9 * 3600 < delta <= 24 * 3600 + 5, delta)


class TestEcho(ZaloTestCase):
	def setUp(self):
		super().setUp()
		self.deliver(load("user_send_text", timestamp=now_ms()), profile={"display_name": "Khách Td108"})
		self.ident = frappe.db.get_value(IDENTITY, {"identity_key": f"Zalo OA:{USER}"}, "name")

	def echo(self, admin_id=None, msg_id="echo-v307"):
		data = load(
			"oa_send_text", timestamp=now_ms(), message={"msg_id": msg_id, "text": "Dạ em gửi báo giá"}
		)
		if admin_id:
			data["sender"]["admin_id"] = admin_id
		self.deliver(data, profile={})
		return frappe.db.get_value(
			OUT,
			{"external_message_id": msg_id},
			["source", "sent_by", "message_kind", "bot_draft", "status", "is_sandbox", "lead"],
			as_dict=True,
		)

	def test_admin_echo_mapped_to_crm_user_and_pauses(self):
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		doc.append("agent_map", {"agent_ref": "4267886274574868951", "user": "Administrator"})
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		row = self.echo(admin_id="4267886274574868951")
		self.assertEqual(
			(row.source, row.sent_by, row.message_kind), ("Sale", "Administrator", routing.CS_48H)
		)
		self.assertTrue(frappe.db.get_value(IDENTITY, self.ident, "bot_paused_until"))

	def test_echo_without_admin_is_bot_and_not_a_sale_response(self):
		row = self.echo()
		self.assertEqual(row.source, "Bot")
		self.assertFalse(first_response.counts_as_response(row))
		self.assertFalse(frappe.db.get_value(IDENTITY, self.ident, "bot_paused_until"))
