"""API Marketing Hub trong giao diện CRM (ADR 0031): quyền theo vai trò, chiến dịch, bài, lịch, báo cáo, thiết lập,
mục Marketing trang Lead. Dữ liệu tiền tố td29/TD29, dọn như test_marketing_hub."""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import marketing_ui as ui
from omni_crm.tests.test_marketing_hub import (
	CAMPAIGN,
	MANAGER,
	SALES,
	_delete_all,
	make_campaign,
	make_channel,
	make_lead,
	make_post,
	make_users,
	restore_settings,
	snapshot_settings,
)

POST = "Omni Content Post"
CAMPAIGN_DT = "Omni Campaign"


def td29(rows):
	"""Chỉ dòng của test (site thử có thể có dữ liệu thật của người dùng)."""
	return [r.name for r in rows if r.name.lower().startswith("td29")]


class UICase(FrappeTestCase):
	def setUp(self):
		_delete_all()
		self._snap = snapshot_settings(ui.SETTINGS_FIELDS)
		self.addCleanup(self._restore)
		make_users()
		make_campaign()
		frappe.db.commit()

	def _restore(self):
		frappe.set_user("Administrator")
		_delete_all()
		restore_settings(self._snap)
		frappe.db.commit()


class TestUIState(UICase):
	def test_roles(self):
		frappe.set_user(SALES)
		s = ui.ui_state()
		self.assertTrue(s["enabled"])
		self.assertFalse(s["can_create_post"] or s["can_edit_campaign"] or s["is_admin"])
		frappe.set_user(MANAGER)
		s = ui.ui_state()
		self.assertTrue(s["can_create_post"] and s["can_edit_campaign"] and s["can_create_campaign"])
		self.assertFalse(s["is_admin"])
		frappe.set_user("Administrator")
		self.assertTrue(ui.ui_state()["is_admin"])


class TestCampaignsAndPosts(UICase):
	def test_campaign_list_and_detail_counts(self):
		make_post("td29-b01", publish_mode="Đăng tay")
		make_post("td29-b02")
		frappe.db.set_value(POST, "td29-b02", "status", "Lỗi")
		lead = make_lead("TD29 Khách UI")
		frappe.db.set_value("CRM Lead", lead, "omni_campaign", CAMPAIGN)
		row = next(r for r in ui.campaigns() if r.name == CAMPAIGN)
		self.assertEqual((row.posts_total, row.posts_attention, row.leads), (2, 1, 1))
		self.assertEqual(td29(ui.campaigns(status="Kết thúc")), [])
		d = ui.campaign(CAMPAIGN)
		self.assertEqual([p.name for p in d["posts"]], ["td29-b01", "td29-b02"])
		self.assertEqual(d["leads"], 1)

	def test_save_campaign_and_post_respect_permissions(self):
		frappe.set_user(SALES)
		with self.assertRaises(frappe.PermissionError):
			ui.save_campaign({"campaign_name": "TD29 Mới", "platform": "Đa kênh"})
		with self.assertRaises(frappe.PermissionError):
			ui.save_post({"post_code": "td29-b09", "campaign": CAMPAIGN, "title": "x"})
		frappe.set_user(MANAGER)
		name = ui.save_campaign(
			{"campaign_name": "TD29 Mới", "platform": "Đa kênh", "status": "Nháp", "utm_campaign": "td29-moi"}
		)
		self.assertEqual(ui.save_campaign({"objective": "Lấy đăng ký"}, name=name), name)
		self.assertEqual(frappe.db.get_value(CAMPAIGN_DT, name, "objective"), "Lấy đăng ký")
		code = ui.save_post(
			{
				"post_code": "TD29-B09",
				"campaign": CAMPAIGN,
				"title": "Bài từ CRM",
				"platform": "Facebook",
				"publish_mode": "Đăng tay",
				"publish_at": str(now_datetime() + timedelta(days=1)),
				"content": "Nội dung {link}",
				"status": "Đã đăng",  # bị bỏ qua: trạng thái chỉ đổi qua nút
			}
		)
		self.assertEqual(code, "td29-b09")
		p = ui.post(code)
		self.assertEqual(p["status"], "Nháp")
		self.assertIn("utm_content=td29-b09", p["utm_link"])
		self.assertTrue(p["can_edit"])
		ui.save_post({"title": "Đổi tiêu đề"}, name=code)
		self.assertEqual(frappe.db.get_value(POST, code, "title"), "Đổi tiêu đề")
		frappe.set_user("Administrator")
		frappe.delete_doc(CAMPAIGN_DT, name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_calendar_range_and_campaign_filter(self):
		make_post("td29-b01", publish_at=now_datetime() + timedelta(days=2))
		make_post("td29-b02", publish_at=now_datetime() + timedelta(days=60))
		start = str(now_datetime() - timedelta(days=1))
		end = str(now_datetime() + timedelta(days=30))
		self.assertEqual(td29(ui.calendar(start, end)), ["td29-b01"])
		self.assertEqual(ui.calendar(start, end, campaign="khong-co"), [])


class TestReport(UICase):
	def test_totals_pillar_funnel_errors(self):
		make_post("td29-b01", publish_mode="Đăng tay", pillar="Giới thiệu dịch vụ", funnel_stage="Nhận biết")
		make_post("td29-b02", pillar="Giới thiệu dịch vụ", funnel_stage="Chuyển đổi")
		frappe.db.set_value(
			POST, "td29-b01", {"status": "Đã đăng", "reach": 100, "reactions": 7, "shares": 3}
		)
		frappe.db.set_value(POST, "td29-b02", {"status": "Lỗi", "last_error": "mã 200"})
		lead = make_lead("TD29 Lead báo cáo")
		frappe.db.set_value("CRM Lead", lead, {"omni_campaign": CAMPAIGN, "omni_content_post": "td29-b01"})
		r = ui.report(campaign=CAMPAIGN)
		t = r["totals"]
		self.assertEqual((t["posts"], t["published"], t["attention"], t["success_rate"]), (2, 1, 1, 50))
		self.assertEqual((t["reach"], t["engagement"], t["leads"]), (100, 10, 1))
		self.assertEqual(r["by_pillar"][0]["pillar"], "Giới thiệu dịch vụ")
		self.assertEqual(sorted(f["stage"] for f in r["by_funnel"]), ["Chuyển đổi", "Nhận biết"])
		self.assertEqual([e["name"] for e in r["errors"]], ["td29-b02"])
		self.assertEqual(r["unknown"]["leads"], 0)


class TestSettings(UICase):
	def test_admin_only_and_secret_never_returned(self):
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.PermissionError):
			ui.settings()
		with self.assertRaises(frappe.PermissionError):
			ui.save_settings({"content_publish_enabled": 1})
		frappe.set_user("Administrator")
		out = ui.save_settings(
			{
				"content_late_window_minutes": "45",
				"s3_media_enabled": "0",
				"s3_bucket": "td29-bucket",
				"s3_secret_key": "td29-secret-xyz",
				"sandbox_mode": 0,  # không trong danh sách: bị bỏ qua
			}
		)
		self.assertEqual(out["values"]["content_late_window_minutes"], 45)
		self.assertEqual(out["values"]["s3_bucket"], "td29-bucket")
		self.assertTrue(out["secret_set"])
		self.assertNotIn("td29-secret-xyz", frappe.as_json(out))
		self.assertNotIn("s3_secret_key", out["values"])
		self.assertEqual(frappe.get_single("Omni Settings").get_password("s3_secret_key"), "td29-secret-xyz")


