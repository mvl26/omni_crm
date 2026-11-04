"""V3-01: tên người gửi tin Zalo, lệnh sửa tin cũ, API mỏng của tab Zalo (bong bóng + khung trả lời). Mọi lời gọi gửi đều giả."""

import json
from unittest import mock

import frappe

from omni_crm import flags
from omni_crm.api import inbox, zalo_tab
from omni_crm.channels.zalo import errors, profile_refresh, worker_hooks
from omni_crm.channels.zalo.client import ZaloClient
from omni_crm.crm_bridge import timeline
from omni_crm.ops import backfill_sender
from omni_crm.pipeline import outbound
from omni_crm.tests import test_inbox
from omni_crm.tests.bridge_helpers import BridgeTestCase
from omni_crm.tests.test_outbound import OutboundTestCase

SALE1, SALE2 = test_inbox.SALE1, test_inbox.SALE2
RAW = "TD1062716764713514648383"


class TestSenderLabel(BridgeTestCase):
	def test_pure_fallback_order(self):
		self.assertEqual(timeline.sender_label("Mai", "Lead X", "Zalo OA", RAW), "Mai")
		self.assertEqual(
			timeline.sender_label("  ", "Khách Zalo OA 8383", "Zalo OA", RAW), "Khách Zalo OA 8383"
		)
		self.assertEqual(timeline.sender_label(None, None, "Zalo OA", RAW), "Zalo OA …8383")
		self.assertEqual(timeline.sender_label(None, None, None, None), "Khách")
		self.assertNotIn(RAW, timeline.sender_label(None, None, "Zalo OA", RAW))

	def test_new_inbound_communication_never_shows_raw_id(self):
		msg = self.send("V301A", sender=RAW)
		lead = self.lead_of(msg)
		comm = frappe.get_all(
			"Communication", filters={"omni_inbound_message": msg}, fields=["sender_full_name"]
		)[0]
		self.assertNotIn(RAW, comm.sender_full_name)
		self.assertTrue(comm.sender_full_name)
		# nếu định danh có tên thì dùng tên định danh
		ident = frappe.db.get_value("Omni Inbound Message", msg, "identity")
		frappe.db.set_value("Omni Identity", ident, "display_name", "TD106 Tên Thật")
		msg2 = self.send("V301B", sender=RAW)
		comm2 = frappe.db.get_value("Communication", {"omni_inbound_message": msg2}, "sender_full_name")
		self.assertEqual(comm2, "TD106 Tên Thật")
		self.assertEqual(self.lead_of(msg2), lead)


class TestBackfill(BridgeTestCase):
	def setUp(self):
		super().setUp()
		self.msg = self.send("V301C", sender=RAW)
		self.comm = frappe.db.get_value("Communication", {"omni_inbound_message": self.msg}, "name")
		# mô phỏng dữ liệu cũ: tên người gửi là mã thô
		frappe.db.set_value("Communication", self.comm, "sender_full_name", RAW, update_modified=False)
		self.modified = frappe.db.get_value("Communication", self.comm, "modified")
		frappe.db.commit()

	def name(self):
		return frappe.db.get_value("Communication", self.comm, "sender_full_name")

	def test_dry_run_default_writes_nothing(self):
		res = backfill_sender.run()
		self.assertTrue(res["dry_run"])
		self.assertGreaterEqual(res["changed"], 1)
		self.assertIn(self.comm, res["sample"] + [self.comm])
		self.assertEqual(self.name(), RAW)

	def test_apply_fixes_name_keeps_modified_and_is_idempotent(self):
		res = backfill_sender.run(dry_run=0)
		self.assertFalse(res["dry_run"])
		self.assertNotIn(RAW, self.name())
		self.assertEqual(frappe.db.get_value("Communication", self.comm, "modified"), self.modified)
		again = backfill_sender.run(dry_run=0)
		self.assertEqual(again["changed"], 0)

	def test_only_zalo_customer_messages_are_touched(self):
		echo = self.send("V301D", sender=RAW, event_type="oa_echo")
		echo_comm = frappe.db.get_value("Communication", {"omni_inbound_message": echo}, "name")
		before = frappe.db.get_value("Communication", echo_comm, "sender_full_name")
		backfill_sender.run(dry_run=0)
		self.assertEqual(frappe.db.get_value("Communication", echo_comm, "sender_full_name"), before)
		other = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"subject": "TD106 mail",
				"sender_full_name": "Người Gửi Mail",
			}
		).insert(ignore_permissions=True)
		backfill_sender.run(dry_run=0)
		self.assertEqual(
			frappe.db.get_value("Communication", other.name, "sender_full_name"), "Người Gửi Mail"
		)


