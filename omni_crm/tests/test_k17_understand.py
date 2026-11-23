"""Bước 4 "hiểu ngữ cảnh" 20/11: bước HIỂU (script.understand) — kiểm bằng code bản đọc hiểu của AI, chuyển sang dạng bộ
máy kịch bản dùng, câu hỏi làm rõ. Không gọi AI thật."""

from datetime import datetime
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.script import understand as u
from omni_crm.script.flows import menu
from omni_crm.tests.test_k2_flow import FlowCase


def reading(intent="BAOGIA", relation="TRA_LOI", conf=0.9, service="VATTU", **extra):
	return {"dien_giai": "x", "quan_he": relation, "cac_y": [{"y_dinh": intent, "do_tin_cay": conf, "dich_vu": service}], **extra}


class TestClean(FrappeTestCase):
	def test_goods_must_be_the_customers_own_words(self):
		raw = reading(slots={"mat_hang": [
			{"ten": "giường bệnh", "so_luong": 30, "don_vi": "chiếc"},
			{"ten": "máy thở", "so_luong": 5},  # khách không hề nói
			{"ten": "găng tay", "so_luong": 999, "hang": "Toshiba"},  # số lượng và hãng bịa
		]})
		got = u.clean(raw, "Anh muốn đặt 30 chiếc giường bệnh và găng tay")["slots"]["mat_hang"]
		self.assertEqual(got, [{"ten": "giường bệnh", "so_luong": 30, "don_vi": "chiếc"}, {"ten": "găng tay"}])

	def test_unknown_codes_and_bad_shapes_become_unknown(self):
		self.assertEqual(u.clean(None, "x")["quan_he"], "KHONG_RO")
		got = u.clean({"quan_he": "TU_BIA", "cac_y": [{"y_dinh": "MA_LA", "do_tin_cay": 1}, {"y_dinh": "DEMO", "do_tin_cay": "cao"}]}, "x")
		self.assertEqual(got["quan_he"], "KHONG_RO")
		self.assertEqual([(i["y_dinh"], i["do_tin_cay"]) for i in got["cac_y"]], [("DEMO", 0.0)])

	def test_time_slot_must_quote_the_customer(self):
		raw = reading(slots={"thoi_gian_can_hang": {"nguyen_van": "tháng sau", "ngay": "2026-12-01"}})
		self.assertNotIn("thoi_gian_can_hang", u.clean(raw, "Thứ 3 tuần sau được k em")["slots"])

	def test_now_line(self):
		line = u.now_line(datetime(2026, 11, 20, 14, 30), "T2-T6 08:00-17:30")
		self.assertIn("thứ Sáu ngày 20/11/2026, 14:30", line)
		self.assertIn("Giờ làm việc", line)


class TestToDetail(FrappeTestCase):
	def test_answer_with_goods(self):
		d = u.to_detail(u.clean(reading(slots={"mat_hang": [{"ten": "giường bệnh", "so_luong": 30, "don_vi": "chiếc"}]}), "30 chiếc giường bệnh"))
		self.assertEqual((d["code"], d["relation"], d["service"]), ("BAOGIA", "TRA_LOI", "VATTU"))
		self.assertEqual(d["slots"], {"items": "giường bệnh", "quantity": "30 chiếc"})

	def test_counter_question_is_answered_then_the_pending_question_returns(self):
		d = u.to_detail(u.clean(reading("HOI_THONG_TIN", "HOI_NGUOC", 0.7, cau_hoi_kb=["giao sớm nhất khi nào"]), "x"))
		self.assertEqual((d["code"], d["relation"]), ("KB_HOI", "Y_KHAC"))
		self.assertEqual(d["questions"], ["giao sớm nhất khi nào"])

	def test_weak_switch_does_not_leave_the_flow(self):
		self.assertEqual(u.to_detail(u.clean(reading("DEMO", "Y_KHAC", 0.6), "x"))["relation"], "")
		self.assertEqual(u.to_detail(u.clean(reading("DEMO", "Y_KHAC", 0.85), "x"))["relation"], "Y_KHAC")

	def test_social_and_new_intents(self):
		d = u.to_detail(u.clean(reading("CHUA_RO", "XA_GIAO", 0.9), "ok em"))
		self.assertTrue(d["social"])
		self.assertEqual(u.to_detail(u.clean(reading("CAN_NGUOI", "Y_KHAC"), "x"))["code"], "GAP_NHANVIEN")
		self.assertEqual(u.to_detail(u.clean(reading("HOI_THONG_TIN", "Y_KHAC"), "x"))["code"], "KB_HOI")
		self.assertTrue(u.to_detail(u.clean(reading("NHAC_LAI", "Y_KHAC"), "x"))["nudge"])

	def test_quote_with_goods_is_primary_when_several_intents(self):
		raw = {"quan_he": "Y_KHAC", "cac_y": [{"y_dinh": "TAILIEU", "do_tin_cay": 0.95}, {"y_dinh": "BAOGIA", "do_tin_cay": 0.93}],
			"slots": {"mat_hang": [{"ten": "găng tay", "so_luong": 300, "don_vi": "đôi"}]}}
		self.assertEqual(u.primary(u.clean(raw, "tài liệu spd với báo giá găng tay 300 đôi"))["y_dinh"], "BAOGIA")


