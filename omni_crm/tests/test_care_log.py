# ruff: noqa: RUF001
"""V2-04 (docs/13 mục 3.2, 3.3): Omni Care Log — phiên, kết quả, việc trong CRM, phản hồi lần đầu, gộp Lead, xóa dữ liệu."""

from datetime import datetime, timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import admin, customer
from omni_crm.crm_bridge import care_log, first_response
from omni_crm.identity.merge import merge_leads
from omni_crm.tests.bridge_helpers import BridgeTestCase
from omni_crm.tests.fake_adapter import make_channel
from omni_crm.tests.test_prospect_model import make_lead, make_user

LOG = "Omni Care Log"
TASK = "CRM Task"
SALE1 = "td404_sale1@example.com"
SALE2 = "td404_sale2@example.com"
T0 = datetime(2026, 11, 4, 9, 0)  # thứ tư


class CareCase(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("td404_care")
		self.addCleanup(frappe.clear_document_cache, "Omni Settings", "Omni Settings")
		self.addCleanup(frappe.db.rollback, save_point="td404_care")
		self.addCleanup(frappe.set_user, "Administrator")
		make_user(SALE1)
		make_user(SALE2)
		self.lead = make_lead("TD404 Hoa", owner=SALE1)
		frappe.db.set_value("CRM Lead", self.lead, "status", "Tiềm năng")
		self.channel = make_channel("TD404 kenh").name

	def logs(self, lead=None, **filters):
		return frappe.get_all(
			LOG,
			filters={"lead": lead or self.lead, **filters},
			fields=["*"],
			order_by="started_at asc",
		)

	def message(
		self, minutes: float, text="xin chào", channel_type="Zalo OA", event_type="message", status="Done"
	):
		return frappe._dict(
			event_type=event_type,
			channel_type=channel_type,
			received_at=T0 + timedelta(minutes=minutes),
			text=text,
			status=status,
		)


class TestSessions(CareCase):
	def test_three_messages_within_one_hour_make_one_session(self):
		care_log.on_inbound_batch(
			self.lead, [self.message(0, "một"), self.message(20, "hai"), self.message(55, "ba")]
		)
		rows = self.logs()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].channel, "Zalo OA")
		self.assertEqual(rows[0].handled_kind, "Khách")
		self.assertEqual(rows[0].started_at, T0)
		self.assertEqual(rows[0].last_activity_at, T0 + timedelta(minutes=55))
		self.assertEqual(rows[0].summary, "một")  # tin khách ĐẦU phiên
		self.assertTrue(rows[0].auto_created)

	def test_message_after_five_hours_opens_new_session(self):
		care_log.on_inbound_batch(self.lead, [self.message(0), self.message(5 * 60)])
		self.assertEqual(len(self.logs()), 2)

	def test_gap_comes_from_settings_and_zero_falls_back_to_four_hours(self):
		for stored, expected in (("2", 2), ("0", 4), ("", 4)):
			frappe.db.sql(
				"delete from `tabSingles` where doctype='Omni Settings' and field='care_session_gap_hours'"
			)
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','care_session_gap_hours',%s)",
				(stored,),
			)
			self.assertEqual(care_log._gap(), timedelta(hours=expected), stored)
		frappe.db.sql(
			"delete from `tabSingles` where doctype='Omni Settings' and field='care_session_gap_hours'"
		)

	def test_summary_is_first_200_chars_of_first_customer_text(self):
		care_log.on_inbound_batch(self.lead, [self.message(0, "x" * 500)])
		self.assertEqual(self.logs()[0].summary, "x" * 200)

	def test_call_is_always_its_own_session(self):
		care_log.on_inbound_batch(self.lead, [self.message(0)])
		call = frappe.get_doc(
			{
				"doctype": "Omni Call",
				"provider": "Stringee",
				"call_id": "TD404-1",
				"direction": "Inbound",
				"status": "Missed",
				"from_number": "+84900000001",
				"to_number": "+84900000002",
				"started_at": T0 + timedelta(minutes=2),
				"lead": self.lead,
			}
		).insert(ignore_permissions=True)
		care_log.on_call_recorded(call, self.lead)
		care_log.on_call_recorded(call, self.lead)  # chạy lại cùng cuộc gọi: vẫn một phiên
		rows = self.logs()
		self.assertEqual(len(rows), 2)
		phone = [r for r in rows if r.call == call.name]
		self.assertEqual(len(phone), 1)
		self.assertEqual(phone[0].channel, "Điện thoại")

	def test_sandbox_system_follow_and_ignored_do_not_create_sessions(self):
		sale_row = frappe._dict(lead=self.lead, source="Sale", channel=None, sent_by=SALE1)
		care_log.on_outbound_sent(sale_row, T0, True)  # sandbox
		care_log.on_outbound_sent({**sale_row, "source": "System"}, T0, False)  # câu giữ chân
		care_log.on_inbound_batch(
			self.lead,
			[
				self.message(0, event_type="follow"),
				self.message(1, event_type="unfollow"),
				self.message(2, status="Ignored"),
				self.message(3, channel_type="Telephony"),
			],
		)
		self.assertEqual(self.logs(), [])

	def test_real_sale_reply_sets_handler_and_extends_session(self):
		care_log.on_inbound_batch(self.lead, [self.message(0)])
		care_log.on_outbound_sent(
			frappe._dict(lead=self.lead, source="Sale", channel=self.channel, sent_by=SALE1),
			T0 + timedelta(minutes=10),
			False,
		)
		rows = self.logs()
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].handled_kind, rows[0].handled_by), ("Sale", SALE1))
		# Bot trả lời sau khi sale đã xử lý không hạ cấp người xử lý
		care_log.on_outbound_sent(
			frappe._dict(lead=self.lead, source="Bot", channel=self.channel, sent_by=None),
			T0 + timedelta(minutes=12),
			False,
		)
		self.assertEqual(self.logs()[0].handled_kind, "Sale")

	def test_switch_off_creates_nothing_and_does_not_error(self):
		frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field='care_log_enabled'")
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','care_log_enabled','0')"
		)
		self.addCleanup(
			frappe.db.sql,
			"delete from `tabSingles` where doctype='Omni Settings' and field='care_log_enabled'",
		)
		care_log.on_inbound_batch(self.lead, [self.message(0)])
		self.assertIsNone(care_log.touch_session(self.lead, "Zalo OA", T0, "Khách"))
		self.assertEqual(self.logs(), [])

	def test_error_in_session_code_never_breaks_the_batch(self):
		with mock.patch.object(care_log, "touch_session", side_effect=RuntimeError("hỏng")):
			care_log.on_inbound_batch(self.lead, [self.message(0)])  # không ném lỗi


