"""V3-02 (ADR 0017): tin Zalo OA không hiện ở tab Hoạt động/Email; email, trạng thái liên lạc và phản hồi đầu tiên không đổi."""

from datetime import datetime
from unittest import mock

import frappe
from crm.api.activities import get_lead_activities

from omni_crm import flags
from omni_crm.api import zalo_tab
from omni_crm.channels.zalo.adapter import ZaloAdapter
from omni_crm.crm_bridge import first_response, timeline
from omni_crm.dashboard import blocks
from omni_crm.ops import hide_zalo_activity
from omni_crm.pipeline import outbound
from omni_crm.tests.test_outbound import SALE1, OutboundTestCase

FIELD = "zalo_in_activity_enabled"
T0 = datetime(2026, 10, 26, 9, 0)


class HideCase(OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.prev_flag = frappe.db.sql(
			"select value from `tabSingles` where doctype='Omni Settings' and field=%s", FIELD
		)
		self.prev_fcrm = {
			k: frappe.db.get_single_value("FCRM Settings", k)
			for k in ("auto_reopen_on_new_communication", "auto_mark_replied_on_response")
		}
		self.pre_zalo = {
			r.name: r.communication_type
			for r in frappe.db.sql(
				"""select c.name, c.communication_type from `tabCommunication` c
				where ifnull(c.omni_inbound_message, '') != '' or ifnull(c.omni_outbound_message, '') != ''""",
				as_dict=True,
			)
		}
		self.addCleanup(self.restore)
		self.set_flag(None)

	def restore(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		for name, ctype in self.pre_zalo.items():  # lệnh ẩn có thể đã đổi loại bản ghi có sẵn trên site test
			if frappe.db.exists("Communication", name):
				frappe.db.set_value("Communication", name, "communication_type", ctype, update_modified=False)
		self.set_flag(self.prev_flag[0][0] if self.prev_flag else None)
		for k, v in self.prev_fcrm.items():
			frappe.db.set_single_value("FCRM Settings", k, v)
		frappe.db.commit()

	def set_flag(self, value):
		frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field=%s", FIELD)
		if value is not None:
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Omni Settings', %s, %s)",
				(FIELD, str(value)),
			)
		frappe.db.commit()

	def ctype(self, **filters):
		return frappe.db.get_value("Communication", filters, "communication_type")

	def activity_comm_names(self, lead=None):
		"""Mã Communication hiện trong tab Hoạt động/Email (CRM không trả `name`, nên đối chiếu qua subject + ngày)."""
		activities = get_lead_activities(lead or self.lead1)[0]
		shown = [a["data"]["subject"] for a in activities if a.get("activity_type") == "communication"]
		names = frappe.get_all(
			"Communication",
			filters={"subject": ["in", shown or [""]], "reference_name": lead or self.lead1},
			fields=["name"],
		)
		return {n.name for n in names}

	def make_mail(self, sent_or_received="Received", subject="TD106 thư thật", **over):
		doc = {
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": sent_or_received,
			"subject": subject,
			"content": "nội dung thư",
			"reference_doctype": "CRM Lead",
			"reference_name": self.lead1,
			"communication_date": T0,
		}
		doc.update(over)
		return frappe.get_doc(doc).insert(ignore_permissions=True)

	def send_real(self, text="Dạ em chào anh", ext="zl-v302"):
		self.sandbox(False)
		name = self.queue(text)
		with mock.patch.object(ZaloAdapter, "send", return_value=ext):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		return name

	def old_style(self, comm):
		"""Mô phỏng bản ghi cũ trước V3-02."""
		frappe.db.set_value(
			"Communication", comm, "communication_type", "Communication", update_modified=False
		)


class TestSwitch(HideCase):
	def test_default_hidden_when_never_saved_or_zero_and_on_only_for_one(self):
		self.set_flag(None)
		self.assertFalse(flags.zalo_in_activity_enabled())
		self.set_flag(0)
		self.assertFalse(flags.zalo_in_activity_enabled())
		self.set_flag(1)
		self.assertTrue(flags.zalo_in_activity_enabled())

	def test_read_error_means_hidden(self):
		self.set_flag(1)
		with mock.patch("frappe.db.sql", side_effect=Exception("x")):
			self.assertFalse(flags.zalo_in_activity_enabled())

	def test_settings_field_declared_with_default_zero(self):
		meta = frappe.get_meta("Omni Settings") if frappe.get_meta("Omni Settings").has_field(FIELD) else None
		if meta is None:  # site chưa migrate: đọc thẳng JSON
			import json

			path = frappe.get_app_path(
				"omni_crm", "omni_crm", "doctype", "omni_settings", "omni_settings.json"
			)
			fields = {f["fieldname"]: f for f in json.load(open(path))["fields"]}
			self.assertEqual(fields[FIELD]["fieldtype"], "Check")
			self.assertEqual(fields[FIELD]["default"], "0")
		else:
			self.assertEqual(meta.get_field(FIELD).fieldtype, "Check")

	def test_channel_type_mapping(self):
		self.assertEqual(timeline.communication_type_for("Zalo OA"), "Chat")
		for other in ("Email", "Meta Lead Ads", "Google Lead Form", "Web Form", "Telephony", None):
			self.assertEqual(timeline.communication_type_for(other), "Communication")
		self.set_flag(1)
		self.assertEqual(timeline.communication_type_for("Zalo OA"), "Communication")


class TestNewRecords(HideCase):
	def test_inbound_zalo_is_chat_with_same_other_fields(self):
		msg = self.send("V302A", sender="TD106V302A")
		row = frappe.db.get_value(
			"Communication",
			{"omni_inbound_message": msg},
			["communication_type", "communication_medium", "sent_or_received", "reference_doctype"],
			as_dict=True,
		)
		self.assertEqual(row.communication_type, "Chat")
		self.assertEqual(row.communication_medium, "Chat")
		self.assertEqual(row.sent_or_received, "Received")
		self.assertEqual(row.reference_doctype, "CRM Lead")

	def test_oa_echo_is_chat_sent(self):
		self.send("V302B0", sender="TD106V302B")  # có khách trước thì echo mới gắn được Lead
		msg = self.send("V302B", sender="TD106V302B", event_type="oa_echo")
		row = frappe.db.get_value(
			"Communication",
			{"omni_inbound_message": msg},
			["communication_type", "sent_or_received"],
			as_dict=True,
		)
		self.assertEqual((row.communication_type, row.sent_or_received), ("Chat", "Sent"))

	def test_outbound_real_and_sandbox_are_chat(self):
		real = self.send_real()
		self.assertEqual(self.ctype(omni_outbound_message=real), "Chat")
		self.sandbox(True)
		sb = self.queue("thử")
		self.assertEqual(outbound.send_outbound(sb), "Sent")
		self.assertEqual(self.ctype(omni_outbound_message=sb), "Chat")

	def test_switch_on_restores_old_behaviour_for_new_records(self):
		self.set_flag(1)
		msg = self.send("V302C", sender="TD106V302C")
		self.assertEqual(self.ctype(omni_inbound_message=msg), "Communication")
		out = self.send_real()
		self.assertEqual(self.ctype(omni_outbound_message=out), "Communication")

	def test_rerun_append_does_not_duplicate(self):
		msg = self.send("V302D", sender="TD106V302D")
		lead = self.lead_of(msg)
		before = frappe.db.count("Communication", {"omni_inbound_message": msg})
		timeline.append(lead, [frappe._dict(name=msg)])
		self.assertEqual(frappe.db.count("Communication", {"omni_inbound_message": msg}), before)


class TestActivities(HideCase):
	def test_activities_hide_zalo_but_show_email(self):
		msg = self.send("V302E", sender="TD106V302E")
		lead = self.lead_of(msg)
		mail = self.make_mail(reference_name=lead)
		zalo = frappe.db.get_value("Communication", {"omni_inbound_message": msg}, "name")
		out = frappe.db.get_value("Communication", {"omni_outbound_message": self.send_real()}, "name")
		names = self.activity_comm_names(lead)
		self.assertIn(mail.name, names)
		self.assertNotIn(zalo, names)
		self.assertNotIn(out, names)

	def test_docinfo_has_no_zalo_communication(self):
		msg = self.send("V302F", sender="TD106V302F")
		lead = self.lead_of(msg)
		mail = self.make_mail(reference_name=lead)
		zalo = frappe.db.get_value("Communication", {"omni_inbound_message": msg}, "name")
		from frappe.desk.form.load import get_docinfo

		get_docinfo("", "CRM Lead", lead)
		got = {c["name"] for c in frappe.response["docinfo"]["communications"]}
		self.assertIn(mail.name, got)
		self.assertNotIn(zalo, got)

	def test_switch_on_shows_new_zalo_in_activities(self):
		self.set_flag(1)
		msg = self.send("V302G", sender="TD106V302G")
		zalo = frappe.db.get_value("Communication", {"omni_inbound_message": msg}, "name")
		self.assertIn(zalo, self.activity_comm_names(self.lead_of(msg)))

	def test_zalo_tab_still_has_every_message(self):
		self.sandbox(False)
		msg = self.send("V302H", sender="TD106V302H", text="khách hỏi V302H")
		lead = self.lead_of(msg)
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-v302h"):
			name = self.queue("Dạ sale V302H")
			self.assertEqual(outbound.send_outbound(name), "Sent")
		self.send("V302I", sender="TD106V302H", text="echo V302I", event_type="oa_echo")
		frappe.db.set_value("Omni Outbound Message", name, "lead", lead)
		frappe.db.set_value("Omni Identity", self.ident1, "lead", lead)
		frappe.db.commit()
		res = zalo_tab.get_conversation("CRM Lead", lead)
		texts = [m["text"] for m in res["messages"]]
		self.assertIn("khách hỏi V302H", texts)
		self.assertIn("echo V302I", texts)


class TestCommunicationStatus(HideCase):
	def test_received_opens_and_sent_replies_for_chat_type(self):
		frappe.db.set_single_value("FCRM Settings", "auto_reopen_on_new_communication", 1)
		frappe.db.set_single_value("FCRM Settings", "auto_mark_replied_on_response", 1)
		frappe.db.commit()
		msg = self.send("V302J", sender="TD106V302J")
		lead = self.lead_of(msg)
		self.assertEqual(
			frappe.db.get_value("Communication", {"omni_inbound_message": msg}, "communication_type"), "Chat"
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "communication_status"), "Open")
		frappe.db.set_value("CRM Lead", lead, "communication_status", "Replied")
		frappe.db.commit()
		msg2 = self.send("V302K", sender="TD106V302J", text="khách nhắn lại")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "communication_status"), "Open")
		# tin ra: Sent -> Replied (gắn Communication vào đúng Lead của tin)
		self.sandbox(False)
		frappe.db.set_value("Omni Identity", self.ident1, "lead", lead)
		out = self.queue("Dạ em chào anh")
		frappe.db.set_value("Omni Outbound Message", out, "lead", lead)
		frappe.db.commit()
		with mock.patch.object(ZaloAdapter, "send", return_value="zl-v302j"):
			self.assertEqual(outbound.send_outbound(out), "Sent")
		comm = frappe.db.get_value(
			"Communication",
			{"omni_outbound_message": out},
			["communication_type", "reference_name"],
			as_dict=True,
		)
		self.assertEqual(comm.communication_type, "Chat")
		self.assertEqual(
			frappe.db.get_value("CRM Lead", comm.reference_name, "communication_status"), "Replied"
		)
		self.assertTrue(msg2)