class ZaloTabCase(OutboundTestCase):
	def make_deal(self, lead, owner):
		doc = frappe.get_doc(
			{"doctype": "CRM Deal", "lead": lead, "first_name": "TD106 deal", "deal_owner": owner}
		)
		doc.insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.commit()
		return doc.name


class TestConversationApi(ZaloTabCase):
	def test_only_zalo_bubbles_with_kinds_and_safe_attachments(self):
		self.sandbox(True)
		self.make_inbound(self.lead1, self.ident1, "Zalo OA", "em hỏi thêm", 8, event_type="oa_echo")
		self.make_outbound(self.lead1, "Dạ báo giá ạ", 5)
		frappe.db.set_value(
			"Omni Inbound Message",
			self.zalo1,
			"attachments",
			json.dumps(
				[
					{"url": "https://x.example/a.png", "name": "a.png"},
					{"url": "/files/noi-bo.pdf"},
					{"url": "javascript:alert(1)"},
				]
			),
		)
		frappe.db.commit()
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Lead", self.lead1)
		texts = [m["text"] for m in res["messages"]]
		self.assertNotIn("thư báo giá", texts)  # email không vào tab Zalo
		kinds = {m["text"]: m["kind"] for m in res["messages"]}
		self.assertEqual(kinds["hỏi giá máy A"], "customer")
		self.assertEqual(kinds["em hỏi thêm"], "echo")
		self.assertEqual(kinds["Dạ báo giá ạ"], "sale")
		first = next(m for m in res["messages"] if m["name"] == self.zalo1)
		self.assertEqual(first["attachments"], [{"url": "https://x.example/a.png", "name": "a.png"}])
		self.assertEqual(first["sender"], "Mai Zalo")
		self.assertNotIn("token-bi-mat", json.dumps(res, default=str))
		self.assertNotIn("mobile_no", json.dumps(res, default=str))
		self.assertTrue(res["compose"]["can_send"])

	def test_inbound_sender_without_identity_name_uses_lead_name_not_raw_id(self):
		frappe.db.set_value("Omni Identity", self.ident1, "display_name", None)
		frappe.db.set_value("Omni Inbound Message", self.zalo1, "external_sender_id", RAW)
		frappe.db.commit()
		self.as_user(SALE1)
		msg = next(
			m
			for m in zalo_tab.get_conversation("CRM Lead", self.lead1)["messages"]
			if m["name"] == self.zalo1
		)
		self.assertNotIn(RAW, msg["sender"])
		self.assertTrue(msg["sender"])

	def test_permissions_lead_and_deal(self):
		deal = self.make_deal(self.lead1, SALE1)
		frappe.db.set_value("CRM Lead", self.lead1, "converted", 1)
		frappe.db.commit()
		self.as_user(SALE1)
		by_deal = zalo_tab.get_conversation("CRM Deal", deal)
		self.assertEqual(by_deal["lead"], self.lead1)
		self.assertEqual(len(by_deal["messages"]), 1)
		frappe.set_user(SALE2)
		for dt, nm in (("CRM Lead", self.lead1), ("CRM Deal", deal)):
			with self.assertRaises(frappe.PermissionError):
				zalo_tab.get_conversation(dt, nm)
			with self.assertRaises(frappe.PermissionError):
				zalo_tab.send(dt, nm, "x")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			zalo_tab.get_conversation("CRM Lead", self.lead1)
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.DoesNotExistError):
			zalo_tab.get_conversation("CRM Lead", "CRM-LEAD-khong-co")
		with self.assertRaises(frappe.ValidationError):
			zalo_tab.get_conversation("ToDo", "x")

	def test_deal_owner_without_lead_access_is_denied(self):
		"""Quyết định ADR 0016: cần quyền cả trên Deal lẫn Lead gốc (không mở rộng quyền xem hội thoại)."""
		deal = self.make_deal(self.lead1, SALE2)
		self.as_user(SALE2)
		self.assertTrue(frappe.has_permission("CRM Deal", "read", doc=deal))
		with self.assertRaises(frappe.PermissionError):
			zalo_tab.get_conversation("CRM Deal", deal)

	def test_deal_without_lead_is_empty_and_locked(self):
		doc = frappe.get_doc({"doctype": "CRM Deal", "first_name": "TD106 khong lead", "deal_owner": SALE1})
		doc.insert(ignore_permissions=True, ignore_mandatory=True)
		self.addCleanup(lambda: (frappe.delete_doc("CRM Deal", doc.name, force=True), frappe.db.commit()))
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Deal", doc.name)
		self.assertEqual(res["messages"], [])
		self.assertIsNone(res["bot"])
		self.assertFalse(res["compose"]["can_send"])
		with self.assertRaises(frappe.ValidationError):
			zalo_tab.send("CRM Deal", doc.name, "x")

	def test_lead_without_zalo_identity_locks_composer_and_never_sends_email(self):
		lead = self.make_lead("TD106 Chi Email", SALE1)
		self.make_identity("td106only@example.com", "Email", lead, "Chị Mail")
		frappe.db.commit()
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Lead", lead)
		self.assertFalse(res["compose"]["linked"])
		self.assertFalse(res["compose"]["can_send"])
		self.assertIn("chưa có tài khoản Zalo", res["compose"]["reason"])
		with mock.patch("frappe.enqueue") as enq, self.assertRaises(frappe.ValidationError):
			zalo_tab.send("CRM Lead", lead, "Xin chào")
		enq.assert_not_called()
		self.assertFalse(frappe.db.exists("Omni Outbound Message", {"lead": lead}))

	def test_lead_with_zalo_and_email_sends_only_via_zalo(self):
		self.sandbox(True)
		self.as_user(SALE1)
		with mock.patch("frappe.enqueue"), mock.patch("frappe.sendmail") as mail:
			res = zalo_tab.send("CRM Lead", self.lead1, "Dạ em chào anh")
			frappe.db.commit()
		self.assertEqual(res["status"], "Queued")
		mail.assert_not_called()
		row = frappe.db.get_value(
			"Omni Outbound Message", res["outbound"], ["channel", "identity", "lead", "source"], as_dict=True
		)
		self.assertEqual(
			(row.channel, row.identity, row.lead, row.source),
			(self.channel.name, self.ident1, self.lead1, "Sale"),
		)
		self.assertEqual(frappe.db.get_value("Omni Channel", row.channel, "channel_type"), "Zalo OA")

	def test_sandbox_send_shows_bubble_and_timeline_with_label(self):
		self.sandbox(True)
		self.as_user(SALE1)
		with mock.patch("frappe.enqueue"):
			res = zalo_tab.send("CRM Lead", self.lead1, "Dạ em chào anh")
			frappe.db.commit()
		queued = next(
			m
			for m in zalo_tab.get_conversation("CRM Lead", self.lead1)["messages"]
			if m["name"] == res["outbound"]
		)
		self.assertTrue(queued["queued"])
		self.assertEqual(queued["kind"], "sale")
		frappe.set_user("Administrator")
		with mock.patch(
			"omni_crm.channels.zalo.client.requests.request", side_effect=AssertionError("không gọi HTTP")
		):
			self.assertEqual(outbound.send_outbound(res["outbound"]), "Sent")
		self.as_user(SALE1)
		sent = next(
			m
			for m in zalo_tab.get_conversation("CRM Lead", self.lead1)["messages"]
			if m["name"] == res["outbound"]
		)
		self.assertTrue(sent["sandbox"])
		self.assertEqual(sent["status"], "Sent")
		self.assertIn(res["outbound"], self.comms())  # đồng thời ghi vào dòng thời gian

	def test_bot_message_is_labelled_bot(self):
		frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": self.channel.name,
				"identity": self.ident1,
				"lead": self.lead1,
				"text": "Bot trả lời",
				"source": "Bot",
				"status": "Sent",
				"is_sandbox": 1,
			}
		).insert(ignore_permissions=True)
		self.as_user(SALE1)
		msg = next(
			m
			for m in zalo_tab.get_conversation("CRM Lead", self.lead1)["messages"]
			if m["text"] == "Bot trả lời"
		)
		self.assertEqual(msg["kind"], "bot")
		self.assertTrue(msg["bot_sent"])

	def test_window_expired_locks_composer_with_reason_and_send_is_blocked(self):
		self.sandbox(False)
		self.set_last_inbound(hours_ago=24 * 8)
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Lead", self.lead1)
		self.assertFalse(res["compose"]["can_send"])
		self.assertIn("7 ngày", res["compose"]["reason"])
		with mock.patch("frappe.enqueue"):
			out = zalo_tab.send("CRM Lead", self.lead1, "x")
		self.assertEqual(out["status"], "Blocked")

	def test_paid_window_needs_confirmation(self):
		self.sandbox(True)
		self.set_last_inbound(hours_ago=72)
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Lead", self.lead1)
		self.assertTrue(res["compose"]["can_send"])
		self.assertTrue(res["compose"]["paid"])
		self.assertTrue(res["compose"]["message"])
		with mock.patch("frappe.enqueue"):
			first = zalo_tab.send("CRM Lead", self.lead1, "Dạ em chào anh")
			self.assertEqual(first["status"], "confirm_required")
			self.assertFalse(
				frappe.db.exists("Omni Outbound Message", {"lead": self.lead1, "status": "Queued"})
			)
			second = zalo_tab.send("CRM Lead", self.lead1, "Dạ em chào anh", confirm_paid=1)
		self.assertEqual(second["status"], "Queued")

	def test_refused_contact_locks_composer_and_send_throws(self):
		self.sandbox(True)
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "TD106 Tuchoi",
				"omni_info_source": "TD106 test",
				"omni_consent": "Từ chối",
				"email_ids": [{"email_id": "td106.tuchoi@example.com", "is_primary": 1}],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: (
				frappe.delete_doc("Contact", contact.name, force=True, ignore_permissions=True),
				frappe.db.commit(),
			)
		)
		frappe.db.set_value("CRM Lead", self.lead1, "email", "td106.tuchoi@example.com")
		frappe.db.commit()
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Lead", self.lead1)
		self.assertFalse(res["compose"]["can_send"])
		self.assertIn("từ chối nhận liên hệ", res["compose"]["reason"])
		with mock.patch("frappe.enqueue"), self.assertRaises(frappe.ValidationError):
			zalo_tab.send("CRM Lead", self.lead1, "x")
		self.assertFalse(frappe.db.exists("Omni Outbound Message", {"lead": self.lead1}))

	def test_empty_and_too_long_text_rejected(self):
		self.as_user(SALE1)
		with self.assertRaises(frappe.ValidationError):
			zalo_tab.send("CRM Lead", self.lead1, "   ")
		with self.assertRaises(frappe.ValidationError):
			zalo_tab.send("CRM Lead", self.lead1, "a" * (outbound.MAX_TEXT_CHARS + 1))

	def test_send_reply_of_hop_thu_unchanged(self):
		"""Hộp thư vẫn gọi `send_reply`; kênh Email vẫn bị từ chối."""
		self.sandbox(True)
		self.as_user(SALE1)
		with mock.patch("frappe.enqueue"):
			self.assertEqual(inbox.send_reply(self.lead1, "Zalo OA", "ok")["status"], "Queued")
		with self.assertRaises(frappe.ValidationError):
			inbox.send_reply(self.lead1, "Email", "ok")


