# ruff: noqa: RUF001
"""V3-10 Prompt 3 (API lịch / chiến dịch) và Prompt 6 (đồng bộ số liệu, báo cáo, xuất Excel) với dữ liệu mẫu."""

from __future__ import annotations

import io
from datetime import timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import api, insights, report, roles, rules, scoring, views
from omni_crm.marketing.publisher import facebook
from omni_crm.marketing.publisher.errors import TERMINAL

PREFIX = "TM360"
ITEM = "Omni Content Item"
TARGET = "Omni Content Target"
SETTINGS = "Omni Marketing Settings"


class ReportCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		roles.ensure_roles()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.sql("delete from tabSingles where doctype=%s", SETTINGS)
		frappe.db.value_cache.pop(SETTINGS, None)
		self.meta = self._channel("meta", "Meta Lead Ads", meta_page_id="360360", meta_app_id="3603603603")
		self.zalo = self._channel("zalo", "Zalo OA", zalo_app_id="app-360", zalo_oa_id="360")
		self.camp = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PREFIX} SPD {frappe.generate_hash(length=5)}",
				"platform": "Đa kênh",
				"status": "Đang chạy",
				"start_date": "2026-11-01",
			}
		).insert(ignore_permissions=True)
		self.when = now_datetime().replace(day=10, hour=9, minute=0, second=0, microsecond=0)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		# đồng bộ số liệu commit theo từng bài: dọn dữ liệu mẫu đã lỡ commit
		camps = frappe.get_all("Omni Campaign", filters={"name": ["like", f"{PREFIX}%"]}, pluck="name")
		if camps:
			items = frappe.get_all(ITEM, filters={"campaign": ["in", camps]}, pluck="name")
			frappe.db.delete(TARGET, {"content_item": ["in", items or [""]]})
			frappe.db.delete(ITEM, {"name": ["in", items or [""]]})
			frappe.db.delete("CRM Lead", {"first_name": ["like", f"{PREFIX}%"]})
			frappe.db.delete("Omni Campaign", {"name": ["in", camps]})
		frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
		frappe.db.delete("Omni Content Pillar", {"name": ["like", f"{PREFIX}%"]})
		frappe.db.sql("delete from tabSingles where doctype=%s", SETTINGS)
		frappe.db.commit()

	def _channel(self, name, ctype, **kw):
		return (
			frappe.get_doc(
				{"doctype": "Omni Channel", "channel_name": f"{PREFIX}-{name}", "channel_type": ctype, **kw}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def item(self, title, pillar=None, stage="Nhận biết", status=rules.I_PUBLISHED, score=90) -> str:
		if pillar and not frappe.db.exists("Omni Content Pillar", pillar):
			frappe.get_doc({"doctype": "Omni Content Pillar", "pillar_name": pillar}).insert()
		doc = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": self.camp.name,
				"title": title,
				"caption": "Miyano",
				"pillar": pillar,
				"funnel_stage": stage,
				"quality_score": score,
			}
		)
		doc.flags.score_via_ai = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value(ITEM, doc.name, "status", status)
		return doc.name

	def target(
		self, item, platform=rules.FACEBOOK, status=rules.T_SUCCESS, metrics=None, days=0, **kw
	) -> str:
		# dữ liệu mẫu: thêm nền tảng khi bài còn Nháp rồi trả bài về trạng thái mẫu
		item_status = frappe.db.get_value(ITEM, item, "status")
		frappe.db.set_value(ITEM, item, "status", rules.I_DRAFT)
		doc = frappe.get_doc(
			{
				"doctype": TARGET,
				"content_item": item,
				"platform": platform,
				"channel_account": self.meta if platform == rules.FACEBOOK else self.zalo,
				"post_format": "fb_link" if platform == rules.FACEBOOK else "zalo_article",
				"scheduled_at": self.when + timedelta(days=days),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(ITEM, item, "status", item_status)
		values = {"status": status, **kw}
		if metrics is not None:
			values.update(metrics)
			values["insights_fields"] = ",".join(metrics)
		frappe.db.set_value(TARGET, doc.name, values)
		return doc.name

	def lead(self, item=None, converted=0) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": f"{PREFIX} khách {frappe.generate_hash(length=4)}",
				"omni_campaign": self.camp.name,
				"omni_content_item": item,
			}
		).insert(ignore_permissions=True)
		if converted:
			frappe.db.set_value("CRM Lead", doc.name, "converted", 1)
		return doc.name


