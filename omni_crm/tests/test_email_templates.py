"""Bộ mẫu thư dựng sẵn: cài không ghi đè mẫu đã sửa, Lead/Deal thiếu dữ liệu không để lại chỗ trống, cấp quyền không tạo vai trò."""

import re

import frappe
from frappe.email.doctype.email_template.email_template import get_email_template
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_fullname

from omni_crm.crm_bridge import email_templates as et

FULL_LEAD = {
	"doctype": "CRM Lead",
	"name": "CRM-LEAD-TDET",
	"salutation": "Mr",
	"first_name": "Lê Văn Khách",
	"lead_name": "Mr Lê Văn Khách",
	"organization": "Công ty Đông Á",
	"email": "khach@dong-a.vn",
}
EMPTY_LEADS = [
	{"doctype": "CRM Lead", "name": "L1", "lead_name": "Unnamed Lead"},  # CRM đặt khi không có gì
	{"doctype": "CRM Lead", "name": "L2", "first_name": "Khách Email 1234", "lead_name": "Khách Email 1234"},
	{"doctype": "CRM Lead", "name": "L3", "first_name": "khach@x.vn", "lead_name": "khach"},
	{"doctype": "CRM Lead", "name": "L4", "first_name": None, "organization": None, "lead_name": None},
]
DEAL = {
	"doctype": "CRM Deal",
	"name": "CRM-DEAL-TDET",
	"first_name": "Trần Thị",
	"last_name": "Mai",
	"organization": "DONG-A",
	"organization_name": "Công ty Đông Á",
}


