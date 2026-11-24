"""ADR 0035 (Zalo): bài viết video (zalo_video) và bài viết có link tới PDF (zalo_pdf_link) lên Zalo OA, số liệu bài viết.

Giả lập client Zalo, không gọi mạng. Luồng API theo lần thử trên OA thật 23/11/2026 (spd/docs/35 mục 11).
"""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import approval, rules
from omni_crm.marketing.publisher import content, zalo
from omni_crm.marketing.publisher.errors import ProviderError, PublishValidationError
from omni_crm.tests.test_v3_10_publisher import PREFIX as PUB_PREFIX
from omni_crm.tests.test_v3_33_media_storage import ITEM, PNG
from omni_crm.tests.test_v3_35_video_pdf import VideoCase


class ZaloCase(VideoCase):
	def zalo_item(self, fmt: str, video=None, document=None, cover=True) -> tuple[str, str]:
		camp = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PUB_PREFIX} zalo {frappe.generate_hash(length=6)}",
				"platform": "Đa kênh",
				"status": "Đang chạy",
			}
		).insert(ignore_permissions=True)
		item = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": camp.name,
				"title": "Bài video Zalo thử",
				"caption": "Miyano giới thiệu SPD\n\nĐăng ký demo",
				"media": self.file(".png", content=PNG + frappe.generate_hash(length=6).encode())
				if cover
				else None,
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
				"platform": rules.ZALO,
				"channel_account": self.zalo,
				"post_format": fmt,
				"scheduled_at": add_to_date(now_datetime(), minutes=5),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return item.name, target.name


class TestZaloFormats(ZaloCase):
	def test_new_formats_are_zalo_only(self):
		for fmt in ("zalo_video", "zalo_pdf_link"):
			self.assertTrue(rules.check_format(rules.ZALO, fmt))
			self.assertFalse(rules.check_format(rules.FACEBOOK, fmt))

	def test_approve_needs_file_and_cover(self):
		item, _t = self.zalo_item("zalo_video")
		approval.submit(item)
		with self.assertRaisesRegex(frappe.ValidationError, "cần video đăng kèm"):
			approval.approve(item)
		item2, _t2 = self.zalo_item("zalo_pdf_link", document=self.file(".pdf"), cover=False)
		approval.submit(item2)
		with self.assertRaises(frappe.ValidationError):
			approval.approve(item2)  # bài viết Zalo cần ảnh bìa
		item3, _t3 = self.zalo_item("zalo_video", video=self.file(".mp4"))
		approval.submit(item3)
		approval.approve(item3)
		self.assertEqual(frappe.db.get_value(ITEM, item3, "status"), rules.I_SCHEDULED)

	def test_build_video_payload_keeps_crm_file_and_cover(self):
		url = self.file(".mp4")
		_item, target = self.zalo_item("zalo_video", video=url)
		p = content.build(target)
		self.assertEqual(p.video_file, url)
		self.assertTrue(p.video.startswith(("http://", "https://")))
		self.assertEqual(len(p.images), 1, "ảnh bìa làm ảnh đại diện bài video")
		self.assertEqual(p.document, "")

	def test_zalo_video_must_be_mp4(self):
		_item, target = self.zalo_item("zalo_video", video=self.file(".mov"))
		with self.assertRaisesRegex(PublishValidationError, "Video phải là tệp MP4$"):
			content.build(target)

	def test_pdf_link_article_has_pdf_and_tracking_links(self):
		url = self.file(".pdf")
		_item, target = self.zalo_item("zalo_pdf_link", document=url)
		frappe.db.set_value("Omni Content Target", target, "tracking_url", "https://x.vn/?utm_source=zalo")
		p = content.build(target)
		body = zalo.article_body(p)
		html = body["body"][0]["content"]
		self.assertEqual(body["type"], "normal")
		self.assertIn(p.document.replace("&", "&amp;"), html)
		self.assertIn("Xem tài liệu PDF", html)
		self.assertLess(
			html.index("Xem tài liệu PDF"), html.index("utm_source=zalo"), "link PDF trước link theo dõi"
		)
		self.assertEqual(body["tracking_link"], "https://x.vn/?utm_source=zalo")

	def test_plain_article_has_no_pdf_link(self):
		_item, target = self.zalo_item("zalo_article", document=self.file(".pdf"))
		self.assertNotIn("Xem tài liệu PDF", zalo.article_body(content.build(target))["body"][0]["content"])

	def test_video_bytes_reads_file_and_checks_size(self):
		url = self.file(".mp4")
		name, data = zalo.video_bytes(url)
		self.assertTrue(name.endswith(".mp4"))
		self.assertGreater(len(data), 10)
		with mock.patch.object(zalo, "VIDEO_MAX_BYTES", 5):
			with self.assertRaisesRegex(PublishValidationError, "Zalo nhận tối đa"):
				zalo.video_bytes(url)
		with self.assertRaisesRegex(PublishValidationError, "Không tìm thấy video công khai"):
			zalo.video_bytes("/files/khong-co.mp4")


class FakeClient:
	def __init__(self, replies):
		self.replies, self.calls = list(replies), []

	def _request(self, method, path, **kw):
		self.calls.append((method, path, kw))
		reply = self.replies.pop(0)
		if isinstance(reply, Exception):
			raise reply
		return reply


