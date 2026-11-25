"""K19 — bước 8 (yêu cầu 20/11, luật 10 + 11): trong lúc chờ nhân viên bot vẫn trả lời câu hỏi thông tin và nhận yêu cầu
mới; chuyển người luôn có người nhận; xưng hô theo cách khách tự xưng."""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.script import address, tasks
from omni_crm.script.flows import handoff
from omni_crm.tests.k11_helpers import A, ConversationCase

WAIT = ["#MENU_SPD", "cho anh gặp nhân viên"]  # bot đã chuyển người và tạm dừng


class TestK19WhileWaiting(ConversationCase):
	def setUp(self):
		super().setUp()
		old = frappe.conf.get(handoff.WAITING_CONF)
		frappe.conf[handoff.WAITING_CONF] = 1
		self.addCleanup(lambda: frappe.conf.__setitem__(handoff.WAITING_CONF, old))

	def test_handoff_pauses_first(self):
		self.talk(WAIT)
		self.assertTrue(self.T[-1].paused, self.T[-1].nodes)

	def test_information_question_is_answered_and_wait_goes_on(self):
		self.talk(WAIT)
		self.ai["spd triển khai mất bao lâu em"] = A("KB_HOI")
		self.kb["spd triển khai mất bao lâu em"] = "Dạ, thường từ 4 đến 8 tuần ạ."
		self.step("spd triển khai mất bao lâu em")
		self.assertIn("4 đến 8 tuần", self.T[-1].text)
		self.assertTrue(self.T[-1].paused)  # vẫn chờ nhân viên
		self.assertTrue(self.T[-1].st.get("handoff"))

	def test_question_without_answer_is_noted_not_ignored(self):
		self.talk(WAIT)
		self.ai["mã số thuế bên em là gì"] = A("KB_HOI")
		self.step("mã số thuế bên em là gì")
		self.assertEqual(self.T[-1].nodes, ["KB_GHINHAN"])
		self.assertTrue(self.T[-1].paused)

	def test_clear_new_request_reopens_the_bot(self):
		self.talk(WAIT)
		self.ai["thôi anh đặt lịch demo spd luôn"] = A("DEMO", 0.95, "SPD")
		self.step("thôi anh đặt lịch demo spd luôn")
		self.assertFalse(self.T[-1].paused)
		self.assertTrue(any(n.startswith("DEMO") for n in self.T[-1].nodes), self.T[-1].nodes)

	def test_thanks_and_unclear_text_stay_silent(self):
		self.talk([*WAIT, "ok em cảm ơn"])
		self.assertTrue(self.T[-1].silent)
		self.ai["ờ thì cái hôm trước ấy"] = A("CHUA_RO", 0.3)
		self.step("ờ thì cái hôm trước ấy")
		self.assertTrue(self.T[-1].silent)
		self.assertTrue(self.T[-1].paused)

	def test_staff_already_replied_keeps_the_bot_silent(self):
		self.talk(WAIT)
		self.ai["spd triển khai mất bao lâu em"] = A("KB_HOI")
		self.kb["spd triển khai mất bao lâu em"] = "Dạ, thường từ 4 đến 8 tuần ạ."
		with mock.patch.object(handoff, "_staff_replied", return_value=True):
			self.step("spd triển khai mất bao lâu em")
		self.assertTrue(self.T[-1].silent)

	def test_switch_off_keeps_old_behaviour(self):
		frappe.conf[handoff.WAITING_CONF] = 0
		self.talk(WAIT)
		self.ai["spd triển khai mất bao lâu em"] = A("KB_HOI")
		self.kb["spd triển khai mất bao lâu em"] = "Dạ, thường từ 4 đến 8 tuần ạ."
		self.step("spd triển khai mất bao lâu em")
		self.assertTrue(self.T[-1].silent)


class TestK19Pronoun(ConversationCase):
	def setUp(self):
		super().setUp()
		old = frappe.conf.get(address.SWITCH)
		frappe.conf[address.SWITCH] = 1
		self.addCleanup(lambda: frappe.conf.__setitem__(address.SWITCH, old))

	def test_bot_uses_the_word_the_customer_uses(self):
		self.ai["anh muốn xem demo spd"] = A("DEMO", 0.95, "SPD")
		self.talk(["anh muốn xem demo spd"])
		self.assertNotIn("anh/chị", self.T[-1].text.lower())
		self.assertIn("anh", self.T[-1].text.lower())
		self.step("#MENU")
		self.assertNotIn("anh/chị", self.T[-1].text.lower())  # nhớ cho các lượt sau

	def test_unknown_stays_neutral(self):
		self.talk(["#MENU_SPD"])
		self.assertIn("anh/chị", self.T[-1].text.lower())


class TestK19Pure(FrappeTestCase):
	def test_detect(self):
		for text, want in (
			("anh muốn đặt lịch demo", "anh"),
			("Gửi chị báo giá găng tay", "chị"),
			("tư vấn cho a với", "anh"),
			("c cần 200 hộp khẩu trang", "chị"),
			("chị ơi cho em hỏi giá", None),  # khách tự xưng em, "chị" là gọi người của Miyano
			("anh gửi em báo giá nhé", None),
			("em muốn hỏi anh chút", None),
			("báo giá găng tay", None),
			("anh muốn hỏi giúp chị bên khoa dược, chị cần 200 hộp", None),  # lẫn cả hai
			("hộp 100 cái", None),
			("", None),
		):
			with self.subTest(text=text):
				self.assertEqual(address.detect(text), want)

	def test_fit_keeps_capitals(self):
		self.assertEqual(address.fit("Anh/chị cần gì ạ? Miyano gửi anh/chị nhé.", "chị"), "Chị cần gì ạ? Miyano gửi chị nhé.")
		self.assertEqual(address.fit("Anh/chị cần gì ạ?", None), "Anh/chị cần gì ạ?")

	def test_handoff_always_has_a_recipient(self):
		with (
			mock.patch.object(tasks, "recipient", return_value=None),
			mock.patch.object(tasks, "lead_assignee", return_value=None),
			mock.patch("omni_crm.bot.handoff.recipients", return_value=["fallback@example.com"]),
		):
			self.assertEqual(tasks.assignee_for("LEAD-X", "SPD"), "fallback@example.com")
		with (
			mock.patch.object(tasks, "recipient", return_value=None),
			mock.patch.object(tasks, "lead_assignee", return_value="owner@example.com"),
		):
			self.assertEqual(tasks.assignee_for("LEAD-X", "SPD"), "owner@example.com")