class TestLeadSection(UICase):
	def test_lead_marketing(self):
		make_post("td29-b01", publish_mode="Đăng tay", funnel_stage="Cân nhắc")
		lead = make_lead("TD29 Lead mục")
		empty = ui.lead_marketing(lead)
		self.assertFalse(empty["visible"])
		frappe.db.set_value(
			"CRM Lead",
			lead,
			{
				"omni_campaign": CAMPAIGN,
				"omni_content_post": "td29-b01",
				"omni_utm_source": "facebook",
				"omni_utm_campaign": "spd-pk-td29",
			},
		)
		d = ui.lead_marketing(lead)
		self.assertTrue(d["visible"])
		self.assertEqual(
			(d["campaign"], d["post"]["name"], d["post"]["funnel_stage"]), (CAMPAIGN, "td29-b01", "Cân nhắc")
		)
		self.assertEqual(d["utm"]["source"], "facebook")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			ui.lead_marketing(lead)


class TestEnablePublishPatch(UICase):
	def test_turns_on_and_picks_the_only_page_channel(self):
		from omni_crm.patches.v3 import enable_content_publish as patch

		make_channel()  # kênh Meta Messenger đang bật, có Page ID
		frappe.db.set_single_value(
			"Omni Settings", {"content_publish_enabled": 0, "content_publish_channel": ""}
		)
		frappe.db.commit()
		page_channels = frappe.get_all(
			"Omni Channel",
			filters={"channel_type": "Meta Messenger", "enabled": 1, "meta_page_id": ["is", "set"]},
			pluck="name",
		)
		patch.execute()
		s = frappe.get_single("Omni Settings")
		self.assertEqual(s.content_publish_enabled, 1)
		self.assertEqual(s.content_publish_channel or "", page_channels[0] if len(page_channels) == 1 else "")
		# đã có kênh thì không đổi
		frappe.db.set_single_value("Omni Settings", "content_publish_channel", "kenh-khac")
		patch.execute()
		self.assertEqual(frappe.db.get_single_value("Omni Settings", "content_publish_channel"), "kenh-khac")
