# ruff: noqa: RUF001
"""D3-02: chuyển người, tạm dừng bot, thông báo, email nhắc, câu giữ chân. Mọi lời gọi AI và gửi đều giả."""

import re
from datetime import datetime
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.api import bot_control, inbox
from omni_crm.bot import ai_client, handoff, reply
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_reply import DRAFT, G_HOURS, G_PRICE, KB1, reply_ok
from omni_crm.tests.test_draft import TEXT, DraftTestCase
from omni_crm.tests.test_inbox import SALE1, SALE2
from omni_crm.utils import business_hours

SECRET = "SECRETTEXT9"
REASON = "Khách hỏi SECRETREASON7"
FRI_EVENING = datetime(2026, 10, 30, 18, 0)  # thứ Sáu 18:00
SAT_NOON = datetime(2026, 10, 31, 13, 0)  # thứ Bảy 13:00 (đóng cửa 12:00)
MON_MORNING = datetime(2026, 11, 2, 9, 0)


def fixed_hours():
	"""Giờ làm việc mặc định, không phụ thuộc cấu hình site."""
	return (
		mock.patch.object(business_hours, "_hours", return_value=business_hours.parse_hours(None)),
		mock.patch.object(business_hours, "_holidays", return_value=frozenset()),
	)


class HandoffTestCase(DraftTestCase):
	def setUp(self):
		super().setUp()
		self._orig_fallback = frappe.db.get_single_value("Omni Settings", "callback_fallback_user")
		self.addCleanup(self._clean_d302)

	def _clean_d302(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.delete(
			"Comment",
			{
				"reference_name": ["in", [self.lead1, self.lead2]],
				"comment_by": ["in", ["Omni CRM", "Sale Một"]],
			},
		)
		frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
		set_settings(
			handoff_notify_channels="system",
			handoff_pause_hours=24,
			handoff_message_in_hours=None,
			handoff_message_after_hours=None,
			bot_active_hours=None,
			callback_fallback_user=self._orig_fallback,
		)
		frappe.db.commit()

	def handoff_draft(self, text=None, reason=REASON):
		return self.make_draft(
			text or f"Cho hỏi giá {SECRET}",
			reply_ok(action="handoff", question_group=G_PRICE, kb_item_ids=[], handoff_reason=reason),
		)

	def paused(self):
		return frappe.db.get_value("Omni Identity", self.ident1, "bot_paused_until")

	def logs(self):
		return frappe.get_all(
			"Notification Log",
			filters={"document_name": self.lead1},
			fields=["name", "for_user", "subject", "email_content", "read"],
		)

	def age_notice(self, draft, minutes=11):
		frappe.db.set_value(
			DRAFT, draft, "handoff_notified_at", add_to_date(now_datetime(), minutes=-minutes)
		)
		frappe.db.commit()


class TestPauseAndNotify(HandoffTestCase):
	def test_handoff_pauses_for_configured_hours(self):
		set_settings(handoff_pause_hours=6)
		before = now_datetime()
		self.handoff_draft()
		delta = frappe.utils.get_datetime(self.paused()) - before
		self.assertAlmostEqual(delta.total_seconds(), 6 * 3600, delta=120)

	def test_default_pause_is_24_hours_when_unset(self):
		self.assertEqual(handoff.pause_hours(), 24)
		with mock.patch.object(handoff, "_setting", return_value=None):
			self.assertEqual(handoff.pause_hours(), 24)

	def test_shadow_handoff_does_not_pause_or_notify(self):
		set_settings(bot_reply_mode="Shadow")
		batch = self.say("Cho hỏi giá")
		self.fake.add_rule(reply_ok(action="handoff", question_group=G_PRICE, kb_item_ids=[]), kind="Reply")
		reply.reply_batch(batch)
		self.assertIsNone(self.paused())
		self.assertEqual(self.logs(), [])

	def test_one_handoff_one_notification_without_customer_text(self):
		name = self.handoff_draft()
		(log,) = self.logs()
		self.assertEqual(log.for_user, SALE1)
		blob = f"{log.subject} {log.email_content}"
		self.assertIn("omni-inbox", blob)
		self.assertIn(self.lead1, log.subject)
		self.assertNotIn(SECRET, blob)
		self.assertNotIn("SECRETREASON", blob)
		self.assertEqual(handoff.handoff(name)["status"], "already")  # gọi lại không báo trùng
		self.assertEqual(len(self.logs()), 1)

	def test_reason_recorded_on_lead_timeline(self):
		self.handoff_draft()
		notes = frappe.get_all(
			"Comment",
			filters={"reference_name": self.lead1, "content": ["like", "%Bot chuyển người%"]},
			pluck="content",
		)
		self.assertEqual(len(notes), 1)
		self.assertIn("SECRETREASON7", notes[0])  # lý do đầy đủ chỉ ở dòng thời gian (cần quyền đọc Lead)
		self.assertIn("tạm dừng đến", notes[0])

	def test_no_owner_falls_back_to_callback_user(self):
		frappe.db.set_value("CRM Lead", self.lead1, {"lead_owner": None, "_assign": None})
		set_settings(callback_fallback_user=SALE2)
		self.assertEqual(handoff.recipients(self.lead1), [SALE2])
		self.handoff_draft()
		self.assertEqual([x.for_user for x in self.logs()], [SALE2])

	def test_no_bot_outbound_and_holding_message_not_sent(self):
		with mock.patch.object(handoff, "holding_message") as hm:
			self.handoff_draft()
			hm.assert_not_called()
		self.assertEqual(self.bot_outbound(), 0)
		self.assertEqual(self.out_rows(), [])


class TestPausedBot(HandoffTestCase):
	def test_paused_customer_not_drafted_then_resumes_after_expiry(self):
		self.handoff_draft()
		batch = self.say("Cho hỏi giờ làm việc")
		calls = len(self.fake.calls)
		self.assertEqual(reply.reply_batch(batch), "paused")
		self.assertEqual(len(self.fake.calls), calls)
		self.assertEqual(self.drafts(batch_id=batch), [])
		frappe.db.set_value(
			"Omni Identity", self.ident1, "bot_paused_until", add_to_date(now_datetime(), minutes=-1)
		)
		frappe.db.commit()
		self.fake.add_rule(reply_ok(reply_text=TEXT), kind="Reply")
		self.assertEqual(reply.reply_batch(batch), "drafted")

	def test_pause_covers_all_identities_of_lead(self):
		email_ident = self.make_identity("TD106E9", "Email", self.lead1, "Mai Email")
		self.handoff_draft()
		self.assertTrue(handoff.is_bot_paused(self.ident1))
		self.assertTrue(handoff.is_bot_paused(email_ident))
		total = frappe.db.count("Omni Identity", {"lead": self.lead1})
		self.assertGreaterEqual(total, 2)
		self.assertEqual(handoff.resume_lead(self.lead1), total)
		self.assertFalse(handoff.is_bot_paused(email_ident))


class TestWantsHuman(HandoffTestCase):
	def make(self, **kw):
		batch = self.say("Cho tôi gặp nhân viên")
		return reply.make_draft(
			lead=self.lead1,
			identity=self.ident1,
			channel=self.channel.name,
			channel_type="Zalo OA",
			batch_id=batch,
			customer_text="Cho tôi gặp nhân viên",
			wants_human=True,
			mode="Draft",
			**kw,
		)

	def test_wants_human_goes_through_handoff_even_if_bot_proposes_reply(self):
		self.fake.add_rule(reply_ok(reply_text=TEXT), kind="Reply")
		name = self.make()
		d = self.d(name)
		self.assertEqual(d.action, "Handoff")
		self.assertIsNone(d.draft_text)
		self.assertTrue(self.paused())
		(log,) = self.logs()
		self.assertIn("Khách muốn gặp nhân viên", log.email_content)

	def test_wants_human_with_bot_handoff_has_fixed_label(self):
		self.fake.add_rule(reply_ok(action="handoff", kb_item_ids=[], handoff_reason=REASON), kind="Reply")
		d = self.d(self.make())
		self.assertIn(handoff.FLAG_WANTS_HUMAN, d.guard_flags)

	def test_wants_human_when_ai_fails_still_hands_off(self):
		with mock.patch.object(ai_client, "call_tool", return_value=None):
			name = self.make()
		self.assertEqual(self.d(name).action, "Handoff")
		self.assertTrue(self.paused())
		self.assertEqual(len(self.logs()), 1)

	def test_always_handoff_group_pauses_and_labels_group(self):
		self.fake.add_rule(reply_ok(question_group=G_PRICE, kb_item_ids=[KB1], reply_text=TEXT), kind="Reply")
		batch = self.say("Cho hỏi giá")
		reply.reply_batch(batch)
		self.assertEqual(self.drafts(batch_id=batch)[0].action, "Handoff")
		self.assertTrue(self.paused())
		self.assertIn(G_PRICE, self.logs()[0].email_content)


class TestEmailFallback(HandoffTestCase):
	def setUp(self):
		super().setUp()
		p = mock.patch("omni_crm.flags.sandbox", return_value=False)
		p.start()
		self.addCleanup(p.stop)
		# site test không có tài khoản email gửi đi: chặn frappe.sendmail và đọc các lần gọi
		sm = mock.patch("frappe.sendmail")
		self.sm = sm.start()
		self.addCleanup(sm.stop)

	def emails(self):
		return [c.kwargs for c in self.sm.call_args_list]

	def test_default_channel_is_system_only_no_email(self):
		name = self.handoff_draft()
		self.age_notice(name)
		handoff.email_unread_handoffs()
		self.assertEqual(self.emails(), [])

	def test_email_only_after_ten_minutes_and_only_if_unread(self):
		set_settings(handoff_notify_channels="system,email")
		name = self.handoff_draft()
		self.assertEqual(handoff.email_unread_handoffs()["checked"], 0)  # chưa đủ 10 phút
		self.age_notice(name, minutes=9)
		handoff.email_unread_handoffs()
		self.assertEqual(self.emails(), [])
		self.age_notice(name, minutes=11)
		res = handoff.email_unread_handoffs()
		self.assertEqual(res["emailed"], 1)
		(mail,) = self.emails()
		self.assertEqual(mail["recipients"], [frappe.db.get_value("User", SALE1, "email")])
		self.assertNotIn("SECRET", mail["message"])  # không lời khách, không lý do do AI viết
		self.assertIn(self.lead1, mail["message"])
		self.assertIn("omni-inbox", mail["message"])
		handoff.email_unread_handoffs()
		self.assertEqual(len(self.emails()), 1)  # mỗi nháp một lần

	def test_no_email_when_notification_already_opened(self):
		set_settings(handoff_notify_channels="system,email")
		name = self.handoff_draft()
		frappe.db.set_value("Notification Log", self.logs()[0].name, "read", 1)
		self.age_notice(name)
		res = handoff.email_unread_handoffs()
		self.assertEqual((res["emailed"], res["skipped"]), (0, 1))
		self.assertEqual(self.emails(), [])

	def test_no_email_when_sale_already_decided_draft(self):
		set_settings(handoff_notify_channels="system,email")
		name = self.handoff_draft()
		self.call(__import__("omni_crm.api.drafts", fromlist=["x"]).discard_draft, name)
		self.age_notice(name)
		handoff.email_unread_handoffs()
		self.assertEqual(self.emails(), [])

	def test_sandbox_never_sends_email(self):
		set_settings(handoff_notify_channels="system,email")
		name = self.handoff_draft()
		self.age_notice(name)
		with mock.patch("omni_crm.flags.sandbox", return_value=True):
			handoff.email_unread_handoffs()
		self.sm.assert_not_called()
		self.assertEqual(frappe.db.get_value(DRAFT, name, "handoff_email_done"), 1)

	def test_email_only_channel_sends_without_system_log(self):
		set_settings(handoff_notify_channels="email")
		self.handoff_draft()
		self.assertEqual(self.logs(), [])
		self.assertEqual(len(self.emails()), 1)

	def test_job_does_not_call_external_api(self):
		set_settings(handoff_notify_channels="system,email")
		name = self.handoff_draft()
		self.age_notice(name)
		with (
			mock.patch.object(ai_client, "call_tool", side_effect=AssertionError),
			mock.patch("requests.sessions.Session.request", side_effect=AssertionError("mạng")),
		):
			handoff.email_unread_handoffs()


class TestHoldingMessage(HandoffTestCase):
	def run_at(self, when):
		a, b = fixed_hours()
		with a, b:
			return handoff.holding_message(when)

	def test_in_hours_uses_in_hours_template(self):
		text = self.run_at(MON_MORNING)
		self.assertIn("Cảm ơn anh/chị", text)
		self.assertNotIn("{", text)
		self.assertNotIn("08:00", text)

	def test_friday_evening_names_saturday_opening(self):
		text = self.run_at(FRI_EVENING)
		self.assertIn("08:00 ngày 31/10/2026", text)
		self.assertNotIn("{", text)

	def test_saturday_afternoon_names_monday_opening(self):
		text = self.run_at(SAT_NOON)
		self.assertIn("08:00 ngày 02/11/2026", text)

	def test_custom_templates_and_placeholder(self):
		set_settings(
			handoff_message_after_hours="Dạ em ghi nhận, hẹn {gio_lam_viec_ke_tiep} ạ",
			handoff_message_in_hours="Dạ đợi chút ạ",
		)
		self.assertEqual(self.run_at(MON_MORNING), "Dạ đợi chút ạ")
		self.assertEqual(self.run_at(FRI_EVENING), "Dạ em ghi nhận, hẹn 08:00 ngày 31/10/2026 ạ")

	def test_default_texts_promise_no_price_or_specific_time(self):
		for when in (MON_MORNING, FRI_EVENING, SAT_NOON):
			text = self.run_at(when)
			self.assertFalse(re.search(r"\d+\s*(phút|giờ|đ|k|triệu|%)", text), text)
			self.assertNotIn("giá", text.lower())

	def test_bot_active_hours_default_follows_business_hours(self):
		a, b = fixed_hours()
		with a, b:
			set_settings(bot_active_hours="")
			self.assertTrue(handoff.bot_active_now(MON_MORNING))
			self.assertFalse(handoff.bot_active_now(FRI_EVENING))
			set_settings(bot_active_hours='{"mon-sun":"00:00-23:00"}')
			self.assertTrue(handoff.bot_active_now(FRI_EVENING))
		set_settings(bot_active_hours="")


class TestSettingsValidation(HandoffTestCase):
	def test_invalid_channel_and_json_rejected(self):
		s = frappe.get_doc("Omni Settings")
		s.handoff_notify_channels = "system,sms"
		self.assertRaises(frappe.ValidationError, s.validate)
		s.handoff_notify_channels = "system"
		s.bot_active_hours = "{khong json"
		self.assertRaises(frappe.ValidationError, s.validate)
		s.bot_active_hours = ""
		s.handoff_pause_hours = -1
		self.assertRaises(frappe.ValidationError, s.validate)


class TestManualControls(HandoffTestCase):
	def test_resume_clears_pause_and_logs_who(self):
		self.handoff_draft()
		self.assertTrue(self.paused())
		res = self.call(bot_control.resume_bot, self.lead1)
		self.assertEqual(res["status"], "Resumed")
		self.assertIsNone(self.paused())
		who = frappe.db.get_value("User", SALE1, "full_name") or SALE1
		notes = frappe.get_all(
			"Comment",
			filters={"reference_name": self.lead1, "content": ["like", "%cho bot tiếp tục%"]},
			fields=["content", "comment_email"],
		)
		self.assertEqual(len(notes), 1)
		self.assertIn(who, notes[0].content)
		self.assertEqual(notes[0].comment_email, SALE1)
		batch = self.say("Cho hỏi giờ làm việc nữa")
		self.fake.add_rule(reply_ok(reply_text=TEXT), kind="Reply")
		self.assertEqual(reply.reply_batch(batch), "drafted")

	def test_manual_pause_sets_until_and_logs(self):
		res = self.call(bot_control.pause_bot, self.lead1)
		self.assertEqual(res["status"], "Paused")
		self.assertTrue(handoff.is_bot_paused(self.ident1))
		self.assertEqual(
			len(
				frappe.get_all(
					"Comment", filters={"reference_name": self.lead1, "content": ["like", "%tạm dừng bot%"]}
				)
			),
			1,
		)

	def test_requires_write_permission_on_lead(self):
		self.handoff_draft()
		for fn in (bot_control.pause_bot, bot_control.resume_bot):
			with self.assertRaises(frappe.PermissionError):
				self.call(fn, self.lead1, user=SALE2)
		self.assertTrue(handoff.is_bot_paused(self.ident1))

	def test_thread_exposes_pause_state(self):
		self.handoff_draft()
		frappe.set_user(SALE1)
		bot = inbox.get_thread(self.lead1)["bot"]
		frappe.set_user("Administrator")
		self.assertTrue(bot["paused_until"])
		self.assertTrue(bot["can_control"])
		self.call(bot_control.resume_bot, self.lead1)
		frappe.set_user(SALE1)
		self.assertIsNone(inbox.get_thread(self.lead1)["bot"]["paused_until"])
		frappe.set_user("Administrator")
