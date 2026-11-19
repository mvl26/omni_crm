"""Bot trả lời mượt hơn (thử thật 19/11): gõ gần đúng lựa chọn, không đẩy về menu giữa chừng, chào lại khách cũ,
nêu dịch vụ kèm "đặt lịch demo" thì vào thẳng đặt demo."""

from unittest import mock

from frappe.tests.utils import FrappeTestCase

from omni_crm.script import classify as cl
from omni_crm.script import router
from omni_crm.script.flows import demo
from omni_crm.tests.test_k2_flow import FlowCase

MODE = [["Online qua Teams", "DEMO_HT_ONLINE"], ["Gặp trực tiếp", "DEMO_HT_TRUCTIEP"]]
MAIN = [
	["Cung ứng vật tư", "MENU_VATTU"],
	["Phần mềm quản lý", "MENU_PHANMEM"],
	["Hệ thống SPD", "MENU_SPD"],
	["Khác", "MENU_KHAC"],
	["Gặp nhân viên", "GAP_NHANVIEN"],
]


class TestLooseChoice(FrappeTestCase):
	def test_partial_label_and_sentence(self):
		for text in ("online", "Online", "anh muốn demo online", "cho anh online nhé", "truc tiep"):
			with self.subTest(text=text):
				want = "DEMO_HT_TRUCTIEP" if "truc" in text else "DEMO_HT_ONLINE"
				self.assertEqual(router._loose_choice(text.lower(), MODE), want)

	def test_not_guessed(self):
		cases = [
			("không online được", MODE),  # phủ định
			("online hay trực tiếp", MODE),  # trúng hai lựa chọn
			("online thế nào", MODE),  # câu hỏi
			("online?", MODE),
			("báo giá phần mềm bên em", [["Xin báo giá", "BAOGIA_SPD"], ["Đặt lịch demo", "DEMO_SPD"]]),  # 2 từ lạ
			("anh đang cần tìm hiểu thêm về hệ thống spd của bên em", MAIN),  # câu dài
			("xin chào", MAIN),
		]
		for text, items in cases:
			with self.subTest(text=text):
				self.assertIsNone(router._loose_choice(text, items))

	def test_typed_choice_skips_when_free_text_expected_or_unnumbered(self):
		st = {"script_state": {"choices": {"numbered": True, "items": MODE}, "field": "items"}}
		self.assertIsNone(router.typed_choice("online", st))
		st = {"script_state": {"choices": {"numbered": False, "items": MODE}}}
		self.assertIsNone(router.typed_choice("online", st))

	def test_greeting_only(self):
		for text in ("Alo em ơi", "hi", "Chào shop ạ", "alo alo"):
			self.assertTrue(cl.is_greeting_only(text), text)
		for text in ("alo cho anh báo giá", "anh muốn demo", "", "👍", "a", "dạ"):
			self.assertFalse(cl.is_greeting_only(text), text)


class TestSmoothFlow(FlowCase):
	def setUp(self):
		super().setUp()
		self.set_switch(enabled=0, tests=self.ident1)  # ô demo "Cần duyệt" chỉ gửi cho danh sách thử
		p = mock.patch.object(demo, "demo_user", return_value=None)
		p.start()
		self.addCleanup(p.stop)

	def test_returning_customer_greeting_gets_hello_and_menu(self):
		self.greeted()
		self.turn("Alo em ơi")
		self.assertEqual(self.sent(), ["MENU_CHINH"])
		text = self.texts()[-1]
		self.assertIn("Miyano chào anh/chị", text)
		self.assertNotIn("chưa rõ ý", text)

	def test_unclear_inside_a_question_repeats_that_question(self):
		self.greeted()
		self.tap("MENU_SPD")
		self.turn("ờ thì cái kia kìa")
		self.assertEqual(self.sent()[-1], "MENU_SPD")
		again = [t for t in self.texts() if "chưa rõ lựa chọn" in t]
		self.assertEqual(len(again), 1)
		self.assertIn("Đặt lịch demo", again[0])

	def test_unclear_at_main_menu_keeps_old_reask(self):
		self.greeted()
		self.tap("MENU_CHINH")
		self.turn("ờ thì cái kia kìa")
		self.assertEqual(self.sent()[-1], "REASK_CHUARO")

	def test_service_plus_demo_in_one_message_goes_to_demo(self):
		self.greeted()
		text = "Anh thấy bảo bên mình có phần mềm spd anh muốn đặt lich demo"
		self.codes[text] = ("MENU_SPD", 0.9)
		self.turn(text)
		self.assertEqual(self.sent(), ["DEMO_HINHTHUC"])

	def test_typed_online_picks_the_format(self):
		self.greeted()
		self.tap("MENU_SPD")
		self.tap("DEMO_SPD")
		self.assertEqual(self.sent()[-1], "DEMO_HINHTHUC")
		self.turn("Online")
		self.classify.assert_not_called()
		self.assertEqual(self.sstate().get("demo_mode"), "Online qua Teams")
		self.assertNotIn(self.sent()[-1], ("REASK_CHUARO", "DEMO_HINHTHUC", "MENU_CHINH"))
