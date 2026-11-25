"""K18 — đọc lại trước khi chốt (yêu cầu 20/11, bước 5, luật 7): lịch demo và yêu cầu báo giá chỉ được ghi nhận sau khi
khách xác nhận; khách sửa mục nào thì chỉ mục đó đổi."""

from __future__ import annotations

import frappe
from frappe.utils import get_datetime

from omni_crm.tests.k11_helpers import A, ConversationCase
from omni_crm.tests.test_k11_conversations import ATT, CONTACT3, VT_QE

LEAD = "CRM Lead"
READ = [*ATT, "5"]  # bot vừa đọc lại lịch demo
QUOTE = [*VT_QE, *CONTACT3]  # bot vừa đọc lại yêu cầu báo giá


class TestK18Readback(ConversationCase):
	def setUp(self):
		super().setUp()
		old = frappe.conf.get("omni_script_readback")
		frappe.conf["omni_script_readback"] = 1
		self.addCleanup(lambda: frappe.conf.__setitem__("omni_script_readback", old))

	def booked_at(self):
		return frappe.db.get_value(LEAD, self.lead1, "omni_demo_at")

	def last(self):
		return self.T[-1]

	# --- demo -----------------------------------------------------------------------------------------------------
	def test_demo_is_read_back_and_not_booked(self):
		self.talk(READ)
		self.assertEqual(self.last().nodes[-1], "DEMO_DOCLAI")
		self.assertIn("5 người", self.last().text)
		self.assertIsNone(self.booked_at())

	def test_button_yes_books(self):
		self.talk([*READ, "#DEMO_CHOT"])
		self.assertTrue(self.booked_at())
		self.assertTrue(any(n.startswith("DEMO_XACNHAN") for n in self.last().nodes), self.last().nodes)

	def test_typed_yes_books(self):
		self.talk([*READ, "đúng rồi em"])
		self.assertTrue(self.booked_at())

	def test_typed_ok_books(self):
		self.talk([*READ, "ok em"])
		self.assertTrue(self.booked_at())

	def test_typed_hour_changes_only_the_hour(self):
		self.talk(READ)
		before = get_datetime(self.last().st["demo_at"])
		self.step("Sửa giờ thành 3 giờ chiều")
		after = get_datetime(self.last().st["demo_at"])
		self.assertEqual((after.date(), after.hour), (before.date(), 15))
		self.assertEqual(self.last().st.get("demo_attendees"), 5)
		self.assertEqual(self.last().nodes[-1], "DEMO_DOCLAI")
		self.assertIn("15:00", self.last().text)
		self.assertIsNone(self.booked_at())

	def test_typed_count_and_mode_change_only_that_field(self):
		self.talk(READ)
		at = self.last().st["demo_at"]
		self.step("10 người em nhé")
		self.assertEqual((self.last().st.get("demo_attendees"), self.last().st["demo_at"]), (10, at))
		self.assertEqual(self.last().nodes[-1], "DEMO_DOCLAI")
		self.step("đổi sang gặp trực tiếp")
		self.assertIn("trực tiếp", self.last().text.lower())
		self.assertEqual(self.last().st["demo_at"], at)
		self.assertIsNone(self.booked_at())

	def test_edit_button_asks_what_to_change_then_reads_back(self):
		self.talk([*READ, "#DEMO_SUA"])
		self.assertEqual(self.last().nodes, ["DEMO_SUA_GI"])
		self.step("#DEMO_SUA_SONGUOI")
		self.assertEqual(self.last().nodes, ["DEMO_SONGUOI"])
		self.step("8")
		self.assertEqual(self.last().nodes[-1], "DEMO_DOCLAI")
		self.assertIn("8 người", self.last().text)
		self.step("#DEMO_CHOT")
		self.assertTrue(self.booked_at())
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_demo_attendees"), 8)

	def test_unrelated_text_does_not_book(self):
		self.talk([*READ, lambda c: c.ai.update({"hôm nay trời đẹp nhỉ": A("KHONG_LIENQUAN")}) or "hôm nay trời đẹp nhỉ"])
		self.assertIsNone(self.booked_at())
		self.assertFalse(self.last().silent)

	# --- báo giá --------------------------------------------------------------------------------------------------
	def test_quote_is_read_back_and_no_task_yet(self):
		self.talk(QUOTE)
		self.assertEqual(self.last().nodes[-1], "BG_DOCLAI")
		self.assertEqual(len(self.tasks), 0)

	def test_quote_yes_by_button_or_text_creates_one_task(self):
		self.talk([*QUOTE, "#BG_CHOT"])
		self.assertEqual(len(self.tasks), 1)
		self.step("đúng rồi")  # nhắn thêm sau khi đã gửi: không tạo việc lần hai
		self.assertEqual(len(self.tasks), 1)

	def test_quote_typed_yes(self):
		self.talk([*QUOTE, "ok em"])
		self.assertEqual(len(self.tasks), 1)

	def test_quote_edit_changes_the_field_and_reads_back_again(self):
		self.talk([*QUOTE, "#BG_SUA_GI"])
		self.assertEqual(self.last().nodes, ["BG_SUA_GI"])
		self.ai["số lượng 300 hộp nhé"] = A("BAOGIA", so_luong="300 hộp")
		self.step("số lượng 300 hộp nhé")
		self.assertEqual(self.last().nodes[-1], "BG_DOCLAI")
		self.assertIn("300 hộp", self.last().text)
		self.assertEqual(len(self.tasks), 0)
		self.step("#BG_CHOT")
		self.assertEqual(len(self.tasks), 1)


