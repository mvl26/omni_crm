from frappe.tests.utils import FrappeTestCase

from omni_crm.identity.extract import extract_contacts
from omni_crm.identity.normalize import (
	LEGACY_PREFIXES,
	is_informative_name,
	is_placeholder_name,
	is_system_email,
	name_similarity,
	normalize_email,
	normalize_phone,
	normalize_sender,
)

PHONE_CASES = [
	# (đầu vào, kết quả mong đợi)
	("0912345678", "+84912345678"),
	("0912 345 678", "+84912345678"),
	("0912.345.678", "+84912345678"),
	("0912-345-678", "+84912345678"),
	("(0912) 345 678", "+84912345678"),
	("  0912345678  ", "+84912345678"),
	("+84912345678", "+84912345678"),
	("+84 912 345 678", "+84912345678"),
	("+84 (0)912345678", "+84912345678"),  # dạng viết có (0) sau mã nước
	("84912345678", "+84912345678"),
	("84 912 345 678", "+84912345678"),
	("0084912345678", "+84912345678"),
	("00 84 912 345 678", "+84912345678"),
	# đầu số 11 số cũ -> 10 số
	("01623456789", "+84323456789"),
	("0162 345 6789", "+84323456789"),
	("01689123456", "+84389123456"),
	("01201234567", "+84701234567"),
	("01221234567", "+84771234567"),
	("01261234567", "+84761234567"),
	("01281234567", "+84781234567"),
	("01861234567", "+84561234567"),
	("01881234567", "+84581234567"),
	("01991234567", "+84591234567"),
	("+84 162 3456789", "+84323456789"),
	("84 1623456789", "+84323456789"),
	# số cố định
	("024 3123 4567", "+842431234567"),
	("(028) 3822 1234", "+842838221234"),
	("02838221234", "+842838221234"),
	# quốc tế hợp lệ
	("+1 650 253 0000", "+16502530000"),
	("001 650 253 0000", "+16502530000"),
	# rác
	("", None),
	("   ", None),
	(None, None),
	("abc", None),
	("0912abc345", None),
	("0912 345 678 ext 5", None),
	("0912", None),
	("123", None),
	("912345678", None),  # thiếu số 0 đầu: không đoán
	("0123456789", None),  # đầu số 012 10 số không tồn tại
	("0912345678901", None),  # quá dài
	("+", None),
	("++84912345678", None),
]


class TestNormalizePhone(FrappeTestCase):
	def test_cases(self):
		self.assertGreaterEqual(len(PHONE_CASES), 25)
		for raw, expected in PHONE_CASES:
			with self.subTest(raw=raw):
				self.assertEqual(normalize_phone(raw), expected)

	def test_every_legacy_prefix_converts_to_valid_number(self):
		for old, new in LEGACY_PREFIXES.items():
			with self.subTest(old=old):
				self.assertEqual(normalize_phone(old + "2345678"), "+84" + new[1:] + "2345678")

	def test_idempotent(self):
		for _raw, expected in PHONE_CASES:
			if expected:
				self.assertEqual(normalize_phone(expected), expected)


class TestNormalizeEmail(FrappeTestCase):
	def test_lower_and_strip(self):
		self.assertEqual(normalize_email("  An.Nguyen@ABC.vn "), "an.nguyen@abc.vn")

	def test_keeps_dots_and_plus_tag(self):
		self.assertEqual(normalize_email("a.b+sale@x.com"), "a.b+sale@x.com")
		self.assertNotEqual(normalize_email("a.b@x.com"), normalize_email("ab@x.com"))

	def test_display_name_form(self):
		self.assertEqual(normalize_email('"Nam Le" <Nam@Abc.vn>'), "nam@abc.vn")

	def test_invalid(self):
		for raw in ("", None, "abc", "a@b", "a b@c", "@x.com"):
			with self.subTest(raw=raw):
				self.assertIsNone(normalize_email(raw))

	def test_system_email(self):
		for e in ("no-reply@x.com", "NoReply@x.com", "mailer-daemon@x.com", "postmaster@x.com"):
			self.assertTrue(is_system_email(e), e)
		self.assertFalse(is_system_email("sale@x.com"))
		self.assertTrue(is_system_email("bot@x.com", extra=["bot"]))
		self.assertTrue(is_system_email("a@list.com", extra=["a@list.com"]))
		self.assertFalse(is_system_email(None))