class TestClarify(FrappeTestCase):
	def _q(self, *options):
		return menu.clarify_question({"chua_ro": [{"diem": "x", "anh_huong": "DOI_HANH_DONG", "cach_hieu": list(options)}]})

	def test_two_clean_action_phrases_make_a_question(self):
		self.assertEqual(self._q("Đổi lịch demo", "đổi ngày nhận hàng"), "anh/chị muốn đổi lịch demo, hay đổi ngày nhận hàng ạ?")

	def test_system_wording_money_or_links_are_never_sent(self):
		self.assertEqual(self._q("Khách đang trả lời về số lượng nhưng không có số cụ thể", "Khách đang gửi lại tên mặt hàng không rõ"), "")
		self.assertEqual(self._q("đổi lịch demo", "mua với giá 2 triệu"), "")
		self.assertEqual(self._q("đổi lịch demo", "xem tại https://x.vn"), "")
		self.assertEqual(self._q("đổi lịch demo"), "")

	def test_only_when_the_main_intent_is_not_already_clear(self):
		self.assertTrue(menu._must_clarify({"unclear_action": True, "confidence": 0.55}, "BAOGIA"))
		self.assertFalse(menu._must_clarify({"unclear_action": True, "confidence": 0.85}, "BAOGIA"))
		self.assertFalse(menu._must_clarify({"unclear_action": True, "confidence": 0.5}, "DEMO"))
		self.assertFalse(menu._must_clarify({"unclear_action": False, "confidence": 0.1}, "BAOGIA"))


class TestSwitchedOn(FlowCase):
	def setUp(self):
		super().setUp()
		old = frappe.conf.get(u.SWITCH)
		frappe.conf[u.SWITCH] = 1
		self.addCleanup(lambda: frappe.conf.__setitem__(u.SWITCH, old))

	def _read(self, value):
		p = mock.patch.object(u, "read", return_value=u.clean(value, "\n".join(self._said)))
		p.start()
		self.addCleanup(p.stop)

	def test_question_outside_the_knowledge_base_is_noted_without_pausing_the_bot(self):
		from omni_crm.bot import handoff as bot_handoff

		self.greeted()
		self._said = ["Anh muốn biết mã số thuế của công ty"]
		self._read(reading("HOI_THONG_TIN", "Y_KHAC", 0.95, "KHONG_RO", cau_hoi_kb=["Mã số thuế của Miyano"]))
		with mock.patch.object(menu, "kb_draft", return_value=None):
			self.turn(self._said[0])
		self.assertEqual(self.sent(), ["KB_GHINHAN"])
		self.assertEqual(self.handoffs, [])
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))
		self.assertIn("mã số thuế", (frappe.db.get_value("CRM Lead", self.lead1, "omni_unanswered_questions") or "").lower())

	def test_switch_off_keeps_the_old_classifier(self):
		frappe.conf[u.SWITCH] = 0
		self.greeted()
		self.codes["bên em triển khai ra sao"] = ("MENU_SPD", 0.9)
		with mock.patch.object(u, "read") as read:
			self.turn("bên em triển khai ra sao")
		read.assert_not_called()
		self.assertEqual(self.sent(), ["MENU_SPD"])
