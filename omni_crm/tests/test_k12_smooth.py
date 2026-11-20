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

	def test_typed_time_at_slot_list_is_read_as_a_time(self):
		self.greeted()
		self.tap("MENU_SPD")
		self.tap("DEMO_SPD")
		self.tap("DEMO_HT_ONLINE")
		self.assertEqual(self.sent()[-1], "DEMO_KHUNGGIO")
		self.turn("3 giờ chiều ngày thứ 5 tuần sau")
		self.classify.assert_not_called()
		self.assertIn(self.sent()[-1], ("DEMO_SONGUOI", "DEMO_GIOBAN"))

	def test_other_text_at_slot_list_still_understood(self):
		self.greeted()
		self.tap("MENU_SPD")
		self.tap("DEMO_SPD")
		self.tap("DEMO_HT_ONLINE")
		self.codes["bên em là bot à"] = ("BOT_HOI", 1.0)
		self.turn("bên em là bot à")
		self.assertEqual(self.sent()[-2:], ["C3_BOT", "DEMO_KHUNGGIO"])  # đáp xong gửi lại danh sách khung giờ


class TestTypedTimeAndAck(FrappeTestCase):
	def test_parse_customer_phrase(self):
		from datetime import datetime

		now = datetime(2026, 11, 19, 14, 35)  # thứ Năm
		self.assertEqual(demo.parse_time("3 giờ chiều ngày thứ 5 tuần sau", now), datetime(2026, 11, 26, 15, 0))

	def test_question_is_not_confirmed_as_information(self):
		from omni_crm.script.flows import collect

		self.assertEqual(collect.ack_value("Bên mình có thể giao sớm nhất vào khi nào"), "")
		self.assertEqual(collect.ack_value("trong tuần sau"), "trong tuần sau")

	def test_ack_only(self):
		for text in ("ok", "Oki em", "vâng ạ", "cảm ơn em nhé", "được rồi", "Thanks", "dạ", "ừ", "Đúng rồi em"):
			self.assertTrue(cl.is_ack_only(text), text)
		for text in ("ok gửi anh báo giá", "không cảm ơn", "a", "em", "đừng", ""):
			self.assertFalse(cl.is_ack_only(text), text)


class TestAckFlow(FlowCase):
	def test_ok_after_a_closed_step_gets_a_short_reply(self):
		self.greeted()
		self.tap("KHONG_CAMON")
		self.turn("Oki")
		self.classify.assert_not_called()
		self.assertEqual(self.sent()[-1], "DA_VANG")

	def test_ok_at_a_menu_gets_a_short_reply_not_the_whole_menu_again(self):
		self.greeted()
		self.tap("MENU_SPD")
		self.turn("ok em")
		self.assertEqual(self.sent(), ["MENU_SPD", "DA_VANG"])

	def test_ok_means_yes_when_the_question_has_a_yes_button(self):
		self.greeted()
		self.tap("MENU_PHANMEM")
		self.tap("BAOGIA_PHANMEM")
		before = len(self.sent())
		self.turn("ok em")
		after = self.sent()[before:]
		self.assertTrue(after and after[-1] not in ("DA_VANG", "REASK_CHUARO", "BAOGIA_PHANMEM"), after)


class TestBatchB(FlowCase):
	def setUp(self):
		super().setUp()
		self.set_switch(enabled=0, tests=self.ident1)
		p = mock.patch.object(demo, "demo_user", return_value=None)
		p.start()
		self.addCleanup(p.stop)

	def _to_slots(self):
		self.greeted()
		for code in ("MENU_SPD", "DEMO_SPD", "DEMO_HT_ONLINE"):
			self.tap(code)
		self.assertEqual(self.sent()[-1], "DEMO_KHUNGGIO")

	def test_polite_decline_is_not_do_not_contact(self):
		import frappe

		self.greeted()
		self.turn("thôi không cần đâu em")
		self.assertEqual(self.sent(), ["KHONG_CAMON"])
		self.assertFalse(frappe.db.get_value("CRM Lead", self.lead1, "omni_do_not_contact"))

	def test_slot_list_is_numbered_and_number_picks_a_slot(self):
		self._to_slots()
		listed = [t for t in self.texts() if "đề xuất các khung giờ" in t][0]
		self.assertIn("\n1. ", listed)
		self.turn("2")
		self.assertEqual(self.sent()[-1], "DEMO_SONGUOI")
		self.assertEqual(self.sstate()["demo_at"], self.sstate()["demo_slots"][1])

	def test_ordinal_words_pick_a_slot(self):
		self._to_slots()
		self.turn("khung đầu tiên")
		self.assertEqual(self.sstate().get("demo_at"), self.sstate()["demo_slots"][0])

	def test_day_without_time_asks_for_the_time(self):
		self._to_slots()
		self.turn("sáng thứ hai tuần sau")
		self.assertEqual(self.sent()[-1], "DEMO_GIOKHAC_LAI")
		self.assertEqual(self.sstate().get("demo_wait"), "time")

	def test_greeting_at_slot_list_resends_slots(self):
		self._to_slots()
		self.turn("alo em")
		self.assertEqual(self.sent()[-1], "DEMO_KHUNGGIO")

	def test_weekend_is_not_called_fully_booked(self):
		self._to_slots()
		self.tap("DEMO_GIOKHAC")
		self.turn("thứ bảy tuần sau 9h")
		self.assertEqual(self.sent()[-1], "DEMO_NGOAIGIO")

	def test_delete_data_request_is_acknowledged(self):
		self.greeted()
		self.codes["xóa dữ liệu của tôi đi"] = ("XOA_DULIEU", 1.0)
		self.turn("xóa dữ liệu của tôi đi")
		self.assertIn("C4_XOADULIEU_GHINHAN", self.sent())


