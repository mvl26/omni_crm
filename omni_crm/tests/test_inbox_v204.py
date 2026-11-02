# ruff: noqa: RUF001
"""V2-04 (docs/13 mục 2, docs/14 mục 8): Hộp thư — tìm theo SĐT/email/đơn vị, chip lọc, nhãn ưu tiên, đơn vị + hạng, hồ sơ rút gọn."""

from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import now_datetime

from omni_crm.api import inbox
from omni_crm.crm_bridge import care_log
from omni_crm.dashboard import priority
from omni_crm.tests.test_inbox import SALE1, SALE2, InboxTestCase
from omni_crm.tests.test_prospect_model import make_org


class TestInboxV204(InboxTestCase):
	"""Dữ liệu thêm của test này không commit nên bị rollback khi dọn (BridgeTestCase.cleanup)."""

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")
		self.org = make_org("TD404 Inbox Org", "0900404800")
		frappe.db.set_value("CRM Organization", self.org, {"omni_fit_tier": "A"}, update_modified=False)
		frappe.db.set_value(
			"CRM Lead",
			self.lead1,
			{"organization": self.org, "mobile_no": "0900404888", "email": "td404inb@example.com"},
		)
		self.lead3 = self.make_lead("TD106 Hung", SALE1)
		self.make_inbound(self.lead3, None, "Zalo OA", "khách thứ ba", 40)

	def names(self, user, **filters):
		frappe.set_user(user)
		return [r["lead"] for r in inbox.list_conversations(filters)["rows"]]

	def test_search_by_name_phone_email_and_company(self):
		for term in ("Mai", "0900404888", "td404inb@", "TD404 Inbox"):
			with self.subTest(term):
				self.assertEqual(self.names(SALE1, search=term), [self.lead1])
		self.assertEqual(self.names(SALE1, search="khong-co-ai-ten-nay"), [])
		self.assertEqual(self.names(SALE1, search="  "), self.names(SALE1))  # tìm rỗng = không lọc

	def test_search_never_shows_other_peoples_leads(self):
		self.assertEqual(self.names(SALE2, search="Mai"), [])
		self.assertEqual(self.names(SALE2, search="0900404888"), [])

	def test_mine_and_search_combine_with_and_not_or(self):
		self.assertEqual(self.names(SALE1, mine=1, search="Mai"), [self.lead1])
		self.assertEqual(self.names(SALE1, mine=1, search="Hung"), [self.lead3])
		self.assertEqual(self.names(SALE1, mine=1, search="Binh"), [])  # Lead của SALE2

	def test_rows_carry_company_tier_and_priority(self):
		with mock.patch.object(priority.Context, "business_minutes", return_value=30.0):
			frappe.set_user(SALE1)
			rows = {r["lead"]: r for r in inbox.list_conversations({})["rows"]}
		row = rows[self.lead1]
		self.assertEqual((row["organization"], row["fit_tier"]), (self.org, "A"))
		self.assertEqual(row["priority"], "overdue")  # tin cuối là của khách, đã chờ > SLA
		self.assertEqual(row["priority_label"], "Quá hạn phản hồi")
		self.assertEqual((rows[self.lead3]["organization"], rows[self.lead3]["fit_tier"]), ("", None))
		for key in ("unrecorded", "draft_pending", "unanswered", "last_text"):
			self.assertIn(key, row)  # khóa cũ vẫn còn

	def test_answered_conversation_has_no_priority_label(self):
		self.make_outbound(self.lead1, "Dạ em trả lời", 1)
		frappe.set_user(SALE1)
		with mock.patch.object(priority.Context, "business_minutes", return_value=30.0):
			rows = {r["lead"]: r for r in inbox.list_conversations({})["rows"]}
		self.assertFalse(rows[self.lead1]["unanswered"])
		self.assertIsNone(rows[self.lead1]["priority"])

	def test_tier_hidden_when_fit_scoring_is_off(self):
		frappe.set_user(SALE1)
		with mock.patch("omni_crm.prospect.fit.fit_enabled", return_value=False):
			rows = {r["lead"]: r for r in inbox.list_conversations({})["rows"]}
		self.assertIsNone(rows[self.lead1]["fit_tier"])

	def test_has_draft_chip_and_unrecorded_label(self):
		care_log.touch_session(self.lead1, "Zalo OA", now_datetime() - timedelta(hours=3), "Sale", SALE1)
		frappe.set_user(SALE1)
		with mock.patch("omni_crm.api.drafts.leads_with_pending", return_value={self.lead1}):
			rows = inbox.list_conversations({"has_draft": 1})["rows"]
		self.assertEqual([r["lead"] for r in rows], [self.lead1])
		self.assertTrue(rows[0]["unrecorded"])
		self.assertTrue(rows[0]["draft_pending"])

	def test_channel_chip_for_calls(self):
		self.make_identity("TD106T1", "Telephony", self.lead3, "Khách gọi")
		self.assertEqual(self.names(SALE1, channel="Telephony"), [self.lead3])

	def test_thread_has_mini_profile_with_company_and_tier(self):
		frappe.set_user(SALE1)
		profile = inbox.get_thread(self.lead1)["profile"]
		self.assertEqual((profile["organization"], profile["fit_tier"]), (self.org, "A"))
		self.assertIn("next_step", profile)
