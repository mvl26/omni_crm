"""K3: hàm thuần cho bot kịch bản v3 - mốc thời gian (1.4, 3.5, 5.1, C.5), chọn kênh (4.2), kiểm email/SĐT (3.6).

Ngày dùng trong test (năm 2026): 17/11 thứ Ba, 20/11 thứ Sáu, 21/11 thứ Bảy, 22/11 Chủ nhật, 23/11 thứ Hai.
Cấu hình truyền thẳng vào hàm, không phụ thuộc Omni Settings của site test.
"""

from datetime import date, datetime
from itertools import product

from frappe.tests.utils import FrappeTestCase

from omni_crm.script import channelpick, timeutil, validate
from omni_crm.utils.business_hours import parse_hours

WEEKDAYS = (parse_hours({"mon-fri": "08:00-17:30"}), frozenset())
PROD = (parse_hours({"mon-fri": "08:00-17:30", "sat": "08:00-12:00"}), frozenset())  # như máy chủ thật
HOLIDAY = date(2026, 11, 19)  # giả định thứ Năm 19/11 là ngày lễ
WITH_HOLIDAY = (WEEKDAYS[0], frozenset({HOLIDAY}))


def dt(day: int, hh: int, mm: int = 0, month: int = 11) -> datetime:
	return datetime(2026, month, day, hh, mm)


class TestHours(FrappeTestCase):
	def test_in_hours(self):
		self.assertTrue(timeutil.in_hours(dt(17, 8, 0), WEEKDAYS))
		self.assertTrue(timeutil.in_hours(dt(17, 17, 30), WEEKDAYS))
		self.assertFalse(timeutil.in_hours(dt(17, 7, 59), WEEKDAYS))
		self.assertFalse(timeutil.in_hours(dt(17, 17, 31), WEEKDAYS))

	def test_weekend_and_saturday_from_config(self):
		self.assertFalse(timeutil.in_hours(dt(21, 10), WEEKDAYS))
		self.assertTrue(timeutil.in_hours(dt(21, 10), PROD))
		self.assertFalse(timeutil.in_hours(dt(21, 12, 1), PROD))
		self.assertFalse(timeutil.in_hours(dt(22, 10), PROD))

	def test_holiday(self):
		self.assertFalse(timeutil.in_hours(dt(19, 10), WITH_HOLIDAY))
		self.assertFalse(timeutil.is_working_day(HOLIDAY, WITH_HOLIDAY))
		self.assertTrue(timeutil.is_working_day(date(2026, 11, 18), WITH_HOLIDAY))

	def test_load_config_uses_existing_parsers(self):
		hours, holidays = timeutil.load_config()
		self.assertTrue(hours)
		self.assertIsInstance(holidays, frozenset)


class TestNextWorkingDay(FrappeTestCase):
	def test_tomorrow(self):
		self.assertEqual(timeutil.next_working_day(dt(17, 18), WEEKDAYS), date(2026, 11, 18))
		self.assertEqual(timeutil.next_working_day_text(dt(17, 18), WEEKDAYS), "sáng mai")

	def test_before_opening_is_today(self):
		self.assertEqual(timeutil.next_working_day(dt(17, 7), WEEKDAYS), date(2026, 11, 17))
		self.assertEqual(timeutil.next_working_day_text(dt(17, 7), WEEKDAYS), "sáng nay")

	def test_friday_evening_skips_weekend(self):
		self.assertEqual(timeutil.next_working_day(dt(20, 18), WEEKDAYS), date(2026, 11, 23))
		self.assertEqual(timeutil.next_working_day_text(dt(20, 18), WEEKDAYS), "sáng thứ Hai 23/11")

	def test_friday_evening_saturday_from_config(self):
		self.assertEqual(timeutil.next_working_day(dt(20, 18), PROD), date(2026, 11, 21))
		self.assertEqual(timeutil.next_working_day_text(dt(20, 18), PROD), "sáng mai")
		self.assertEqual(timeutil.next_working_day(dt(21, 13), PROD), date(2026, 11, 23))

	def test_holiday_eve(self):
		self.assertEqual(timeutil.next_working_day(dt(18, 17), WITH_HOLIDAY), date(2026, 11, 20))
		self.assertEqual(timeutil.next_working_day_text(dt(18, 17), WITH_HOLIDAY), "sáng thứ Sáu 20/11")

	def test_sunday_to_monday(self):
		self.assertEqual(timeutil.next_working_day_text(dt(22, 9), PROD), "sáng mai")