class TestIndicatorsUnchanged(HideCase):
	def test_first_response_counts_zalo_sale_reply_without_activity_row(self):
		frappe.db.set_value("CRM Lead", self.lead1, first_response.FIELD, None)
		name = self.send_real()
		self.assertEqual(self.ctype(omni_outbound_message=name), "Chat")
		sent_at = frappe.db.get_value("Omni Outbound Message", name, "sent_at")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, first_response.FIELD), sent_at)
		self.assertEqual(first_response.earliest_responses()[self.lead1], sent_at)

	def test_sandbox_zalo_still_not_a_response(self):
		frappe.db.set_value("CRM Lead", self.lead1, first_response.FIELD, None)
		self.sandbox(True)
		self.assertEqual(outbound.send_outbound(self.queue()), "Sent")
		self.assertIsNone(frappe.db.get_value("CRM Lead", self.lead1, first_response.FIELD))
		self.assertNotIn(self.lead1, first_response.earliest_responses())

	def test_email_sent_still_first_response(self):
		frappe.db.set_value("CRM Lead", self.lead1, first_response.FIELD, None)
		self.make_mail(sent_or_received="Sent", subject="TD106 trả lời")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, first_response.FIELD), T0)

	def test_metrics_identical_before_and_after_converting_old_rows(self):
		"""Cùng một dữ liệu: ở dạng cũ (Communication) và sau lệnh ẩn (Chat), các chỉ số đọc ra y hệt nhau."""
		self.sandbox(False)
		msg = self.send("V302L", sender="TD106V302L")
		lead = self.lead_of(msg)
		frappe.db.set_value("Omni Identity", self.ident1, "lead", lead)
		name = self.send_real(ext="zl-v302l")
		frappe.db.set_value("Omni Outbound Message", name, "lead", lead)
		self.make_mail(sent_or_received="Sent", reference_name=lead, communication_date=T0)
		for c in frappe.get_all("Communication", filters={"reference_name": lead}, pluck="name"):
			self.old_style(c)
		frappe.db.commit()

		def snapshot():
			q = blocks.make_query("30d", "team", "Administrator")
			waiting = sorted(r["lead"] for r in blocks.waiting_leads(q))
			turns = [(t["at"], t["actor"], t["kind"], t["text"]) for t in timeline.conversation_turns(lead)]
			return first_response.earliest_responses().get(lead), waiting, turns

		before = snapshot()
		res = hide_zalo_activity.run(dry_run=0)
		self.assertGreaterEqual(res["changed"], 2)
		self.assertEqual(snapshot(), before)
		self.assertEqual(hide_zalo_activity.run(dry_run=0, reverse=1)["changed"], res["changed"])
		self.assertEqual(snapshot(), before)


