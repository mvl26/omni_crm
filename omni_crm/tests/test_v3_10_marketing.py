# ruff: noqa: RUF001
"""V3-10 (Marketing Hub, Prompt 1): mã UTM, link theo dõi, quy tắc duyệt/hủy bài, thiết lập mặc định, kiểm quyền kênh.

Không gọi mạng: Facebook/Zalo dùng client giả. Mỗi test rollback (không commit dữ liệu thử).
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import api, approval, channels, roles, rules
from omni_crm.marketing import settings as mk_settings

PREFIX = "TM310"
ITEM = "Omni Content Item"
TARGET = "Omni Content Target"
SETTINGS = "Omni Marketing Settings"
PAGE_TOKEN = "tm310-page-token-khong-that-" + "q" * 20 + "WXYZ"


def set_setting(field: str, value) -> None:
	"""tabSingles không có khóa duy nhất (doctype, field): xóa rồi ghi, không dùng ON DUPLICATE KEY."""
	frappe.db.sql("delete from tabSingles where doctype=%s and field=%s", (SETTINGS, field))
	frappe.db.sql(
		"insert into tabSingles (doctype, field, value) values (%s, %s, %s)", (SETTINGS, field, value)
	)
	frappe.db.value_cache.pop(SETTINGS, None)


def make_user(email: str, *role_names: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.set("roles", [])
	for r in role_names:
		user.append("roles", {"role": r})
	user.save(ignore_permissions=True)
	frappe.clear_cache(user=email)
	return email


class MarketingCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		roles.ensure_roles()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.sql("delete from tabSingles where doctype=%s", SETTINGS)
		frappe.db.value_cache.pop(SETTINGS, None)
		self.manager = make_user(f"{PREFIX.lower()}-mgr@example.com", roles.MANAGER)
		self.writer = make_user(f"{PREFIX.lower()}-user@example.com", roles.USER)
		self.zalo = self._channel("zalo", "Zalo OA", zalo_app_id="app-1", zalo_oa_id="999")
		self.meta = self._channel(
			"meta",
			"Meta Lead Ads",
			meta_page_id=f"{PREFIX}PAGE",
			meta_app_id="3101310131013101",
			meta_app_secret="tm310-secret-khong-that",
			meta_verify_token="tm310-verify",
			meta_page_access_token=PAGE_TOKEN,
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.value_cache.pop(SETTINGS, None)

	def _channel(self, name: str, ctype: str, **kw) -> str:
		return (
			frappe.get_doc(
				{"doctype": "Omni Channel", "channel_name": f"{PREFIX}-{name}", "channel_type": ctype, **kw}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def campaign(self, title: str = "Giới thiệu SPD Hà Nội", **kw) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Campaign",
					"campaign_name": f"{PREFIX} {title}",
					"platform": "Đa kênh",
					"status": "Nháp",
					"start_date": "2026-11-20",
					**kw,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def item(self, campaign: str, score: int = 90, **kw) -> str:
		doc = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": campaign,
				"title": "Bài thử",
				"caption": "Miyano giới thiệu SPD",
				"media": "/files/tm-cover.png",  # bài viết Zalo cần ảnh bìa trước khi duyệt
				"quality_score": score,
				**kw,
			}
		)
		doc.flags.score_via_ai = True  # điểm chỉ do bộ chấm AI ghi (Prompt 4)
		return doc.insert(ignore_permissions=True).name

	def target(self, item: str, platform: str = rules.ZALO, minutes: int = 120, **kw) -> str:
		channel = {rules.ZALO: self.zalo, rules.FACEBOOK: self.meta}.get(platform)
		fmt = {rules.ZALO: "zalo_article", rules.FACEBOOK: "fb_link", rules.TIKTOK: "tiktok_video"}[platform]
		return (
			frappe.get_doc(
				{
					"doctype": TARGET,
					"content_item": item,
					"platform": platform,
					"channel_account": channel,
					"post_format": fmt,
					"scheduled_at": add_to_date(now_datetime(), minutes=minutes),
					**kw,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)


class TestRules(FrappeTestCase):
	def test_slug_and_campaign_utm(self):
		self.assertEqual(rules.slugify("Giới thiệu dịch vụ SPD – Đà Nẵng!"), "gioi-thieu-dich-vu-spd-da-nang")
		self.assertEqual(
			rules.campaign_utm("Tri ân khách hàng", datetime(2026, 11, 3)), "tri-an-khach-hang-1126"
		)
		self.assertEqual(rules.campaign_utm("!!!", datetime(2026, 1, 3)), "chien-dich-0126")
		self.assertLessEqual(len(rules.campaign_utm("a" * 300, datetime(2026, 1, 3))), rules.SLUG_MAX + 5)

	def test_next_content_code(self):
		self.assertEqual(rules.next_content_code([]), "bai-01")
		self.assertEqual(rules.next_content_code(["bai-01", None, "bai-07", "khac"]), "bai-08")
		self.assertEqual(rules.next_content_code([f"bai-{i:02d}" for i in range(1, 100)]), "bai-100")

	def test_landing_path_keeps_query_through_nginx_redirect(self):
		# nginx: `location = /spd { return 301 /spd/; }` bỏ query string -> luôn dùng /spd/
		self.assertEqual(rules.landing_path("/spd"), "/spd/")
		self.assertEqual(rules.landing_path("tong-thau"), "/tong-thau/")
		self.assertEqual(rules.landing_path("/spd/"), "/spd/")
		self.assertEqual(rules.landing_path("/a/b.html"), "/a/b.html")
		self.assertEqual(rules.landing_path("/spd?x=1#y"), "/spd/")
		with self.assertRaises(ValueError):
			rules.landing_path("https://evil.example/spd")

	def test_tracking_url(self):
		base = "https://yhct.miyano.com.vn/"
		self.assertEqual(
			rules.tracking_url(base, "/spd", rules.FACEBOOK, "gioi-thieu-spd-1126", "bai-03"),
			"https://yhct.miyano.com.vn/spd/?utm_source=facebook&utm_medium=social"
			"&utm_campaign=gioi-thieu-spd-1126&utm_content=bai-03",
		)
		self.assertIn(
			"utm_source=zalo&utm_medium=oa", rules.tracking_url(base, "/spd/", rules.ZALO, "c", "bai-01")
		)
		self.assertIn(
			"utm_source=tiktok&utm_medium=social", rules.tracking_url(base, None, rules.TIKTOK, "c", "bai-01")
		)

	def test_aggregate_status(self):
		ok, fail, cancel = rules.T_SUCCESS, rules.T_FAILED, rules.T_CANCELLED
		self.assertEqual(rules.aggregate_status([ok, ok]), rules.I_PUBLISHED)
		self.assertEqual(rules.aggregate_status([ok, fail]), rules.I_PARTIAL)
		self.assertEqual(rules.aggregate_status([fail, fail]), rules.I_FAILED)
		self.assertEqual(rules.aggregate_status([ok, cancel]), rules.I_PARTIAL)
		self.assertEqual(rules.aggregate_status([cancel, cancel]), rules.I_CANCELLED)
		self.assertEqual(rules.aggregate_status([rules.T_SCHEDULED, rules.T_SCHEDULED]), rules.I_SCHEDULED)
		self.assertEqual(rules.aggregate_status([ok, rules.T_SCHEDULED]), rules.I_PROCESSING)
		self.assertIsNone(rules.aggregate_status([rules.T_REVIEW]))
		self.assertIsNone(rules.aggregate_status([]))


class TestSettings(MarketingCase):
	def test_unsaved_single_means_dry_run_on(self):
		frappe.db.sql("delete from tabSingles where doctype=%s", SETTINGS)
		self.assertTrue(mk_settings.dry_run())
		self.assertTrue(mk_settings.require_approval())
		self.assertEqual(mk_settings.quality_threshold(), 80)
		self.assertEqual(mk_settings.late_window_minutes(), 30)
		self.assertTrue(mk_settings.hub_enabled())

	def test_dry_run_off_but_sandbox_on_is_still_dry(self):
		set_setting("dry_run", "0")
		with mock.patch("omni_crm.flags.sandbox", return_value=True):
			self.assertTrue(mk_settings.dry_run())
		with mock.patch("omni_crm.flags.sandbox", return_value=False):
			self.assertFalse(mk_settings.dry_run())

	def test_ensure_initialized_writes_defaults_without_overwriting(self):
		set_setting("quality_threshold", "65")
		mk_settings.ensure_initialized()
		frappe.db.value_cache.pop(SETTINGS, None)
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "dry_run"), 1)
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "quality_threshold"), 65)
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "default_landing_page"), "/spd/")

	def test_hub_switch(self):
		set_setting("hub_disabled", "1")
		self.assertFalse(api.hub_state()["enabled"])
		with self.assertRaises(frappe.PermissionError):
			api.get_social_channels()


class TestUtmGeneration(MarketingCase):
	def test_campaign_utm_and_content_codes(self):
		c = self.campaign()
		first, second = self.item(c), self.item(c)
		self.assertEqual(
			frappe.db.get_value("Omni Campaign", c, "utm_campaign"), "tm310-gioi-thieu-spd-ha-noi-1126"
		)
		self.assertEqual(frappe.db.get_value(ITEM, first, "utm_content"), "bai-01")
		self.assertEqual(frappe.db.get_value(ITEM, second, "utm_content"), "bai-02")
		other = self.item(self.campaign("Khác"))
		self.assertEqual(frappe.db.get_value(ITEM, other, "utm_content"), "bai-01")

	def test_duplicate_slug_gets_suffix_and_manual_utm_kept(self):
		a = self.campaign("Trùng tên", start_date="2026-11-01")
		b = self.campaign("Trùng tên!", start_date="2026-11-02")
		self.item(a)
		self.item(b)
		self.assertEqual(frappe.db.get_value("Omni Campaign", a, "utm_campaign"), "tm310-trung-ten-1126")
		self.assertEqual(frappe.db.get_value("Omni Campaign", b, "utm_campaign"), "tm310-trung-ten-1126-2")
		manual = self.campaign("Tự đặt", utm_campaign="SPD_Ads")
		self.item(manual)
		self.assertEqual(frappe.db.get_value("Omni Campaign", manual, "utm_campaign"), "SPD_Ads")

	def test_utm_editable_until_approved(self):
		c = self.campaign()
		it = self.item(c)
		self.target(it)
		doc = frappe.get_doc("Omni Campaign", c)
		doc.utm_campaign = "doi-truoc-khi-chay"
		doc.save()  # chưa duyệt: sửa được
		approval.submit(it)
		approval.approve(it)
		doc = frappe.get_doc("Omni Campaign", c)
		doc.utm_campaign = "doi-sau-khi-duyet"
		with self.assertRaises(frappe.ValidationError):
			doc.save()


class TestApproval(MarketingCase):
	def _ready(self, score: int = 90, platforms=(rules.ZALO, rules.FACEBOOK)) -> str:
		it = self.item(self.campaign(), score=score)
		for p in platforms:
			self.target(it, p)
		approval.submit(it)
		return it

	def test_manager_approves_and_targets_get_key_and_tracking_url(self):
		it = self._ready()
		frappe.set_user(self.manager)
		self.assertEqual(api.approve_item(it), rules.I_SCHEDULED)
		frappe.set_user("Administrator")
		doc = frappe.get_doc(ITEM, it)
		self.assertEqual(doc.approved_by, self.manager)
		self.assertTrue(doc.approved_at)
		rows = frappe.get_all(
			TARGET, {"content_item": it}, ["name", "platform", "status", "idempotency_key", "tracking_url"]
		)
		self.assertEqual({r.status for r in rows}, {rules.T_SCHEDULED})
		for r in rows:
			self.assertEqual(r.idempotency_key, f"omni-mk-{r.name}")
			src = rules.UTM_SOURCE[r.platform]
			self.assertEqual(
				r.tracking_url,
				f"https://yhct.miyano.com.vn/spd/?utm_source={src}&utm_medium={rules.UTM_MEDIUM[r.platform]}"
				"&utm_campaign=tm310-gioi-thieu-spd-ha-noi-1126&utm_content=bai-01",
			)

	def test_non_manager_cannot_approve(self):
		it = self._ready()
		frappe.set_user(self.writer)
		with self.assertRaises(frappe.PermissionError):
			api.approve_item(it)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(ITEM, it, "status"), rules.I_REVIEW)

	def test_score_below_threshold_cannot_be_scheduled(self):
		it = self._ready(score=79)
		frappe.set_user(self.manager)
		with self.assertRaisesRegex(frappe.ValidationError, "dưới ngưỡng 80"):
			api.approve_item(it)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(ITEM, it, "status"), rules.I_REVIEW)
		self.assertEqual(
			{t.status for t in frappe.get_all(TARGET, {"content_item": it}, ["status"])}, {rules.T_REVIEW}
		)
		set_setting("quality_threshold", "70")
		frappe.set_user(self.manager)
		self.assertEqual(api.approve_item(it), rules.I_SCHEDULED)

	def test_needs_at_least_one_target(self):
		it = self.item(self.campaign())
		with self.assertRaisesRegex(frappe.ValidationError, "chưa chọn nền tảng"):
			approval.submit(it)

	def test_past_schedule_outside_late_window_blocked(self):
		it = self.item(self.campaign())
		self.target(it, minutes=-45)
		approval.submit(it)
		frappe.set_user(self.manager)
		with self.assertRaisesRegex(frappe.ValidationError, "cửa sổ đăng muộn"):
			api.approve_item(it)

	def test_require_approval_off_lets_user_schedule_from_draft(self):
		set_setting("require_approval", "0")
		it = self.item(self.campaign())
		self.target(it)
		frappe.set_user(self.writer)
		self.assertEqual(api.approve_item(it), rules.I_SCHEDULED)

	def test_status_cannot_be_set_directly(self):
		it = self._ready()
		frappe.set_user(self.writer)
		doc = frappe.get_doc(ITEM, it)
		doc.status = rules.I_SCHEDULED
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		t = frappe.get_doc(TARGET, frappe.get_all(TARGET, {"content_item": it}, pluck="name")[0])
		t.status = rules.T_SUCCESS
		with self.assertRaises(frappe.ValidationError):
			t.save()

	def test_cancel_marks_unpublished_targets_cancelled(self):
		it = self._ready()
		frappe.set_user(self.manager)
		api.approve_item(it)
		names = frappe.get_all(TARGET, {"content_item": it}, pluck="name", order_by="platform")
		frappe.db.set_value(
			TARGET, names[0], "status", rules.T_SUCCESS
		)  # giả bộ đăng (Prompt 2) đã đăng 1 nền tảng
		self.assertEqual(api.cancel_item(it), rules.I_PARTIAL)
		self.assertEqual(frappe.db.get_value(TARGET, names[1], "status"), rules.T_CANCELLED)
		self.assertEqual(frappe.db.get_value(TARGET, names[0], "status"), rules.T_SUCCESS)

	def test_cancel_draft(self):
		it = self.item(self.campaign())
		self.target(it)
		self.assertEqual(approval.cancel(it), rules.I_CANCELLED)

	def test_target_channel_and_format_must_match_platform(self):
		it = self.item(self.campaign())
		with self.assertRaises(frappe.ValidationError):
			self.target(it, rules.ZALO, channel_account=self.meta)
		with self.assertRaises(frappe.ValidationError):
			self.target(it, rules.FACEBOOK, post_format="zalo_article")
		self.target(
			it, rules.TIKTOK
		)  # TikTok chưa có kênh: vẫn tạo được (Prompt 2 báo chưa được duyệt quyền)

	def test_refresh_item_status_from_targets(self):
		it = self._ready()
		frappe.set_user(self.manager)
		api.approve_item(it)
		frappe.set_user("Administrator")
		for n in frappe.get_all(TARGET, {"content_item": it}, pluck="name"):
			frappe.db.set_value(TARGET, n, "status", rules.T_SUCCESS)
		self.assertEqual(approval.refresh_item_status(it), rules.I_PUBLISHED)
		self.assertEqual(frappe.db.get_value(ITEM, it, "status"), rules.I_PUBLISHED)


class FakeMeta:
	def __init__(self, scopes, valid=True):
		self.scopes, self.valid = scopes, valid

	def debug_token(self, app_id, secret):
		return {"is_valid": self.valid, "expires_at": 0, "scopes": self.scopes}

	def _get(self, path, params=None):
		return {"id": path, "name": "Miyano Việt Nam"}


class FakeZalo:
	def get_oa_info(self):
		return {"oa_id": "999", "name": "Miyano OA"}


class TestChannelCheck(MarketingCase):
	def test_accounts_reuse_omni_channel_one_row_per_page_plus_tiktok(self):
		self._channel(
			"meta2",
			"Meta Lead Ads",
			meta_page_id=f"{PREFIX}PAGE",
			meta_verify_token="v",
			meta_page_access_token=PAGE_TOKEN,
		)
		rows = [
			r for r in channels.accounts() if r["name"].startswith(PREFIX) or r["platform"] == rules.TIKTOK
		]
		self.assertEqual(sorted(r["platform"] for r in rows), [rules.FACEBOOK, rules.TIKTOK, rules.ZALO])
		fb = next(r for r in rows if r["platform"] == rules.FACEBOOK)
		self.assertEqual(fb["token_tail"], "…WXYZ")
		self.assertNotIn(PAGE_TOKEN, frappe.as_json(rows))
		self.assertEqual(
			next(r for r in rows if r["platform"] == rules.TIKTOK)["status"], channels.NOT_CONNECTED
		)

	def test_facebook_missing_permissions(self):
		res = channels.check(self.meta, meta_client=FakeMeta(["pages_read_engagement", "leads_retrieval"]))
		self.assertEqual(res["status"], channels.RECONNECT)
		self.assertIn("pages_manage_posts", res["missing"])
		self.assertEqual(res["page_id"], f"{PREFIX}PAGE")
		self.assertEqual(res["account_name"], "Miyano Việt Nam")
		self.assertEqual(res["token_tail"], "…WXYZ")
		saved = frappe.parse_json(frappe.db.get_single_value(SETTINGS, "channel_checks"))
		self.assertEqual(saved[self.meta]["status"], channels.RECONNECT)
		self.assertNotIn(PAGE_TOKEN, frappe.as_json(saved))

	def test_facebook_all_permissions(self):
		res = channels.check(self.meta, meta_client=FakeMeta(list(channels.FB_REQUIRED)))
		self.assertEqual(res["status"], channels.CONNECTED)
		self.assertEqual(res["missing"], [])

	def test_zalo_check_reads_oa_info_and_expiry(self):
		doc = frappe.get_doc("Omni Channel", self.zalo)
		doc.zalo_access_token = "tm310-zalo-access-khong-that-ABCD"
		doc.zalo_token_expires_at = add_to_date(now_datetime(), hours=5)
		doc.save(ignore_permissions=True)
		with mock.patch("omni_crm.flags.sandbox", return_value=False):
			res = channels.check(self.zalo, zalo_client=FakeZalo())
		self.assertEqual(res["status"], channels.CONNECTED)
		self.assertEqual(res["account_name"], "Miyano OA")
		self.assertEqual(res["token_tail"], "…ABCD")

	def test_zalo_without_token_not_connected_and_no_network(self):
		with mock.patch("requests.request") as req, mock.patch("omni_crm.flags.sandbox", return_value=False):
			res = channels.check(self.zalo)
		req.assert_not_called()
		self.assertEqual(res["status"], channels.NOT_CONNECTED)

	def test_only_admin_can_check(self):
		frappe.set_user(self.manager)
		with self.assertRaises(frappe.PermissionError):
			api.check_channel(self.meta)
