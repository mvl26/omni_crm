# ruff: noqa: RUF001
"""V2-04 (docs/13 mục 3b; docs/14 mục 8): Chi tiết khách/nhà thầu — tiêu đề, người liên hệ, gói thầu, Mức phù hợp, hoạt động."""

from datetime import datetime, timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import customer
from omni_crm.crm_bridge import care_log
from omni_crm.prospect import contacts as contacts_mod
from omni_crm.prospect import fit
from omni_crm.tests.fake_adapter import make_channel
from omni_crm.tests.test_prospect_model import make_award, make_lead, make_org, make_user

LOG = "Omni Care Log"
SALE1 = "td404_c1@example.com"
SALE2 = "td404_c2@example.com"
T0 = datetime(2026, 11, 4, 9, 0)


def make_contact(org: str, first: str, phone: str | None = None, email: str | None = None, **kw) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first,
			"company_name": org,
			"omni_info_source": "TD404 test",
			"phone_nos": [{"phone": phone, "is_primary_mobile_no": 1}] if phone else [],
			"email_ids": [{"email_id": email, "is_primary": 1}] if email else [],
			**kw,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class CustomerCase(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("td404_cust")
		self.addCleanup(frappe.db.rollback, save_point="td404_cust")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.clear_document_cache, "Omni Settings", "Omni Settings")
		make_user(SALE1)
		make_user(SALE2)
		self.org = make_org("TD404 Công ty Vật tư", "0900404100", omni_region="Miền Bắc")
		self.lead = make_lead("TD404 Linh", organization=self.org, owner=SALE1)
		self.person = make_lead("TD404 Khach le", organization="TD404 khong co trong danh sach", owner=SALE1)


class TestHeader(CustomerCase):
	def test_title_is_company_name_and_tax_id_when_organization_exists(self):
		h = customer.get_header(self.lead)
		self.assertEqual(
			(h["title"], h["tax_id"], h["is_prospect"]), ("TD404 Công ty Vật tư", "0900404100", True)
		)
		self.assertEqual(h["subtitle"], "TD404 Linh")
		self.assertEqual(h["organization"]["name"], self.org)

	def test_title_stays_person_name_without_organization_record(self):
		h = customer.get_header(self.person)
		self.assertEqual((h["title"], h["tax_id"], h["is_prospect"]), ("TD404 Khach le", None, False))
		self.assertIn(
			"TD404 khong co trong danh sach", h["subtitle"]
		)  # đơn vị gõ tay vẫn hiện, không phải nhà thầu
		lone = make_lead("TD404 Khong don vi", owner=SALE1)
		self.assertEqual(customer.get_header(lone)["title"], "TD404 Khong don vi")

	def test_stage_bar_comes_from_crm_lead_status_list_not_hardcoded(self):
		frappe.db.set_value("CRM Lead", self.lead, "status", "Đã liên hệ")
		stages = customer.get_header(self.lead)["stages"]
		names = [s["name"] for s in stages["lead_stages"]]
		expected = frappe.get_all(
			"CRM Lead Status", filters={"type": ["!=", "Lost"]}, order_by="position asc", pluck="name"
		)
		self.assertEqual(names, expected)
		self.assertEqual(stages["lead_status"], "Đã liên hệ")
		self.assertIn("Đã liên hệ", names)

	def test_call_and_message_hidden_when_person_refused(self):
		frappe.db.set_value(
			"CRM Lead", self.lead, {"mobile_no": "0900404555", "email": "td404.linh@example.com"}
		)
		self.assertTrue(customer.get_header(self.lead)["actions"]["can_call"])
		contact = make_contact(self.org, "TD404 Linh", phone="0900404555", email="td404.linh@example.com")
		self.assertEqual(contacts_mod.contacts_of_lead(self.lead), [contact])
		self.assertTrue(customer.get_header(self.lead)["actions"]["can_call"])  # Chưa hỏi: vẫn gọi được
		frappe.db.set_value("Contact", contact, "omni_consent", "Từ chối")
		actions = customer.get_header(self.lead)["actions"]
		self.assertFalse(actions["can_call"])
		self.assertFalse(actions["can_email"])
		self.assertFalse(actions["can_message"])
		self.assertIsNone(actions["phone"])
		self.assertTrue(actions["person_refused"])
		profile = customer.get_profile(self.lead)
		values = [
			i["value"] for g in profile["groups"] for i in g["items"] if i["field"] in ("mobile_no", "email")
		]
		self.assertEqual(values, ["", ""])  # SĐT/email không hiện

	def test_call_hidden_after_contact_data_erased(self):
		frappe.db.set_value("CRM Lead", self.lead, "mobile_no", "0900404556")
		contact = make_contact(self.org, "TD404 Linh2", phone="0900404556")
		self.assertTrue(contacts_mod.erase_contact(contact, "Khách yêu cầu"))
		actions = customer.get_header(self.lead)["actions"]
		self.assertFalse(actions["can_call"])
		self.assertTrue(actions["person_refused"])  # Xóa dữ liệu đặt omni_consent = Từ chối

	def test_header_requires_read_permission(self):
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			customer.get_header(self.lead)
		frappe.set_user(SALE1)
		self.assertEqual(customer.get_header(self.lead)["lead"], self.lead)


