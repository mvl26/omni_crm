from unittest import mock

import frappe
from crm.api.activities import get_activities

from omni_crm.channels.email import adapter as email_adapter
from omni_crm.channels.email.adapter import EmailAdapter, external_message_id, on_communication_insert
from omni_crm.channels.email.config import crm_auto_lead_issues
from omni_crm.channels.registry import get_adapter
from omni_crm.ingest.events import OutboundRequest
from omni_crm.tests.email_helpers import (
	ACCOUNT,
	CHANNEL,
	EmailTestCase,
	build_raw,
	load_fixtures,
)

MINH = "td107.minh@example.com"


def _comm_activities(lead: str) -> list[dict]:
	"""Communication hiện trong tab hoạt động của CRM (qua chính API của CRM)."""
	return [a for a in get_activities(lead)[0] if a["activity_type"] == "communication"]


class TestInboundFlow(EmailTestCase):
	def test_new_customer_creates_one_lead_and_mail_shows_in_crm(self):
		comm, msg = self.deliver(MINH, "Hỏi giá xe nâng", sender_name="Tran Van Minh")
		lead = self.lead_of(msg)
		self.assertTrue(lead)
		self.assertEqual(self.lead_count(), 1)
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "status"), "Done")
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", msg, "communication"), comm.name)
		ref = frappe.db.get_value("Communication", comm.name, ["reference_doctype", "reference_name"])
		self.assertEqual(tuple(ref), ("CRM Lead", lead))
		shown = _comm_activities(lead)
		self.assertEqual(len(shown), 1)
		self.assertEqual(shown[0]["data"]["subject"], "Hỏi giá xe nâng")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "email"), MINH)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "first_name"), "Tran Van Minh")

	def test_three_mails_three_subjects_one_lead(self):
		subjects = ("Hỏi giá xe nâng", "Kệ pallet loại nào tốt", "Lịch giao hàng")
		comms = [self.deliver(MINH, subject, sender_name="Tran Van Minh") for subject in subjects]
		leads = {self.lead_of(msg) for _c, msg in comms}
		self.assertEqual(len(leads), 1)
		self.assertEqual(self.lead_count(), 1)
		shown = _comm_activities(leads.pop())
		self.assertEqual(sorted(a["data"]["subject"] for a in shown), sorted(subjects))

	def test_reply_in_thread_same_lead_from_other_sender(self):
		raw, mid = build_raw(MINH, "Hỏi giá xe nâng", "Cần báo giá", sender_name="Tran Van Minh")
		first = self.receive(raw)
		self.run_worker(MINH)
		lead = frappe.db.get_value("Omni Inbound Message", {"communication": first.name}, "lead")
		# đồng nghiệp (email khác, tên khác) trả lời trong chuỗi: Frappe đã trỏ sẵn tham chiếu về Lead
		colleague = "td107.dongnghiep@example.com"
		raw2, _ = build_raw(colleague, "Re: Hỏi giá xe nâng", "Bên em đồng ý", in_reply_to=mid)
		second = self.receive(raw2)
		self.assertEqual(second.in_reply_to, first.name)
		self.run_worker(colleague)
		msg2 = frappe.db.get_value("Omni Inbound Message", {"communication": second.name}, "name")
		self.assertEqual(self.lead_of(msg2), lead)
		self.assertEqual(self.lead_count(), 1)
		self.assertEqual(frappe.db.get_value("Communication", second.name, "reference_name"), lead)

	def test_thread_signal_works_when_frappe_set_no_reference(self):
		first, msg = self.deliver(MINH, "Hỏi giá", sender_name="Tran Van Minh")
		lead = self.lead_of(msg)
		colleague = "td107.ketoan@example.com"
		reply = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"subject": "Chuyển khoản",
				"content": "Đã chuyển khoản",
				"sender": colleague,
				"email_account": ACCOUNT,
				"message_id": "<td107-thread-1@example.com>",
				"in_reply_to": first.name,
			}
		).insert(ignore_permissions=True)
		with mock.patch("frappe.enqueue"):
			frappe.db.commit()
		self.assertFalse(frappe.db.get_value("Communication", reply.name, "reference_name"))
		self.run_worker(colleague)
		msg2 = frappe.db.get_value("Omni Inbound Message", {"communication": reply.name}, "name")
		self.assertEqual(self.lead_of(msg2), lead)
		self.assertEqual(frappe.db.get_value("Communication", reply.name, "reference_name"), lead)
		self.assertEqual(self.lead_count(), 1)

	def test_signature_phone_matches_existing_lead_as_medium_signal(self):
		other = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "TD107 Tran Van Minh", "mobile_no": "+84912345678"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		body = "Cho tôi xin báo giá.\n\nTrân trọng,\nTran Van Minh\n0912 345 678"
		_c, msg = self.deliver("td107.moi@example.com", "Báo giá", body, sender_name="TD107 Tran Van Minh")
		self.assertEqual(self.lead_of(msg), other.name)

	def test_signature_phone_not_in_text_but_in_profile(self):
		body = "Cho tôi xin báo giá.\n\nTrân trọng,\nAn\n0912 345 678"
		_c, msg = self.deliver("td107.an@example.com", "Báo giá", body)
		row = frappe.db.get_value("Omni Inbound Message", msg, ["text", "sender_profile"], as_dict=True)
		self.assertNotIn("0912", row.text)
		self.assertIn("+84912345678", row.sender_profile)

	def test_duplicate_message_id_no_second_record(self):
		raw, _mid = build_raw(MINH, "Hỏi giá", "Cần báo giá")
		first = self.receive(raw)
		second = self.receive(raw)  # Frappe kéo lại cùng thư
		self.assertEqual(first.name, second.name)
		self.assertEqual(frappe.db.count("Communication", {"email_account": ACCOUNT}), 1)
		self.assertEqual(frappe.db.count("Omni Inbound Message", {"channel": CHANNEL}), 1)

	def test_save_inbound_dedupes_even_if_hook_runs_twice(self):
		comm, _msg = self.deliver(MINH, "Hỏi giá")
		with mock.patch("frappe.enqueue"):
			on_communication_insert(comm)
		self.assertEqual(frappe.db.count("Omni Inbound Message", {"channel": CHANNEL}), 1)

	def test_long_message_id_is_hashed_and_deduped(self):
		long_id = "<" + "x" * 200 + "@mail.example.com>"
		raw, _ = build_raw(MINH, "Hỏi giá", "Cần báo giá", message_id=long_id)
		comm = self.receive(raw)
		row = frappe.db.get_value(
			"Omni Inbound Message",
			{"communication": comm.name},
			["dedupe_key", "external_message_id"],
			as_dict=True,
		)
		self.assertTrue(row, "thư có Message-ID dài vẫn phải được lưu")
		self.assertLessEqual(len(row.dedupe_key), 140)
		self.assertTrue(row.external_message_id.startswith("sha:"))
		self.receive(raw)
		self.assertEqual(frappe.db.count("Omni Inbound Message", {"channel": CHANNEL}), 1)

	def test_external_message_id_rules(self):
		self.assertEqual(external_message_id("k", "<a@b.c>", "COMM-1"), "<a@b.c>")
		self.assertEqual(len(external_message_id("k", None, "COMM-1")), 64)
		self.assertTrue(external_message_id("k", "x" * 300, "COMM-1").startswith("sha:"))