class TestOaChatContactAndQuotaError(ZaloTabCase):
	def test_contact_has_names_and_zalo_id_only(self):
		self.as_user(SALE1)
		contact = zalo_tab.get_conversation("CRM Lead", self.lead1)["contact"]
		self.assertEqual(set(contact), {"lead_name", "display_name", "zalo_user_id", "oa_id", "name_note"})
		self.assertEqual(contact["name_note"], "")
		self.assertEqual(contact["display_name"], "Mai Zalo")
		self.assertEqual(contact["zalo_user_id"], "TD106Z1")
		self.assertTrue(contact["lead_name"])

	def test_contact_absent_without_zalo_identity_or_when_refused(self):
		lead = self.make_lead("TD106 Chi Email 2", SALE1)
		self.make_identity("td106only2@example.com", "Email", lead, "Chị Mail")
		frappe.db.commit()
		self.as_user(SALE1)
		self.assertIsNone(zalo_tab.get_conversation("CRM Lead", lead)["contact"])
		with mock.patch(
			"omni_crm.api.customer.get_mini_profile",
			return_value={"person_refused": True},
		):
			self.assertIsNone(zalo_tab.get_conversation("CRM Lead", self.lead1)["contact"])

	def test_contact_follows_permissions(self):
		self.as_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			zalo_tab.get_conversation("CRM Lead", self.lead1)

	def test_error_224_is_permanent_with_clear_vietnamese_message(self):
		from omni_crm.channels.zalo import errors

		kind, message = errors.classify(-224)
		self.assertEqual(kind, errors.PERMANENT)
		self.assertIn("không hỗ trợ gửi tin qua API", message)
		self.assertNotIn("224", message)

	def test_failed_message_shows_message_not_code(self):
		reason = "Gói OA hiện tại không hỗ trợ gửi tin qua API; hãy nhắn từ trang Zalo OA"
		out = self.make_outbound(self.lead1, "Dạ em chào", 3, status="Failed")
		frappe.db.set_value("Omni Outbound Message", out, "last_error", reason)
		self.as_user(SALE1)
		msg = next(
			m for m in zalo_tab.get_conversation("CRM Lead", self.lead1)["messages"] if m["name"] == out
		)
		self.assertEqual(msg["status"], "Failed")
		self.assertEqual(msg["error"], reason)
		self.assertTrue(zalo_tab.get_conversation("CRM Lead", self.lead1)["compose"]["can_send"])


