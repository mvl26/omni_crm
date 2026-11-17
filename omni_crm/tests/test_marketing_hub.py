"""Marketing Hub (ADR 0029): link UTM, mã bài, trạng thái chỉ đổi qua nút, quyền, gắn Lead theo bài, job đăng bài
(Graph giả: đăng, lỗi quyền, mất kết nối, 5xx, bỏ lỡ, sandbox, tranh chấp), chỉ số, lịch, báo cáo. Không gọi mạng.

Job đăng bài commit giữa chừng nên dữ liệu được dọn thủ công theo tiền tố td29 / TD29.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import content_posts as api
from omni_crm.crm_bridge.form_lead import _attribution_values
from omni_crm.marketing import attribution, content_posts, metrics, publisher
from omni_crm.marketing.graph import PageClient
from omni_crm.marketing.utm import build_utm_link, is_valid_post_code, render_message

POST = "Omni Content Post"
CAMPAIGN = "TD29 Chiến dịch SPD"
CHANNEL = "TD29 page"
PAGE = "571684352685074"
SYSTEM_TOKEN = "EAAtd29SYSTEMtoken000"
PAGE_TOKEN = "EAAtd29PAGEtoken111"
MANAGER = "td29_manager@example.com"
SALES = "td29_sales@example.com"
LANDING = "https://yhct.miyano.com.vn/spd/"
PNG = (
	b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
	b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)
SETTINGS_KEYS = (
	"sandbox_mode",
	"content_publish_enabled",
	"content_publish_channel",
	"content_late_window_minutes",
	"content_metrics_enabled",
	"content_reach_metric",
	"content_clicks_metric",
)


class Resp:
	def __init__(self, status, data):
		self.status_code, self._data = status, data

	def json(self):
		return self._data


class Graph:
	"""Giả lập Graph cho `requests.request`: ghi lại lời gọi, trả theo (method, đường dẫn) và chế độ `mode`."""

	def __init__(self, mode="ok"):
		self.mode = mode
		self.calls = []

	def __call__(self, method, url, params=None, data=None, files=None, timeout=None, **kw):
		path = "/" + url.split("/", 4)[4]
		self.calls.append(
			{
				"method": method,
				"path": path,
				"params": dict(params or {}),
				"data": dict(data or {}),
				"files": files,
			}
		)
		token = (params or {}).get("access_token")
		if path == f"/{PAGE}" and method == "GET":
			assert token == SYSTEM_TOKEN
			return Resp(200, {"id": PAGE, "access_token": PAGE_TOKEN})
		assert token == PAGE_TOKEN, f"lời gọi {path} phải dùng token Page"
		if method == "POST" and path in (f"/{PAGE}/feed", f"/{PAGE}/photos"):
			if self.mode == "quyen":
				return Resp(
					403,
					{"error": {"code": 200, "message": f"(#200) needs pages_manage_posts {SYSTEM_TOKEN}"}},
				)
			if self.mode == "mang":
				raise requests.ConnectionError(f"boom {url}?access_token={token}")
			if self.mode == "5xx":
				return Resp(500, {"error": {"code": 2, "message": "Service temporarily unavailable"}})
			if path.endswith("/photos"):
				return Resp(200, {"id": "999", "post_id": f"{PAGE}_777"})
			return Resp(200, {"id": f"{PAGE}_555"})
		if method == "GET" and path.startswith(f"/{PAGE}_") and path.endswith("/insights"):
			if self.mode == "insights_loi":
				return Resp(400, {"error": {"code": 100, "message": "(#100) invalid metric"}})
			return Resp(
				200,
				{
					"data": [
						{"name": "post_impressions_unique", "values": [{"value": 321}]},
						{
							"name": "post_clicks_by_type",
							"values": [{"value": {"link clicks": 12, "other clicks": 5}}],
						},
					]
				},
			)
		if method == "GET" and path.startswith(f"/{PAGE}_"):
			fields = (params or {}).get("fields", "")
			if fields == "permalink_url":
				return Resp(200, {"permalink_url": f"https://www.facebook.com/miyano/posts/{path[1:]}"})
			return Resp(
				200,
				{
					"shares": {"count": 3},
					"reactions": {"summary": {"total_count": 40}},
					"comments": {"summary": {"total_count": 6}},
				},
			)
		raise AssertionError(f"lời gọi lạ {method} {path}")


def _delete_all():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	for name in frappe.get_all("CRM Lead", filters={"first_name": ["like", "TD29%"]}, pluck="name"):
		for deal in frappe.get_all("CRM Deal", filters={"lead": name}, pluck="name"):
			frappe.delete_doc("CRM Deal", deal, force=True, ignore_permissions=True)
		frappe.delete_doc("CRM Lead", name, force=True, ignore_permissions=True)
	for name in frappe.get_all(POST, filters={"name": ["like", "td29%"]}, pluck="name"):
		for f in frappe.get_all(
			"File", filters={"attached_to_doctype": POST, "attached_to_name": name}, pluck="name"
		):
			frappe.delete_doc("File", f, force=True, ignore_permissions=True)
		frappe.db.delete("Notification Log", {"document_type": POST, "document_name": name})
		frappe.db.delete("Comment", {"reference_doctype": POST, "reference_name": name})
		frappe.delete_doc(POST, name, force=True, ignore_permissions=True)
	for name in frappe.get_all("Omni Campaign", filters={"name": ["like", "TD29%"]}, pluck="name"):
		frappe.delete_doc("Omni Campaign", name, force=True, ignore_permissions=True)
	if frappe.db.exists("Omni Channel", CHANNEL):
		frappe.delete_doc("Omni Channel", CHANNEL, force=True, ignore_permissions=True)
	frappe.db.commit()


def make_users():
	for email, role in ((MANAGER, "Sales Manager"), (SALES, "Sales User")):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "TD29",
					"send_welcome_email": 0,
					"roles": [{"role": role}],
				}
			).insert(ignore_permissions=True)


def make_campaign(utm="spd-pk-td29", landing=LANDING):
	frappe.get_doc(
		{
			"doctype": "Omni Campaign",
			"campaign_name": CAMPAIGN,
			"platform": "Đa kênh",
			"status": "Đang chạy",
			"utm_campaign": utm,
			"landing_url": landing,
		}
	).insert(ignore_permissions=True)


def make_post(code="td29-b01", **kw):
	values = {
		"doctype": POST,
		"post_code": code,
		"campaign": CAMPAIGN,
		"title": "SPD là gì",
		"platform": "Facebook",
		"publish_mode": "Tự động",
		"publish_at": now_datetime() + timedelta(hours=1),
		"content": "Bài thử TD29. Đăng ký tư vấn: {link}",
	}
	values.update(kw)
	return frappe.get_doc(values).insert(ignore_permissions=True)


def make_channel():
	frappe.get_doc(
		{
			"doctype": "Omni Channel",
			"channel_name": CHANNEL,
			"channel_type": "Meta Messenger",
			"enabled": 1,
			"outbound_enabled": 1,
			"meta_page_id": PAGE,
			"meta_page_access_token": SYSTEM_TOKEN,
		}
	).insert(ignore_permissions=True)


def snapshot_settings(keys) -> dict:
	"""{trường: giá trị đang lưu, hoặc None nếu trường CHƯA có dòng trong tabSingles}."""
	rows = dict(
		frappe.db.sql(
			"select field, value from `tabSingles` where doctype='Omni Settings' and field in %(f)s",
			{"f": tuple(keys)},
		)
	)
	return {k: rows.get(k) for k in keys}


def restore_settings(snap: dict) -> None:
	"""Trả Omni Settings về như trước test: trường chưa từng lưu thì xóa dòng (không ghi rỗng lên site)."""
	for key, value in snap.items():
		if value is None:
			frappe.db.delete("Singles", {"doctype": "Omni Settings", "field": key})
		else:
			frappe.db.set_single_value("Omni Settings", key, value)
	frappe.clear_document_cache("Omni Settings", "Omni Settings")


def set_settings(**values):
	for key, value in values.items():
		frappe.db.set_single_value("Omni Settings", key, value)
	frappe.clear_document_cache("Omni Settings", "Omni Settings")


def make_lead(first_name, **values):
	doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": first_name})
	doc.insert(ignore_permissions=True)
	if values:
		frappe.db.set_value("CRM Lead", doc.name, values, update_modified=False)
	return doc.name


class MarketingCase(FrappeTestCase):
	def setUp(self):
		_delete_all()
		self._saved = snapshot_settings(SETTINGS_KEYS)
		self.addCleanup(self._restore)
		make_users()
		make_campaign()
		frappe.db.commit()

	def _restore(self):
		_delete_all()
		restore_settings(self._saved)
		frappe.db.commit()


class TestUtm(FrappeTestCase):
	def test_link_matches_prompt_m1_format(self):
		self.assertEqual(
			build_utm_link(LANDING, "Facebook", "spd-pk-2611", "spd-b01"),
			"https://yhct.miyano.com.vn/spd/?utm_source=facebook&utm_medium=social&utm_campaign=spd-pk-2611&utm_content=spd-b01",
		)

	def test_link_keeps_other_params_and_replaces_old_utm(self):
		link = build_utm_link("https://x.vn/a?ref=1&utm_source=old&utm_term=k", "Zalo OA", "c1", "SPD-B02")
		self.assertEqual(
			link, "https://x.vn/a?ref=1&utm_source=zalo&utm_medium=social&utm_campaign=c1&utm_content=spd-b02"
		)

	def test_link_empty_when_missing_parts_or_not_http(self):
		self.assertEqual(build_utm_link("", "Facebook", "c", "p"), "")
		self.assertEqual(build_utm_link(LANDING, "Facebook", "", "p"), "")
		self.assertEqual(build_utm_link("javascript:alert(1)", "Facebook", "c", "p"), "")

	def test_post_code_rules(self):
		self.assertTrue(is_valid_post_code("spd-b01"))
		for bad in ("", "SPD B01", "spd_b01", "-spd", "bài-1", "a" * 41):
			self.assertFalse(is_valid_post_code(bad), bad)

	def test_render_message(self):
		self.assertEqual(render_message("Xem {link} nhé", "https://l"), ("Xem https://l nhé", True))
		self.assertEqual(render_message("Không có chỗ link", "https://l"), ("Không có chỗ link", False))


class TestPostRecord(MarketingCase):
	def test_code_is_normalized_and_utm_link_built_from_campaign(self):
		doc = make_post(" TD29-B01 ")
		self.assertEqual(doc.name, "td29-b01")
		self.assertEqual(
			doc.utm_link,
			f"{LANDING}?utm_source=facebook&utm_medium=social&utm_campaign=spd-pk-td29&utm_content=td29-b01",
		)

	def test_invalid_code_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_post("td29 b01")

	def test_status_cannot_be_edited_directly(self):
		doc = make_post()
		doc.status = "Đã đăng"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_external_image_link_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_post(image="https://evil.example/a.png")

	def test_campaign_utm_change_rebuilds_unpublished_links_only(self):
		make_post("td29-b01")
		make_post("td29-b02", publish_mode="Đăng tay")
		published = frappe.get_doc(POST, "td29-b02")
		published.post_url = "https://www.facebook.com/miyano/posts/1"
		published.save(ignore_permissions=True)
		old_link = published.utm_link
		camp = frappe.get_doc("Omni Campaign", CAMPAIGN)
		camp.utm_campaign = "spd-moi"
		camp.save(ignore_permissions=True)
		self.assertIn("utm_campaign=spd-moi", frappe.db.get_value(POST, "td29-b01", "utm_link"))
		self.assertEqual(frappe.db.get_value(POST, "td29-b02", "utm_link"), old_link)


class TestStatusButtons(MarketingCase):
	def test_schedule_unschedule_cancel_reopen(self):
		make_post()
		self.assertEqual(api.schedule("td29-b01"), "Đã lên lịch")
		self.assertEqual(api.unschedule("td29-b01"), "Nháp")
		self.assertEqual(api.cancel("td29-b01"), "Đã huỷ")
		with self.assertRaises(frappe.ValidationError):
			api.schedule("td29-b01")
		self.assertEqual(api.reopen("td29-b01"), "Nháp")

	def test_schedule_requires_content_and_campaign_utm(self):
		make_post(content="")
		with self.assertRaises(frappe.ValidationError):
			api.schedule("td29-b01")
		frappe.db.set_value("Omni Campaign", CAMPAIGN, "utm_campaign", None)
		make_post("td29-b02")
		with self.assertRaises(frappe.ValidationError):
			api.schedule("td29-b02")

	def test_auto_post_in_the_past_or_not_facebook_cannot_be_scheduled(self):
		make_post(publish_at=now_datetime() - timedelta(hours=2))
		with self.assertRaises(frappe.ValidationError):
			api.schedule("td29-b01")
		make_post("td29-b02", platform="Zalo OA")
		with self.assertRaises(frappe.ValidationError):
			api.schedule("td29-b02")
		make_post(
			"td29-b03",
			platform="Zalo OA",
			publish_mode="Đăng tay",
			publish_at=now_datetime() - timedelta(days=2),
		)
		self.assertEqual(api.schedule("td29-b03"), "Đã lên lịch")

	def test_manual_post_url_marks_published_then_content_locked(self):
		doc = make_post(publish_mode="Đăng tay")
		doc.post_url = "https://www.facebook.com/miyano/posts/9"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.status, "Đã đăng")
		self.assertTrue(doc.published_at)
		doc.reload()
		doc.content = "đổi nội dung sau khi đã đăng"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		doc.reload()
		doc.reach = 100  # số liệu nhập tay vẫn sửa được
		doc.save(ignore_permissions=True)

	def test_sales_manager_can_schedule_sales_user_cannot(self):
		make_post()
		frappe.db.commit()
		frappe.set_user(SALES)
		with self.assertRaises(frappe.PermissionError):
			api.schedule("td29-b01")
		frappe.set_user(MANAGER)
		self.assertEqual(api.schedule("td29-b01"), "Đã lên lịch")
		frappe.set_user("Administrator")

	def test_resolve_uncertain(self):
		make_post()
		frappe.db.set_value(POST, "td29-b01", "status", "Cần kiểm")
		with self.assertRaises(frappe.ValidationError):
			api.resolve_uncertain("td29-b01", 1, "")
		self.assertEqual(api.resolve_uncertain("td29-b01", 1, "https://www.facebook.com/p/1"), "Đã đăng")
		make_post("td29-b02")
		frappe.db.set_value(POST, "td29-b02", "status", "Cần kiểm")
		self.assertEqual(api.resolve_uncertain("td29-b02", 0), "Lỗi")


class TestLeadAttribution(MarketingCase):
	def test_first_touch_gets_post_when_utm_content_matches(self):
		make_post("td29-b01")
		form = {"platform": "Website", "utm": {"utm_campaign": "spd-pk-td29", "utm_content": "TD29-B01"}}
		values, _ = _attribution_values(form, CAMPAIGN, {}, True)
		self.assertEqual(values["omni_content_post"], "td29-b01")
		values, _ = _attribution_values(
			{"platform": "Website", "utm": {"utm_content": "khong-co"}}, CAMPAIGN, {}, True
		)
		self.assertNotIn("omni_content_post", values)
		# không phải lần chạm đầu: giữ bài đầu tiên
		values, _ = _attribution_values(
			form, CAMPAIGN, {"omni_campaign": CAMPAIGN, "omni_ad_platform": "Website"}, False
		)
		self.assertNotIn("omni_content_post", values)

	def test_post_created_later_relinks_earlier_leads_and_counts_skip_test_leads(self):
		real = make_lead("TD29 Khách", omni_utm_content="td29-b05")
		test = make_lead("TD29 Thử", omni_utm_content="td29-b05", omni_is_test=1)
		other = make_lead("TD29 Khác", omni_utm_content="td29-b06")
		frappe.get_doc({"doctype": "CRM Deal", "lead": real, "organization": None}).insert(
			ignore_permissions=True, ignore_mandatory=True
		)
		make_post("td29-b05", publish_mode="Đăng tay")
		self.assertEqual(frappe.db.get_value("CRM Lead", real, "omni_content_post"), "td29-b05")
		self.assertEqual(frappe.db.get_value("CRM Lead", test, "omni_content_post"), "td29-b05")
		self.assertFalse(frappe.db.get_value("CRM Lead", other, "omni_content_post"))
		self.assertEqual(attribution.counts("td29-b05"), {"leads": 1, "deals": 1})
		self.assertEqual(frappe.db.get_value(POST, "td29-b05", ["lead_count", "deal_count"]), (1, 1))


class PublisherCase(MarketingCase):
	def setUp(self):
		super().setUp()
		make_channel()
		set_settings(
			sandbox_mode=0,
			content_publish_enabled=1,
			content_publish_channel=CHANNEL,
			content_late_window_minutes=30,
		)
		frappe.db.commit()


class TestPublisher(PublisherCase):
	def _due(self, code="td29-b01", **kw):
		make_post(code, **kw)
		api.schedule(code)
		frappe.db.set_value(POST, code, "publish_at", now_datetime() - timedelta(minutes=1))
		frappe.db.commit()

	def _run(self, graph):
		with mock.patch("omni_crm.marketing.graph.requests.request", graph):
			publisher.run_due()
		return frappe.get_doc(POST, "td29-b01")

	def test_publishes_feed_post_with_link_in_message(self):
		self._due()
		graph = Graph()
		doc = self._run(graph)
		self.assertEqual(doc.status, "Đã đăng")
		self.assertEqual(doc.external_post_id, f"{PAGE}_555")
		self.assertEqual(doc.post_url, f"https://www.facebook.com/miyano/posts/{PAGE}_555")
		feed = next(c for c in graph.calls if c["path"] == f"/{PAGE}/feed")
		self.assertIn("utm_content=td29-b01", feed["data"]["message"])
		self.assertNotIn("link", feed["data"])  # link đã nằm trong nội dung qua {link}
		self.assertEqual(feed["data"]["published"], "true")

	def test_publishes_photo_post_with_image_bytes(self):
		self._due(content="Không có chỗ đặt link")
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "td29.png",
				"content": PNG,
				"is_private": 1,
				"attached_to_doctype": POST,
				"attached_to_name": "td29-b01",
				"attached_to_field": "image",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(POST, "td29-b01", "image", f.file_url)
		frappe.db.commit()
		graph = Graph()
		doc = self._run(graph)
		self.assertEqual(doc.status, "Đã đăng")
		self.assertEqual(doc.external_post_id, f"{PAGE}_777")
		photo = next(c for c in graph.calls if c["path"] == f"/{PAGE}/photos")
		self.assertEqual(photo["files"]["source"][1], PNG)
		self.assertTrue(photo["data"]["caption"].endswith(doc.utm_link))

	def test_permission_error_marks_failed_without_leaking_token_and_notifies(self):
		self._due()
		doc = self._run(Graph("quyen"))
		self.assertEqual(doc.status, "Lỗi")
		self.assertIn("pages_manage_posts", doc.last_error)
		self.assertNotIn(SYSTEM_TOKEN, doc.last_error)
		self.assertNotIn(PAGE_TOKEN, doc.last_error)
		self.assertTrue(
			frappe.db.exists("Notification Log", {"document_type": POST, "document_name": "td29-b01"})
		)

	def test_network_error_marks_uncertain_and_never_retries(self):
		self._due()
		graph = Graph("mang")
		doc = self._run(graph)
		self.assertEqual(doc.status, "Cần kiểm")
		self.assertNotIn("access_token", doc.last_error)
		posts_before = len([c for c in graph.calls if c["method"] == "POST"])
		self._run(graph)
		self.assertEqual(len([c for c in graph.calls if c["method"] == "POST"]), posts_before)

	def test_server_error_on_publish_is_uncertain(self):
		self._due()
		self.assertEqual(self._run(Graph("5xx")).status, "Cần kiểm")

	def test_past_late_window_is_missed_without_calling_meta(self):
		self._due()
		frappe.db.set_value(POST, "td29-b01", "publish_at", now_datetime() - timedelta(minutes=45))
		frappe.db.commit()
		graph = Graph()
		self.assertEqual(self._run(graph).status, "Bỏ lỡ")
		self.assertEqual(graph.calls, [])

	def test_switch_off_or_sandbox_means_no_call(self):
		self._due()
		for values in ({"content_publish_enabled": 0}, {"content_publish_enabled": 1, "sandbox_mode": 1}):
			set_settings(**values)
			graph = Graph()
			self.assertEqual(self._run(graph).status, "Đã lên lịch")
			self.assertEqual(graph.calls, [])

	def test_post_unscheduled_before_job_is_skipped(self):
		self._due()
		api.unschedule("td29-b01")
		frappe.db.commit()
		client = PageClient(frappe.get_doc("Omni Channel", CHANNEL))
		graph = Graph()
		with mock.patch("omni_crm.marketing.graph.requests.request", graph):
			self.assertIsNone(publisher.publish_one("td29-b01", client))
		self.assertEqual(graph.calls, [])

	def test_stuck_publishing_becomes_uncertain(self):
		make_post()
		frappe.db.set_value(
			POST,
			"td29-b01",
			{"status": "Đang đăng", "last_attempt_at": now_datetime() - timedelta(minutes=20)},
		)
		frappe.db.commit()
		publisher.sweep_stuck()
		self.assertEqual(frappe.db.get_value(POST, "td29-b01", "status"), "Cần kiểm")

	def test_form_shows_why_auto_publish_is_off(self):
		set_settings(content_publish_enabled=0)
		self.assertIn("tắt", content_posts.auto_publish_state())
		set_settings(content_publish_enabled=1)
		self.assertIsNone(content_posts.auto_publish_state())


class TestMetrics(PublisherCase):
	def _published(self):
		make_post()
		frappe.db.set_value(
			POST,
			"td29-b01",
			{"status": "Đã đăng", "external_post_id": f"{PAGE}_555", "published_at": now_datetime()},
		)
		set_settings(content_metrics_enabled=1, content_reach_metric="", content_clicks_metric="")
		frappe.db.commit()

	def test_sync_reads_engagement_and_insights(self):
		self._published()
		with mock.patch("omni_crm.marketing.graph.requests.request", Graph()):
			metrics.sync_daily()
		d = frappe.get_doc(POST, "td29-b01")
		self.assertEqual((d.reactions, d.comments, d.shares, d.reach, d.link_clicks), (40, 6, 3, 321, 12))
		self.assertIn("post_impressions_unique", json.loads(d.metrics_json))

	def test_insights_error_keeps_engagement(self):
		self._published()
		with mock.patch("omni_crm.marketing.graph.requests.request", Graph("insights_loi")):
			metrics.sync_daily()
		d = frappe.get_doc(POST, "td29-b01")
		self.assertEqual(d.reactions, 40)
		self.assertIn("Insights", d.last_error)


class TestCalendarAndReport(MarketingCase):
	def test_calendar_and_report(self):
		make_post("td29-b01", publish_at=now_datetime() + timedelta(days=1))
		make_post("td29-b02", publish_at=now_datetime() + timedelta(days=40))
		lead = make_lead("TD29 Không bài")
		frappe.db.set_value("CRM Lead", lead, "omni_campaign", CAMPAIGN)
		start = (now_datetime() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
		end = (now_datetime() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
		events = api.calendar_events("Omni Content Post", start, end)
		names = [e["name"] for e in events]
		self.assertIn("td29-b01", names)
		self.assertNotIn("td29-b02", names)
		self.assertEqual(events[names.index("td29-b01")]["editable"], 0)

		from omni_crm.omni_crm.report.hiệu_quả_bài_đăng import hiệu_quả_bài_đăng as report

		columns, data = report.execute({"campaign": CAMPAIGN})
		self.assertEqual([r["post"] for r in data[:2]], ["td29-b01", "td29-b02"])
		self.assertEqual((data[-1]["title"], data[-1]["leads"]), ("Không rõ bài", 1))