class TestReport(ReportCase):
	def sample(self):
		a = self.item("Bài A", pillar=f"{PREFIX} Giáo dục", stage="Nhận biết")
		self.target(
			a,
			metrics={
				"reach": 1000,
				"impressions": 1500,
				"likes": 30,
				"comments": 5,
				"shares": 2,
				"clicks": 40,
			},
		)
		self.target(a, platform=rules.ZALO, metrics={})  # Zalo chưa có số liệu
		b = self.item("Bài B", pillar=f"{PREFIX} Giáo dục", stage="Chuyển đổi", status=rules.I_FAILED)
		self.target(b, status=rules.T_FAILED, error_type=TERMINAL, last_error="Token Trang hết hạn", days=1)
		c = self.item("Bài C", stage="Cân nhắc", status=rules.I_REVIEW)
		self.target(c, status=rules.T_REVIEW, days=2)
		self.lead(a)
		self.lead(a, converted=1)
		self.lead(None)
		return a, b, c

	def test_numbers_match_sample_data(self):
		a, b, c = self.sample()
		data = report.build({"campaign": self.camp.name})
		cards = data["cards"]
		self.assertEqual(cards["total_posts"], 3)
		self.assertEqual(cards["published_posts"], 1)
		self.assertEqual(cards["waiting_review"], 1)
		self.assertEqual(cards["success_rate"], 66.7)  # 2 nền tảng thành công / 3 kết thúc
		self.assertEqual(cards["reach"], 1000)
		self.assertEqual(cards["engagement"], 37)
		self.assertEqual((cards["leads"], cards["deals"]), (3, 1))
		posts = {p["name"]: p for p in data["posts"]}
		self.assertEqual(posts[a]["platforms"], "Facebook, Zalo OA")
		self.assertEqual((posts[a]["clicks"], posts[a]["leads"]), (40, 2))
		self.assertIsNone(posts[b]["reach"])  # chưa có số liệu: None (giao diện "—"), không phải 0
		self.assertIsNone(posts[c]["engagement"])
		pillar = next(p for p in data["pillars"] if p["pillar"] == f"{PREFIX} Giáo dục")
		self.assertEqual((pillar["posts"], pillar["engagement"], pillar["leads"]), (2, 37, 2))
		funnel = {f["stage"]: f for f in data["funnel"]}
		self.assertEqual((funnel["Chuyển đổi"]["posts"], funnel["Chuyển đổi"]["percent"]), (1, 33.3))
		[err] = data["errors"]
		self.assertEqual((err.error_type, err.count), (TERMINAL, 1))
		self.assertTrue(err.permission)

	def test_lead_count_matches_filtered_lead_list(self):
		self.sample()
		data = report.build({"campaign": self.camp.name})
		listed = frappe.get_all("CRM Lead", filters={"omni_campaign": self.camp.name}, pluck="name")
		self.assertEqual(data["cards"]["leads"], len(listed))

	def test_platform_and_date_filters(self):
		a, b, c = self.sample()
		zalo = report.build({"campaign": self.camp.name, "platform": rules.ZALO})
		self.assertEqual([p["name"] for p in zalo["posts"]], [a])
		self.assertIsNone(zalo["cards"]["reach"])
		day = (self.when + timedelta(days=2)).date()
		only_c = report.build({"campaign": self.camp.name, "from_date": str(day), "to_date": str(day)})
		self.assertEqual([p["name"] for p in only_c["posts"]], [c])
		with self.assertRaises(frappe.ValidationError):
			report.build({"from_date": "2026-11-10", "to_date": "2026-11-01"})

	def test_empty_report_shows_dash_not_zero(self):
		data = report.build({"campaign": self.camp.name})
		self.assertEqual(data["cards"]["total_posts"], 0)
		self.assertIsNone(data["cards"]["success_rate"])
		self.assertIsNone(data["cards"]["reach"])

	def test_excel_opens_and_uses_dash(self):
		from openpyxl import load_workbook

		self.sample()
		raw = report.to_xlsx(report.build({"campaign": self.camp.name}))
		wb = load_workbook(io.BytesIO(raw))
		self.assertEqual(wb.sheetnames, ["Tong quan", "Bai dang", "Tru cot va pheu", "Loi dang tai"])
		rows = list(wb["Bai dang"].iter_rows(values_only=True))
		b = next(r for r in rows if r[0] == "Bài B")
		self.assertEqual(b[6], "—")
		self.assertEqual(wb["Loi dang tai"]["B2"].value, "Token Trang hết hạn")

	def test_export_endpoint_sets_binary_response(self):
		self.sample()
		api.export_report({"campaign": self.camp.name})
		self.assertEqual(frappe.local.response.type, "binary")
		self.assertTrue(frappe.local.response.filename.endswith(".xlsx"))

	def test_user_without_role_cannot_read_report(self):
		email = f"{PREFIX.lower()}-x@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "x", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		with self.assertRaises(frappe.PermissionError):
			api.marketing_report({})