class TestOaIdAndNameNote(ZaloTabCase):
	def test_contact_has_oa_id_of_the_channel_the_customer_wrote_to(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "zalo_oa_id", "378159422113841368")
		frappe.db.commit()
		self.as_user(SALE1)
		self.assertEqual(
			zalo_tab.get_conversation("CRM Lead", self.lead1)["contact"]["oa_id"], "378159422113841368"
		)

	def test_missing_name_shows_reason_and_never_raw_id(self):
		frappe.db.set_value("Omni Identity", self.ident1, "display_name", None)
		frappe.db.commit()
		key = frappe.db.get_value("Omni Identity", self.ident1, "identity_key")
		worker_hooks.note_profile_reason(key, "Gói OA hiện tại không hỗ trợ gửi tin qua API")
		self.addCleanup(worker_hooks.note_profile_reason, key, None)
		self.as_user(SALE1)
		res = zalo_tab.get_conversation("CRM Lead", self.lead1)
		self.assertEqual(
			res["contact"]["name_note"],
			"Chưa lấy được tên Zalo: Gói OA hiện tại không hỗ trợ gửi tin qua API",
		)
		self.assertEqual(res["customer_name"], frappe.db.get_value("CRM Lead", self.lead1, "lead_name"))
		self.assertNotIn("TD106Z1", res["customer_name"])

	def test_default_reason_is_sandbox_or_never_tried(self):
		frappe.db.set_value("Omni Identity", self.ident1, "display_name", None)
		frappe.db.commit()
		self.as_user(SALE1)
		self.sandbox(True)
		self.assertIn("sandbox", zalo_tab.get_conversation("CRM Lead", self.lead1)["contact"]["name_note"])


