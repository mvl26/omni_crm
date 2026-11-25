"""K21: nút Zalo hiện chữ trên nút cho khách; tin về được đổi lại `#mã` (script.buttons)."""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.script import buttons, classify

NEW = [{"label": "Gửi danh mục", "target": "DANHMUC_VATTU"}, {"label": "Không", "target": "KHONG_CAN"}]
OLD = [{"label": "Đặt lịch demo", "target": "DEMO_SPD"}, {"label": "Được", "target": "BG_VT_THAYTHE_DUOC"}]


class TestK21Buttons(FrappeTestCase):
	def test_payload_is_the_label(self):
		self.assertEqual(buttons.payload(NEW[0]), "Gửi danh mục")

	def test_switch_restores_code_payload(self):
		old = frappe.conf.get(buttons.CODE_PAYLOAD_CONF)
		frappe.conf[buttons.CODE_PAYLOAD_CONF] = 1
		self.addCleanup(lambda: frappe.conf.__setitem__(buttons.CODE_PAYLOAD_CONF, old))
		self.assertEqual(buttons.payload(NEW[0]), "#DANHMUC_VATTU")

	def test_label_of_latest_message_matches_ignoring_case_and_spaces(self):
		self.assertEqual(buttons.match("  gửi  danh mục ", [NEW, OLD]), "DANHMUC_VATTU")
		self.assertEqual(buttons.match("Không", [NEW, OLD]), "KHONG_CAN")

	def test_old_message_only_matches_long_labels(self):
		self.assertEqual(buttons.match("Đặt lịch demo", [NEW, OLD]), "DEMO_SPD")
		self.assertIsNone(buttons.match("Được", [NEW, OLD]))  # câu khách gõ, không phải bấm nút cũ

	def test_other_text_and_codes_are_left_alone(self):
		self.assertIsNone(buttons.match("anh muốn gửi danh mục vật tư", [NEW, OLD]))
		self.assertIsNone(buttons.match("#DANHMUC_VATTU", [NEW, OLD]))
		self.assertIsNone(buttons.match("", [NEW]))

	def test_unknown_customer_is_not_an_error(self):
		self.assertIsNone(buttons.code_for("Zalo OA:khong-co-khach-nay", "Gửi danh mục"))

	def test_bot_identity_questions_are_caught_by_rules(self):
		for said in ("em tên gì", "bạn là ai vậy", "ai đang trả lời anh thế"):
			self.assertEqual((classify.classify_by_rules(said) or ("",))[0], "BOT_HOI", said)
		self.assertNotEqual((classify.classify_by_rules("anh tên Nam") or ("",))[0], "BOT_HOI")


from omni_crm.script import gather  # noqa: E402
from omni_crm.tests.k11_helpers import ConversationCase  # noqa: E402


class TestK21ButtonsInConversation(ConversationCase):
	def _old_button(self):
		"""(nhãn, mã) của một nút nhãn dài trên tin kèm nút CŨ (không phải tin mới nhất)."""
		import json

		rows = frappe.get_all(
			"Omni Outbound Message",
			filters={"identity": self.ident1, "buttons": ["is", "set"]},
			fields=["buttons"],
			order_by="creation asc",
		)
		newest = {b["label"] for b in json.loads(rows[-1].buttons)}
		for row in rows[:-1]:
			for b in json.loads(row.buttons):
				if len(b["label"].split()) >= 2 and len(b["label"]) >= 8 and b["label"] not in newest:
					return b["label"], b["target"]
		self.fail(f"không có nút nhãn dài để thử: {[r.buttons for r in rows]}")

	def test_label_from_zalo_is_read_as_the_button(self):
		self.talk(["#MENU_SPD", "#DEMO_SPD"])
		label, target = self._old_button()
		key = frappe.db.get_value("Omni Identity", self.ident1, "identity_key")
		self.assertEqual(buttons.code_for_identity(self.ident1, label), f"#{target}")
		self.assertTrue(buttons.is_tap(label, key))
		self.assertEqual(gather.quiet_needed([frappe._dict(text=label)], 15, key), 0)  # bấm nút: không đợi gom tin
		self.assertEqual(gather.quiet_needed([frappe._dict(text="anh hỏi chút")], 15, key), 15)
		turn = self.step(label)
		self.assertFalse(turn.reasked, turn.text)
		self.assertEqual(turn.ai, [])  # hiểu là bấm nút, không cần hỏi AI
		self.assertTrue(turn.nodes)