def video_payload():
	return content.Payload(
		target="t1",
		platform=rules.ZALO,
		post_format="zalo_video",
		channel="kenh-zalo",
		title="Video SPD",
		caption="Miyano giới thiệu SPD " * 40,
		tracking_url="https://yhct.miyano.com.vn/spd/?utm_source=zalo",
		images=["https://yhct.miyano.com.vn/omni-media/a-" + "0" * 32 + ".jpg"],
		video="https://yhct.miyano.com.vn/omni-media/b-" + "1" * 32 + ".mp4",
		video_file="/omni-media/b-" + "1" * 32 + ".mp4",
	)


class TestZaloVideoAdapter(FrappeTestCase):
	def setUp(self):
		mock.patch.object(zalo, "video_bytes", return_value=("clip.mp4", b"\x00mp4")).start()
		self.addCleanup(mock.patch.stopall)

	def test_upload_wait_then_create_video_article(self):
		fake = FakeClient(
			[
				{"token": "vt"},
				{"status": 3, "convert_percent": 0},
				{"status": 1, "video_id": "vid1"},
				{"token": "tk"},
				{"article_id": "art-1"},
			]
		)
		with mock.patch.object(zalo, "_client", return_value=fake):
			res = zalo.ZaloAdapter().publish(video_payload(), sleep=lambda s: None)
		self.assertEqual(res.external_post_id, "art-1")
		prepare, verify, _v2, create, _check = fake.calls
		self.assertEqual(prepare[1], "/v2.0/article/upload_video/preparevideo")
		self.assertEqual(prepare[2]["files"]["file"], ("clip.mp4", b"\x00mp4", "video/mp4"))
		self.assertEqual(prepare[2]["timeout"], 120)
		self.assertEqual((verify[0], verify[1]), ("GET", "/v2.0/article/upload_video/verify"))
		self.assertEqual(verify[2]["headers"], {"token": "vt"})
		body = create[2]["body"]
		self.assertEqual((body["type"], body["video_id"], body["status"]), ("video", "vid1", "show"))
		self.assertEqual(body["avatar"], video_payload().images[0])
		self.assertLessEqual(len(body["description"].encode()), 300)

	def test_broken_video_stops_before_creating_article(self):
		fake = FakeClient([{"token": "vt"}, {"status": 4, "status_message": "Video is invalid"}])
		with mock.patch.object(zalo, "_client", return_value=fake):
			with self.assertRaisesRegex(
				PublishValidationError, "Zalo không nhận video \\(lỗi\\): Video is invalid"
			):
				zalo.ZaloAdapter().publish(video_payload(), sleep=lambda s: None)
		self.assertEqual(len(fake.calls), 2, "không tạo bài")

	def test_slow_video_is_retryable_not_ambiguous(self):
		fake = FakeClient([{"token": "vt"}] + [{"status": 3}] * 100)
		with mock.patch.object(zalo, "_client", return_value=fake):
			with self.assertRaises(ProviderError) as ctx:
				zalo.ZaloAdapter().publish(video_payload(), sleep=lambda s: None)
		self.assertFalse(ctx.exception.ambiguous, "chưa tạo bài: đăng lại không trùng")

	def test_reconcile_video_lists_video_articles(self):
		fake = FakeClient([{"medias": [{"id": "art-7", "title": "Video SPD", "create_date": 9e15}]}])
		with mock.patch.object(zalo, "_client", return_value=fake):
			res = zalo.ZaloAdapter().reconcile(video_payload())
		self.assertEqual(res.external_post_id, "art-7")
		self.assertEqual(fake.calls[0][2]["params"]["type"], "video")

	def test_insights_from_article_detail(self):
		detail = {"type": "video", "total_view": 40, "total_like": 3, "total_comment": 2, "total_share": 1}
		fake = FakeClient([detail, {**detail, "type": "normal"}])
		with mock.patch.object(zalo, "_client", return_value=fake):
			video = zalo.ZaloAdapter().fetch_insights("kenh-zalo", "art-1")
			normal = zalo.ZaloAdapter().fetch_insights("kenh-zalo", "art-2")
		self.assertEqual(fake.calls[0][1], "/v2.0/article/getdetail")
		self.assertEqual(fake.calls[0][2]["params"], {"id": "art-1"})
		self.assertEqual(
			(video["impressions"], video["likes"], video["comments"], video["shares"], video["video_views"]),
			(40, 3, 2, 1, 40),
		)
		self.assertIsNone(video["reach"])
		self.assertIsNone(normal["video_views"])
		self.assertEqual(normal["impressions"], 40)


class TestZaloClientHeaders(FrappeTestCase):
	def test_extra_header_is_sent_and_cannot_replace_access_token(self):
		from omni_crm.channels.zalo.client import ZaloClient

		resp = mock.Mock(status_code=200)
		resp.json.return_value = {"error": 0, "data": {"status": 1}}
		with (
			mock.patch("omni_crm.flags.sandbox", return_value=False),
			mock.patch.object(ZaloClient, "_access_token", return_value="tok"),
			mock.patch("requests.request", return_value=resp) as req,
		):
			data = ZaloClient("k")._request("GET", "/x", headers={"token": "vt", "access_token": "khac"})
		self.assertEqual(data, {"status": 1})
		sent = req.call_args.kwargs["headers"]
		self.assertEqual((sent["token"], sent["access_token"]), ("vt", "tok"))