class TestDeadline(FrappeTestCase):
	def test_1629_same_day(self):
		self.assertFalse(timeutil.cutoff_1630(dt(17, 16, 29), WEEKDAYS))
		self.assertFalse(timeutil.cutoff_1630(dt(17, 16, 30), WEEKDAYS))
		self.assertEqual(timeutil.task_due(dt(17, 16, 29), WEEKDAYS), dt(17, 17, 30))

	def test_1631_next_day(self):
		self.assertTrue(timeutil.cutoff_1630(dt(17, 16, 31), WEEKDAYS))
		self.assertEqual(timeutil.task_due(dt(17, 16, 31), WEEKDAYS), dt(18, 17, 30))

	def test_before_opening_due_today(self):
		self.assertTrue(timeutil.cutoff_1630(dt(17, 7), WEEKDAYS))
		self.assertEqual(timeutil.task_due(dt(17, 7), WEEKDAYS), dt(17, 17, 30))

	def test_friday_after_1630_to_monday(self):
		self.assertEqual(timeutil.task_due(dt(20, 16, 31), WEEKDAYS), dt(23, 17, 30))

	def test_friday_after_1630_saturday_config(self):
		# Thứ Bảy làm 08:00-12:00 → hạn là giờ đóng cửa thứ Bảy.
		self.assertEqual(timeutil.task_due(dt(20, 16, 31), PROD), dt(21, 12, 0))

	def test_saturday_cutoff_one_hour_before_close(self):
		self.assertFalse(timeutil.cutoff_1630(dt(21, 10, 59), PROD))
		self.assertEqual(timeutil.task_due(dt(21, 10, 59), PROD), dt(21, 12, 0))
		self.assertTrue(timeutil.cutoff_1630(dt(21, 11, 1), PROD))
		self.assertEqual(timeutil.task_due(dt(21, 11, 1), PROD), dt(23, 17, 30))

	def test_weekend_and_holiday(self):
		self.assertTrue(timeutil.cutoff_1630(dt(22, 10), WEEKDAYS))
		self.assertEqual(timeutil.task_due(dt(22, 10), WEEKDAYS), dt(23, 17, 30))
		self.assertEqual(timeutil.task_due(dt(19, 10), WITH_HOLIDAY), dt(20, 17, 30))
		self.assertEqual(timeutil.task_due(dt(18, 16, 45), WITH_HOLIDAY), dt(20, 17, 30))


class TestProactive(FrappeTestCase):
	def test_window_bounds(self):
		self.assertFalse(timeutil.in_proactive_window(dt(17, 8, 59), WEEKDAYS))
		self.assertTrue(timeutil.in_proactive_window(dt(17, 9, 0), WEEKDAYS))
		self.assertTrue(timeutil.in_proactive_window(dt(17, 16, 30), WEEKDAYS))
		self.assertFalse(timeutil.in_proactive_window(dt(17, 16, 31), WEEKDAYS))
		self.assertFalse(timeutil.in_proactive_window(dt(21, 10), WEEKDAYS))
		self.assertFalse(timeutil.in_proactive_window(dt(19, 10), WITH_HOLIDAY))

	def test_saturday_window_from_config(self):
		self.assertTrue(timeutil.in_proactive_window(dt(21, 10), PROD))
		self.assertFalse(timeutil.in_proactive_window(dt(21, 11, 1), PROD))

	def test_next_slot(self):
		self.assertEqual(timeutil.next_proactive_slot(dt(17, 10), WEEKDAYS), dt(17, 10))
		self.assertEqual(timeutil.next_proactive_slot(dt(17, 7), WEEKDAYS), dt(17, 9))
		self.assertEqual(timeutil.next_proactive_slot(dt(17, 16, 31), WEEKDAYS), dt(18, 9))
		self.assertEqual(timeutil.next_proactive_slot(dt(20, 17), WEEKDAYS), dt(23, 9))
		self.assertEqual(timeutil.next_proactive_slot(dt(18, 17), WITH_HOLIDAY), dt(20, 9))

	def test_last_moment(self):
		self.assertEqual(timeutil.last_proactive_moment(dt(17, 20), WEEKDAYS), dt(17, 16, 30))
		self.assertEqual(timeutil.last_proactive_moment(dt(23, 8), WEEKDAYS), dt(20, 16, 30))
		self.assertEqual(timeutil.last_proactive_moment(dt(17, 12), WEEKDAYS), dt(17, 12))

	def test_postdemo(self):
		self.assertEqual(timeutil.postdemo_send_at(dt(17, 10), WEEKDAYS), dt(17, 12))
		self.assertEqual(timeutil.postdemo_send_at(dt(17, 14, 30), WEEKDAYS), dt(17, 16, 30))
		self.assertEqual(timeutil.postdemo_send_at(dt(17, 15), WEEKDAYS), dt(18, 9))
		self.assertEqual(timeutil.postdemo_send_at(dt(20, 15), WEEKDAYS), dt(23, 9))