class TestContacts(CustomerCase):
	def test_refused_and_erased_contacts_are_labeled_and_have_no_buttons_or_numbers(self):
		ok = make_contact(
			self.org,
			"TD404 Co",
			phone="0900404601",
			email="co@td404.example",
			omni_contact_role="Người quyết định",
			omni_consent="Có",
			omni_has_zalo="Có",
		)
		refused = make_contact(
			self.org, "TD404 Tu", phone="0900404602", email="tu@td404.example", omni_consent="Từ chối"
		)
		erased = make_contact(self.org, "TD404 Xoa", phone="0900404603")
		contacts_mod.erase_contact(erased, "Khách yêu cầu")
		rows = {r["name"]: r for r in customer.get_contacts(self.lead)["rows"] if r["name"] != erased}
		self.assertTrue(rows[ok]["can_call"])
		self.assertTrue(rows[ok]["can_message"])
		self.assertEqual((rows[ok]["role"], rows[ok]["consent"]), ("Người quyết định", "Có"))
		self.assertTrue(rows[refused]["refused"])
		self.assertEqual((rows[refused]["phone"], rows[refused]["email"]), (None, None))
		self.assertFalse(rows[refused]["can_call"])
		self.assertFalse(rows[refused]["can_message"])
		erased_rows = [r for r in customer.get_contacts(self.lead)["rows"] if r["erased"]]
		self.assertEqual(len(erased_rows), 1)
		self.assertEqual(erased_rows[0]["full_name"], "Đã xóa dữ liệu")
		self.assertFalse(erased_rows[0]["can_call"])
		self.assertIsNone(erased_rows[0]["phone"])

	def test_only_contacts_of_this_organization_and_none_without_organization(self):
		other = make_org("TD404 Org Khac", "0900404200")
		mine = make_contact(self.org, "TD404 Mot")
		theirs = make_contact(other, "TD404 Hai")
		names = [r["name"] for r in customer.get_contacts(self.lead)["rows"]]
		self.assertIn(mine, names)
		self.assertNotIn(theirs, names)
		self.assertEqual(customer.get_contacts(self.person)["rows"], [])


class TestAwards(CustomerCase):
	def test_awards_table_shows_only_own_organization_newest_first_with_active_flag(self):
		other = make_org("TD404 Org Awards Khac", "0900404300")
		a1 = make_award(self.org, "TD404 BV Mot", "N1", award_date="2026-01-10")
		a2 = make_award(self.org, "TD404 BV Hai", "N2", award_date="2026-03-20")
		frappe.db.set_value(
			"Omni Bid Award", a1, "end_date", "2026-12-31"
		)  # validate tự tính end_date: đặt lại để kiểm cờ
		frappe.db.set_value("Omni Bid Award", a2, "end_date", "2026-01-01")
		make_award(other, "TD404 BV Ba", "N3", award_date="2026-04-01")
		data = customer.get_awards(self.lead)
		self.assertEqual([r["name"] for r in data["rows"]], [a2, a1])
		self.assertEqual(data["total"], 2)
		flags_ = {r["name"]: r["active"] for r in data["rows"]}
		self.assertEqual(flags_[a1], frappe.utils.getdate("2026-12-31") >= frappe.utils.getdate())
		self.assertFalse(flags_[a2])
		self.assertEqual(customer.get_awards(self.person)["rows"], [])

	def test_awards_are_paged_by_twenty(self):
		for i in range(23):
			make_award(self.org, f"TD404 BV P{i}", f"NP{i}", award_date="2026-02-01")
		first = customer.get_awards(self.lead, 1)
		second = customer.get_awards(self.lead, 2)
		self.assertEqual((len(first["rows"]), len(second["rows"]), first["total"]), (20, 3, 23))

	def test_user_without_lead_of_the_organization_gets_nothing(self):
		make_award(self.org, "TD404 BV Rieng", "NR1")
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			customer.get_awards(self.lead)


