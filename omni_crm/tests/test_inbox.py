import json
import time
from unittest import mock

import frappe

from omni_crm.api import inbox
from omni_crm.tests.bridge_helpers import PREFIX, USERS, BridgeTestCase, make_users

SALE1, SALE2, SALE3 = USERS


class InboxTestCase(BridgeTestCase):
	def setUp(self):
		super().setUp()
		make_users()
		# Lead 1 của SALE1 (có tin Zalo + email + tin ra), Lead 2 của SALE2
		self.lead1 = self.make_lead("TD106 Mai", SALE1)
		self.lead2 = self.make_lead("TD106 Binh", SALE2)
		self.ident1 = self.make_identity("TD106Z1", "Zalo OA", self.lead1, "Mai Zalo")
		self.ident2 = self.make_identity("TD106Z2", "Zalo OA", self.lead2, "Binh Zalo")
		self.make_identity("td106mai@example.com", "Email", self.lead1, "Mai Mail")
		self.zalo1 = self.make_inbound(self.lead1, self.ident1, "Zalo OA", "hỏi giá máy A", 30)
		self.mail1 = self.make_inbound(self.lead1, None, "Email", "thư báo giá", 20)
		self.zalo2 = self.make_inbound(self.lead2, self.ident2, "Zalo OA", "tin của khách khác", 10)
		frappe.db.commit()

	def make_lead(self, name: str, owner: str) -> str:
		doc = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": name, "lead_owner": owner, "omni_first_channel": "Zalo OA"}
		)
		doc.flags.ignore_permissions = True
		with mock.patch("frappe.enqueue"):
			doc.insert()
		frappe.db.set_value("CRM Lead", doc.name, "omni_last_inbound_at", frappe.utils.now_datetime())
		return doc.name

	def make_identity(self, ext: str, channel_type: str, lead: str, display: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Identity",
					"identity_key": f"{channel_type}:{ext}",
					"channel_type": channel_type,
					"external_id": ext,
					"display_name": display,
					"lead": lead,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_inbound(self, lead, identity, channel_type, text, minutes_ago, event_type="message") -> str:
		at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-minutes_ago)
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Inbound Message",
					"dedupe_key": f"{self.channel.name}:{frappe.generate_hash(length=10)}",
					"channel": self.channel.name,
					"channel_type": channel_type,
					"event_type": event_type,
					"identity_key": f"{channel_type}:TD106{frappe.generate_hash(length=6)}",
					"identity": identity,
					"lead": lead,
					"received_at": at,
					"text": text,
					"attachments": json.dumps(
						[{"url": "https://x.example/a.png", "name": "a.png"}, {"url": "javascript:x"}]
					),
					"raw_payload": json.dumps({"secret": "token-bi-mat"}),
					"status": "Done",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_outbound(self, lead, text, minutes_ago, status="Sent") -> str:
		at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-minutes_ago)
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Outbound Message",
					"channel": self.channel.name,
					"lead": lead,
					"text": text,
					"source": "Sale",
					"sent_by": SALE1,
					"status": status,
					"sent_at": at,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def as_user(self, user):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(user)


class TestGetThread(InboxTestCase):
	def test_owner_sees_all_channels_in_time_order(self):
		self.make_outbound(self.lead1, "Dạ báo giá ạ", 5)
		self.as_user(SALE1)
		data = inbox.get_thread(self.lead1)
		self.assertEqual(
			[m["text"] for m in data["messages"]], ["hỏi giá máy A", "thư báo giá", "Dạ báo giá ạ"]
		)
		self.assertEqual([m["direction"] for m in data["messages"]], ["in", "in", "out"])
		self.assertEqual([m["channel_type"] for m in data["messages"]], ["Zalo OA", "Email", "Zalo OA"])
		self.assertEqual({i["channel_type"] for i in data["identities"]}, {"Zalo OA", "Email"})
		self.assertEqual(data["crm_url"], f"/crm/leads/{self.lead1}")
		self.assertNotIn("token-bi-mat", json.dumps(data, default=str))

	def test_attachments_only_safe_urls(self):
		self.as_user(SALE1)
		msg = next(m for m in inbox.get_thread(self.lead1)["messages"] if m["name"] == self.zalo1)
		self.assertEqual(msg["attachments"], [{"url": "https://x.example/a.png", "name": "a.png"}])

	def test_sales_user_without_access_is_denied(self):
		self.as_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			inbox.get_thread(self.lead1)
		with self.assertRaises(frappe.PermissionError):
			frappe.set_user(SALE3)
			inbox.get_thread(self.lead2)

	def test_guest_denied_and_unknown_lead(self):
		self.as_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			inbox.get_thread(self.lead1)
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.DoesNotExistError):
			inbox.get_thread("CRM-LEAD-khong-co")

	def test_converted_lead_points_to_deal(self):
		frappe.get_doc({"doctype": "CRM Deal", "lead": self.lead1, "first_name": "x"}).insert(
			ignore_permissions=True, ignore_mandatory=True
		)
		frappe.db.set_value("CRM Lead", self.lead1, "converted", 1)
		deal = frappe.db.get_value("CRM Deal", {"lead": self.lead1}, "name")
		data = inbox.get_thread(self.lead1)
		self.assertEqual(data["deal"], deal)
		self.assertEqual(data["crm_url"], f"/crm/deals/{deal}")


