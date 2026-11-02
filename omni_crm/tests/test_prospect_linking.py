# ruff: noqa: RUF001
"""V2-02: patch chuyển Lead hiện có sang mô hình nhà thầu (dry run mặc định, mơ hồ không tự xử lý, chạy lại không trùng)
và giai đoạn bán hàng B2B (chỉ thêm; không đổi trạng thái Lead/Deal; bảng ánh xạ đề xuất).
"""

from unittest import mock

import frappe

from omni_crm.patches.v2 import add_b2b_sales_stages, link_existing_leads_to_organizations
from omni_crm.prospect import linking, stages
from omni_crm.tests.identity_helpers import IdentityTestCase, phone
from omni_crm.tests.test_resolver_orgs import make_contact, make_org

P = "TD213L"
DOMAIN_NEW = "td213l-congty.example"
DOMAIN_BIG = "td213l-big.example"


def cleanup_linking() -> None:
	frappe.db.rollback()
	for c in frappe.get_all("Contact", filters={"first_name": ["like", f"{P}%"]}, pluck="name"):
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True, delete_permanently=True)
	for lead in frappe.get_all("CRM Lead", filters={"first_name": ["like", f"{P}%"]}, pluck="name"):
		frappe.db.delete("Comment", {"reference_name": lead})
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	for org in frappe.get_all(
		"CRM Organization",
		or_filters=[["name", "like", f"{P}%"], ["name", "like", "td213l%"]],
		pluck="name",
	):
		frappe.delete_doc("CRM Organization", org, force=True, ignore_permissions=True)
	frappe.db.commit()


class LinkingCase(IdentityTestCase):
	def setUp(self):
		super().setUp()
		cleanup_linking()
		self.addCleanup(cleanup_linking)
		# Organization có sẵn
		self.xyz = make_org(f"{P} Công ty XYZ", "0100213601")
		self.dup1 = make_org(f"{P} Co. Dup")
		self.dup2 = make_org(f"{P} Co Dup")
		self.big = make_org(f"{P} Big", "0100213602", website=f"https://www.{DOMAIN_BIG}")
		# Lead tổng hợp (dữ liệu giả, không phải dữ liệu thật)
		self.l1 = self.make_lead(
			f"{P} Một", mobile=phone(70), organization=f"{P} Công ty ABC", job_title="Giám đốc"
		)
		self.l2 = self.make_lead(f"{P} Hai", mobile=phone(71), organization=f"{P.lower()} cong ty abc")
		self.l3 = self.make_lead(f"{P} Ba", mobile=phone(72), organization=f"{P} Cong ty XYZ")
		self.l4 = self.make_lead(f"{P} Bốn", mobile=phone(73), organization=f"{P} CO DUP")
		self.l5 = self.make_lead(f"{P} Năm", mobile=phone(74))
		self.l6 = self.make_lead(f"{P} Sáu", mobile=phone(75))
		self.l7 = self.make_lead(f"{P} Bảy", mobile=phone(76))
		self.l8 = self.make_lead(f"{P} Tám")
		self.l9 = self.make_lead(f"{P} Chín", mobile=phone(77))
		self.l10 = self.make_lead(
			f"{P} Mười", mobile=phone(78), organization=f"{P} Công ty XYZ"
		)  # đã liên kết sẵn
		for lead, mail in (
			(self.l5, f"a@{DOMAIN_NEW}"),
			(self.l6, f"b@{DOMAIN_NEW}"),
			(self.l7, "c@gmail.com"),
			(self.l9, f"d@{DOMAIN_BIG}"),
		):
			frappe.db.set_value(
				"CRM Lead", lead, {"email": mail, "omni_email_norm": mail}, update_modified=False
			)  # như v1: không qua validate (tránh has_gravatar)
		self.merged = self.make_lead(f"{P} Đã gộp", organization=f"{P} Công ty ABC")
		frappe.db.set_value("CRM Lead", self.merged, "omni_merged_into", self.l1)
		frappe.db.commit()
		self.leads = [
			self.l1,
			self.l2,
			self.l3,
			self.l4,
			self.l5,
			self.l6,
			self.l7,
			self.l8,
			self.l9,
			self.l10,
		]

	def snapshot(self) -> dict:
		rows = frappe.get_all(
			"CRM Lead",
			filters={"name": ["in", [*self.leads, self.merged]]},
			fields=["name", "status", "lead_owner", "modified", "omni_merged_into", "organization"],
		)
		return {r.name: r for r in rows}