class TestFitBlock(CustomerCase):
	def setUp(self):
		super().setUp()
		for i in range(6):  # 6 bệnh viện, mỗi gói 100 mã hàng, 2 tỷ
			make_award(
				self.org,
				f"TD404 Benh vien {i}",
				f"NF{i}",
				award_date=str(frappe.utils.add_days(frappe.utils.getdate(), -30)),
				end_date=str(frappe.utils.add_days(frappe.utils.getdate(), 90)),
				item_count=250 if i == 0 else 100,
				award_value=2_000_000_000,
			)
		fit.apply_fit(self.org)

	def test_fit_block_equals_numbers_from_prospect_fit(self):
		block = customer.get_fit(self.lead)["fit"]
		live = fit.compute_fit(self.org)
		stored = frappe.db.get_value(
			"CRM Organization",
			self.org,
			["omni_fit_score", "omni_fit_tier", "omni_hospital_count", "omni_items_for_scoring"],
			as_dict=True,
		)
		self.assertEqual((block["score"], block["tier"]), (live["fit_score"], live["fit_tier"]))
		self.assertEqual((block["score"], block["tier"]), (stored.omni_fit_score, stored.omni_fit_tier))
		self.assertFalse(block["stale"])
		by_key = {c["key"]: c for c in block["criteria"]}
		self.assertEqual(by_key["hospitals"]["value"], stored.omni_hospital_count)
		self.assertEqual(by_key["items"]["value"], stored.omni_items_for_scoring)
		self.assertTrue(by_key["hospitals"]["met"] and by_key["items"]["met"])
		points = sum(c["points"] for c in block["components"])
		self.assertEqual(fit.excel_round(__import__("decimal").Decimal(str(points))), block["score"])
		self.assertEqual(block["meets_criteria"], bool(live["meets_criteria"]))

	def test_formula_text_uses_real_settings_parameters(self):
		def put(field, value):
			frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field=%s", field)
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Omni Settings', %s, %s)",
				(field, value),
			)

		put("fit_min_hospitals", "8")
		put("fit_w_hospitals", "55")
		put("fit_tier_a", "90")
		block = customer.get_fit(self.lead)["fit"]
		required = {c["key"]: c["required"] for c in block["criteria"]}
		self.assertEqual(required["hospitals"], 8)
		self.assertFalse({c["key"]: c["met"] for c in block["criteria"]}["hospitals"])  # 6 < 8: chưa đạt
		self.assertEqual(block["tier"], "C")
		self.assertIn("55·min(1, BV/", block["formula"])
		self.assertIn("từ 90", block["formula"])
		self.assertTrue(block["stale"])  # điểm lưu (tính với tham số cũ) khác điểm tính lại

	def test_engagement_block_sits_next_to_fit_only_when_scoring_is_on(self):
		with mock.patch("omni_crm.bot.scoring.scoring_enabled", return_value=False):
			self.assertIsNone(customer.get_fit(self.lead)["engagement"])
		with mock.patch("omni_crm.bot.scoring.scoring_enabled", return_value=True):
			engagement = customer.get_fit(self.lead)["engagement"]
		self.assertEqual(engagement["threshold"], 60)

	def test_fit_hidden_when_switch_off_and_for_non_prospect(self):
		with mock.patch("omni_crm.prospect.fit.fit_enabled", return_value=False):
			self.assertIsNone(customer.get_fit(self.lead)["fit"])
		self.assertIsNone(customer.get_fit(self.person)["fit"])

	def test_interested_package_is_shown(self):
		frappe.db.set_value("CRM Organization", self.org, "omni_interested_package", "Gói Điều phối")
		self.assertEqual(customer.get_fit(self.lead)["package"], "Gói Điều phối")


