# ruff: noqa: RUF001
"""V2-02: hàm thuần chuẩn hóa nhà thầu (docs/14 mục 2.1, 2.2, tên miền, tên công ty)."""

import unittest

from omni_crm.prospect.normalize import (
	FREE_EMAIL_DOMAINS,
	InvalidTaxId,
	company_name_key,
	corporate_domain,
	email_domain,
	hospital_key,
	normalize_tax_id,
	tax_id_base,
	website_domain,
)


class TestTaxId(unittest.TestCase):
	def test_ten_digits(self):
		self.assertEqual(normalize_tax_id("0123456789"), "0123456789")

	def test_thirteen_with_dash(self):
		self.assertEqual(normalize_tax_id("0123456789-001"), "0123456789-001")

	def test_spaces_dots_and_unicode_dash_are_removed(self):
		self.assertEqual(normalize_tax_id(" 0123 456.789 "), "0123456789")
		self.assertEqual(normalize_tax_id("0123.456.789 – 001"), "0123456789-001")

	def test_thirteen_digits_without_dash_gets_dash(self):
		self.assertEqual(normalize_tax_id("0123456789001"), "0123456789-001")

	def test_blank_is_none(self):
		self.assertIsNone(normalize_tax_id(None))
		self.assertIsNone(normalize_tax_id("   "))

	def test_wrong_length_or_characters_rejected(self):
		for bad in (
			"012345678",
			"01234567890",
			"0123456789-01",
			"0123456789-0012",
			"ABC123456789",
			"01234-56789",
		):
			with self.subTest(bad), self.assertRaises(InvalidTaxId):
				normalize_tax_id(bad)

	def test_base(self):
		self.assertEqual(tax_id_base("0123456789-001"), "0123456789")
		self.assertIsNone(tax_id_base(None))


class TestHospitalKey(unittest.TestCase):
	def test_synonymous_prefixes_merge(self):
		a = hospital_key("BV Đa khoa X")
		b = hospital_key("Bệnh viện đa khoa X")
		c = hospital_key("BVĐK X")
		self.assertEqual(a, "benh vien da khoa x")
		self.assertEqual(a, b)
		self.assertEqual(a, c)

	def test_ttyt_and_punctuation_and_spaces(self):
		self.assertEqual(hospital_key("TTYT  Huyện A."), "trung tam y te huyen a")
		self.assertEqual(hospital_key("  Bệnh   viện,  Nhi (TW) "), "benh vien nhi tw")

	def test_different_hospitals_stay_different(self):
		self.assertNotEqual(hospital_key("BV Đa khoa X"), hospital_key("BV Đa khoa Y"))

	def test_blank(self):
		self.assertEqual(hospital_key(None), "")
		self.assertEqual(hospital_key(" . "), "")


class TestDomains(unittest.TestCase):
	def test_corporate_domain(self):
		self.assertEqual(corporate_domain("Sale@CongTy.vn"), "congty.vn")

	def test_free_domains_are_not_company_domains(self):
		for mail in ("a@gmail.com", "a@yahoo.com.vn", "a@outlook.com", "a@hotmail.com", "a@icloud.com"):
			with self.subTest(mail):
				self.assertIsNone(corporate_domain(mail))
		self.assertIn("gmail.com", FREE_EMAIL_DOMAINS)

	def test_invalid_email(self):
		self.assertIsNone(email_domain("khong-phai-email"))
		self.assertIsNone(corporate_domain(None))

	def test_website_domain(self):
		self.assertEqual(website_domain("https://www.CongTy.vn/gioi-thieu?x=1"), "congty.vn")
		self.assertEqual(website_domain("congty.vn"), "congty.vn")
		self.assertEqual(website_domain("http://sub.congty.vn:8080"), "sub.congty.vn")
		self.assertIsNone(website_domain("khong phai ten mien"))
		self.assertIsNone(website_domain(""))

	def test_company_name_key(self):
		self.assertEqual(company_name_key("Công ty TNHH  ABC."), company_name_key("CONG TY TNHH ABC"))