class TestDryRun(LinkingCase):
	def test_dry_run_reports_and_writes_nothing(self):
		before = self.snapshot()
		orgs, contacts = frappe.db.count("CRM Organization"), frappe.db.count("Contact")
		report = linking.run(dry_run=True)
		self.assertTrue(report["dry_run"])
		self.assertEqual(frappe.db.count("CRM Organization"), orgs)
		self.assertEqual(frappe.db.count("Contact"), contacts)
		self.assertEqual(self.snapshot(), before)
		self.assertEqual(
			sorted(report["organizations_to_create"]),
			sorted([f"{P} Công ty ABC", DOMAIN_NEW]),
		)
		amb = [a for a in report["ambiguous"] if a["lead"] == self.l4]
		self.assertEqual(len(amb), 1)
		self.assertEqual(sorted(amb[0]["ung_vien"]), sorted([self.dup1, self.dup2]))
		self.assertEqual(report["leads_relink_spelling"], 2)  # l2 (theo ABC sẽ tạo) và l3 (theo XYZ có sẵn)

	def test_report_text_has_mapping_and_ambiguity(self):
		text = linking.format_report(linking.run(dry_run=True))
		self.assertIn("DRY RUN", text)
		self.assertIn("Bảng ánh xạ trạng thái ĐỀ XUẤT", text)
		self.assertIn("Nhiều Organization trùng tên chuẩn hóa", text)

	def test_patch_entry_defaults_to_dry_run(self):
		orgs = frappe.db.count("CRM Organization")
		report = link_existing_leads_to_organizations.execute()
		self.assertTrue(report["dry_run"])
		self.assertEqual(frappe.db.count("CRM Organization"), orgs)
		# chuỗi từ --kwargs của bench: "False" là chạy thật, "true"/"1" là dry run (không chạy thật trong test)
		with (
			mock.patch.object(linking, "run", return_value={"dry_run": None}) as run,
			mock.patch.object(linking, "format_report", return_value=""),
		):
			link_existing_leads_to_organizations.execute(dry_run="False")
			link_existing_leads_to_organizations.execute(dry_run="true")
		self.assertEqual([c.kwargs["dry_run"] for c in run.call_args_list], [False, True])

	def test_patch_is_not_registered_for_automatic_run(self):
		import pathlib

		import omni_crm

		text = (pathlib.Path(omni_crm.__file__).parent / "patches.txt").read_text()
		self.assertNotIn("link_existing_leads_to_organizations", text)
		self.assertIn("add_b2b_sales_stages", text)


class TestRealRun(LinkingCase):
	def test_real_run_links_creates_and_is_idempotent(self):
		before = self.snapshot()
		report = linking.run(dry_run=False)
		self.assertFalse(report["dry_run"])
		abc = f"{P} Công ty ABC"
		self.assertTrue(frappe.db.exists("CRM Organization", abc))
		self.assertTrue(frappe.db.exists("CRM Organization", DOMAIN_NEW))
		org = frappe.db.get_value(
			"CRM Organization", abc, ["omni_tax_id", "omni_verified", "omni_data_source"], as_dict=True
		)
		self.assertEqual((org.omni_tax_id, org.omni_verified), (None, 0))
		get = lambda lead, f: frappe.db.get_value("CRM Lead", lead, f)  # noqa: E731
		self.assertEqual(get(self.l1, "organization"), abc)
		self.assertEqual(get(self.l2, "organization"), abc)  # cách viết khác -> tên Organization chuẩn
		self.assertEqual(get(self.l3, "organization"), self.xyz)
		self.assertEqual(get(self.l5, "organization"), DOMAIN_NEW)
		self.assertEqual(get(self.l6, "organization"), DOMAIN_NEW)
		self.assertEqual(get(self.l9, "organization"), self.big)
		# mơ hồ, gmail, không thông tin: không đụng tới
		self.assertEqual(get(self.l4, "organization"), f"{P} CO DUP")
		self.assertFalse(get(self.l7, "organization"))
		self.assertFalse(get(self.l8, "organization"))
		# Lead đã gộp bị loại khỏi chuyển đổi
		self.assertEqual(get(self.merged, "organization"), f"{P} Công ty ABC")
		# KHÔNG đổi trạng thái, người phụ trách, thời điểm sửa của Lead nào
		after = self.snapshot()
		for name, row in before.items():
			self.assertEqual(
				(
					after[name].status,
					after[name].lead_owner,
					after[name].modified,
					after[name].omni_merged_into,
				),
				(row.status, row.lead_owner, row.modified, row.omni_merged_into),
				name,
			)
		# Contact từ thông tin người của Lead
		c = frappe.get_all(
			"Contact",
			filters={"first_name": f"{P} Một"},
			fields=["name", "company_name", "designation", "omni_info_source"],
		)
		self.assertEqual(len(c), 1)
		self.assertEqual((c[0].company_name, c[0].designation), (abc, "Giám đốc"))
		self.assertIn(self.l1, c[0].omni_info_source)
		# chạy lại: không tạo thêm gì
		orgs, contacts = frappe.db.count("CRM Organization"), frappe.db.count("Contact")
		again = linking.run(dry_run=False)
		self.assertEqual((frappe.db.count("CRM Organization"), frappe.db.count("Contact")), (orgs, contacts))
		self.assertEqual((again["leads_to_link"], again["organizations_to_create"]), (0, []))
		self.assertEqual(len(again["ambiguous"]), len([a for a in report["ambiguous"]]))

	def test_existing_contact_is_not_duplicated(self):
		make_contact(self.xyz, f"{P} Người cũ", phone_no=phone(72))  # cùng SĐT với l3
		contacts = frappe.db.count("Contact", {"first_name": ["like", f"{P}%"]})
		linking.run(dry_run=False)
		# l3 đã có Contact theo SĐT: không tạo thêm; các Lead có SĐT khác thì có
		self.assertFalse(frappe.db.exists("Contact", {"first_name": f"{P} Ba"}))
		self.assertGreater(frappe.db.count("Contact", {"first_name": ["like", f"{P}%"]}), contacts)