class TestProfileAndEdit(CustomerCase):
	def test_need_group_hidden_until_bot_exists_and_sources_are_labeled(self):
		frappe.db.set_value("CRM Lead", self.lead, {"mobile_no": "0900404700", "job_title": "Trưởng phòng"})
		with mock.patch("omni_crm.flags.bot_analyze_enabled", return_value=False):
			groups = {g["key"]: g for g in customer.get_profile(self.lead)["groups"]}
		self.assertNotIn("need", groups)
		items = {i["field"]: i for i in groups["contact"]["items"]}
		self.assertEqual(items["mobile_no"]["source"], "Kênh")
		frappe.db.set_value(
			"CRM Lead",
			self.lead,
			{
				"omni_need": "Găng tay y tế",
				"omni_manual_fields": '["omni_products"]',
				"omni_products": "Găng",
			},
		)
		with mock.patch("omni_crm.flags.bot_analyze_enabled", return_value=False):
			groups = {g["key"]: g for g in customer.get_profile(self.lead)["groups"]}
		need = {i["field"]: i for i in groups["need"]["items"]}
		self.assertEqual(need["omni_need"]["source"], "Bot")
		self.assertEqual(need["omni_products"]["source"], "Sale")

	def test_inline_edit_marks_manual_and_rejects_unknown_fields(self):
		frappe.set_user(SALE1)
		res = customer.update_field(self.lead, "omni_need", "Cần 100 hộp găng")
		self.assertTrue(res["manual"])
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "omni_need"), "Cần 100 hộp găng")
		self.assertIn("omni_need", frappe.db.get_value("CRM Lead", self.lead, "omni_manual_fields"))
		with self.assertRaises(frappe.ValidationError):
			customer.update_field(self.lead, "lead_owner", SALE2)
		with self.assertRaises(frappe.ValidationError):
			customer.update_field(self.lead, "omni_score", "99")
		with self.assertRaises(frappe.ValidationError):
			customer.update_field(self.lead, "organization", "Công ty khác")  # nhà thầu: đổi ở hồ sơ nhà thầu
		self.assertEqual(
			customer.update_field(self.person, "organization", "Đơn vị mới")["field"], "organization"
		)

	def test_edit_denied_without_write_permission(self):
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			customer.update_field(self.lead, "omni_need", "x")


class TestActivity(CustomerCase):
	def setUp(self):
		super().setUp()
		self.channel = make_channel("TD404 cust").name
		frappe.get_doc(
			{
				"doctype": "Omni Inbound Message",
				"dedupe_key": "td404-act-1",
				"channel": self.channel,
				"channel_type": "Zalo OA",
				"event_type": "message",
				"identity_key": "Zalo OA:TD404ACT",
				"lead": self.lead,
				"received_at": T0,
				"text": "Cần báo giá",
				"status": "Done",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": self.channel,
				"lead": self.lead,
				"text": "Dạ em gửi báo giá",
				"source": "Sale",
				"sent_by": SALE1,
				"status": "Sent",
				"sent_at": T0 + timedelta(minutes=5),
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Omni Call",
				"provider": "Stringee",
				"call_id": "TD404-ACT",
				"direction": "Inbound",
				"status": "Missed",
				"from_number": "+84900000001",
				"to_number": "+84900000002",
				"started_at": T0 + timedelta(minutes=10),
				"lead": self.lead,
			}
		).insert(ignore_permissions=True)
		self.log = care_log.touch_session(self.lead, "Zalo OA", T0 + timedelta(minutes=15), "Sale", SALE1)
		frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": "CRM Lead",
				"docname": self.lead,
				"data": '{"changed": [["lead_owner", "a@x.com", "b@x.com"], ["status", "Tiềm năng", "Đã liên hệ"], ["email", "p", "q"]]}',
			}
		).insert(ignore_permissions=True)

	def test_all_kinds_newest_first(self):
		rows = customer.get_activity(self.lead)["rows"]
		kinds = [r["kind"] for r in rows]
		self.assertEqual(set(kinds), {"message", "call", "care", "system"})
		self.assertEqual([r["at"] for r in rows], sorted((r["at"] for r in rows), reverse=True))
		texts = " | ".join(r["title"] + " " + r["detail"] for r in rows)
		self.assertIn("Cần báo giá", texts)
		self.assertIn("Dạ em gửi báo giá", texts)
		self.assertIn("Gọi nhỡ", texts)
		self.assertIn("Tạo Lead", texts)
		self.assertIn("Đổi người phụ trách", texts)
		self.assertIn("Đổi trạng thái", texts)
		self.assertNotIn("Đổi email", texts)  # chỉ những thay đổi có nghĩa vận hành

	def test_filters_return_only_their_kind(self):
		for kind, expected in (
			("message", {"message"}),
			("call", {"call"}),
			("care", {"care"}),
			("system", {"system"}),
		):
			with self.subTest(kind):
				self.assertEqual(
					{r["kind"] for r in customer.get_activity(self.lead, kind)["rows"]}, expected
				)
		with self.assertRaises(frappe.ValidationError):
			customer.get_activity(self.lead, "linh tinh")

	def test_message_directions_and_sandbox_label(self):
		rows = [r for r in customer.get_activity(self.lead, "message")["rows"]]
		self.assertEqual(sorted(r["direction"] for r in rows), ["in", "out"])

	def test_activity_denied_for_user_without_read_permission(self):
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			customer.get_activity(self.lead)

	def test_care_log_table_newest_first_and_paged_by_twenty(self):
		for i in range(22):
			care_log.touch_session(
				self.lead, "Email", T0 + timedelta(days=i + 1), "Khách", None, None, f"t{i}"
			)
		page1 = customer.get_care_logs(self.lead, 1)
		page2 = customer.get_care_logs(self.lead, 2)
		self.assertEqual((len(page1["rows"]), len(page2["rows"]), page1["total"]), (20, 3, 23))
		times = [r["last_activity_at"] for r in page1["rows"]]
		self.assertEqual(times, sorted(times, reverse=True))
		self.assertEqual(page1["outcomes"][0], "Đã tư vấn")
		self.assertIn("Đã chốt đơn", page1["no_next_outcomes"])