class TestIgnored(EmailTestCase):
	def test_all_ignored_fixtures_saved_ignored_without_lead(self):
		fixtures = [f for f in load_fixtures() if f["ignored"]]
		self.assertGreaterEqual(len(fixtures), 3)
		for fx in fixtures:
			with self.subTest(fx["file"]):
				raw, _ = build_raw(
					fx["sender"],
					fx["subject"],
					fx["body"],
					sender_name=fx["sender_name"],
					html=fx["format"] == "html",
				)
				comm = self.receive(raw)
				self.run_worker(fx["sender"])
				row = frappe.db.get_value(
					"Omni Inbound Message",
					{"communication": comm.name},
					["status", "lead", "identity"],
					as_dict=True,
				)
				self.assertEqual(row.status, "Ignored")
				self.assertFalse(row.lead)
				self.assertFalse(row.identity)
				self.assertFalse(frappe.db.get_value("Communication", comm.name, "reference_name"))
		self.assertEqual(self.lead_count(""), 0)

	def test_ignored_mail_does_not_enqueue(self):
		raw, _ = build_raw("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", "lỗi")
		with mock.patch("frappe.enqueue") as enq:
			from frappe.email.receive import InboundMail

			InboundMail(raw, frappe.get_doc("Email Account", ACCOUNT)).process()
			frappe.db.commit()
		enq.assert_not_called()