class TestStages(IdentityTestCase):
	def test_stages_are_added_idempotently_and_existing_untouched(self):
		before = dict(frappe.db.sql("select lead_status, concat(type, position) from `tabCRM Lead Status`"))
		add_b2b_sales_stages.execute()
		add_b2b_sales_stages.execute()
		for name in ("Tiềm năng", "Đã liên hệ", "Đã trao đổi nhu cầu", "Không phù hợp"):
			self.assertTrue(frappe.db.exists("CRM Lead Status", name), name)
		for name in ("Đề xuất gói", "Đàm phán", "Ký hợp đồng", "Không phù hợp"):
			self.assertTrue(frappe.db.exists("CRM Deal Status", name), name)
		for name in ("Không có nhu cầu", "Đã tự vận hành", "Quy mô nhỏ", "Không liên lạc được"):
			self.assertTrue(frappe.db.exists("CRM Lost Reason", name), name)
		after = dict(frappe.db.sql("select lead_status, concat(type, position) from `tabCRM Lead Status`"))
		for k, v in before.items():  # trạng thái cũ (kể cả New, Junk, Qualified) giữ nguyên
			self.assertEqual(after[k], v)
		types = dict(frappe.db.sql("select lead_status, type from `tabCRM Lead Status`"))
		self.assertEqual(
			(types["Tiềm năng"], types["Đã liên hệ"], types["Đã trao đổi nhu cầu"], types["Không phù hợp"]),
			("Open", "Ongoing", "Ongoing", "Lost"),
		)
		deal_types = dict(frappe.db.sql("select deal_status, type from `tabCRM Deal Status`"))
		self.assertEqual(
			(
				deal_types["Đề xuất gói"],
				deal_types["Đàm phán"],
				deal_types["Ký hợp đồng"],
				deal_types["Không phù hợp"],
			),
			("Ongoing", "Ongoing", "Won", "Lost"),
		)
		self.assertTrue(frappe.db.exists("CRM Lost Reason", "Other"))  # "Khác" dùng bản có sẵn

	def test_positions_follow_docs_order_after_existing(self):
		stages.ensure_stages()
		pos = dict(frappe.db.sql("select lead_status, position from `tabCRM Lead Status`"))
		self.assertLess(pos["Junk"], pos["Tiềm năng"])
		self.assertLess(pos["Tiềm năng"], pos["Đã liên hệ"])
		self.assertLess(pos["Đã liên hệ"], pos["Đã trao đổi nhu cầu"])

	def test_new_leads_still_default_to_new_status(self):
		stages.ensure_stages()
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD213L Mặc định"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(frappe.delete_doc, "CRM Lead", lead.name, force=True, ignore_permissions=True)
		self.assertEqual(lead.status, "New")

	def test_mapping_table_counts_and_changes_nothing(self):
		stages.ensure_stages()
		table = stages.mapping_table()
		by = {(r["loai"], r["hien_tai"]): r for r in table}
		self.assertEqual(by[("Lead", "New")]["de_xuat"], "Tiềm năng")
		self.assertEqual(by[("Deal", "Negotiation")]["de_xuat"], "Đàm phán")
		self.assertEqual(by[("Lead", "Unqualified")]["de_xuat"], "Không phù hợp")
		total = sum(r["so_luong"] for r in table if r["loai"] == "Lead")
		self.assertEqual(total, frappe.db.count("CRM Lead"))
		# không hàm nào của module đổi trạng thái
		self.assertFalse([n for n in dir(stages) if "apply" in n.lower() or "migrate_status" in n.lower()])