class TestBatchC(FrappeTestCase):
	def test_attendee_count(self):
		cases = {"20": 20, "khoảng 20 người": 20, "hai mươi người": 20, "mười lăm": 15, "ba người": 3,
			"anh và 2 đồng nghiệp": 3, "chưa biết": None, "asdf": None, "0": None}
		for text, want in cases.items():
			self.assertEqual(demo.attendee_count(text), want, text)

	def test_only_extras(self):
		from omni_crm.script.flows import collect

		self.assertTrue(collect._only_extras("anh cần 100 hộp", {"quantity": "100 hộp"}))
		self.assertFalse(collect._only_extras("Bơm 5ml g23 10 hộp", {"quantity": "10 hộp"}))

	def test_ack_drops_question_tail(self):
		from omni_crm.script.flows import collect

		self.assertEqual(collect.ack_value("Thứ 3 tuần sau được k em"), "Thứ 3 tuần sau")
		self.assertEqual(collect.ack_value("găng tay nitrile nhé em"), "găng tay nitrile")
		self.assertEqual(collect.ack_value("30 cái"), "30 cái")
		self.assertEqual(collect.ack_value("được không em"), "")

	def test_sticker_placeholder_is_not_text(self):
		for raw in ("[Sticker]", "[sticker]", "[Hình ảnh]", "[GIF]"):
			self.assertTrue(router.NON_TEXT.match(raw), raw)
		for raw in ("[1] găng tay", "Alo em", "[ghi chú] cần gấp"):
			self.assertFalse(router.NON_TEXT.match(raw), raw)


class TestStickerWithGreeting(FlowCase):
	def test_alo_plus_sticker_is_a_greeting(self):
		self.greeted()
		self.turn("Alo em", "[Sticker]")
		self.assertEqual(self.sent(), ["MENU_CHINH"])


class TestDeliveryNote(FlowCase):
	def test_need_by_with_question_gets_staff_will_confirm(self):
		self.greeted()
		for code in ("MENU_VATTU", "BAOGIA_VATTU"):
			self.tap(code)
		self.codes["găng tay nitrile"] = ("MENU_VATTU", 0.9)
		self.turn("găng tay nitrile")
		self.turn("100 hộp")
		self.assertEqual(self.sent()[-1], "BG_VT_THOIGIAN")
		self.turn("Thứ 3 tuần sau được không")
		self.classify.assert_called_once()  # chỉ lượt "găng tay nitrile" hỏi AI; câu thời gian kèm lời hỏi thì không
		self.assertEqual(self.sent()[-1], "BG_VT_THAYTHE")
		self.reset()
		self.greeted()
		for code in ("MENU_VATTU", "BAOGIA_VATTU"):
			self.tap(code)
		self.turn("găng tay nitrile")
		self.turn("100 hộp")
		self.turn("Thứ 3 tuần sau được k em")
		last = [t for t in self.texts() if "hãng" in t.lower() and "xác nhận thông tin" in t]
		self.assertEqual(len(last), 1, self.texts())
		self.assertIn("thời gian cần hàng Thứ 3 tuần sau.", last[0])
		self.assertIn("Nhân viên Miyano sẽ xác nhận thời gian giao hàng khi gửi báo giá ạ.", last[0])
		self.assertNotIn("được k em", last[0])

	def test_plain_need_by_has_no_note(self):
		self.greeted()
		for code in ("MENU_VATTU", "BAOGIA_VATTU"):
			self.tap(code)
		self.codes["găng tay nitrile"] = ("MENU_VATTU", 0.9)
		self.turn("găng tay nitrile")
		self.turn("100 hộp")
		self.turn("trong tuần sau")
		self.assertFalse([t for t in self.texts() if "xác nhận thời gian giao hàng" in t])
