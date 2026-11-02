"""V2-01 (02.25, 13.08): `CRM Lead.omni_first_response_at` và chỉ số "Phản hồi lần đầu" đo theo trường này."""

from datetime import datetime, timedelta
from unittest import mock

import frappe

from omni_crm.channels.zalo.adapter import ZaloAdapter
from omni_crm.crm_bridge import first_response
from omni_crm.pipeline import outbound
from omni_crm.tests.acceptance import measure_dot1 as m
from omni_crm.tests.test_outbound import SALE1, OutboundTestCase
from omni_crm.utils.business_hours import business_minutes_between

OUT = "Omni Outbound Message"
FIELD = first_response.FIELD
T0 = datetime(2026, 10, 26, 9, 0)  # thứ hai


class FRCase(OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(self._purge_calls)

	@staticmethod
	def _purge_calls():
		frappe.db.rollback()
		frappe.db.delete("Omni Call", {"call_id": ["like", "FR-%"]})
		frappe.db.commit()

	def at(self, lead=None):
		return frappe.db.get_value("CRM Lead", lead or self.lead1, FIELD)

	def reset(self):
		frappe.db.set_value("CRM Lead", self.lead1, FIELD, None)

	def make_out(self, source="Sale", status="Sent", sandbox=0, sent_at=T0, sent_by=None):
		return frappe.get_doc(
			{
				"doctype": OUT,
				"channel": self.channel.name,
				"identity": self.ident1,
				"lead": self.lead1,
				"text": "Dạ em chào anh",
				"source": source,
				"status": status,
				"is_sandbox": sandbox,
				"sent_at": sent_at,
				"sent_by": sent_by,
			}
		).insert(ignore_permissions=True)

	def make_call(self, direction="Outbound", status="Answered", answered=T0, call_id="FR-1"):
		return frappe.get_doc(
			{
				"doctype": "Omni Call",
				"provider": "Stringee",
				"call_id": call_id,
				"direction": direction,
				"status": status,
				"from_number": "+84900000001",
				"to_number": "+84900000002",
				"started_at": answered - timedelta(seconds=5),
				"answered_at": answered,
				"lead": self.lead1,
			}
		).insert(ignore_permissions=True)

	def make_mail(self, **over):
		doc = {
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": "Sent",
			"subject": "Trả lời",
			"content": "Dạ em chào anh",
			"reference_doctype": "CRM Lead",
			"reference_name": self.lead1,
			"communication_date": T0,
		}
		doc.update(over)
		return frappe.get_doc(doc).insert(ignore_permissions=True)


class TestRecording(FRCase):
	def test_new_lead_has_no_first_response(self):
		self.assertIsNone(self.at())

	def test_real_send_through_pipeline_sets_field(self):
		self.sandbox(False)
		name = self.queue()
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-fr-1"):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		self.assertIsNotNone(self.at())
		self.assertEqual(self.at(), frappe.db.get_value(OUT, name, "sent_at"))

	def test_sandbox_send_does_not_count(self):
		self.sandbox(True)
		name = self.queue()
		self.assertEqual(outbound.send_outbound(name), "Sent")
		self.assertIsNone(self.at())

	def test_prebuilt_sent_rows_such_as_oa_echo_count_but_only_real_sale_or_bot(self):
		self.make_out(sandbox=1)
		self.make_out(status="Queued")
		self.make_out(status="Failed")
		self.make_out(source="System")
		self.assertIsNone(self.at())
		self.make_out(source="Bot", sent_at=T0 + timedelta(minutes=9))
		self.assertEqual(self.at(), T0 + timedelta(minutes=9))

	def test_keeps_earliest_even_if_earlier_message_arrives_later(self):
		self.make_out(sent_at=T0 + timedelta(minutes=30))
		self.make_out(sent_at=T0 + timedelta(minutes=45))
		self.assertEqual(self.at(), T0 + timedelta(minutes=30))
		self.make_out(sent_at=T0 + timedelta(minutes=5))  # echo xử lý muộn nhưng gửi sớm hơn
		self.assertEqual(self.at(), T0 + timedelta(minutes=5))

	def test_email_sent_from_crm_counts_but_omni_timeline_copy_does_not(self):
		out = self.make_out(status="Queued")
		self.make_mail(omni_outbound_message=out.name, communication_date=T0)
		self.assertIsNone(self.at())
		self.make_mail(communication_date=T0 + timedelta(minutes=12))
		self.assertEqual(self.at(), T0 + timedelta(minutes=12))
		self.reset()
		self.make_mail(sent_or_received="Received")
		self.make_mail(communication_type="Automated Message")
		self.assertIsNone(self.at())

	def test_answered_outgoing_call_counts_other_calls_do_not(self):
		self.make_call(direction="Inbound", call_id="FR-2")
		self.make_call(status="Missed", call_id="FR-3")
		self.assertIsNone(self.at())
		call = self.make_call(call_id="FR-4", answered=T0 + timedelta(minutes=3))
		from omni_crm.channels.telephony import worker_hooks

		with (
			mock.patch.object(worker_hooks.calls, "apply_final", return_value=call.name),
			mock.patch.object(worker_hooks.call_log, "upsert_call_log", return_value=None),
		):
			worker_hooks._record(mock.Mock(), mock.Mock(), self.lead1, None)
		self.assertEqual(self.at(), T0 + timedelta(minutes=3))

	def test_merged_lead_is_followed(self):
		frappe.db.set_value("CRM Lead", self.lead2, "omni_merged_into", self.lead1)
		self.assertTrue(first_response.mark(self.lead2, T0))
		self.assertEqual(self.at(), T0)
		self.assertIsNone(self.at(self.lead2))


class TestBackfill(FRCase):
	def test_dry_run_counts_without_writing_then_real_run_is_repeatable(self):
		self.make_out(source="Bot", sent_at=T0 + timedelta(minutes=20))
		self.make_out(sandbox=1, sent_at=T0 + timedelta(minutes=1))
		self.make_mail(communication_date=T0 + timedelta(minutes=10))
		self.make_call(answered=T0 + timedelta(minutes=15), call_id="FR-5")
		self.reset()
		before = first_response.backfill(dry_run=True)
		self.assertGreaterEqual(before["to_set"], 1)
		self.assertIsNone(self.at())  # dry run không ghi
		real = first_response.backfill(dry_run=False)
		self.assertEqual(real["to_set"], before["to_set"])
		self.assertEqual(self.at(), T0 + timedelta(minutes=10))  # sớm nhất trong thư / cuộc gọi / tin Bot
		again = first_response.backfill(dry_run=False)
		self.assertEqual(again["to_set"], 0)  # chạy lại không đổi gì
		self.assertGreaterEqual(again["already_ok"], 1)
		self.assertEqual(self.at(), T0 + timedelta(minutes=10))

	def test_backfill_does_not_raise_a_later_value_or_touch_leads_without_response(self):
		self.make_out(sent_at=T0 + timedelta(minutes=40))
		frappe.db.set_value("CRM Lead", self.lead1, FIELD, T0 + timedelta(minutes=10))
		first_response.backfill(dry_run=False)
		self.assertEqual(self.at(), T0 + timedelta(minutes=10))
		self.assertIsNone(self.at(self.lead2))


class TestMetric(FRCase):
	def test_business_minutes_between_skips_night_sunday_and_holiday(self):
		fri_1700 = datetime(2026, 10, 30, 17, 0)
		mon_0830 = datetime(2026, 11, 2, 8, 30)
		# thứ sáu 17:00-17:30 (30) + thứ bảy 08:00-12:00 (240) + thứ hai 08:00-08:30 (30); đêm và chủ nhật không tính
		self.assertEqual(business_minutes_between(fri_1700, mon_0830), 30 + 240 + 30)
		self.assertEqual(business_minutes_between(mon_0830, mon_0830), 0)
		self.assertEqual(business_minutes_between(mon_0830, mon_0830 - timedelta(hours=1)), 0)
		# thứ hai 02/11 là ngày lễ thì không tính phút nào của ngày đó
		self.assertEqual(
			business_minutes_between(fri_1700, mon_0830, holidays=frozenset({mon_0830.date()})), 30 + 240
		)

	def test_first_response_metric_reads_field_in_business_minutes(self):
		fri = datetime(2026, 10, 30, 17, 0)
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "FR metric", "omni_first_channel": "Zalo OA"}
		).insert(ignore_permissions=True)
		msg = frappe.get_doc(
			{
				"doctype": "Omni Inbound Message",
				"dedupe_key": f"FR:{frappe.generate_hash(length=10)}",
				"channel": self.channel.name,
				"channel_type": "Zalo OA",
				"event_type": "message",
				"identity_key": "Zalo OA:FRU",
				"lead": lead.name,
				"received_at": fri,
				"status": "Done",
				"text": "xin chào",
			}
		).insert(ignore_permissions=True)
		self.assertTrue(msg.name)
		frappe.db.set_value(
			"CRM Lead", lead.name, "creation", datetime(2020, 1, 1, 9, 0)
		)  # tách khỏi dữ liệu khác
		frappe.db.set_value("CRM Lead", lead.name, FIELD, datetime(2026, 11, 2, 8, 30))
		res = m.first_response(datetime(2020, 1, 1), datetime(2020, 1, 2), [], False)
		self.assertEqual(res["answered"], 1)
		self.assertEqual(
			res["median_min"], 300.0
		)  # 30 + 240 + 30 phút làm việc, không phải 3.810 phút đồng hồ
