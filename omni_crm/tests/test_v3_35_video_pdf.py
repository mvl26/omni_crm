"""ADR 0035: bài Facebook có video (fb_video) và bài có link tới PDF (fb_pdf_link).

Giả lập HTTP, không gọi mạng thật. Video và PDF nằm ở hai ô mới của bài (`video`, `document`), chuyển lên kho S3 khi
lưu bài như ô ảnh (ADR 0033).
"""

from __future__ import annotations

import os
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import approval, rules, s3_media
from omni_crm.marketing.publisher import content, facebook
from omni_crm.marketing.publisher.errors import PublishValidationError
from omni_crm.tests.test_v3_10_publisher import PREFIX as PUB_PREFIX
from omni_crm.tests.test_v3_10_publisher import FakeResp, PublisherCase, fb_payload
from omni_crm.tests.test_v3_33_media_storage import ITEM, MediaCase, configure, disable

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
FILE_PREFIX = "tm335"


def pdf_bytes(tag: str) -> bytes:
	from io import BytesIO

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=10, height=10)
	writer.add_metadata({"/Title": tag})
	buf = BytesIO()
	writer.write(buf)
	return buf.getvalue()


def drop_files() -> None:
	for f in frappe.get_all("File", {"file_name": ["like", f"{FILE_PREFIX}%"]}, ["name", "file_url"]):
		url = str(f.file_url or "")
		if url.startswith(("/files/", "/private/files/")):
			base = "private" if url.startswith("/private/") else "public"
			try:
				os.remove(frappe.get_site_path(base, "files", os.path.basename(url)))
			except OSError:
				pass
		frappe.db.delete("File", {"name": f.name})
	frappe.db.commit()


