"""D1-09: chiều ra (queue/send_outbound, send_reply, request_phone, conversation_turns). Mọi lời gọi gửi đều giả."""

from unittest import mock

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from omni_crm.api import inbox
from omni_crm.channels.zalo import token as token_mod
from omni_crm.channels.zalo.adapter import ZaloAdapter
from omni_crm.crm_bridge import timeline
from omni_crm.ingest.events import OutboundRequest
from omni_crm.pipeline import outbound, sweeper
from omni_crm.tests import test_inbox
from omni_crm.tests.zalo_helpers import OK_SEND, TOKEN_ERR, response

REQ = "omni_crm.channels.zalo.client.requests.request"
SALE1, SALE2 = test_inbox.SALE1, test_inbox.SALE2
OUT = "Omni Outbound Message"


class OutboundTestCase(test_inbox.InboxTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_value("Omni Channel", self.channel.name, "outbound_enabled", 1)
		frappe.db.set_value("Omni Channel", self.channel.name, "zalo_access_token", None)
		self.set_last_inbound(minutes_ago=10)
		frappe.db.commit()
		self.addCleanup(frappe.set_user, "Administrator")

	def set_last_inbound(self, minutes_ago=None, hours_ago=None):
		at = add_to_date(now_datetime(), minutes=-(minutes_ago or 0), hours=-(hours_ago or 0))
		frappe.db.set_value("Omni Identity", self.ident1, "last_inbound_at", at)
		frappe.db.commit()

	def set_token(self):
		frappe.get_doc("Omni Channel", self.channel.name).update(
			{"zalo_access_token": "access-td109-khong-that"}
		).save(ignore_permissions=True)
		frappe.db.commit()

	def sandbox(self, value: bool):
		p = mock.patch("omni_crm.flags.sandbox", return_value=value)
		p.start()
		self.addCleanup(p.stop)

	def queue(self, text="Dạ em chào anh", source="Sale", confirmed_paid=False) -> str:
		with mock.patch("frappe.enqueue"):
			name = outbound.queue_outbound(
				OutboundRequest(self.channel.name, self.ident1, text, None, source),
				user=SALE1,
				confirmed_paid=confirmed_paid,
			)
			frappe.db.commit()
		return name

	def row(self, name):
		return frappe.db.get_value(
			OUT,
			name,
			[
				"status",
				"attempts",
				"next_attempt_at",
				"last_error",
				"external_message_id",
				"is_sandbox",
				"sent_at",
				"sent_by",
				"source",
			],
			as_dict=True,
		)

	def comms(self):
		return frappe.get_all(
			"Communication",
			filters={"reference_name": self.lead1, "omni_outbound_message": ["is", "set"]},
			pluck="omni_outbound_message",
		)


class TestSendOutbound(OutboundTestCase):
	def test_queue_creates_queued_and_enqueues_with_job_id(self):
		with mock.patch("frappe.enqueue") as enq:
			name = outbound.queue_outbound(
				OutboundRequest(self.channel.name, self.ident1, " xin chào ", None, "Sale"), user=SALE1
			)
			frappe.db.commit()
		r = self.row(name)
		self.assertEqual((r.status, r.sent_by, r.source), ("Queued", SALE1, "Sale"))
		self.assertEqual(frappe.db.get_value(OUT, name, ["text", "lead"]), ("xin chào", self.lead1))
		kw = enq.call_args.kwargs
		self.assertEqual(kw["job_id"], f"omni-out::{name}")
		self.assertTrue(kw["deduplicate"])
		self.assertEqual(kw["queue"], "omni")

	def test_sandbox_sent_without_any_http(self):
		self.sandbox(True)
		name = self.queue()
		with (
			mock.patch(REQ, side_effect=AssertionError("không được gọi HTTP")) as http,
			mock.patch.object(ZaloAdapter, "send", side_effect=AssertionError("không được gọi send")),
		):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		r = self.row(name)
		self.assertEqual((r.status, r.is_sandbox), ("Sent", 1))
		self.assertIsNotNone(r.sent_at)
		http.assert_not_called()
		self.assertIn(name, self.comms())

	def test_sandbox_communication_is_labelled(self):
		self.sandbox(True)
		name = self.queue()
		outbound.send_outbound(name)
		comm = frappe.db.get_value(
			"Communication", {"omni_outbound_message": name}, ["subject", "content"], as_dict=True
		)
		self.assertTrue(comm.subject.startswith("[Thử nghiệm]"))
		self.assertIn("Tin thử nghiệm (sandbox), chưa gửi tới khách", comm.content)

	def test_real_communication_not_labelled(self):
		self.sandbox(False)
		name = self.queue()
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-mid-9"):
			outbound.send_outbound(name)
		comm = frappe.db.get_value(
			"Communication", {"omni_outbound_message": name}, ["subject", "content"], as_dict=True
		)
		self.assertNotIn("Thử nghiệm", comm.subject)
		self.assertNotIn("sandbox", comm.content)

	def test_real_send_stores_message_id_and_timeline(self):
		self.sandbox(False)
		name = self.queue()
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-mid-1") as send:
			self.assertEqual(outbound.send_outbound(name), "Sent")
		r = self.row(name)
		self.assertEqual((r.status, r.external_message_id, r.is_sandbox), ("Sent", "zl-mid-1", 0))
		self.assertFalse(r.last_error)
		self.assertEqual(send.call_args.args[0].text, "Dạ em chào anh")
		self.assertEqual(self.comms().count(name), 1)

	def test_double_enqueue_sends_once(self):
		self.sandbox(False)
		name = self.queue()
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-mid-2") as send:
			outbound.send_outbound(name)
			outbound.send_outbound(name)  # job thứ hai cùng tin
		self.assertEqual(send.call_count, 1)
		self.assertEqual(self.comms().count(name), 1)

	def test_concurrent_job_while_leased_does_not_send(self):
		self.sandbox(False)
		name = self.queue()
		frappe.db.set_value(
			OUT,
			name,
			{"last_error": outbound.SENDING_MARK, "next_attempt_at": add_to_date(now_datetime(), minutes=3)},
		)
		frappe.db.commit()
		with mock.patch.object(ZaloAdapter, "send") as send:
			outbound.send_outbound(name)
		send.assert_not_called()
		self.assertEqual(self.row(name).status, "Queued")

	def test_crashed_midflight_is_failed_not_resent(self):
		self.sandbox(False)
		name = self.queue()
		frappe.db.set_value(
			OUT,
			name,
			{"last_error": outbound.SENDING_MARK, "next_attempt_at": add_to_date(now_datetime(), minutes=-1)},
		)
		frappe.db.commit()
		with mock.patch.object(ZaloAdapter, "send") as send:
			self.assertEqual(outbound.send_outbound(name), "Failed")
		send.assert_not_called()
		self.assertIn("Không rõ", self.row(name).last_error)

	def test_user_blocked_oa_fails_with_reason(self):
		self.sandbox(False)
		self.set_token()
		name = self.queue()
		with mock.patch(REQ, return_value=response({"error": -213, "message": "x"})) as http:
			self.assertEqual(outbound.send_outbound(name), "Failed")
		self.assertEqual(http.call_count, 1)  # lỗi vĩnh viễn không thử lại
		r = self.row(name)
		self.assertEqual(r.status, "Failed")
		self.assertIn("quan tâm", r.last_error)
		self.assertNotIn(name, self.comms())

	def test_token_error_refreshes_then_sent(self):
		self.sandbox(False)
		frappe.get_doc("Omni Channel", self.channel.name).update(
			{"zalo_access_token": "access-OLD-td109"}
		).save(ignore_permissions=True)
		frappe.db.commit()
		name = self.queue()
		seen = []

		def fake(method, url, **kw):
			seen.append(kw["headers"]["access_token"])
			return response(TOKEN_ERR if len(seen) == 1 else OK_SEND)

		def fake_refresh(channel, force=False, stale_access_token=None):
			doc = frappe.get_doc("Omni Channel", channel)
			doc.zalo_access_token = "access-NEW-td109"
			doc.save(ignore_permissions=True)

		with (
			mock.patch(REQ, side_effect=fake),
			mock.patch.object(token_mod, "refresh_channel", side_effect=fake_refresh),
		):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		self.assertEqual(seen, ["access-OLD-td109", "access-NEW-td109"])
		self.assertEqual(self.row(name).external_message_id, "zl-sent-1")

	def test_transient_retries_then_fails_after_max_attempts(self):
		self.sandbox(False)
		self.set_token()
		name = self.queue()
		with mock.patch(REQ, return_value=response({"error": -223, "message": "rate"})):
			self.assertEqual(outbound.send_outbound(name), "Queued")
			r = self.row(name)
			self.assertEqual((r.status, r.attempts), ("Queued", 1))
			self.assertGreater(r.next_attempt_at, now_datetime())
			# chưa đến hạn: không gửi lại
			with mock.patch.object(ZaloAdapter, "send") as send:
				outbound.send_outbound(name)
			send.assert_not_called()
			for _i in range(2):
				frappe.db.set_value(OUT, name, "next_attempt_at", add_to_date(now_datetime(), minutes=-1))
				frappe.db.commit()
				outbound.send_outbound(name)
		r = self.row(name)
		self.assertEqual((r.status, r.attempts), ("Failed", 3))

	def test_transient_then_success(self):
		self.sandbox(False)
		self.set_token()
		name = self.queue()
		with mock.patch(REQ, side_effect=requests.ConnectTimeout("x")):
			self.assertEqual(outbound.send_outbound(name), "Queued")
		frappe.db.set_value(OUT, name, "next_attempt_at", add_to_date(now_datetime(), minutes=-1))
		frappe.db.commit()
		with mock.patch(REQ, return_value=response(OK_SEND)):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		self.assertEqual(self.row(name).external_message_id, "zl-sent-1")

	def test_ambiguous_network_error_fails_without_retry(self):
		"""Đọc phản hồi quá hạn/đứt kết nối giữa chừng: tin có thể đã tới khách -> Failed, không gửi lại."""
		self.sandbox(False)
		self.set_token()
		for exc in (requests.ReadTimeout("x"), requests.ConnectionError("reset")):
			name = self.queue()
			with mock.patch(REQ, side_effect=exc) as http:
				self.assertEqual(outbound.send_outbound(name), "Failed")
			self.assertEqual(http.call_count, 1)
			r = self.row(name)
			self.assertEqual((r.status, r.attempts), ("Failed", 0))
			self.assertIn("Không rõ", r.last_error)
			self.assertIsNone(r.next_attempt_at)

	def test_gateway_5xx_without_zalo_json_fails_without_retry(self):
		self.sandbox(False)
		self.set_token()
		for status, body in ((504, {}), (502, {"error": -1, "message": "x"}), (500, {})):
			name = self.queue()
			with mock.patch(REQ, return_value=response(body, status)) as http:
				self.assertEqual(outbound.send_outbound(name), "Failed")
			self.assertEqual(http.call_count, 1)
			self.assertIn("Không rõ", self.row(name).last_error)

	def test_503_with_zalo_json_and_429_are_retried(self):
		self.sandbox(False)
		self.set_token()
		for status, body in ((503, {"error": -1, "message": "x"}), (429, {})):
			name = self.queue()
			with mock.patch(REQ, return_value=response(body, status)):
				self.assertEqual(outbound.send_outbound(name), "Queued")

	def test_partial_send_fails_without_retry(self):
		self.sandbox(False)
		name = self.queue("a" * 10)
		from omni_crm.channels.zalo.errors import TRANSIENT, ZaloError

		err = ZaloError(None, TRANSIENT, "mạng lỗi")
		err.sent_ids = ["zl-part-1"]
		with mock.patch.object(ZaloAdapter, "send", side_effect=err):
			self.assertEqual(outbound.send_outbound(name), "Failed")
		r = self.row(name)
		self.assertEqual((r.status, r.external_message_id), ("Failed", "zl-part-1"))

	def test_window_expired_is_blocked_with_reason(self):
		self.sandbox(False)
		name = self.queue()
		self.set_last_inbound(hours_ago=24 * 8)
		with mock.patch.object(ZaloAdapter, "send") as send:
			self.assertEqual(outbound.send_outbound(name), "Blocked")
		send.assert_not_called()
		self.assertIn("7 ngày", self.row(name).last_error)

	def test_bot_blocked_in_paid_window_but_sale_allowed(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=72)
		bot = self.queue(source="Bot")
		sale = self.queue(source="Sale", confirmed_paid=True)
		self.assertEqual(outbound.send_outbound(bot), "Blocked")
		self.assertEqual(outbound.send_outbound(sale), "Sent")

	def test_free_at_queue_then_paid_at_send_is_blocked(self):
		self.sandbox(True)
		name = self.queue()  # khung miễn phí lúc xếp hàng
		self.set_last_inbound(hours_ago=72)  # trong lúc chờ chuyển sang có phí
		self.assertEqual(outbound.send_outbound(name), "Blocked")
		self.assertIn("có phí", self.row(name).last_error)

	def test_paid_confirmed_at_queue_still_sends(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=72)
		name = self.queue(confirmed_paid=True)
		self.assertEqual(outbound.send_outbound(name), "Sent")

	def test_paid_without_confirmation_flag_is_blocked(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=72)
		name = self.queue()
		self.assertEqual(outbound.send_outbound(name), "Blocked")

	def test_send_reply_confirm_paid_sets_flag(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=72)
		self.as_user(SALE1)
		with mock.patch("frappe.enqueue"):
			res = inbox.send_reply(self.lead1, "Zalo OA", "x", confirm_paid=1)
			frappe.db.commit()
		frappe.set_user("Administrator")
		self.assertEqual(outbound.send_outbound(res["outbound"]), "Sent")

	def test_channel_outbound_disabled_is_blocked(self):
		self.sandbox(True)
		name = self.queue()
		frappe.db.set_value("Omni Channel", self.channel.name, "outbound_enabled", 0)
		frappe.db.commit()
		self.assertEqual(outbound.send_outbound(name), "Blocked")
		self.assertIn("tắt", self.row(name).last_error)

	def test_echo_duplicate_created_before_message_id_is_removed(self):
		self.sandbox(False)
		name = self.queue()
		dup = frappe.get_doc(
			{
				"doctype": OUT,
				"channel": self.channel.name,
				"identity": self.ident1,
				"lead": self.lead1,
				"text": "Dạ em chào anh",
				"source": "Sale",
				"status": "Sent",
				"external_message_id": "zl-mid-echo",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-mid-echo"):
			outbound.send_outbound(name)
		self.assertFalse(frappe.db.exists(OUT, dup.name))
		self.assertEqual(self.row(name).status, "Sent")

	def test_error_message_is_redacted(self):
		self.sandbox(False)
		name = self.queue()
		with mock.patch.object(ZaloAdapter, "send", side_effect=ValueError("lỗi với 0912345678 và a@b.vn")):
			outbound.send_outbound(name)
		err = self.row(name).last_error
		self.assertNotIn("0912345678", err)
		self.assertNotIn("a@b.vn", err)


class TestSweeperOutbound(OutboundTestCase):
	def test_requeues_due_and_orphans_only(self):
		due = self.queue("due")
		old = self.queue("old")
		fresh = self.queue("fresh")
		waiting = self.queue("waiting")
		frappe.db.set_value(OUT, due, "next_attempt_at", add_to_date(now_datetime(), minutes=-1))
		frappe.db.set_value(OUT, old, "creation", add_to_date(now_datetime(), minutes=-10))
		frappe.db.set_value(OUT, waiting, "next_attempt_at", add_to_date(now_datetime(), minutes=5))
		frappe.db.commit()
		with mock.patch("frappe.enqueue") as enq:
			sweeper.requeue_stuck_outbound()
		names = {c.kwargs["outbound_name"] for c in enq.call_args_list}
		self.assertTrue({due, old} <= names)
		self.assertFalse({fresh, waiting} & names)
		for c in enq.call_args_list:
			self.assertEqual(c.kwargs["job_id"], f"omni-out::{c.kwargs['outbound_name']}")


class TestSendReply(OutboundTestCase):
	def reply(self, text="Dạ em chào anh", **kw):
		with mock.patch("frappe.enqueue"):
			res = inbox.send_reply(self.lead1, "Zalo OA", text, **kw)
			frappe.db.commit()
		return res

	def test_owner_sale_queues_reply_i08(self):
		self.sandbox(True)
		self.as_user(SALE1)
		res = self.reply()
		self.assertEqual(res["status"], "Queued")
		frappe.set_user("Administrator")
		self.assertEqual(outbound.send_outbound(res["outbound"]), "Sent")
		r = self.row(res["outbound"])
		self.assertEqual((r.status, r.is_sandbox, r.sent_by), ("Sent", 1, SALE1))

	def test_other_sales_user_denied(self):
		self.as_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			self.reply()
		self.assertFalse(frappe.db.exists(OUT, {"lead": self.lead1, "sent_by": SALE2}))

	def test_guest_and_unknown_lead_denied(self):
		self.as_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			self.reply()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.DoesNotExistError):
			inbox.send_reply("CRM-LEAD-khong-co", "Zalo OA", "x")

	def test_empty_and_too_long_text_rejected(self):
		self.as_user(SALE1)
		for text in ("", "   ", "x" * (outbound.MAX_TEXT_CHARS + 1)):
			with self.assertRaises(frappe.ValidationError):
				self.reply(text)
		self.assertFalse(frappe.db.exists(OUT, {"lead": self.lead1, "sent_by": SALE1}))

	def test_email_channel_points_to_crm(self):
		self.as_user(SALE1)
		with self.assertRaises(frappe.ValidationError) as cm:
			inbox.send_reply(self.lead1, "Email", "x")
		self.assertIn("CRM", str(cm.exception))

	def test_paid_window_needs_confirmation_i09a(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=72)
		self.as_user(SALE1)
		res = self.reply()
		self.assertEqual(res["status"], "confirm_required")
		self.assertIn("tính phí", res["warning"])
		self.assertFalse(frappe.db.exists(OUT, {"lead": self.lead1, "sent_by": SALE1}))
		res = self.reply(confirm_paid=1)
		self.assertEqual(res["status"], "Queued")

	def test_beyond_seven_days_blocked_i09(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=24 * 9)
		self.as_user(SALE1)
		res = self.reply(confirm_paid=1)
		self.assertEqual(res["status"], "Blocked")
		self.assertIn("7 ngày", res["reason"])
		r = self.row(res["outbound"])
		self.assertEqual(r.status, "Blocked")
		self.assertIn("kênh khác", r.last_error)

	def test_thread_shows_status_error_and_compose_state(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=24 * 9)
		self.as_user(SALE1)
		self.reply()
		data = inbox.get_thread(self.lead1)
		blocked = [m for m in data["messages"] if m.get("status") == "Blocked"]
		self.assertEqual(len(blocked), 1)
		self.assertIn("kênh khác", blocked[0]["error"])
		z = data["compose"]["zalo"]
		self.assertFalse(z["can_send"])
		self.assertTrue(data["compose"]["can_write"])
		self.assertTrue(data["compose"]["email"])

	def test_compose_flags_paid_and_phone_button(self):
		self.set_last_inbound(hours_ago=72)
		z = inbox.compose_state(self.lead1)["zalo"]
		self.assertTrue(z["can_send"] and z["paid"])
		self.assertTrue(z["can_request_phone"])
		frappe.db.set_value("CRM Lead", self.lead1, "mobile_no", "0912345678")
		z = inbox.compose_state(self.lead1)["zalo"]
		self.assertFalse(z["can_request_phone"])
		self.assertTrue(z["has_phone"])


class TestRequestPhone(OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.sandbox(True)

	def call(self):
		with mock.patch("frappe.enqueue"):
			res = inbox.request_phone(self.lead1)
			frappe.db.commit()
		return res

	def test_sets_phone_requested_at_and_records_outbound(self):
		self.as_user(SALE1)
		res = self.call()
		self.assertEqual(res["status"], "Sent")
		self.assertTrue(frappe.db.get_value("Omni Identity", self.ident1, "phone_requested_at"))
		r = self.row(res["outbound"])
		self.assertEqual((r.status, r.is_sandbox, r.sent_by), ("Sent", 1, SALE1))

	def test_second_request_within_seven_days_rejected(self):
		self.as_user(SALE1)
		self.call()
		with self.assertRaises(frappe.ValidationError):
			self.call()
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1}), 1)

	def test_after_seven_days_allowed_again(self):
		self.as_user(SALE1)
		self.call()
		frappe.db.set_value(
			"Omni Identity", self.ident1, "phone_requested_at", add_to_date(now_datetime(), days=-8)
		)
		frappe.db.commit()
		self.assertEqual(self.call()["status"], "Sent")

	def test_paid_window_needs_confirmation(self):
		self.set_last_inbound(hours_ago=72)
		self.as_user(SALE1)
		res = self.call()
		self.assertEqual(res["status"], "confirm_required")
		self.assertIn("tính phí", res["warning"])
		self.assertIsNone(frappe.db.get_value("Omni Identity", self.ident1, "phone_requested_at"))
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1}), 0)
		with mock.patch("frappe.enqueue"):
			res = inbox.request_phone(self.lead1, confirm_paid=1)
			frappe.db.commit()
		self.assertEqual(res["status"], "Sent")

	def test_window_closed_rejected(self):
		self.set_last_inbound(hours_ago=24 * 9)
		self.as_user(SALE1)
		with self.assertRaises(frappe.ValidationError):
			self.call()
		self.assertIsNone(frappe.db.get_value("Omni Identity", self.ident1, "phone_requested_at"))

	def test_real_send_drops_echo_duplicate(self):
		self.sandbox(False)
		self.as_user(SALE1)
		echo = frappe.get_doc(
			{
				"doctype": OUT,
				"channel": self.channel.name,
				"identity": self.ident1,
				"lead": self.lead1,
				"text": "echo",
				"source": "Sale",
				"status": "Sent",
				"external_message_id": "zl-info-1",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		with mock.patch.object(ZaloAdapter, "request_user_info", return_value=("zl-info-1", "yêu cầu")):
			res = self.call()
		self.assertFalse(frappe.db.exists(OUT, echo.name))
		self.assertEqual(self.row(res["outbound"]).external_message_id, "zl-info-1")

	def test_lead_with_phone_rejected(self):
		frappe.db.set_value("CRM Lead", self.lead1, "mobile_no", "0912345678")
		frappe.db.commit()
		self.as_user(SALE1)
		with self.assertRaises(frappe.ValidationError):
			self.call()

	def test_permission_denied(self):
		self.as_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			self.call()

	def test_failure_restores_phone_requested_at(self):
		self.as_user(SALE1)
		with mock.patch.object(ZaloAdapter, "request_user_info", side_effect=RuntimeError("0912345678")):
			with self.assertRaises(frappe.ValidationError):
				self.call()
		self.assertIsNone(frappe.db.get_value("Omni Identity", self.ident1, "phone_requested_at"))
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1}), 0)


