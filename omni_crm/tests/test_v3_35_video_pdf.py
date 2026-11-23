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

	def test_video_insights_only_reactions_and_comments(self):
		body = {"reactions": {"summary": {"total_count": 4}}, "comments": {"summary": {"total_count": 2}}}
		with mock.patch("requests.request", return_value=FakeResp(200, body)) as req:
			out = self.ad.fetch_insights("kenh", "1548814027082620")
		self.assertEqual((out["likes"], out["comments"]), (4, 2))
		self.assertIsNone(out["shares"])
		self.assertEqual(req.call_count, 1, "không gọi insights của bài Trang cho video")


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
