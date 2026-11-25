"""V3-13: duyệt nháp bot ngay trong app CRM (tab Zalo của Lead) và báo bằng chuông CRM, không qua Hộp thư Desk.

Mọi lời gọi AI qua FakeAIClient, gửi ở chế độ sandbox; bot không tự gửi gì.
"""

from unittest import mock

import frappe

from omni_crm.api import drafts, zalo_tab
from omni_crm.crm_bridge import notify
from omni_crm.marketing import kb
from omni_crm.tests.test_bot_reply import reply_ok
from omni_crm.tests.test_draft import TEXT, DraftTestCase
from omni_crm.tests.test_inbox import SALE1

PREFIX = "T313"


class CrmDraftTestCase(DraftTestCase):
	def setUp(self):
		super().setUp()
		p = mock.patch("omni_crm.flags.zalo_tab_enabled", return_value=True)
		p.start()
		self.addCleanup(p.stop)
		self.addCleanup(self._clean_crm)

	def _clean_crm(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.delete(notify.CRM_NOTIFICATION, {"reference_name": ["in", [self.lead1, self.lead2]]})
		for d in frappe.get_all(kb.DOCUMENT, filters={"title": ["like", f"{PREFIX}%"]}, pluck="name"):
			frappe.db.delete(kb.CHUNK, {"document": d})
			frappe.db.delete(kb.DOCUMENT, d)
		frappe.db.commit()
		kb._index_cache.clear()

	def conversation(self, user=SALE1):
		frappe.set_user(user)
		try:
			return zalo_tab.get_conversation("CRM Lead", self.lead1)
		finally:
			frappe.set_user("Administrator")

	def crm_notes(self):
		return frappe.get_all(
			notify.CRM_NOTIFICATION,
			# chỉ thông báo của omni_crm (CRM tự tạo thông báo giao việc riêng khi gán Lead)
			filters={"reference_name": self.lead1, "notification_text": ["like", "%omni-bot%"]},
			fields=["to_user", "notification_text", "reference_doctype", "type"],
		)


class TestZaloTabDrafts(CrmDraftTestCase):
	def test_conversation_has_pending_draft_and_send_works(self):
		name = self.make_draft()
		(item,) = self.conversation()["drafts"]
		self.assertEqual((item["name"], item["text"], item["can_send"]), (name, TEXT, True))
		self.assertEqual(self.call(drafts.send_draft, name)["status"], "Queued")
		self.assertEqual(self.conversation()["drafts"], [])
		self.assertEqual(self.bot_outbound(), 0)

	def test_template_draft_listed_for_fill_and_send(self):
		self.make_draft(
			"Cho xin báo giá găng tay",
			reply_ok(action="handoff", kb_item_ids=[], handoff_reason="Khách hỏi giá"),
		)
		(item,) = self.conversation()["drafts"]
		self.assertTrue(item["template"])
		self.assertIn("[", item["text"])

	def test_shadow_drafts_not_listed(self):
		from omni_crm.tests.test_ai_client import set_settings

		set_settings(bot_reply_mode="Shadow")
		self.make_draft()
		self.assertEqual(self.conversation()["drafts"], [])

	def test_document_passage_shown_as_source(self):
		with mock.patch.object(frappe, "enqueue"):
			doc = frappe.get_doc(
				{
					"doctype": kb.DOCUMENT,
					"title": f"{PREFIX} Giờ làm việc",
					"content": "Miyano làm việc từ 8 giờ đến 17 giờ 30 ạ.",
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
		kb.index_document(doc.name)
		chunk = frappe.get_all(kb.CHUNK, filters={"document": doc.name}, pluck="name")[0]
		name = self.make_draft(result=reply_ok(reply_text=TEXT, kb_item_ids=[chunk]))
		(item,) = self.conversation()["drafts"]
		self.assertEqual([k["id"] for k in item["kb"]], [chunk])
		self.assertEqual(item["kb"][0]["question"], f"{PREFIX} Giờ làm việc")
		frappe.set_user(SALE1)
		try:
			src = drafts.get_draft_kb_item(name, chunk)
		finally:
			frappe.set_user("Administrator")
		self.assertIn("17 giờ 30", src["answer"])


class TestCrmNotification(CrmDraftTestCase):
	def test_draft_notifies_crm_bell_without_customer_text(self):
		self.make_draft("Cho hỏi giờ làm việc SECRETTEXT")
		(note,) = self.crm_notes()
		self.assertEqual((note.to_user, note.reference_doctype), (SALE1, "CRM Lead"))
		self.assertIn(self.lead1, note.notification_text)
		self.assertNotIn("SECRETTEXT", note.notification_text)
		self.assertNotIn(TEXT, note.notification_text)

	def test_handoff_notifies_crm_bell(self):
		self.make_draft(
			"Tôi muốn khiếu nại SECRETTEXT", reply_ok(action="handoff", kb_item_ids=[], handoff_reason="x")
		)
		notes = self.crm_notes()
		self.assertTrue(notes)
		self.assertTrue(all("SECRETTEXT" not in n.notification_text for n in notes))

	def test_link_points_to_crm(self):
		self.assertEqual(notify.crm_lead_link("CRM-LEAD-1"), "/crm/leads/CRM-LEAD-1#zalo")