class TestPickChannel(FrappeTestCase):
	NOW = dt(17, 10)
	DUE = dt(18, 10)  # mốc mục tiêu: 1 ngày làm việc sau

	def pick(self, last_inbound, *, email=True, phone=True, zns=True, free=48, start=None):
		return channelpick.pick_channel(
			self.NOW, last_inbound, free, self.DUE, email, phone, zns, start_at=start, cfg=WEEKDAYS
		)

	def test_chat_on_time(self):
		r = self.pick(dt(17, 9), free=48)
		self.assertEqual((r["kind"], r["at"], r["early"]), ("chat", self.DUE, False))

	def test_chat_early(self):
		# Tin cuối 17/11 09:00, hạn 24 giờ → 18/11 09:00; khung muộn nhất trước hạn ≥1 giờ là 17/11 16:30.
		r = self.pick(dt(17, 9), free=24)
		self.assertEqual((r["kind"], r["at"], r["early"]), ("chat", dt(17, 16, 30), True))

	def test_chat_early_respects_one_hour_margin(self):
		r = self.pick(dt(17, 3), free=10)  # hạn 13:00 → gửi muộn nhất 12:00
		self.assertEqual((r["kind"], r["at"]), ("chat", dt(17, 12)))

	def test_chat_early_needs_three_hours_after_docs(self):
		r = self.pick(dt(17, 3), free=10, start=dt(17, 9, 30))  # sớm nhất 12:30 > 12:00
		self.assertEqual(r["kind"], "email_task")
		r = self.pick(dt(17, 3), free=10, start=dt(17, 8, 30))  # sớm nhất 11:30 ≤ 12:00
		self.assertEqual((r["kind"], r["at"]), ("chat", dt(17, 12)))

	def test_closed_window_no_slot_before_expiry(self):
		r = self.pick(dt(17, 0), free=10.5)  # hạn 10:30 → muộn nhất 09:30 < bây giờ 10:00
		self.assertEqual(r["kind"], "email_task")
		r = self.pick(dt(16, 9), free=24)  # đã hết hạn từ trước
		self.assertEqual(r["kind"], "email_task")

	def test_full_table(self):
		# open: còn hạn tại mốc / closed: không có tin hoặc đã hết hạn, không còn khung sớm.
		for window_open, email, phone, zns in product((True, False), repeat=4):
			with self.subTest(window_open=window_open, email=email, phone=phone, zns=zns):
				last = dt(17, 9) if window_open else None
				r = self.pick(last, email=email, phone=phone, zns=zns, free=48)
				if window_open:
					expected = ("chat", self.DUE, False)
				elif email:
					expected = ("email_task", self.DUE, phone and zns)
				elif phone and zns:
					expected = ("zns", self.DUE, True)
				else:
					expected = ("none", None, False)
				self.assertEqual((r["kind"], r["at"], r["zns"]), expected)
				if expected[0] == "none":
					self.assertEqual(r["reason"], "Hết kênh liên hệ")

	def test_messenger_24h_variant(self):
		self.assertEqual(self.pick(dt(17, 10, 30), free=24)["kind"], "chat")
		self.assertEqual(self.pick(dt(17, 10, 30), free=24)["at"], self.DUE)
		self.assertEqual(self.pick(dt(17, 9, 59), free=24)["early"], True)

	def test_no_free_hours(self):
		self.assertEqual(self.pick(dt(17, 9), free=0)["kind"], "email_task")
		self.assertIsNone(channelpick.chat_deadline(None, 24))
		self.assertEqual(channelpick.chat_deadline(dt(17, 9), 24), dt(18, 9))


class TestValidate(FrappeTestCase):
	def test_email(self):
		self.assertEqual(validate.validate_email(" An.Nguyen+x@Miyano.com.VN "), "an.nguyen+x@miyano.com.vn")
		for bad in ("", None, "abc", "a@b", "a@@b.vn", "a b@c", "no-reply@miyano.vn"):
			with self.subTest(bad=bad):
				self.assertIsNone(validate.validate_email(bad))

	def test_phone(self):
		for raw in (
			"0912345678",
			"0912 345 678",
			"0912.345.678",
			"+84912345678",
			"+84 912 345 678",
			"84912345678",
		):
			with self.subTest(raw=raw):
				self.assertEqual(validate.validate_phone(raw), "+84912345678")
		for bad in (
			"",
			None,
			"091234567",
			"09123456789",
			"912345678",
			"abc0912345678",
			"+12025550123",
			"02838123456",
		):
			with self.subTest(bad=bad):
				self.assertIsNone(validate.validate_phone(bad))

	def test_phone_local(self):
		self.assertEqual(validate.phone_local("+84912345678"), "0912345678")

	def test_find_in_text(self):
		self.assertEqual(validate.find_email("email em là An@Benhvien.vn nhé"), "an@benhvien.vn")
		self.assertEqual(validate.find_phone("sđt em 0912 345 678 ạ"), "+84912345678")
		self.assertIsNone(validate.find_phone("em không cho số"))
		self.assertIsNone(validate.find_email("không có"))

	def test_masking(self):
		self.assertEqual(validate.mask_phone("0912345678"), "091****678")
		self.assertNotIn("2345", validate.mask_phone("+84912345678"))
		self.assertEqual(validate.mask_email("an.nguyen@miyano.vn"), "a***@miyano.vn")
		self.assertEqual(validate.mask_email(""), "")
		self.assertEqual(validate.mask_phone(None), "")
