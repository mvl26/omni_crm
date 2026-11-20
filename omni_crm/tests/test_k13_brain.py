"""Bộ não cải tiến của bot kịch bản (ADR 0033): xuất kịch bản thành tài liệu, rà hội thoại, đề xuất chờ duyệt."""

from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import now_datetime

from omni_crm.script import brain
from omni_crm.tests.test_k2_flow import FlowCase

OUT = "Omni Outbound Message"
NODE = "Omni Script Node"


class TestBrain(FlowCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(self._clean)
		self._clean()

	def _clean(self):
		frappe.db.rollback()
		for name in frappe.get_all(NODE, filters={"code": ["like", "DX_%"]}, pluck="name"):
			frappe.delete_doc(NODE, name, force=True, ignore_permissions=True)
		for name in (brain.VOCAB_ACTIVE, brain.VOCAB_PENDING):
			if frappe.db.exists(NODE, name):
				frappe.delete_doc(NODE, name, force=True, ignore_permissions=True)
		frappe.db.delete("Omni Knowledge Item", {"source": ["like", f"{brain.SOURCE}%"]})
		frappe.db.delete("Omni KB Document", {"title": brain.DOC_TITLE})
		frappe.cache().delete_value(brain._vocab_key())
		frappe.db.commit()

	def _staff_says(self, text):
		frappe.get_doc(
			{"doctype": OUT, "channel": self.channel.name, "identity": self.ident1, "lead": self.lead1, "text": text, "source": "Sale", "status": "Sent"}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def test_script_markdown_is_compact_and_skips_internal_nodes(self):
		nodes = [
			{"code": "MENU_X", "status": "Đang dùng", "step": "1", "service": "", "title": "Menu", "text": "Chào {ten}", "buttons": ["A → B"], "action": "service:SPD"},
			{"code": "DX_MENU_X_ABC123", "status": "Cần duyệt", "text": "đề xuất"},
			{"code": brain.VOCAB_ACTIVE, "status": "Đang dùng", "text": "ống thở => MENU_VATTU"},
		]
		md = brain.script_markdown(nodes)
		self.assertIn("## MENU_X [Đang dùng]", md)
		self.assertIn("Nút: A → B", md)
		self.assertNotIn("DX_MENU_X", md)
		self.assertNotIn("ống thở", md)

	def test_sync_document_creates_inactive_kb_document_once(self):
		name = brain.sync_document()
		self.assertTrue(name)
		doc = frappe.get_doc("Omni KB Document", name)
		self.assertEqual((doc.title, doc.is_active, doc.category), (brain.DOC_TITLE, 0, brain.DOC_CATEGORY))
		self.assertIn("## MENU_CHINH", doc.content)
		self.assertIsNone(brain.sync_document())  # không đổi thì không ghi lại

	def test_weak_spots_and_transcript_mask_contact(self):
		self.greeted()
		self.turn("ờ thì cái kia kìa, sđt anh 0912345678")
		self.assertEqual(self.sent()[-1], "REASK_CHUARO")
		spots = brain.weak_spots(now_datetime().date())
		self.assertEqual([(s["identity"], s["kind"]) for s in spots], [(self.ident1, "unclear")])
		convo = brain.transcript(self.ident1, spots[0]["at"])
		self.assertIn("KH: ờ thì cái kia kìa", convo)
		self.assertIn("BOT[REASK_CHUARO]", convo)
		self.assertNotIn("0912345678", convo)

	def test_safe_text(self):
		self.assertEqual(brain.safe_text("  Dạ,  Miyano  nhận ạ. "), "Dạ, Miyano nhận ạ.")
		for bad in ("Giá 2 triệu ạ", "giảm 10% nhé", "xem tại https://x.vn", "", "x" * 700):
			self.assertEqual(brain.safe_text(bad), "", bad)

	def test_review_stores_pending_proposals_only(self):
		self.greeted()
		self.turn("bên em có ống thở không")
		self._staff_says("Dạ bên em có ống thở nội khí quản các cỡ ạ.")
		old_menu = frappe.db.get_value(NODE, "MENU_CHINH", "text")
		proposals = {
			"proposals": [
				{"type": "kb_item", "reason": "nhân viên đã trả lời", "question": "Miyano có ống thở không?", "answer": "Dạ có ống thở nội khí quản các cỡ ạ."},
				{"type": "kb_item", "reason": "có giá", "question": "Giá ống thở?", "answer": "Giá 2 triệu ạ"},
				{"type": "node_text", "reason": "gọn hơn", "node_code": "MENU_CHINH", "new_text": "Dạ, anh/chị quan tâm nội dung nào ạ?"},
				{"type": "node_text", "reason": "ô lạ", "node_code": "KHONG_CO_O_NAY", "new_text": "abc"},
				{"type": "phrase", "reason": "ý rõ", "phrase": "bên em có ống thở không", "intent_code": "MENU_VATTU"},
				{"type": "phrase", "reason": "bịa", "phrase": "câu khách không hề nói", "intent_code": "MENU_VATTU"},
				{"type": "phrase", "reason": "mã lạ", "phrase": "bên em có ống thở không", "intent_code": "TU_BIA"},
			]
		}
		with mock.patch.object(brain.ai_client, "call_tool", return_value=proposals) as ai:
			stats = brain.review(now_datetime().date())
		ai.assert_called_once()
		sent_to_ai = ai.call_args.args[3][0]["content"]
		self.assertIn("<hoi_thoai>", sent_to_ai)
		self.assertIn("NV: Dạ bên em có ống thở", sent_to_ai)
		self.assertEqual(stats["proposals"], 3)
		item = frappe.get_all("Omni Knowledge Item", filters={"source": ["like", f"{brain.SOURCE}%"]}, fields=["question", "active", "question_group"])
		self.assertEqual([(i.question, i.active, i.question_group) for i in item], [("Miyano có ống thở không?", 0, brain.PROPOSAL_GROUP)])
		dx = frappe.get_all(NODE, filters={"code": ["like", "DX_MENU_CHINH_%"]}, fields=["name", "status", "text"])
		self.assertEqual([(d.status, d.text) for d in dx], [("Cần duyệt", "Dạ, anh/chị quan tâm nội dung nào ạ?")])
		self.assertEqual(frappe.db.get_value(NODE, "MENU_CHINH", "text"), old_menu)  # chưa duyệt: câu thật không đổi
		self.assertEqual(frappe.db.get_value(NODE, brain.VOCAB_PENDING, "status"), "Cần duyệt")
		self.assertIsNone(brain.learned_code("bên em có ống thở không"))  # chưa duyệt: chưa dùng

		# duyệt
		frappe.set_user("Administrator")
		self.assertEqual(brain.approve_phrases(), {"approved": 1})
		self.assertEqual(brain.learned_code("Ben em co ong tho khong"), "MENU_VATTU")
		self.assertIsNone(brain.learned_code("bên em có ống thở loại nào tốt nhất cho bệnh viện tuyến tỉnh vậy em"))
		self.assertEqual(brain.approve(dx[0].name), {"node": "MENU_CHINH"})
		self.assertEqual(frappe.db.get_value(NODE, "MENU_CHINH", "text"), "Dạ, anh/chị quan tâm nội dung nào ạ?")
		self.assertFalse(frappe.db.exists(NODE, dx[0].name))
		frappe.db.set_value(NODE, "MENU_CHINH", "text", old_menu)
		frappe.db.commit()

	def test_no_weak_spot_means_no_ai_call(self):
		self.greeted()
		self.tap("MENU_SPD")
		with mock.patch.object(brain.ai_client, "call_tool") as ai:
			stats = brain.review(now_datetime().date())
		ai.assert_not_called()
		self.assertEqual((stats["spots"], stats["proposals"]), (0, 0))

	def test_switch_off(self):
		with mock.patch.object(brain, "enabled", return_value=False), mock.patch.object(brain.ai_client, "call_tool") as ai:
			self.assertEqual(brain.review()["reviewed"], 0)
			self.assertIsNone(brain.learned_code("alo"))
		ai.assert_not_called()

	def test_internal_nodes_are_never_sent_to_customers(self):
		from omni_crm.script import store

		brain._append_vocab(brain.VOCAB_ACTIVE, "ống thở", "MENU_VATTU", "Đang dùng")
		self.assertIsNone(store.render(store.get_node(brain.VOCAB_ACTIVE), {"identity": self.ident1}))