class TestHookGuards(EmailTestCase):
	def _count(self):
		return frappe.db.count("Omni Inbound Message", {"channel": CHANNEL})

	def test_disabled_channel_ignored(self):
		frappe.db.set_value("Omni Channel", CHANNEL, "enabled", 0)
		frappe.db.commit()
		frappe.clear_cache()
		raw, _ = build_raw(MINH, "Hỏi giá", "Cần báo giá")
		self.receive(raw)
		self.assertEqual(self._count(), 0)

	def test_other_account_ignored(self):
		doc = frappe._dict(
			communication_medium="Email",
			sent_or_received="Received",
			email_account="Khong co tai khoan nay",
			name="X",
		)
		on_communication_insert(doc)
		self.assertEqual(self._count(), 0)

	def test_sent_mail_and_non_email_ignored(self):
		for medium, direction in (("Email", "Sent"), ("Chat", "Received")):
			doc = frappe._dict(
				communication_medium=medium, sent_or_received=direction, email_account=ACCOUNT, name="X"
			)
			on_communication_insert(doc)
		self.assertEqual(self._count(), 0)

	def test_hook_never_raises(self):
		doc = frappe._dict(
			communication_medium="Email",
			sent_or_received="Received",
			email_account=ACCOUNT,
			name="COMM-LOI",
			sender=MINH,
			subject="x",
			message_id="<a@b.c>",
		)
		with mock.patch.object(email_adapter.gateway, "save_inbound", side_effect=RuntimeError("0912345678")):
			on_communication_insert(doc)  # không được ném lỗi
		log = frappe.get_all(
			"Error Log", filters={"method": ["like", "Omni: lỗi đưa email%"]}, fields=["error"], limit=1
		)
		self.assertTrue(log)
		self.assertNotIn("0912345678", log[0].error)


class TestCrmInteraction(EmailTestCase):
	def test_crm_auto_lead_flag_gives_candidate_lead_without_duplicate(self):
		"""Nếu cấu hình tắt cơ chế CRM bị bỏ sót, Lead do CRM tạo được dùng lại (không có Lead thứ hai)."""
		frappe.db.set_value("Email Account", ACCOUNT, "create_lead_from_incoming_email", 1)
		frappe.db.commit()
		self.assertTrue(crm_auto_lead_issues(ACCOUNT))
		comm, msg = self.deliver(MINH, "Hỏi giá", sender_name="Tran Van Minh")
		self.assertEqual(self.lead_of(msg), frappe.db.get_value("Communication", comm.name, "reference_name"))
		self.assertEqual(self.lead_count(), 1)
		comm2, msg2 = self.deliver(MINH, "Thêm câu hỏi", sender_name="Tran Van Minh")
		self.assertEqual(self.lead_of(msg2), self.lead_of(msg))
		self.assertEqual(self.lead_count(), 1)

	def test_config_check_clean_account(self):
		self.assertEqual(crm_auto_lead_issues(ACCOUNT), [])
		self.assertEqual(crm_auto_lead_issues(None), [])
		frappe.db.set_value("Email Account", ACCOUNT, "append_to", "CRM Lead")
		self.assertEqual(len(crm_auto_lead_issues(ACCOUNT)), 1)

	def test_communication_status_opens_lead(self):
		old = frappe.db.get_single_value("FCRM Settings", "auto_reopen_on_new_communication")
		frappe.db.set_single_value("FCRM Settings", "auto_reopen_on_new_communication", 1)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.db.set_single_value("FCRM Settings", "auto_reopen_on_new_communication", old),
				frappe.db.commit(),
			)
		)
		_c, msg = self.deliver(MINH, "Hỏi giá")
		lead = self.lead_of(msg)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "communication_status"), "Open")

	def test_link_does_not_override_other_document_reference(self):
		raw, _ = build_raw(MINH, "Hỏi giá", "Cần báo giá")
		comm = self.receive(raw)
		todo = frappe.get_doc({"doctype": "ToDo", "description": "TD107 việc khác"}).insert(
			ignore_permissions=True
		)
		frappe.db.set_value(
			"Communication", comm.name, {"reference_doctype": "ToDo", "reference_name": todo.name}
		)
		frappe.db.commit()
		self.addCleanup(lambda: (frappe.delete_doc("ToDo", todo.name, force=True), frappe.db.commit()))
		self.run_worker(MINH)
		ref = frappe.db.get_value("Communication", comm.name, ["reference_doctype", "reference_name"])
		self.assertEqual(tuple(ref), ("ToDo", todo.name))

	def test_link_repoints_from_merged_lead(self):
		raw, _ = build_raw(MINH, "Hỏi giá", "Cần báo giá")
		comm = self.receive(raw)
		old = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD107 Lead cũ"}).insert(
			ignore_permissions=True
		)
		main = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "TD107 Lead chính", "mobile_no": "+84912000111"}
		)
		main.insert(ignore_permissions=True)
		frappe.db.set_value("CRM Lead", main.name, "email", MINH)
		frappe.db.set_value("CRM Lead", main.name, "omni_email_norm", MINH)
		frappe.db.set_value("CRM Lead", old.name, "omni_merged_into", main.name)
		frappe.db.set_value(
			"Communication", comm.name, {"reference_doctype": "CRM Lead", "reference_name": old.name}
		)
		frappe.db.commit()
		self.run_worker(MINH)
		self.assertEqual(frappe.db.get_value("Communication", comm.name, "reference_name"), main.name)