class TestInsightsSync(ReportCase):
	def test_sync_saves_metrics_skips_dry_run_and_survives_errors(self):
		a = self.item("Bài A")
		ok = self.target(a, published_at=now_datetime(), external_post_id="360_1")
		bad = self.target(a, published_at=now_datetime(), external_post_id="360_2")
		dry = self.target(a, published_at=now_datetime(), external_post_id="dryrun-x")
		old = self.target(a, published_at=add_to_date(now_datetime(), days=-40), external_post_id="360_3")
		frappe.db.commit()  # dữ liệu thật đã commit trước khi job chạy (job rollback khi một bài lỗi)

		class Fake:
			def fetch_insights(self, channel, post_id):
				if post_id == "360_2":
					raise RuntimeError("Graph lỗi")
				return {"likes": 3, "comments": 0, "shares": 1, "reach": None}

		with (
			mock.patch.object(insights, "get_adapter", return_value=Fake()),
			mock.patch.object(frappe, "log_error") as log,
		):
			out = insights.sync_all()
		self.assertGreaterEqual(out["ok"], 1)
		self.assertGreaterEqual(out["errors"], 1)
		row = frappe.db.get_value(
			TARGET, ok, ["likes", "shares", "insights_fields", "insights_synced_at"], as_dict=True
		)
		self.assertEqual((row.likes, row.shares), (3, 1))
		self.assertEqual(row.insights_fields, "likes,comments,shares")
		self.assertTrue(row.insights_synced_at)
		for name in (bad, dry, old):
			self.assertFalse(frappe.db.get_value(TARGET, name, "insights_synced_at"))
		self.assertTrue(log.called)

	def test_sync_due_respects_hours_and_switch(self):
		frappe.cache().delete(frappe.cache().make_key(insights.LAST_RUN_KEY))
		with mock.patch.object(insights, "sync_all", return_value={"ok": 0, "errors": 0}) as run:
			insights.sync_due()
			insights.sync_due()  # chưa đủ insights_sync_hours: không chạy lại
		self.assertEqual(run.call_count, 1)
		frappe.cache().delete(frappe.cache().make_key(insights.LAST_RUN_KEY))
		frappe.db.sql(
			"insert into tabSingles (doctype, field, value) values (%s, 'insights_sync_hours', '0')", SETTINGS
		)
		with mock.patch.object(insights, "sync_all") as run:
			self.assertIsNone(insights.sync_due())
		run.assert_not_called()

	def test_facebook_insights_without_read_insights_keeps_engagement(self):
		token_doc = frappe.get_doc("Omni Channel", self.meta)
		token_doc.meta_page_access_token = "tm360-token-khong-that"
		token_doc.save(ignore_permissions=True)

		class Resp:
			def __init__(self, status, body):
				self.status_code, self._body = status, body

			def json(self):
				return self._body

		def fake(method, url, **kw):
			if url.endswith("/insights"):
				return Resp(403, {"error": {"code": 10, "message": "Thiếu quyền read_insights"}})
			return Resp(
				200,
				{
					"reactions": {"summary": {"total_count": 12}},
					"comments": {"summary": {"total_count": 4}},
					"shares": {"count": 2},
				},
			)

		with mock.patch.object(facebook.requests, "request", side_effect=fake):
			out = facebook.FacebookAdapter().fetch_insights(self.meta, "360360_1")
		self.assertEqual((out["likes"], out["comments"], out["shares"]), (12, 4, 2))
		self.assertIsNone(out["reach"])
		self.assertIsNone(out["clicks"])