def text(html: str) -> str:
	return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class TestEmailTemplates(FrappeTestCase):
	def setUp(self):
		self.names = [n for n, _dt, _c in et.TEMPLATES]
		frappe.db.delete("Email Template", {"name": ["in", self.names]})
		self.addCleanup(
			lambda: (frappe.db.delete("Email Template", {"name": ["in", self.names]}), frappe.db.commit())
		)

	def render(self, name, doc):
		out = get_email_template(name, doc)
		return out["subject"], text(out["message"])

	def assert_clean(self, subject, body):
		for s in (subject, body):
			self.assertNotIn("None", s)
			self.assertNotIn("{{", s)
			self.assertNotIn("{%", s)
			self.assertNotIn("Unnamed", s)
			self.assertNotRegex(s, r" [,.;:]")  # không có chỗ trống trước dấu câu
		self.assertTrue(subject.strip())

	def test_cai_tao_du_mau_dung_loai_trang_va_chay_lai_khong_ghi_de(self):
		out = et.install()
		self.assertEqual(sorted(out["created"]), sorted(self.names))
		row = frappe.db.get_value(
			"Email Template",
			"Miyano - Cảm ơn đã liên hệ",
			["enabled", "use_html", "reference_doctype"],
			as_dict=True,
		)
		self.assertEqual((row.enabled, row.use_html, row.reference_doctype), (1, 0, "CRM Lead"))
		self.assertEqual(
			frappe.db.get_value("Email Template", "Miyano - Hẹn lịch trao đổi (Deal)", "reference_doctype"),
			"CRM Deal",
		)
		frappe.db.set_value("Email Template", "Miyano - Cảm ơn đã liên hệ", "subject", "Người quản lý đã sửa")
		again = et.install()
		self.assertEqual(again["created"], [])
		self.assertEqual(
			frappe.db.get_value("Email Template", "Miyano - Cảm ơn đã liên hệ", "subject"),
			"Người quản lý đã sửa",
		)

	def test_lead_du_du_lieu_dien_ten_cong_ty_va_nguoi_gui_khong_dung_danh_xung(self):
		et.install()
		subject, body = self.render("Miyano - Cảm ơn đã liên hệ", FULL_LEAD)
		self.assertEqual(subject, "Miyano Việt Nam cảm ơn Công ty Đông Á đã liên hệ")
		self.assertIn("Kính gửi anh/chị Lê Văn Khách,", body)
		self.assertIn("Cảm ơn anh/chị và Công ty Đông Á đã quan tâm", body)
		self.assertNotIn("Mr", body)
		self.assertIn(get_fullname(frappe.session.user), body)
		self.assertIn("Công ty TNHH Miyano Việt Nam", body)
		self.assert_clean(subject, body)

	def test_moi_mau_voi_lead_thieu_du_lieu_khong_co_cho_trong(self):
		et.install()
		for name, doctype, _c in et.TEMPLATES:
			if doctype != "CRM Lead":
				continue
			for lead in EMPTY_LEADS:
				with self.subTest(mau=name, lead=lead["name"]):
					subject, body = self.render(name, lead)
					self.assertIn("Kính gửi anh/chị,", body)
					self.assertNotIn("Khách Email", body)
					self.assertNotIn("@", body.split("Trân trọng")[0])
					self.assert_clean(subject, body)

	def test_mau_deal_dung_ten_lien_he_va_ten_cong_ty(self):
		et.install()
		for name, doctype, _c in et.TEMPLATES:
			if doctype != "CRM Deal":
				continue
			with self.subTest(mau=name):
				subject, body = self.render(name, DEAL)
				self.assertIn("Kính gửi anh/chị Trần Thị Mai,", body)
				self.assertNotIn("DONG-A", subject + body)
				self.assert_clean(subject, body)

	def test_so_dien_thoai_sale_chi_hien_khi_co(self):
		et.install()
		user = frappe.session.user
		old = frappe.db.get_value("User", user, "mobile_no")
		self.addCleanup(frappe.db.set_value, "User", user, "mobile_no", old)
		frappe.db.set_value("User", user, "mobile_no", None)
		_s, body = self.render("Miyano - Gọi không được", FULL_LEAD)
		self.assertNotIn("Điện thoại", body)
		self.assertNotIn("hoặc gọi trực tiếp", body)
		frappe.db.set_value("User", user, "mobile_no", "0912345678")
		_s, body = self.render("Miyano - Gọi không được", FULL_LEAD)
		self.assertIn("Điện thoại: 0912345678", body)
		self.assertIn("hoặc gọi trực tiếp số 0912345678.", body)

	def test_khong_neu_phi_hay_cam_ket(self):
		# Quy tắc trả lời khách (bot/prompts/reply_v2.md): không nêu phí, tỷ lệ, chiết khấu, cam kết. Kiểm trên thư đã điền.
		et.install()
		for name, doctype, _c in et.TEMPLATES:
			subject, body = self.render(name, FULL_LEAD if doctype == "CRM Lead" else DEAL)
			whole = (subject + " " + body).lower()
			self.assertNotIn("%", whole)
			for word in (
				"phí",
				"chiết khấu",
				"giá",
				"cam kết",
				"bảo đảm",
			):  # nguyên từ: "phía", "đánh giá" không tính
				with self.subTest(mau=name, tu=word):
					self.assertNotRegex(whole, rf"(?<!\w)(?<!đánh ){re.escape(word)}(?!\w)")

	def test_cap_quyen_cho_sales_manager_khong_tao_vai_tro(self):
		before = frappe.get_all("Custom DocPerm", filters={"parent": "Email Template"}, fields=["*"])
		roles_before = frappe.db.count("Role")

		def restore():
			frappe.db.delete("Custom DocPerm", {"parent": "Email Template"})
			for row in before:
				frappe.get_doc({**row, "doctype": "Custom DocPerm"}).db_insert()
			frappe.clear_cache(doctype="Email Template")
			frappe.db.commit()

		self.addCleanup(restore)
		et.grant_manager_access()
		perm = frappe.db.get_value(
			"Custom DocPerm",
			{"parent": "Email Template", "role": "Sales Manager", "permlevel": 0},
			["read", "write", "create", "delete"],
			as_dict=True,
		)
		self.assertEqual((perm.read, perm.write, perm.create, perm.delete), (1, 1, 1, 1))
		# Quyền chuẩn được chép sang, System Manager không mất quyền.
		self.assertTrue(
			frappe.db.get_value(
				"Custom DocPerm", {"parent": "Email Template", "role": "System Manager"}, "create"
			)
		)
		self.assertEqual(frappe.db.count("Role"), roles_before)
		et.grant_manager_access()  # chạy lại không sao
		self.assertRaises(frappe.ValidationError, et.grant_manager_access, "Vai Tro Khong Co")
		self.assertEqual(frappe.db.count("Role"), roles_before)