class TestListConversations(InboxTestCase):
	def names(self, **filters):
		return [r["lead"] for r in inbox.list_conversations(filters)["rows"]]

	def test_sales_user_sees_only_own_leads(self):
		self.as_user(SALE1)
		self.assertEqual(self.names(), [self.lead1])
		frappe.set_user(SALE2)
		self.assertEqual(self.names(), [self.lead2])
		frappe.set_user(SALE3)
		self.assertEqual(self.names(), [])

	def test_admin_sees_both(self):
		names = self.names()
		self.assertIn(self.lead1, names)
		self.assertIn(self.lead2, names)

	def test_unanswered_and_reply(self):
		self.as_user(SALE1)
		row = inbox.list_conversations({})["rows"][0]
		self.assertTrue(row["unanswered"])
		self.assertEqual(row["last_direction"], "in")
		self.assertEqual(row["last_text"], "thư báo giá")
		self.assertEqual(self.names(unanswered=1), [self.lead1])
		self.make_outbound(self.lead1, "đã trả lời", 1)
		frappe.db.commit()
		self.assertEqual(self.names(unanswered=1), [])
		self.assertTrue(inbox.list_conversations({})["rows"][0]["last_direction"] == "out")

	def test_failed_outbound_does_not_count_as_reply(self):
		self.make_outbound(self.lead1, "lỗi gửi", 1, status="Failed")
		self.as_user(SALE1)
		self.assertEqual(self.names(unanswered=1), [self.lead1])

	def test_oa_echo_counts_as_reply(self):
		self.make_inbound(self.lead1, self.ident1, "Zalo OA", "em trả lời ngoài", 1, event_type="oa_echo")
		self.as_user(SALE1)
		self.assertEqual(self.names(unanswered=1), [])

	def test_filter_by_channel_mine_and_search(self):
		self.assertEqual(sorted(self.names(channel="Email")), [self.lead1])
		self.assertIn(self.lead2, self.names(channel="Zalo OA"))
		self.assertEqual(self.names(search="Binh"), [self.lead2])
		frappe.set_user(SALE1)
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertEqual(self.names(mine=1), [self.lead1])
		frappe.set_user("Administrator")
		self.assertEqual(sorted(self.names(mine=1)), [])  # Administrator không phải chủ Lead nào

	def test_filters_accept_json_string_and_paging(self):
		res = inbox.list_conversations(json.dumps({"search": "TD106"}), page=1, page_length=1)
		self.assertEqual(len(res["rows"]), 1)
		self.assertTrue(res["has_more"])
		self.assertEqual(res["total"], 2)
		res2 = inbox.list_conversations({"search": "TD106"}, page=2, page_length=1)
		self.assertEqual(len(res2["rows"]), 1)
		self.assertNotEqual(res["rows"][0]["lead"], res2["rows"][0]["lead"])

	def test_fifty_conversations_under_one_second(self):
		with mock.patch("frappe.enqueue"):
			for i in range(50):
				lead = self.make_lead(f"TD106 Bulk {i:02d}", SALE1)
				self.make_inbound(lead, None, "Zalo OA", f"tin số {i}", i)
		frappe.db.commit()
		self.as_user(SALE1)
		start = time.perf_counter()
		res = inbox.list_conversations({}, page_length=50)
		elapsed = time.perf_counter() - start
		self.assertEqual(len(res["rows"]), 50)
		self.assertLess(elapsed, 1.0, f"list_conversations mất {elapsed:.2f}s")


class TestDocPermissions(InboxTestCase):
	def test_sales_user_reads_only_messages_of_visible_leads(self):
		self.as_user(SALE1)
		got = frappe.get_list(
			"Omni Inbound Message", filters={"channel": self.channel.name}, pluck="name", limit_page_length=50
		)
		self.assertCountEqual(got, [self.zalo1, self.mail1])
		self.assertTrue(frappe.has_permission("Omni Inbound Message", "read", doc=self.zalo1))
		self.assertFalse(frappe.has_permission("Omni Inbound Message", "read", doc=self.zalo2))
		idents = frappe.get_list("Omni Identity", filters={"identity_key": ["like", "%TD106%"]}, pluck="name")
		self.assertNotIn(self.ident2, idents)
		self.assertIn(self.ident1, idents)

	def test_other_sales_user_sees_nothing(self):
		self.as_user(SALE3)
		self.assertEqual(
			frappe.get_list("Omni Inbound Message", filters={"channel": self.channel.name}, pluck="name"), []
		)
		out = self.make_outbound(self.lead1, "x", 1)
		frappe.set_user(SALE3)
		self.assertFalse(frappe.has_permission("Omni Outbound Message", "read", doc=out))

	def test_admin_unrestricted(self):
		got = frappe.get_list("Omni Inbound Message", filters={"channel": self.channel.name}, pluck="name")
		self.assertCountEqual(got, [self.zalo1, self.mail1, self.zalo2])

	def test_raw_payload_hidden_from_sales_user(self):
		self.as_user(SALE1)
		doc = frappe.get_doc("Omni Inbound Message", self.zalo1)
		doc.apply_fieldlevel_read_permissions()
		self.assertFalse(doc.get("raw_payload"))
		rows = frappe.get_list(
			"Omni Inbound Message", filters={"name": self.zalo1}, fields=["name", "raw_payload"]
		)
		self.assertEqual(len(rows), 1)
		self.assertFalse(rows[0].get("raw_payload"))  # Frappe bỏ trường permlevel 1 khỏi truy vấn
		frappe.set_user("Administrator")
		self.assertTrue(frappe.get_doc("Omni Inbound Message", self.zalo1).raw_payload)