class TestViews(ReportCase):
	def test_calendar_month(self):
		a = self.item("Bài lịch", status=rules.I_SCHEDULED)
		self.target(a, status=rules.T_SCHEDULED)
		rows = api.get_calendar(self.when.month, self.when.year, campaign=self.camp.name)
		self.assertEqual([r.title for r in rows], ["Bài lịch"])
		self.assertEqual(rows[0].platform, rules.FACEBOOK)
		with self.assertRaises(frappe.ValidationError):
			api.get_calendar(13, 2026)

	def test_campaign_list_and_detail(self):
		a = self.item("Bài 1", status=rules.I_DRAFT)
		self.target(a, status=rules.T_REVIEW)
		row = next(r for r in api.list_campaigns() if r.name == self.camp.name)
		self.assertEqual((row.posts, row.draft), (1, 1))
		detail = api.campaign_detail(self.camp.name)
		self.assertEqual(detail["items"][0]["targets"][0]["platform"], rules.FACEBOOK)
		info = api.item_detail(a)
		self.assertTrue(info["can_edit"])
		self.assertEqual(info["campaign"]["name"], self.camp.name)

	def test_approve_whole_campaign_reports_each_post(self):
		good = self.item("Đủ điểm", status=rules.I_DRAFT, score=95)
		frappe.db.set_value(ITEM, good, "status", rules.I_DRAFT)
		self.target(good, status=rules.T_REVIEW, days=0)
		frappe.db.set_value(
			TARGET, {"content_item": good}, "scheduled_at", add_to_date(now_datetime(), hours=5)
		)
		low = self.item("Điểm thấp", status=rules.I_DRAFT, score=40)
		self.target(low, status=rules.T_REVIEW)
		frappe.db.set_value(
			TARGET, {"content_item": low}, "scheduled_at", add_to_date(now_datetime(), hours=5)
		)
		results = {r["title"]: r for r in api.approve_campaign(self.camp.name)}
		self.assertEqual(results["Đủ điểm"]["status"], rules.I_SCHEDULED)
		self.assertEqual(results["Điểm thấp"]["status"], rules.I_REVIEW)
		self.assertIn("dưới ngưỡng", results["Điểm thấp"]["message"])
		self.assertEqual(views.route(low)["status"], rules.I_REVIEW)  # chạy lại không lỗi

	def test_score_api_rejects_approved_post(self):
		a = self.item("Đã duyệt", status=rules.I_SCHEDULED)
		with self.assertRaises(frappe.ValidationError):
			api.score_item(a)
		self.assertTrue(callable(scoring.score_item))

	def test_marketing_user_cannot_schedule_or_rescore_via_pages(self):
		"""Các trang gọi frappe.client trực tiếp: Marketing User không được tự lên lịch, đổi trạng thái hay điểm."""
		from frappe.client import insert, set_value

		from omni_crm.tests.test_v3_10_marketing import make_user

		writer = make_user(f"{PREFIX.lower()}-writer@example.com", roles.USER)
		good = self.item("Đủ điểm", status=rules.I_DRAFT, score=95)
		t = self.target(good, status=rules.T_REVIEW)
		frappe.db.set_value(TARGET, t, "scheduled_at", add_to_date(now_datetime(), hours=5))
		frappe.db.commit()
		frappe.set_user(writer)
		results = {r["title"]: r for r in api.approve_campaign(self.camp.name)}
		self.assertEqual(results["Đủ điểm"]["status"], rules.I_REVIEW)  # chỉ gửi duyệt, không lên lịch
		self.assertEqual(frappe.db.get_value(TARGET, t, "status"), rules.T_REVIEW)
		for doctype, name, field, value in (
			(TARGET, t, "status", rules.T_SCHEDULED),
			(ITEM, good, "status", rules.I_SCHEDULED),
		):
			with self.assertRaises(frappe.ValidationError):
				set_value(doctype, name, field, value)
		frappe.db.set_value(ITEM, good, "status", rules.I_DRAFT)
		with self.assertRaises(frappe.ValidationError):
			set_value(ITEM, good, "quality_score", 100)
		self.assertEqual(frappe.db.get_value(ITEM, good, "quality_score"), 95)
		frappe.db.set_value(ITEM, good, "status", rules.I_REVIEW)
		new = insert(
			{
				"doctype": TARGET,
				"content_item": good,
				"platform": rules.FACEBOOK,
				"channel_account": self.meta,
				"post_format": "fb_link",
				"scheduled_at": add_to_date(now_datetime(), hours=6),
				"status": rules.T_SCHEDULED,
			}
		)
		self.assertEqual(new["status"], rules.T_REVIEW)