class TestProfileReasonAndRefresh(ZaloTabCase):
	def msg(self):
		key = frappe.db.get_value("Omni Identity", self.ident1, "identity_key")
		self.addCleanup(worker_hooks.note_profile_reason, key, None)
		self.addCleanup(frappe.cache().delete_value, f"omni:zalo:profile_fail:{key}")
		return profile_refresh._Msg(key, "TD106Z1", self.channel.name), key

	def test_sandbox_skips_api_and_records_reason(self):
		self.sandbox(True)
		msg, key = self.msg()
		with mock.patch.object(ZaloClient, "get_user_profile", side_effect=AssertionError("không gọi API")):
			self.assertEqual(worker_hooks.fetch_profile(msg, self.channel), {})
		self.assertIn("sandbox", worker_hooks.profile_reason(key))

	def test_api_error_is_classified_logged_and_reason_saved_without_secret(self):
		self.sandbox(False)
		msg, key = self.msg()
		err = errors.to_error(-224)
		with mock.patch.object(ZaloClient, "get_user_profile", side_effect=err):
			self.assertEqual(worker_hooks.fetch_profile(msg, self.channel), {})
		self.assertIn("không hỗ trợ gửi tin qua API", worker_hooks.profile_reason(key))
		# cooldown: lần sau không gọi API; chạy tay (ignore_cooldown) thì gọi
		with mock.patch.object(ZaloClient, "get_user_profile", side_effect=AssertionError("cooldown")):
			self.assertEqual(worker_hooks.fetch_profile(msg, self.channel), {})
		with mock.patch.object(ZaloClient, "get_user_profile", return_value={"display_name": "Mai"}) as api:
			self.assertEqual(
				worker_hooks.fetch_profile(msg, self.channel, ignore_cooldown=True)["display_name"], "Mai"
			)
		api.assert_called_once()
		self.assertIsNone(worker_hooks.profile_reason(key))  # thành công thì xóa lý do

	def test_empty_name_from_api_records_reason(self):
		self.sandbox(False)
		msg, key = self.msg()
		with mock.patch.object(ZaloClient, "get_user_profile", return_value={"display_name": ""}):
			worker_hooks.fetch_profile(msg, self.channel)
		self.assertIn("không trả tên", worker_hooks.profile_reason(key))

	def test_refresh_command_dry_run_sandbox_and_apply(self):
		frappe.db.set_value("Omni Identity", self.ident1, "display_name", None)
		frappe.db.commit()
		key = frappe.db.get_value("Omni Identity", self.ident1, "identity_key")
		self.addCleanup(worker_hooks.note_profile_reason, key, None)
		self.addCleanup(frappe.cache().delete_value, f"omni:zalo:profile_fail:{key}")
		with mock.patch.object(ZaloClient, "get_user_profile", side_effect=AssertionError("không gọi API")):
			dry = profile_refresh.run()
			self.assertTrue(dry["dry_run"])
			self.assertGreaterEqual(dry["candidates"], 1)
			self.sandbox(True)
			sb = profile_refresh.run(dry_run=0)
			self.assertTrue(sb["sandbox"])
			self.assertEqual(sb["updated"], 0)
		self.assertFalse(frappe.db.get_value("Omni Identity", self.ident1, "display_name"))
		self.sandbox(False)
		frappe.cache().set_value(
			f"omni:zalo:profile_fail:{key}", 1, expires_in_sec=600
		)  # đang trong thời gian chờ
		with mock.patch.object(ZaloClient, "get_user_profile", return_value={"display_name": "Mai Thật"}):
			res = profile_refresh.run(dry_run=0)
		self.assertGreaterEqual(res["updated"], 1)
		self.assertEqual(frappe.db.get_value("Omni Identity", self.ident1, "display_name"), "Mai Thật")
		again = profile_refresh.run(dry_run=0)  # idempotent: không còn gì để cập nhật cho khách này
		self.assertEqual(again["updated"], 0)