class TestConversationTurns(OutboundTestCase):
	def test_mixed_channels_in_order(self):
		# setUp có sẵn: zalo1 (30 phút trước), mail1 (20 phút trước)
		self.make_outbound(self.lead1, "Dạ báo giá Zalo", 15)
		self.make_inbound(self.lead1, self.ident1, "Zalo OA", "sale trả lời ngoài", 10, "oa_echo")
		self.make_inbound(self.lead1, self.ident1, "Zalo OA", "khách hỏi tiếp", 5)
		self.make_outbound(self.lead1, "tin bị chặn", 4, status="Blocked")
		frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": "Re: báo giá",
				"content": "<p>Thư <b>báo giá</b> gửi khách</p>",
				"communication_date": add_to_date(now_datetime(), minutes=-18),
				"reference_doctype": "CRM Lead",
				"reference_name": self.lead1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		turns = timeline.conversation_turns(self.lead1)
		self.assertEqual(
			[(t["direction"], t["channel_type"], t["text"]) for t in turns],
			[
				("in", "Zalo OA", "hỏi giá máy A"),
				("in", "Email", "thư báo giá"),
				("out", "Email", "Thư báo giá gửi khách"),
				("out", "Zalo OA", "Dạ báo giá Zalo"),
				("out", "Zalo OA", "sale trả lời ngoài"),
				("in", "Zalo OA", "khách hỏi tiếp"),
			],
		)
		self.assertEqual([t["actor"] for t in turns][-3:], ["sale", "external", "customer"])
		until = add_to_date(now_datetime(), minutes=-12)
		self.assertEqual(len(timeline.conversation_turns(self.lead1, until=until)), 4)

	def test_echo_and_outbound_with_same_message_id_counted_once(self):
		out = self.make_outbound(self.lead1, "gửi ngoài", 5)
		frappe.db.set_value(OUT, out, "external_message_id", "zl-same")
		echo = self.make_inbound(self.lead1, self.ident1, "Zalo OA", "gửi ngoài", 5, "oa_echo")
		frappe.db.set_value("Omni Inbound Message", echo, "external_message_id", "zl-same")
		frappe.db.commit()
		texts = [t["text"] for t in timeline.conversation_turns(self.lead1)]
		self.assertEqual(texts.count("gửi ngoài"), 1)