class TestWorkerIntegration(BridgeTestCase):
	"""Qua worker thật: tin Zalo vào -> một phiên Care Log; dữ liệu được commit nên dọn tay."""

	def setUp(self):
		super().setUp()
		self.addCleanup(self._purge)  # LIFO: chạy trước phần dọn của BridgeTestCase

	@staticmethod
	def _purge():
		frappe.db.rollback()
		leads = frappe.get_all("CRM Lead", filters={"first_name": ["like", "TD106%"]}, pluck="name")
		leads += frappe.get_all("Omni Identity", filters={"identity_key": ["like", "%:TD106%"]}, pluck="lead")
		frappe.db.delete(LOG, {"lead": ["in", [x for x in leads if x] or [""]]})
		frappe.db.commit()

	def test_messages_through_worker_make_one_session(self):
		msg = self.send("TD404-W1", text="Cần báo giá găng tay y tế")
		lead = frappe.db.get_value("Omni Inbound Message", msg, "lead")
		self.send("TD404-W2", text="Số lượng 100 hộp")
		rows = frappe.get_all(LOG, filters={"lead": lead}, fields=["channel", "summary", "handled_kind"])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].channel, "Zalo OA")
		self.assertEqual(rows[0].summary, "Cần báo giá găng tay y tế")
		self.assertEqual(rows[0].handled_kind, "Khách")
		self.assertIsNone(
			frappe.db.get_value("CRM Lead", lead, first_response.FIELD)
		)  # tin khách không phải phản hồi

	def test_follow_event_through_worker_makes_no_session(self):
		msg = self.send("TD404-F1", event_type="follow", text="")
		lead = frappe.db.get_value("Omni Inbound Message", msg, "lead")
		self.assertEqual(frappe.db.count(LOG, {"lead": lead}) if lead else 0, 0)


