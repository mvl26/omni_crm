"""Mẫu tin nhắn chat (ADR 0026): kiểm mẫu lúc lưu, điền biến, lọc kênh/thứ tự, API tab Messenger, quyền, bộ mẫu khởi đầu.
Dữ liệu commit thật nên dọn thủ công (tiền tố TD26)."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import messenger_tab
from omni_crm.crm_bridge import chat_templates

PREFIX = "TD26"
DT = chat_templates.DOCTYPE
NOPERM = "td26.noperm@example.com"
SEED_NAMES = [t[0] for t in chat_templates.TEMPLATES]


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	frappe.db.delete(DT, {"name": ["like", f"{PREFIX}%"]})
	for lead in frappe.get_all("CRM Lead", filters={"first_name": ["like", f"%{PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	if frappe.db.exists("User", NOPERM):
		frappe.delete_doc("User", NOPERM, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.commit()


def tpl(name, content, **kw):
	return frappe.get_doc(
		{"doctype": DT, "template_name": f"{PREFIX} {name}", "content": content, **kw}
	).insert()


def lead(first_name, **kw):
	return (
		frappe.get_doc({"doctype": "CRM Lead", "first_name": first_name, **kw})
		.insert(ignore_permissions=True)
		.name
	)


def only_td26(items):
	return [i for i in items if i["name"].startswith(PREFIX)]


class TestChatTemplates(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		saved = frappe.db.get_single_value("Omni Settings", "messenger_tab_disabled")
		self.addCleanup(
			lambda: frappe.db.set_single_value("Omni Settings", "messenger_tab_disabled", saved or 0)
		)
		frappe.db.set_single_value("Omni Settings", "messenger_tab_disabled", 0)
		mobile = frappe.db.get_value("User", "Administrator", "mobile_no")
		self.addCleanup(lambda: frappe.db.set_value("User", "Administrator", "mobile_no", mobile))
		frappe.db.set_value("User", "Administrator", "mobile_no", "0901234567")
		frappe.db.commit()

	def test_kiem_mau_luc_luu(self):
		for bad in ("", "{% if %}", "Chào {{ ten_kach }}", "{{ x.__class__ }}", "a" * 2001):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				tpl("loi", bad)
		ok = tpl("ok", "  Chào {{ ten_goi }}  ", sort_order="3", usage_note="  dùng   khi  ")
		self.assertEqual((ok.content, ok.sort_order, ok.usage_note), ("Chào {{ ten_goi }}", 3, "dùng khi"))

	def test_dien_bien_va_don_khoang_trang(self):
		name = lead(f"Tổng Bị Minh {PREFIX}", organization="BV TD26")
		tpl(
			"day du",
			"Chào anh/chị {{ ten_goi }} ({{ ten_khach }}) - {{ cong_ty }}. Em là {{ ten_sale }}"
			"{% if sdt_sale %}, số {{ sdt_sale }}{% endif %}. Email: {{ lead.email }}. A & B <c>",
		)
		item = only_td26(messenger_tab.get_templates("CRM Lead", name)["templates"])[0]
		self.assertIsNone(item["error"])
		self.assertEqual(
			item["text"],
			f"Chào anh/chị {PREFIX} (Tổng Bị Minh {PREFIX}) - BV TD26. Em là Administrator, số 0901234567. "
			"Email:. A & B <c>",  # không escape HTML: chữ thuần; trường trống thành "" (không phải "None")
		)

	def test_ten_tam_khong_dien(self):
		name = lead(f"Khách Meta Messenger {PREFIX}")
		tpl("chao", "Dạ chào anh/chị{{ ' ' ~ ten_goi if ten_goi else '' }} , em hỗ trợ được gì ạ?")
		item = only_td26(messenger_tab.get_templates("CRM Lead", name)["templates"])[0]
		self.assertEqual(item["text"], "Dạ chào anh/chị, em hỗ trợ được gì ạ?")

	def test_loc_kenh_tat_va_thu_tu(self):
		name = lead(f"An {PREFIX}")
		tpl("b chung", "b", sort_order=5)
		tpl("a messenger", "a", channel_type="Meta Messenger", sort_order=5)
		tpl("z dau", "z", sort_order=1)
		tpl("zalo", "zalo", channel_type="Zalo OA")
		tpl("tat", "tat", enabled=0)
		titles = [i["title"] for i in only_td26(messenger_tab.get_templates("CRM Lead", name)["templates"])]
		self.assertEqual(titles, [f"{PREFIX} z dau", f"{PREFIX} a messenger", f"{PREFIX} b chung"])

	def test_mau_loi_khong_lam_hong_danh_sach(self):
		name = lead(f"An {PREFIX}")
		tpl("tot", "tốt")
		hong = tpl("hong", "tạm")
		frappe.db.set_value(DT, hong.name, "content", "{% if %}")  # sửa thẳng DB, bỏ qua kiểm lúc lưu
		frappe.local.message_log = []
		items = {i["title"]: i for i in only_td26(messenger_tab.get_templates("CRM Lead", name)["templates"])}
		self.assertEqual(items[f"{PREFIX} tot"]["text"], "tốt")
		self.assertEqual(items[f"{PREFIX} hong"]["text"], "")
		self.assertIn("cú pháp", items[f"{PREFIX} hong"]["error"])
		self.assertFalse(frappe.message_log)  # không bật hộp thông báo khi chỉ liệt kê mẫu

	def test_tab_tat_va_quyen(self):
		name = lead(f"An {PREFIX}")
		tpl("x", "x")
		frappe.db.set_single_value("Omni Settings", "messenger_tab_disabled", 1)
		self.assertEqual(messenger_tab.get_templates("CRM Lead", name), {"templates": []})
		frappe.db.set_single_value("Omni Settings", "messenger_tab_disabled", 0)
		with mock.patch("frappe.enqueue"):
			frappe.get_doc(
				{"doctype": "User", "email": NOPERM, "first_name": PREFIX, "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.set_user(NOPERM)
		with self.assertRaises(frappe.PermissionError):
			messenger_tab.get_templates("CRM Lead", name)

	def test_bo_mau_khoi_dau(self):
		created = [n for n in SEED_NAMES if not frappe.db.exists(DT, n)]

		def drop_created():
			frappe.db.rollback()
			for n in created:
				frappe.db.delete(DT, {"name": n})
			frappe.db.commit()

		self.addCleanup(drop_created)
		first = chat_templates.install()
		self.assertEqual(sorted(first["created"]), sorted(created))
		frappe.db.set_value(DT, SEED_NAMES[0], "content", "Đã sửa bởi quản lý")
		second = chat_templates.install()
		self.assertEqual((second["created"], sorted(second["kept"])), ([], sorted(SEED_NAMES)))
		self.assertEqual(frappe.db.get_value(DT, SEED_NAMES[0], "content"), "Đã sửa bởi quản lý")
		frappe.db.set_value(DT, SEED_NAMES[0], "content", chat_templates.TEMPLATES[0][3])
		# mọi mẫu khởi đầu dựng được cho khách chưa biết tên và khách có tên, không còn biến chưa điền
		for lead_row in ({}, {"first_name": "Tổng Bị Minh"}):
			ctx = chat_templates.context_for(lead_row)
			for _n, _note, _order, content in chat_templates.TEMPLATES:
				text = chat_templates.render(content, ctx)
				self.assertNotIn("{", text)
				self.assertNotIn("None", text)
				self.assertNotIn("  ", text)