class TestSideAndTasks(CustomerCase):
	def test_tasks_and_duplicates_and_permission_for_toggle(self):
		task = customer.add_task(self.lead, "TD404 gọi lại")
		side = customer.get_side(self.lead)
		self.assertEqual([t["title"] for t in side["tasks"]], ["TD404 gọi lại"])
		self.assertEqual(side["tasks"][0]["assigned_to"], SALE1)
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			customer.set_task_done(task)
		frappe.set_user(SALE1)
		self.assertEqual(customer.set_task_done(task), "Done")
		self.assertEqual(customer.set_task_done(task, 0), "Todo")
		with self.assertRaises(frappe.ValidationError):
			customer.add_task(self.lead, "  ")

	def test_mini_profile_for_inbox(self):
		frappe.db.set_value("CRM Organization", self.org, {"omni_fit_tier": "A"})
		log = care_log.touch_session(self.lead, "Zalo OA", now_datetime() - timedelta(hours=3), "Sale", SALE1)
		profile = customer.get_mini_profile(self.lead)
		self.assertEqual((profile["fit_tier"], profile["unrecorded"]), ("A", True))
		care_log.save_outcome(
			log,
			"Hẹn gọi lại",
			next_action="TD404 gọi",
			next_due=now_datetime() + timedelta(days=1),
			user=SALE1,
		)
		profile = customer.get_mini_profile(self.lead)
		self.assertFalse(profile["unrecorded"])
		self.assertEqual(profile["next_step"]["action"], "TD404 gọi")


class TestOrganizationRename(CustomerCase):
	def test_rename_keeps_lead_contact_and_awards_attached(self):
		contact = make_contact(self.org, "TD404 Nguoi")
		award = make_award(self.org, "TD404 BV Doi ten", "NRN1")
		new = "TD404 Công ty Vật tư Mới"
		frappe.rename_doc("CRM Organization", self.org, new, force=True)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "organization"), new)
		self.assertEqual(frappe.db.get_value("Contact", contact, "company_name"), new)
		self.assertEqual(frappe.db.get_value("Omni Bid Award", award, "organization"), new)
		self.assertEqual(customer.get_header(self.lead)["organization"]["name"], new)  # vẫn là nhà thầu
		self.assertEqual([r["name"] for r in customer.get_awards(self.lead)["rows"]], [award])
		self.assertEqual([r["name"] for r in customer.get_contacts(self.lead)["rows"]], [contact])
		self.assertEqual(
			frappe.db.get_value("CRM Lead", self.person, "organization"), "TD404 khong co trong danh sach"
		)

	def test_rename_with_merge_is_blocked_and_points_to_merge_organizations(self):
		other = make_org("TD404 Org Dich", "0900404999")
		with self.assertRaises(frappe.ValidationError) as ctx:
			frappe.rename_doc("CRM Organization", self.org, other, merge=True, force=True)
		self.assertIn("Gộp nhà thầu", str(ctx.exception))
		self.assertTrue(frappe.db.exists("CRM Organization", self.org))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "organization"), self.org)