class TestNormalizeSender(FrappeTestCase):
	def test_by_channel(self):
		self.assertEqual(normalize_sender("Zalo OA", " 2973 "), "2973")
		self.assertEqual(normalize_sender("Email", " An@ABC.vn "), "an@abc.vn")
		self.assertEqual(normalize_sender("Telephony", "0912 345 678"), "+84912345678")
		self.assertEqual(
			normalize_sender("Telephony", "anonymous"), "anonymous"
		)  # giữ nguyên nếu không chuẩn hóa được
		self.assertEqual(normalize_sender("Email", None), "")


class TestNames(FrappeTestCase):
	def test_similarity(self):
		self.assertEqual(name_similarity("Nguyễn Văn Nam", "nguyen van nam"), 1.0)
		self.assertEqual(name_similarity("Đặng Thị Hoa", "Dang Thi Hoa"), 1.0)
		self.assertEqual(name_similarity("Nam Nguyen Van", "Nguyen Van Nam"), 1.0)  # không phụ thuộc thứ tự
		self.assertAlmostEqual(name_similarity("Nguyen Van A", "Nguyen Van B"), 2 / 3)
		self.assertLess(name_similarity("Tran Minh Hoa", "Le Van Khach"), 0.5)
		self.assertEqual(name_similarity("", "Nam"), 0.0)
		self.assertEqual(name_similarity(None, None), 0.0)

	def test_placeholder(self):
		for n in (None, "", "Anh", "Chị", "Khách", "Zalo user", "Khách Zalo OA 4821", "Anh khách", "  "):
			self.assertTrue(is_placeholder_name(n), n)
		for n in ("Nam", "Anh Nam", "Nguyễn Văn Nam"):
			self.assertFalse(is_placeholder_name(n), n)

	def test_informative_needs_two_words(self):
		self.assertFalse(is_informative_name("Nam"))
		self.assertFalse(is_informative_name("Anh Nam"))
		self.assertTrue(is_informative_name("Nguyễn Nam"))
		self.assertTrue(is_informative_name("Trần Minh Hoa"))


class TestExtractContacts(FrappeTestCase):
	def test_signature_multiline(self):
		text = """Kính gửi anh,

Em xin báo giá như file đính kèm.

Trân trọng,
Lê Thị Mai - Phòng Mua hàng
Công ty ABC
Tel: 024 3123 4567 | Mobile: 0912.345.678
Email: Mai.Le@ABC.vn
"""
		r = extract_contacts(text)
		self.assertEqual(r["phones"], ["+842431234567", "+84912345678"])
		self.assertEqual(r["emails"], ["mai.le@abc.vn"])

	def test_spaced_number(self):
		self.assertEqual(extract_contacts("Số em là 0912 345 678 nhé")["phones"], ["+84912345678"])
		self.assertEqual(extract_contacts("zalo +84 912 345 678")["phones"], ["+84912345678"])

	def test_two_numbers_in_one_sentence(self):
		r = extract_contacts("Gọi 0912345678 hoặc 0987 654 321 đều được")
		self.assertEqual(r["phones"], ["+84912345678", "+84987654321"])
		r = extract_contacts("0912345678 0987654321")
		self.assertEqual(r["phones"], ["+84912345678", "+84987654321"])

	def test_dedupe_keeps_order(self):
		r = extract_contacts("0912345678, 0987654321, +84912345678, 0912.345.678")
		self.assertEqual(r["phones"], ["+84912345678", "+84987654321"])

	def test_ignores_non_phone_numbers(self):
		r = extract_contacts("Đơn 20240312 giá 1.500.000 đ, mã 12345, ngày 12/03/2024")
		self.assertEqual(r["phones"], [])

	def test_email_digits_not_phone(self):
		r = extract_contacts("mail 0912345678@abc.vn")
		self.assertEqual(r["phones"], [])
		self.assertEqual(r["emails"], ["0912345678@abc.vn"])

	def test_system_email_dropped(self):
		r = extract_contacts("Từ no-reply@x.com và sale@x.com")
		self.assertEqual(r["emails"], ["sale@x.com"])

	def test_empty(self):
		self.assertEqual(extract_contacts(None), {"phones": [], "emails": []})
		self.assertEqual(extract_contacts(""), {"phones": [], "emails": []})