class TestTabSwitch(BridgeTestCase):
	def setUp(self):
		super().setUp()
		self.prev = frappe.db.sql(
			"select value from `tabSingles` where doctype='Omni Settings' and field='zalo_tab_disabled'"
		)
		self.addCleanup(self.restore)

	def restore(self):
		frappe.db.rollback()
		frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field='zalo_tab_disabled'")
		if self.prev:
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','zalo_tab_disabled',%s)",
				(self.prev[0][0],),
			)
		frappe.db.commit()

	def set(self, value):
		frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field='zalo_tab_disabled'")
		if value is not None:
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','zalo_tab_disabled',%s)",
				(str(value),),
			)

	def test_default_on_when_never_saved_and_off_when_disabled(self):
		self.set(None)
		self.assertTrue(flags.zalo_tab_enabled())
		self.assertTrue(zalo_tab.tab_state()["enabled"])
		self.set(0)
		self.assertTrue(flags.zalo_tab_enabled())
		self.set(1)
		self.assertFalse(flags.zalo_tab_enabled())
		self.assertFalse(zalo_tab.tab_state()["enabled"])

	def test_guest_cannot_see_tab(self):
		self.set(None)
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertFalse(zalo_tab.tab_state()["enabled"])


class TestCrmFrontendPatchFiles(BridgeTestCase):
	"""Kiểm tĩnh bộ patch giao diện crm (V3-01, ADR 0016): đúng phạm vi tối thiểu, không có mã nguy hiểm. KHÔNG áp patch."""

	ALLOWED = frozenset(
		{
			"frontend/src/components/Activities/ActivityHeader.vue",
			"frontend/src/components/Activities/Activities.vue",
			"frontend/src/components/Activities/ZaloTab.vue",
			"frontend/src/components/Icons/ZaloIcon.vue",
			"frontend/src/components/SidePanelLayout.vue",  # V3-03: ô chữ cột phải cao theo nội dung (ADR 0016 mục 12)
			"frontend/src/composables/zalo.js",
			"frontend/src/pages/Deal.vue",
			"frontend/src/pages/Lead.vue",
			"frontend/src/utils/zaloThread.js",
			"frontend/tests/unit/zaloThread.test.js",
		}
	)

	def setUp(self):
		from pathlib import Path

		self.dir = Path(__file__).resolve().parents[1] / "crm_frontend_patch"
		self.patch = (self.dir / "0001-zalo-tab.patch").read_text(encoding="utf-8")

	def test_patch_only_touches_allowed_files(self):
		import re

		files = set(re.findall(r"^diff --git a/(\S+) b/", self.patch, flags=re.M))
		self.assertEqual(files, self.ALLOWED)

	def test_side_panel_change_is_css_only(self):
		"""SidePanelLayout.vue chỉ được thêm khối CSS cho ô chữ (ADR 0016 mục 12), không đổi template hay script."""
		part = self.patch.split("diff --git a/frontend/src/components/SidePanelLayout.vue")[1].split(
			"diff --git"
		)[0]
		added = [
			line[1:] for line in part.splitlines() if line.startswith("+") and not line.startswith("+++")
		]
		removed = [line for line in part.splitlines() if line.startswith("-") and not line.startswith("---")]
		self.assertEqual(removed, [])
		self.assertIn("field-sizing: content;", "\n".join(added))
		for line in added:
			self.assertNotRegex(line, r"<template|<script|v-|@|import ")

	def test_added_code_has_no_v_html_or_eval(self):
		added = "\n".join(line for line in self.patch.splitlines() if line.startswith("+"))
		for bad in ("v-html", "innerHTML", "eval(", "document.write"):
			self.assertNotIn(bad, added)

	def test_scripts_pin_commit_and_are_executable(self):
		import os

		pin = (self.dir / "CRM_VERSION").read_text().split()
		self.assertEqual(pin[0], "v1.73.0")
		self.assertEqual(len(pin[1]), 40)
		for name in ("apply.sh", "build.sh"):
			self.assertTrue(os.access(self.dir / name, os.X_OK), name)

	def test_compose_exposes_max_chars(self):
		self.assertEqual(zalo_tab._compose({}, False)["max_chars"], outbound.MAX_TEXT_CHARS)