class TestHideCommand(HideCase):
	def setUp(self):
		super().setUp()
		self.msg = self.send("V302M", sender="TD106V302M")
		self.lead = self.lead_of(self.msg)
		self.zalo = frappe.db.get_value("Communication", {"omni_inbound_message": self.msg}, "name")
		self.out_name = self.send_real(ext="zl-v302m")
		self.out = frappe.db.get_value("Communication", {"omni_outbound_message": self.out_name}, "name")
		self.mail_in = self.make_mail(reference_name=self.lead, omni_inbound_message=self.mail1)
		self.mail_plain = self.make_mail(subject="TD106 thư lẻ", reference_name=self.lead)
		self.chat_plain = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Chat",
				"communication_medium": "Chat",
				"sent_or_received": "Received",
				"subject": "TD106 chat không của omni",
			}
		).insert(ignore_permissions=True)
		for c in (self.zalo, self.out):  # dữ liệu cũ
			self.old_style(c)
		frappe.db.commit()
		self.mod = {
			c: frappe.db.get_value("Communication", c, "modified")
			for c in (self.zalo, self.out, self.mail_in.name, self.mail_plain.name, self.chat_plain.name)
		}

	def type_of(self, name):
		return frappe.db.get_value("Communication", name, "communication_type")

	def test_dry_run_default_writes_nothing(self):
		res = hide_zalo_activity.run()
		self.assertTrue(res["dry_run"])
		self.assertFalse(res["reverse"])
		self.assertGreaterEqual(res["matched"], 2)
		self.assertEqual(res["changed"], 0)
		self.assertEqual(self.type_of(self.zalo), "Communication")
		self.assertEqual(self.type_of(self.out), "Communication")

	def test_apply_only_touches_zalo_keeps_modified_and_is_idempotent(self):
		res = hide_zalo_activity.run(dry_run=0)
		self.assertGreaterEqual(res["changed"], 2)
		self.assertEqual(self.type_of(self.zalo), "Chat")
		self.assertEqual(self.type_of(self.out), "Chat")
		self.assertEqual(
			self.type_of(self.mail_in.name), "Communication"
		)  # email có khóa omni_inbound_message
		self.assertEqual(self.type_of(self.mail_plain.name), "Communication")
		self.assertEqual(self.type_of(self.chat_plain.name), "Chat")
		for name, mod in self.mod.items():
			self.assertEqual(frappe.db.get_value("Communication", name, "modified"), mod, name)
		again = hide_zalo_activity.run(dry_run=0)
		self.assertEqual((again["matched"], again["changed"]), (0, 0))

	def test_apply_makes_old_zalo_leave_activities_but_email_stays(self):
		self.assertIn(self.zalo, self.activity_comm_names(self.lead))
		hide_zalo_activity.run(dry_run=0)
		names = self.activity_comm_names(self.lead)
		self.assertNotIn(self.zalo, names)
		self.assertNotIn(self.out, names)
		self.assertIn(self.mail_in.name, names)
		self.assertIn(self.mail_plain.name, names)

	def test_reverse_restores_only_omni_zalo(self):
		hide_zalo_activity.run(dry_run=0)
		dry = hide_zalo_activity.run(reverse=1)
		self.assertTrue(dry["reverse"] and dry["dry_run"] and dry["changed"] == 0)
		self.assertEqual(self.type_of(self.zalo), "Chat")
		res = hide_zalo_activity.run(dry_run=0, reverse=1)
		self.assertGreaterEqual(res["changed"], 2)
		self.assertEqual(self.type_of(self.zalo), "Communication")
		self.assertEqual(self.type_of(self.out), "Communication")
		self.assertEqual(self.type_of(self.chat_plain.name), "Chat")  # Chat không của Omni giữ nguyên
		self.assertEqual(self.type_of(self.mail_in.name), "Communication")
		for name, mod in self.mod.items():
			self.assertEqual(frappe.db.get_value("Communication", name, "modified"), mod, name)
		self.assertEqual(hide_zalo_activity.run(dry_run=0, reverse=1)["changed"], 0)

	def test_accepts_string_flags_from_bench_execute(self):
		self.assertEqual(hide_zalo_activity.run(dry_run="1")["changed"], 0)
		self.assertGreaterEqual(hide_zalo_activity.run(dry_run="0", reverse="0")["changed"], 2)