class TestSaveOutcome(CareCase):
	def session(self, minutes=0, kind="Khách"):
		return care_log.touch_session(
			self.lead, "Zalo OA", T0 + timedelta(minutes=minutes), kind, None, None, "hỏi giá"
		)

	def test_call_back_without_next_step_is_rejected_with_clear_error(self):
		log = self.session()
		with self.assertRaises(frappe.ValidationError) as ctx:
			care_log.save_outcome(log, "Hẹn gọi lại", user=SALE1)
		self.assertIn("bước tiếp theo", str(ctx.exception))
		self.assertEqual(frappe.db.count(TASK, {"title": ["like", "TD404%"]}), 0)
		self.assertFalse(frappe.db.get_value(LOG, log, "outcome"))

	def test_unknown_outcome_and_action_without_due_are_rejected(self):
		log = self.session()
		with self.assertRaises(frappe.ValidationError):
			care_log.save_outcome(log, "Linh tinh", user=SALE1)
		with self.assertRaises(frappe.ValidationError):
			care_log.save_outcome(log, "Đã tư vấn", next_action="TD404 gọi", user=SALE1)  # thiếu hạn

	def test_closed_deal_and_no_need_do_not_require_next_step(self):
		for outcome in care_log.NO_NEXT_OUTCOMES:
			res = care_log.save_outcome(self.session(len(outcome)), outcome, user=SALE1)
			self.assertIsNone(res["task"])
		log = self.session(600)
		self.assertTrue(care_log.save_outcome(log, "Đã chốt đơn", user=SALE1)["suggest_deal"])

	def test_next_step_with_due_creates_exactly_one_task_for_lead_owner(self):
		log = self.session()
		due = T0 + timedelta(days=1)
		res = care_log.save_outcome(
			log, "Hẹn gọi lại", next_action="TD404 gọi lại báo giá", next_due=due, user=SALE2
		)
		task = frappe.get_doc(TASK, res["task"])
		self.assertEqual(task.assigned_to, SALE1)  # người phụ trách Lead, không phải người ghi
		self.assertEqual((task.reference_doctype, task.reference_docname), ("CRM Lead", self.lead))
		self.assertEqual(task.title, "TD404 gọi lại báo giá")
		self.assertEqual(frappe.utils.get_datetime(task.due_date), due)
		row = frappe.db.get_value(
			LOG, log, ["crm_task", "outcome_by", "outcome_at", "next_due"], as_dict=True
		)
		self.assertEqual((str(row.crm_task), row.outcome_by), (str(task.name), SALE2))
		self.assertIsNotNone(row.outcome_at)

	def test_changing_due_updates_same_task_not_a_second_one(self):
		log = self.session()
		first = care_log.save_outcome(
			log, "Hẹn gọi lại", next_action="TD404 gọi", next_due=T0 + timedelta(days=1), user=SALE1
		)
		later = T0 + timedelta(days=3)
		second = care_log.save_outcome(
			log, "Hẹn gọi lại", next_action="TD404 gọi lần 2", next_due=later, user=SALE1
		)
		self.assertEqual(first["task"], second["task"])
		self.assertEqual(frappe.db.count(TASK, {"reference_docname": self.lead}), 1)
		task = frappe.get_doc(TASK, first["task"])
		self.assertEqual((task.title, frappe.utils.get_datetime(task.due_date)), ("TD404 gọi lần 2", later))

	def test_create_task_unchecked_makes_no_task(self):
		res = care_log.save_outcome(
			self.session(),
			"Chờ khách phản hồi",
			next_action="TD404 chờ",
			next_due=T0 + timedelta(days=1),
			create_task=0,
			user=SALE1,
		)
		self.assertIsNone(res["task"])
		self.assertEqual(frappe.db.count(TASK, {"reference_docname": self.lead}), 0)

	def test_lead_moves_to_contacted_only_from_first_stage_and_not_when_unreachable(self):
		res = care_log.save_outcome(
			self.session(),
			"Hẹn gọi lại",
			next_action="TD404 gọi",
			next_due=T0 + timedelta(days=1),
			user=SALE1,
		)
		self.assertTrue(res["stage_changed"])
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "status"), "Đã liên hệ")
		# Đã ở giai đoạn sau thì không bị kéo lùi
		frappe.db.set_value("CRM Lead", self.lead, "status", "Đã trao đổi nhu cầu")
		care_log.save_outcome(
			self.session(900), "Đã tư vấn", next_action="TD404 a", next_due=T0 + timedelta(days=2), user=SALE1
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "status"), "Đã trao đổi nhu cầu")
		# Không liên lạc được thì chưa phải "đã liên hệ"
		other = make_lead("TD404 Khong", owner=SALE1)
		frappe.db.set_value("CRM Lead", other, "status", "Tiềm năng")
		log = care_log.touch_session(other, "Điện thoại", T0, "Sale", SALE1)
		care_log.save_outcome(
			log,
			"Không liên lạc được",
			next_action="TD404 thử lại",
			next_due=T0 + timedelta(days=1),
			user=SALE1,
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", other, "status"), "Tiềm năng")


class TestFirstResponseFromCareLog(CareCase):
	def at(self):
		return frappe.db.get_value("CRM Lead", self.lead, first_response.FIELD)

	def test_inbound_only_session_does_not_count_as_a_response(self):
		care_log.on_inbound_batch(self.lead, [self.message(0)])
		self.assertIsNone(self.at())  # nếu tính, phản hồi lần đầu = lúc khách nhắn (chỉ số về 0)

	def test_sale_recorded_log_is_first_response_at_contact_time(self):
		care_log.on_inbound_batch(self.lead, [self.message(0)])
		log = self.logs()[0].name
		care_log.save_outcome(
			log,
			"Đã tư vấn",
			next_action="TD404 x",
			next_due=T0 + timedelta(days=1),
			user=SALE1,
			at=T0 + timedelta(minutes=7),
		)
		self.assertEqual(self.at(), T0 + timedelta(minutes=7))

	def test_earliest_response_is_kept(self):
		first_response.mark(self.lead, T0 + timedelta(minutes=3))
		log = care_log.touch_session(self.lead, "Zalo OA", T0, "Khách", None, None, "a")
		care_log.save_outcome(
			log,
			"Đã tư vấn",
			next_action="TD404 x",
			next_due=T0 + timedelta(days=1),
			user=SALE1,
			at=T0 + timedelta(minutes=30),
		)
		self.assertEqual(self.at(), T0 + timedelta(minutes=3))

	def test_backfill_counts_sale_recorded_logs_only(self):
		care_log.on_inbound_batch(self.lead, [self.message(0)])
		self.assertNotIn(self.lead, first_response.earliest_responses())
		manual = care_log.create_manual_log(
			self.lead, "Gặp trực tiếp", T0 + timedelta(minutes=45), "gặp", SALE1
		)
		care_log.save_outcome(
			manual,
			"Đã tư vấn",
			next_action="TD404 x",
			next_due=T0 + timedelta(days=1),
			user=SALE1,
			at=T0 + timedelta(minutes=45),
		)
		self.assertEqual(first_response.earliest_responses()[self.lead], T0 + timedelta(minutes=45))


class TestApiAndPermissions(CareCase):
	def test_unrecorded_label_needs_sale_session_silent_for_two_hours(self):
		now = now_datetime()
		old = care_log.touch_session(self.lead, "Zalo OA", now - timedelta(hours=3), "Sale", SALE1)
		self.assertEqual(care_log.unrecorded_by_lead([self.lead]), {self.lead: old})
		care_log.save_outcome(
			old, "Đã tư vấn", next_action="TD404 x", next_due=now + timedelta(days=1), user=SALE1
		)
		self.assertEqual(care_log.unrecorded_by_lead([self.lead]), {})
		other = make_lead("TD404 Moi", owner=SALE1)
		care_log.touch_session(other, "Zalo OA", now - timedelta(minutes=30), "Sale", SALE1)  # mới 30 phút
		care_log.touch_session(
			other, "Email", now - timedelta(hours=5), "Khách", None, None, "k"
		)  # khách, không phải sale
		self.assertEqual(care_log.unrecorded_by_lead([other]), {})

	def test_digest_sends_one_notification_per_sale_and_skips_non_working_day(self):
		now = now_datetime()
		for _ in range(2):
			lead = make_lead("TD404 D", owner=SALE1)
			care_log.touch_session(lead, "Zalo OA", now - timedelta(hours=3), "Sale", SALE1)
		before = frappe.db.count("Notification Log", {"for_user": SALE1})
		with mock.patch("omni_crm.utils.business_hours.is_working_day", return_value=False):
			self.assertEqual(care_log.end_of_day_digest(), {})
		with mock.patch("omni_crm.utils.business_hours.is_working_day", return_value=True):
			sent = care_log.end_of_day_digest()
		self.assertEqual(sent.get(SALE1), 2)
		self.assertEqual(
			frappe.db.count("Notification Log", {"for_user": SALE1}), before + 1
		)  # gộp, không báo từng phiên

	def test_sales_user_sees_only_care_logs_of_own_leads(self):
		mine = care_log.touch_session(self.lead, "Zalo OA", T0, "Khách", None, None, "của tôi")
		other = make_lead("TD404 Khac", owner=SALE2)
		theirs = care_log.touch_session(other, "Zalo OA", T0, "Khách", None, None, "của người khác")
		frappe.set_user(SALE1)
		names = frappe.get_list(LOG, pluck="name")
		self.assertIn(mine, names)
		self.assertNotIn(theirs, names)
		self.assertTrue(frappe.has_permission(LOG, "read", doc=mine))
		self.assertFalse(frappe.has_permission(LOG, "read", doc=theirs))
		with self.assertRaises(frappe.PermissionError):
			customer.get_care_logs(other)
		with self.assertRaises(frappe.PermissionError):
			customer.save_care_log(other, "Đã tư vấn", next_action="x", next_due=str(T0))

	def test_sales_user_has_no_direct_write_to_system_fields(self):
		log = care_log.touch_session(self.lead, "Zalo OA", T0, "Khách", None, None, "a")
		frappe.set_user(SALE1)
		self.assertFalse(frappe.has_permission(LOG, "write", doc=log))  # chỉ ghi qua API

	def test_save_care_log_api_creates_manual_log_and_marks_response(self):
		frappe.set_user(SALE1)
		res = customer.save_care_log(
			self.lead,
			"Hẹn gọi lại",
			channel="Điện thoại",
			at=str(T0),
			summary="Gọi giới thiệu, hẹn gọi lại",
			next_action="TD404 gọi lại",
			next_due=str(T0 + timedelta(days=1)),
		)
		row = frappe.db.get_value(
			LOG, res["log"], ["auto_created", "channel", "handled_by", "summary"], as_dict=True
		)
		self.assertEqual((row.auto_created, row.channel, row.handled_by), (0, "Điện thoại", SALE1))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, first_response.FIELD), T0)
		self.assertTrue(res["task"])
		# kết quả sai không để lại phiên rỗng
		before = frappe.db.count(LOG, {"lead": self.lead})
		with self.assertRaises(frappe.ValidationError):
			customer.save_care_log(self.lead, "Hẹn gọi lại", channel="Khác")
		self.assertEqual(frappe.db.count(LOG, {"lead": self.lead}), before)