BOOKED = [*READ, "#DEMO_CHOT"]


class TestK18Reschedule(ConversationCase):
	"""Bước 7 (luật 8): đổi / huỷ lịch demo — lịch cũ giữ nguyên cho tới khi khách xác nhận."""

	def setUp(self):
		super().setUp()
		old = frappe.conf.get("omni_script_readback")
		frappe.conf["omni_script_readback"] = 1
		self.addCleanup(lambda: frappe.conf.__setitem__("omni_script_readback", old))

	def booked_at(self):
		return frappe.db.get_value(LEAD, self.lead1, "omni_demo_at")

	def test_reschedule_keeps_the_old_booking_until_confirmed(self):
		self.talk(BOOKED)
		old = self.booked_at()
		self.step("#DEMO_DOILICH")
		self.assertEqual(self.T[-1].nodes, ["DEMO_KHUNGGIO"])
		self.assertEqual(self.booked_at(), old)
		self.step("#DEMO_KHUNG_3")
		self.assertEqual(self.T[-1].nodes[-1], "DEMO_DOCLAI")  # không hỏi lại số người / hình thức
		self.assertIn("5 người", self.T[-1].text)
		self.assertEqual(self.booked_at(), old)
		self.step("#DEMO_CHOT")
		new = self.booked_at()
		self.assertTrue(new and new != old)
		self.assertEqual(len(self.events()), 1)
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_demo_attendees"), 5)

	def test_customer_who_drops_out_of_rescheduling_still_has_the_booking(self):
		self.talk(BOOKED)
		old = self.booked_at()
		self.talk(["#DEMO_DOILICH", "#MENU"])
		self.assertEqual(self.booked_at(), old)
		self.assertEqual(len(self.events()), 1)

	def test_typed_cancel_asks_before_cancelling(self):
		self.talk(BOOKED)
		old = self.booked_at()
		self.step("hủy lịch demo giúp anh")
		self.assertEqual(self.T[-1].nodes, ["DEMO_DACOLICH"])
		self.assertEqual(self.booked_at(), old)
		self.step("#DEMO_HUY")
		self.assertIsNone(self.booked_at())

	def test_typed_move_with_weekend_time_offers_three_slots(self):
		self.talk(BOOKED)
		old = self.booked_at()
		self.step("đổi lịch demo sang 9h chủ nhật tuần này")
		self.assertEqual(self.T[-1].nodes, ["DEMO_NGOAIGIO_3"])
		self.assertEqual(len(self.T[-1].st.get("demo_slots") or []), 3)
		self.assertEqual(self.booked_at(), old)