class TestAdapter(EmailTestCase):
	def test_registered_and_trivial_methods(self):
		adapter = get_adapter("Email")
		self.assertIsInstance(adapter, EmailAdapter)
		self.assertTrue(adapter.verify_request(b"", {}, None))
		self.assertEqual(adapter.parse(b"", {}, None), [])

	def test_can_send_now_follows_outbound_flag(self):
		adapter = EmailAdapter()
		channel = frappe.get_doc("Omni Channel", CHANNEL)
		self.assertFalse(adapter.can_send_now(None, channel)[0])
		channel.outbound_enabled = 1
		self.assertEqual(adapter.can_send_now(None, channel), (True, ""))

	def _identity(self):
		_c, msg = self.deliver(MINH, "Hỏi giá xe nâng", sender_name="Tran Van Minh")
		return frappe.db.get_value(
			"Omni Inbound Message", msg, ["identity", "external_message_id", "lead"], as_dict=True
		)

	def test_send_in_sandbox_does_not_call_sendmail(self):
		row = self._identity()
		channel = frappe.get_doc("Omni Channel", CHANNEL)
		req = OutboundRequest(CHANNEL, row.identity, "Xin chào", row.external_message_id, "Sale")
		with (
			mock.patch("omni_crm.flags.sandbox", return_value=True),
			mock.patch("frappe.sendmail") as sendmail,
		):
			message_id = EmailAdapter().send(req, channel)
		sendmail.assert_not_called()
		self.assertTrue(message_id.startswith("<"))

	def test_send_real_path_is_mocked_and_threaded(self):
		row = self._identity()
		channel = frappe.get_doc("Omni Channel", CHANNEL)
		req = OutboundRequest(CHANNEL, row.identity, "Xin chào <b>", row.external_message_id, "Sale")
		with (
			mock.patch("omni_crm.flags.sandbox", return_value=False),
			mock.patch("frappe.sendmail") as sendmail,
		):
			message_id = EmailAdapter().send(req, channel)
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], [MINH])
		self.assertEqual(kwargs["sender"], "td107.sales@example.org")
		self.assertEqual(kwargs["reference_doctype"], "CRM Lead")
		self.assertEqual(kwargs["reference_name"], row.lead)
		self.assertEqual(kwargs["in_reply_to"], row.external_message_id)
		self.assertEqual(kwargs["subject"], "Re: Hỏi giá xe nâng")
		self.assertEqual(kwargs["message_id"], message_id)
		self.assertNotIn("<b>", kwargs["message"])

	def test_send_without_email_is_permanent_error(self):
		channel = frappe.get_doc("Omni Channel", CHANNEL)
		ident = frappe.get_doc(
			{"doctype": "Omni Identity", "identity_key": "Email:td107.khong-email", "channel_type": "Email"}
		)
		ident.insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (frappe.delete_doc("Omni Identity", ident.name, force=True), frappe.db.commit())
		)
		req = OutboundRequest(CHANNEL, ident.name, "Xin chào", None, "Sale")
		with (
			mock.patch("omni_crm.flags.sandbox", return_value=False),
			mock.patch("frappe.sendmail") as sendmail,
			self.assertRaises(ValueError),
		):
			EmailAdapter().send(req, channel)
		sendmail.assert_not_called()