class TestMergeAndErase(CareCase):
	def test_merge_leads_moves_care_logs(self):
		secondary = make_lead("TD404 Phu", owner=SALE1)
		keep = care_log.touch_session(secondary, "Zalo OA", T0, "Khách", None, None, "từ Lead phụ")
		merge_leads(self.lead, secondary, "Administrator")
		self.assertEqual(frappe.db.get_value(LOG, keep, "lead"), self.lead)

	def test_erase_customer_data_clears_summary_keeps_outcome(self):
		log = care_log.touch_session(self.lead, "Zalo OA", T0, "Khách", None, None, "Số CCCD 0123 của khách")
		care_log.save_outcome(
			log, "Đã tư vấn", next_action="TD404 a", next_due=T0 + timedelta(days=1), user=SALE1
		)
		frappe.set_user("Administrator")
		res = admin.erase_customer_data(self.lead, "Khách yêu cầu xóa dữ liệu")
		self.assertEqual(res["care_logs"], 1)
		row = frappe.db.get_value(LOG, log, ["summary", "outcome"], as_dict=True)
		self.assertFalse(row.summary)
		self.assertEqual(row.outcome, "Đã tư vấn")


class TestSettingsAndSwitches(FrappeTestCase):
	def test_first_save_of_settings_keeps_care_switches_on(self):
		"""Single không áp default JSON: lần lưu đầu từng ghi 0 làm tắt Dashboard âm thầm (đã gặp trên omni.test)."""
		from omni_crm import flags

		fields = tuple(flags.CARE_DEFAULTS)
		try:
			frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field in %s", (fields,))
			frappe.get_doc("Omni Settings").save()
			self.assertTrue(flags.dashboard_enabled())
			self.assertTrue(flags.care_log_enabled())
			self.assertEqual(flags.care_setting("response_sla_minutes"), 15)
			self.assertEqual(flags.care_setting("care_session_gap_hours"), 4)
			self.assertEqual(flags.care_setting("team_lead_role"), "Sales Manager")
		finally:
			frappe.db.rollback()
			frappe.clear_document_cache("Omni Settings", "Omni Settings")

	def test_care_defaults_when_never_saved_or_zero(self):
		from omni_crm import flags

		for field in flags.CARE_DEFAULTS:
			frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field=%s", field)
		try:
			for field, default in flags.CARE_DEFAULTS.items():
				self.assertEqual(flags.care_setting(field), default, field)
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','response_sla_minutes','0')"
			)
			self.assertEqual(flags.care_setting("response_sla_minutes"), 15)  # 0 không có nghĩa (SLA 0 phút)
			frappe.db.sql(
				"update `tabSingles` set value='30' where doctype='Omni Settings' and field='response_sla_minutes'"
			)
			self.assertEqual(flags.care_setting("response_sla_minutes"), 30)
		finally:
			frappe.db.rollback()