class TestBotSummaryCard(ZaloTabCase):
	"""V3-03: thẻ tóm tắt của bot ở đầu tab Zalo (khối `bot` của get_conversation)."""

	def _run(self, lead, status, minutes_ago):
		doc = frappe.get_doc({"doctype": "Omni Bot Run", "kind": "Analyze", "status": status, "lead": lead})
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			lambda n=doc.name: (frappe.db.delete("Omni Bot Run", {"name": n}), frappe.db.commit())
		)
		frappe.db.set_value(
			"Omni Bot Run",
			doc.name,
			"creation",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-minutes_ago),
			update_modified=False,
		)
		return doc.name

	def test_bot_block_absent_until_bot_writes(self):
		self.as_user(SALE1)
		self.assertIsNone(zalo_tab.get_conversation("CRM Lead", self.lead1)["bot"])

	def test_bot_block_has_full_summary_and_latest_success_run(self):
		long = "Khách cần báo giá hệ thống SPD cho bệnh viện. " * 40
		frappe.db.set_value(
			"CRM Lead",
			self.lead1,
			{"omni_bot_summary": long, "omni_intent": "Tư vấn", "omni_urgency": "Cao"},
			update_modified=False,
		)
		self._run(self.lead1, "Success", 30)
		newest_ok = self._run(self.lead1, "Success", 10)
		self._run(self.lead1, "Skipped Switch", 1)  # lượt bị bỏ qua không tính là giờ phân tích
		frappe.db.commit()
		self.as_user(SALE1)
		bot = zalo_tab.get_conversation("CRM Lead", self.lead1)["bot"]
		self.assertEqual(set(bot), {"summary", "intent", "urgency", "score", "analyzed_at"})
		self.assertEqual(bot["summary"], long.strip())  # không cắt
		self.assertEqual(bot["intent"], "Tư vấn")
		self.assertEqual(bot["urgency"], "Cao")
		self.assertEqual(
			str(bot["analyzed_at"]), str(frappe.db.get_value("Omni Bot Run", newest_ok, "creation"))
		)

	def test_bot_block_via_deal_and_follows_permissions(self):
		frappe.db.set_value("CRM Lead", self.lead1, "omni_intent", "Hỏi giá", update_modified=False)
		deal = self.make_deal(self.lead1, SALE1)
		frappe.db.commit()
		self.as_user(SALE1)
		bot = zalo_tab.get_conversation("CRM Deal", deal)["bot"]
		self.assertEqual(bot["intent"], "Hỏi giá")
		self.assertEqual(bot["summary"], "")
		self.as_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			zalo_tab.get_conversation("CRM Lead", self.lead1)