class VideoCase(PublisherCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(drop_files)
		self.n = 0

	def file(self, ext=".mp4", private=0, content=None) -> str:
		self.n += 1
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{FILE_PREFIX}-{self.n}-{frappe.generate_hash(length=5)}{ext}",
				"content": content
				if content is not None
				else (pdf_bytes(frappe.generate_hash(length=6)) if ext == ".pdf" else MP4 + os.urandom(8)),
				"is_private": private,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.file_url

	def item_with(self, fmt: str, video=None, document=None) -> tuple[str, str]:
		camp = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PUB_PREFIX} video {frappe.generate_hash(length=6)}",
				"platform": "Đa kênh",
				"status": "Đang chạy",
			}
		).insert(ignore_permissions=True)
		item = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": camp.name,
				"title": "Bài video thử",
				"caption": "Miyano giới thiệu SPD",
				"video": video,
				"document": document,
				"quality_score": 95,
			}
		)
		item.flags.score_via_ai = True
		item.insert(ignore_permissions=True)
		target = frappe.get_doc(
			{
				"doctype": "Omni Content Target",
				"content_item": item.name,
				"platform": rules.FACEBOOK,
				"channel_account": self.meta,
				"post_format": fmt,
				"scheduled_at": add_to_date(now_datetime(), minutes=5),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return item.name, target.name


class TestFormatsAndApproval(VideoCase):
	def test_new_formats_are_facebook_only(self):
		for fmt in ("fb_video", "fb_pdf_link"):
			self.assertTrue(rules.check_format(rules.FACEBOOK, fmt))
			self.assertFalse(rules.check_format(rules.ZALO, fmt))

	def test_approve_needs_the_file(self):
		item, _t = self.item_with("fb_video")
		approval.submit(item)
		with self.assertRaisesRegex(frappe.ValidationError, "cần video đăng kèm"):
			approval.approve(item)
		item2, _t2 = self.item_with("fb_pdf_link")
		approval.submit(item2)
		with self.assertRaisesRegex(frappe.ValidationError, "cần tài liệu PDF đăng kèm"):
			approval.approve(item2)
		item3, _t3 = self.item_with("fb_video", video=self.file(".mp4"))
		approval.submit(item3)
		approval.approve(item3)
		self.assertEqual(frappe.db.get_value(ITEM, item3, "status"), rules.I_SCHEDULED)


class TestBuild(VideoCase):
	def test_video_payload_has_absolute_public_url(self):
		url = self.file(".mp4")
		_item, target = self.item_with("fb_video", video=url)
		p = content.build(target)
		self.assertTrue(p.video.startswith(("http://", "https://")))
		self.assertTrue(p.video.endswith(os.path.basename(url).replace(" ", "%20")))
		self.assertEqual(p.images, [])
		self.assertEqual(p.document, "")

	def test_pdf_payload(self):
		url = self.file(".pdf")
		_item, target = self.item_with("fb_pdf_link", document=url)
		p = content.build(target)
		self.assertTrue(p.document.endswith(os.path.basename(url)))
		self.assertEqual(p.video, "")

	def test_private_wrong_type_and_missing(self):
		_i, t1 = self.item_with("fb_video", video=self.file(".mp4", private=1))
		with self.assertRaisesRegex(PublishValidationError, "Video .* là tệp riêng tư"):
			content.build(t1)
		_i, t2 = self.item_with("fb_video", video=self.file(".pdf"))
		with self.assertRaisesRegex(PublishValidationError, "Video phải là tệp MP4, MOV"):
			content.build(t2)
		_i, t3 = self.item_with("fb_pdf_link", document=self.file(".mp4"))
		with self.assertRaisesRegex(PublishValidationError, "Tài liệu PDF phải là tệp PDF"):
			content.build(t3)
		_i, t4 = self.item_with("fb_video")
		with self.assertRaisesRegex(PublishValidationError, "Thiếu video đăng kèm"):
			content.build(t4)

	def test_moved_video_uses_omni_media_url(self):
		url = self.file(".mp4")
		item, target = self.item_with("fb_video", video=url)
		new = "/omni-media/abc-" + "0" * 32 + ".mp4"
		frappe.db.set_value(ITEM, item, "video", new)
		with mock.patch.object(s3_media, "absolute_public_url", return_value="https://crm.test" + new) as m:
			self.assertEqual(content.build(target).video, "https://crm.test" + new)
		m.assert_called_once_with(new)


def video_payload(fmt="fb_video"):
	p = fb_payload(fmt)
	p.video = "https://yhct.miyano.com.vn/omni-media/f1-" + "a" * 32 + ".mp4"
	p.document = "https://yhct.miyano.com.vn/omni-media/f2-" + "b" * 32 + ".pdf"
	return p


class TestFacebookVideoAdapter(FrappeTestCase):
	def setUp(self):
		self.ad = facebook.FacebookAdapter()
		mock.patch.object(facebook.FacebookAdapter, "_page", return_value=("320320", "tok")).start()
		self.addCleanup(mock.patch.stopall)

	def test_video_post_goes_to_video_host_with_file_url(self):
		with mock.patch("requests.request", return_value=FakeResp(200, {"id": "1548814027082620"})) as req:
			res = self.ad.publish(video_payload())
		self.assertEqual(res.external_post_id, "1548814027082620")
		args, kwargs = req.call_args
		self.assertEqual(args[0], "POST")
		self.assertTrue(args[1].startswith("https://graph-video.facebook.com/"))
		self.assertTrue(args[1].endswith("/320320/videos"))
		self.assertEqual(kwargs["data"]["file_url"], video_payload().video)
		self.assertEqual(kwargs["data"]["published"], "true")
		self.assertIn("utm_content=bai-01", kwargs["data"]["description"], "mã theo dõi để đối chiếu")
		self.assertEqual(kwargs["timeout"], facebook.VIDEO_TIMEOUT)
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

	def test_pdf_post_is_a_link_to_the_pdf(self):
		with mock.patch("requests.request", return_value=FakeResp(200, {"id": "320_7"})) as req:
			res = self.ad.publish(video_payload("fb_pdf_link"))
		self.assertEqual(res.external_post_id, "320_7")
		args, kwargs = req.call_args
		self.assertTrue(args[1].startswith("https://graph.facebook.com/"))
		self.assertTrue(args[1].endswith("/320320/feed"))
		self.assertEqual(kwargs["data"]["link"], video_payload().document)
		self.assertEqual(
			kwargs["timeout"], facebook.LINK_TIMEOUT, "Facebook tải PDF dựng thẻ xem trước: chờ lâu hơn"
		)

	def test_video_permission_error_is_terminal(self):
		from omni_crm.marketing.publisher.errors import TerminalError

		err = {
			"error": {
				"code": 200,
				"message": "(#200) User does not have sufficient administrative permission",
			}
		}
		with mock.patch("requests.request", return_value=FakeResp(403, err)):
			with self.assertRaises(TerminalError):
				self.ad.publish(video_payload())

	def test_reconcile_video_by_marker_in_description(self):
		p = video_payload()
		from datetime import datetime, timezone

		now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")  # Graph trả giờ UTC
		videos = {
			"data": [
				{"id": "v-khac", "description": "bài khác", "created_time": now},
				{
					"id": "v-1",
					"description": "Xin chào\n\n" + p.tracking_url,
					"permalink_url": "/320320/videos/v-1/",
					"created_time": now,
				},
			]
		}
		with mock.patch("requests.request", return_value=FakeResp(200, videos)) as req:
			res = self.ad.reconcile(p)
		self.assertEqual(res.external_post_id, "v-1")
		self.assertEqual(res.permalink, "https://www.facebook.com/320320/videos/v-1/")
		self.assertTrue(req.call_args.args[1].endswith("/320320/videos"))
		old = {"data": [dict(videos["data"][1], created_time="2020-01-01T00:00:00+0000")]}
		with mock.patch("requests.request", return_value=FakeResp(200, old)):
			self.assertIsNone(self.ad.reconcile(p), "video cũ hơn cửa sổ đối chiếu không tính")

	def test_permalink_of_video_is_absolute(self):
		with mock.patch("requests.request", return_value=FakeResp(200, {"permalink_url": "/reel/123/"})):
			self.assertEqual(
				self.ad.fetch_permalink(video_payload(), "123"), "https://www.facebook.com/reel/123/"
			)
		with mock.patch(
			"requests.request", return_value=FakeResp(200, {"permalink_url": "https://fb.com/x"})
		):
			self.assertEqual(self.ad.fetch_permalink(video_payload(), "320_1"), "https://fb.com/x")

	def test_video_insights_read_on_page_post_of_video(self):
		"""T3 23/11/2026: post_id của nút video là số trần, mã bài Trang là <page>_<số>; cảm xúc, bình luận, chia sẻ
		và tiếp cận đọc trên bài đó; lượt xem video trên video_insights; không hỏi reactions trên nút video."""
		calls = []
		out = self._insights(
			calls, {("1548814027082620", "post_id"): FakeResp(200, {"post_id": "122175987854723505"})}
		)
		self.assertEqual((out["likes"], out["comments"], out["shares"]), (4, 2, 6))
		self.assertEqual(out["video_views"], 321)
		self.assertEqual((out["reach"], out["impressions"], out["clicks"]), (900, 1500, 40))
		self.assertIn(("320320_122175987854723505/insights", "post_media_view"), calls)
		metrics = [m for _p, m in calls if m]
		for gone in ("post_impressions", "post_impressions_unique", "total_video_impressions"):
			self.assertNotIn(gone, metrics, "Meta đã gỡ")
		self.assertNotIn(("1548814027082620", facebook.REACTION_FIELDS), calls)

	def test_reel_uses_reel_plays_when_video_views_empty(self):
		calls = []
		out = self._insights(calls, {}, values={"total_video_views": None, "fb_reels_total_plays": 55})
		self.assertEqual(out["video_views"], 55)
		self.assertEqual(out["reach"], 900, "không có post_id thì dùng <page>_<video>")
		self.assertIn(("320320_1548814027082620/insights", "post_total_media_view_unique"), calls)

	def test_no_page_post_reads_likes_and_comments_on_video(self):
		calls = []
		bad = FakeResp(400, {"error": {"code": 100, "message": "x"}})
		out = self._insights(
			calls, {("1548814027082620", "post_id"): bad, ("320320_1548814027082620", "id"): bad}
		)
		self.assertEqual((out["likes"], out["comments"], out["shares"]), (8, 1, 3))
		self.assertEqual(out["video_views"], 321)
		self.assertIsNone(out["reach"])
		self.assertFalse(
			any(p.endswith("/insights") for p, _m in calls), "không có bài Trang: không gọi insights"
		)

	def test_post_metric_removed_by_meta_only_blanks_that_number(self):
		calls = []
		out = self._insights(
			calls,
			{
				("320320_5/insights", "post_clicks"): FakeResp(
					400, {"error": {"code": 100, "message": "invalid metric"}}
				)
			},
			post="320320_5",
		)
		self.assertEqual((out["likes"], out["comments"], out["shares"]), (4, 2, 6))
		self.assertEqual((out["reach"], out["impressions"]), (900, 1500))
		self.assertIsNone(out["clicks"])
		self.assertIsNone(out["video_views"])

	def test_without_read_user_content_post_numbers_come_from_insights(self):
		"""T3 23/11/2026: token chưa có pages_read_user_content (lỗi mã 10 khi đọc reactions, comments của bài)."""
		calls = []
		denied = FakeResp(400, {"error": {"code": 10, "message": "requires pages_read_user_content"}})
		out = self._insights(
			calls, {("320320_5", f"shares,{facebook.REACTION_FIELDS}"): denied}, post="320320_5"
		)
		self.assertEqual((out["likes"], out["comments"], out["shares"]), (4, 2, 1))
		self.assertEqual((out["reach"], out["impressions"], out["clicks"]), (900, 1500, 40))
		self.assertIn(("320320_5", "id"), calls, "bài còn và token dùng được: kiểm bằng trường id")

	def test_reel_without_read_user_content_keeps_likes_comments_of_video(self):
		calls = []
		denied = FakeResp(400, {"error": {"code": 10, "message": "requires pages_read_user_content"}})
		out = self._insights(
			calls,
			{
				("1548814027082620", "post_id"): FakeResp(200, {"post_id": "122175987854723505"}),
				("320320_122175987854723505", f"shares,{facebook.REACTION_FIELDS}"): denied,
			},
			values={"total_video_views": None, "fb_reels_total_plays": 13},
		)
		self.assertEqual((out["likes"], out["comments"]), (8, 1), "tương tác Reel không vào metric bài Trang")
		self.assertEqual((out["shares"], out["video_views"]), (1, 13))
		self.assertEqual(out["impressions"], 1500)

	def test_deleted_post_or_broken_token_still_raises_for_sync_log(self):
		from omni_crm.marketing.publisher.errors import PublishError

		calls = []
		denied = FakeResp(400, {"error": {"code": 10, "message": "requires pages_read_user_content"}})
		gone = FakeResp(400, {"error": {"code": 190, "message": "token expired"}})
		with self.assertRaises(PublishError):
			self._insights(
				calls,
				{("320320_5", f"shares,{facebook.REACTION_FIELDS}"): denied, ("320320_5", "id"): gone},
				post="320320_5",
			)

	def _insights(self, calls, special, post="1548814027082620", values=None):
		metric_values = {
			"total_video_views": 321,
			"fb_reels_total_plays": 99,
			"total_video_stories_by_action_type": {"share": 3, "like": 5, "comment": 2},
			"post_total_media_view_unique": 900,
			"post_media_view": 1500,
			"post_clicks": 40,
			"post_reactions_by_type_total": {"like": 3, "love": 1},
			"post_activity_by_action_type": {"comment": 2, "share": 1},
			**(values or {}),
		}

		def fake(method, url, params=None, **kw):
			path = url.split("/v", 1)[1].split("/", 1)[1]
			params = params or {}
			metric = params.get("metric", "")
			fields = params.get("fields", "")
			calls.append((path, metric or fields))
			key = (path, metric or fields)
			if key in special:
				return special[key]
			if metric:
				value = metric_values.get(metric)
				rows = (
					[]
					if value is None
					else [{"name": metric, "period": "lifetime", "values": [{"value": value}]}]
				)
				return FakeResp(200, {"data": rows})
			if fields in ("id", "post_id"):
				return FakeResp(200, {"id": path})
			if fields == facebook.VIDEO_EDGE_FIELDS:
				return FakeResp(
					200,
					{"likes": {"summary": {"total_count": 8}}, "comments": {"summary": {"total_count": 1}}},
				)
			return FakeResp(
				200,
				{
					"reactions": {"summary": {"total_count": 4}},
					"comments": {"summary": {"total_count": 2}},
					"shares": {"count": 6},
				},
			)

		with mock.patch("requests.request", side_effect=fake):
			return self.ad.fetch_insights("kenh", post)


class TestVideoMetricsSaved(VideoCase):
	"""Lượt xem video lưu vào cột mới, có trong báo cáo marketing và tệp Excel."""

	def test_sync_and_report(self):
		from omni_crm.marketing import insights, report

		item, target = self.item_with("fb_video", video=self.file(".mp4"))
		frappe.db.set_value(
			"Omni Content Target",
			target,
			{
				"status": rules.T_SUCCESS,
				"external_post_id": "1548814027082620",
				"published_at": now_datetime(),
			},
		)
		data = {"likes": 4, "comments": 2, "shares": 3, "video_views": 321, "reach": None}
		with mock.patch.object(insights, "get_adapter") as adapter:
			adapter.return_value.fetch_insights.return_value = data
			insights.sync_target({"name": target, "platform": rules.FACEBOOK, "channel_account": self.meta,
				"external_post_id": "1548814027082620"})  # fmt: skip
		row = frappe.db.get_value(
			"Omni Content Target", target, ["video_views", "insights_fields"], as_dict=True
		)
		self.assertEqual(row.video_views, 321)
		self.assertIn("video_views", row.insights_fields.split(","))
		self.assertNotIn("reach", row.insights_fields.split(","))
		campaign = frappe.db.get_value(ITEM, item, "campaign")
		built = report.build({"campaign": campaign})
		self.assertEqual(built["cards"]["video_views"], 321)
		self.assertEqual(built["posts"][0]["video_views"], 321)
		self.assertIsNone(built["posts"][0]["reach"])
		from io import BytesIO

		from openpyxl import load_workbook

		wb = load_workbook(BytesIO(report.to_xlsx(built)))
		self.assertIn("Lượt xem video", [c.value for c in wb["Bai dang"][1]])


class TestVideoFieldOnS3(MediaCase):
	"""Ô video / tài liệu của bài chuyển lên kho S3 khi lưu bài (như ô ảnh), quay lui được."""

	def setUp(self):
		super().setUp()
		configure()

	def test_video_field_moves_on_save_and_pulls_back(self):
		name = self.item()
		f = self.upload(
			ext=".mp4",
			content=MP4 + frappe.generate_hash(length=6).encode(),
			attach={"attached_to_doctype": ITEM, "attached_to_name": name, "attached_to_field": "video"},
		)
		self.assertTrue(f.file_url.startswith("/files/"), "tải qua ô: chưa chuyển lúc tải lên")
		doc = frappe.get_doc(ITEM, name)
		doc.video = f.file_url
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		url = frappe.db.get_value(ITEM, name, "video")
		self.assertTrue(s3_media.is_public_url(url))
		self.assertEqual(doc.video, url, "bản trả về giao diện có địa chỉ mới")
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		self.assertEqual(self.s3.objects[key][1], "video/mp4")
		self.assertFalse(os.path.exists(frappe.get_site_path("public", f.file_url.lstrip("/"))))
		self.assertEqual(s3_media.pending_count()["on_disk"], 0)
		disable()
		res = s3_media.pull_back(dry_run=0, item=name)
		self.assertEqual(res["errors"], [])
		self.assertTrue(frappe.db.get_value(ITEM, name, "video").startswith("/files/"))

	def test_private_video_field_stays_and_is_counted(self):
		name = self.item()
		f = self.upload(
			ext=".mp4",
			private=1,
			content=MP4 + b"rieng",
			attach={"attached_to_doctype": ITEM, "attached_to_name": name, "attached_to_field": "video"},
		)
		doc = frappe.get_doc(ITEM, name)
		doc.video = f.file_url
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertTrue(frappe.db.get_value(ITEM, name, "video").startswith("/private/files/"))
		self.assertEqual(self.s3.objects, {})


class TestCrmPageFlow(MediaCase):
	"""Trang bài trên /crm (bản vá giao diện 0002): tải tệp gắn đúng ô rồi `frappe.client.set_value`; hoặc chọn tệp đã
	đính kèm trên Desk. `item_detail` trả hai ô mới và danh sách tệp của bài (tên gốc theo địa chỉ hiện tại)."""

	def setUp(self):
		super().setUp()
		configure()

	def test_upload_to_field_then_set_value(self):
		from frappe.client import set_value

		from omni_crm.marketing import views

		name = self.item()
		f = self.upload(
			ext=".mp4",
			content=MP4 + frappe.generate_hash(length=6).encode(),
			attach={"attached_to_doctype": ITEM, "attached_to_name": name, "attached_to_field": "video"},
		)
		set_value(ITEM, name, {"video": f.file_url, "caption": "Chữ đang sửa dở"})
		frappe.db.commit()
		url = frappe.db.get_value(ITEM, name, "video")
		self.assertTrue(s3_media.is_public_url(url), "lưu bài thì video chuyển lên kho S3")
		info = views.item_detail(name)
		self.assertEqual(info["item"]["video"], url)
		self.assertIn("document", info["item"])
		self.assertEqual(info["item"]["caption"], "Chữ đang sửa dở")
		row = next(r for r in info["files"] if r.file_url == url)
		self.assertEqual(row.file_name, f.file_name)
		self.assertEqual(row.attached_to_field, "video")
		self.assertEqual(row.is_private, 0)

	def test_pick_desk_attachment_already_on_s3(self):
		from frappe.client import set_value

		from omni_crm.marketing import views

		name = self.item()
		f = self.upload(
			ext=".mp4",
			content=MP4 + frappe.generate_hash(length=6).encode(),
			attach={"attached_to_doctype": ITEM, "attached_to_name": name},
		)
		moved = frappe.db.get_value("File", f.name, "file_url")
		self.assertTrue(s3_media.is_public_url(moved), "thanh Đính kèm: chuyển ngay khi tải lên (mức 2)")
		self.assertIn(moved, [r.file_url for r in views.item_detail(name)["files"]])
		set_value(ITEM, name, {"video": moved})
		frappe.db.commit()
		self.assertEqual(
			frappe.db.get_value(ITEM, name, "video"), moved, "không chuyển lại, không đổi địa chỉ"
		)
		set_value(ITEM, name, {"caption": "Lưu lại lần nữa"})
		frappe.db.commit()
		self.assertEqual(frappe.db.get_value(ITEM, name, "video"), moved)
		self.assertEqual(frappe.db.get_value("File", f.name, "file_url"), moved)
