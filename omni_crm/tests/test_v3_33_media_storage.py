"""ADR 0033: ảnh bài marketing (Omni Content Item) trên kho S3 (MinIO).

Phần lớn test dùng kho S3 giả trong bộ nhớ. `TestS3Real` chạy với boto3 thật khi có biến môi trường
OMNI_S3_TEST_ENDPOINT (ví dụ moto_server hoặc MinIO thử), OMNI_S3_TEST_BUCKET, tùy chọn OMNI_S3_TEST_KEY/SECRET/CA.
Kho ảnh commit sau mỗi bài (job nền), nên test tự dọn dữ liệu theo tiền tố.
"""

from __future__ import annotations

import os
import tempfile
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import rules, s3_media
from omni_crm.marketing.publisher import content
from omni_crm.marketing.publisher.errors import PublishValidationError

PREFIX = "TM333"
ITEM = s3_media.ITEM
TARGET = s3_media.TARGET
SETTINGS = s3_media.SETTINGS
# PNG 1x1 hợp lệ; mỗi test thêm byte khác nhau để Frappe không gộp tệp theo hash giữa các test
PNG = bytes.fromhex(
	"89504e470d0a1a0a0000000d4948445200000001000000010806000000"
	"1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def cleanup() -> None:
	frappe.db.rollback()
	frappe.set_user("Administrator")
	camps = frappe.get_all("Omni Campaign", {"campaign_name": ["like", f"{PREFIX}%"]}, pluck="name")
	items = frappe.get_all(ITEM, {"campaign": ["in", camps or [""]]}, pluck="name")
	frappe.db.delete(TARGET, {"content_item": ["in", items or [""]]})
	frappe.db.delete("Omni Content Media", {"parent": ["in", items or [""]]})
	frappe.db.delete(ITEM, {"name": ["in", items or [""]]})
	frappe.db.delete("Omni Campaign", {"name": ["in", camps or [""]]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	for f in frappe.get_all("File", {"file_name": ["like", f"{PREFIX.lower()}%"]}, ["name", "file_url"]):
		if str(f.file_url or "").startswith("/files/"):
			try:
				os.remove(frappe.get_site_path("public", f.file_url.lstrip("/")))
			except OSError:
				pass
		frappe.db.delete("File", {"name": f.name})
	frappe.db.sql("delete from tabSingles where doctype=%s", SETTINGS)
	frappe.db.delete("__Auth", {"doctype": SETTINGS})
	frappe.db.commit()
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def configure(enabled=1, **extra) -> None:
	doc = frappe.get_single(SETTINGS)
	doc.update(
		{
			"enabled": enabled,
			"endpoint_url": "http://s3.test:9000",
			"bucket": "crm-media",
			"access_key": "crm-yhct",
			"secret_key": "khong-that",
			"prefix": "omni-content-item/",
			"verify_tls": 1,
			**extra,
		}
	)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def disable() -> None:
	"""Chỉ tắt công tắc, giữ endpoint/bucket/khóa."""
	doc = frappe.get_single(SETTINGS)
	doc.enabled = 0
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_document_cache(SETTINGS, SETTINGS)


class FakeS3:
	"""Kho S3 giả: dict khóa -> (bytes, Content-Type); `down` = MinIO ngừng."""

	def __init__(self):
		self.objects: dict[str, tuple[bytes, str]] = {}
		self.down = False

	def _check(self):
		if self.down:
			raise s3_media.S3Error("Kho ảnh S3: ghi ảnh không được (EndpointConnectionError)")

	def put(self, key, body, content_type):
		self._check()
		self.objects[key] = (body, content_type)

	def get(self, key):
		self._check()
		if key not in self.objects:
			raise s3_media.S3Error("Kho ảnh S3: đọc ảnh không được (NoSuchKey)")
		return self.objects[key]

	def delete(self, key):
		self._check()
		self.objects.pop(key, None)


class MediaCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup()
		self.s3 = FakeS3()
		for name, fn in (
			("put_object", self.s3.put),
			("get_object", self.s3.get),
			("delete_object", self.s3.delete),
		):
			mock.patch.object(s3_media, name, side_effect=fn).start()
		self.addCleanup(mock.patch.stopall)
		self.addCleanup(cleanup)
		self.n = 0

	def upload(self, private=0, attach=None, ext=".png", content=None) -> frappe._dict:
		"""Tải một ảnh như giao diện /crm (công khai, chưa gắn vào đâu)."""
		self.n += 1
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{PREFIX.lower()}-anh {self.n}-{frappe.generate_hash(length=4)}{ext}",
				"content": content or (PNG + frappe.generate_hash(length=8).encode()),
				"is_private": private,
				**(attach or {}),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return doc

	def item(self, media=None, extra=()) -> str:
		camp = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PREFIX} {frappe.generate_hash(length=6)}",
				"platform": "Đa kênh",
				"status": "Đang chạy",
			}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": camp.name,
				"title": "Bài thử ảnh",
				"caption": "Miyano giới thiệu SPD",
				"media": media,
				"media_items": [{"image": u} for u in extra],
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name

	def path_of(self, url: str) -> str:
		return frappe.get_site_path("public", url.lstrip("/"))

	def target(self, item: str, status: str, minutes: int = 60) -> str:
		ch = frappe.db.get_value("Omni Channel", {"channel_name": f"{PREFIX}-zalo"}, "name")
		if not ch:
			ch = (
				frappe.get_doc(
					{
						"doctype": "Omni Channel",
						"channel_name": f"{PREFIX}-zalo",
						"channel_type": "Zalo OA",
						"zalo_app_id": "app-1",
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		t = frappe.get_doc(
			{
				"doctype": TARGET,
				"content_item": item,
				"platform": rules.ZALO,
				"channel_account": ch,
				"post_format": "zalo_article",
				"scheduled_at": add_to_date(now_datetime(), minutes=minutes),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(TARGET, t.name, "status", status)
		frappe.db.commit()
		return t.name


class TestConfig(MediaCase):
	def test_tls_param_and_ca_bundle(self):
		self.assertIs(s3_media.tls_verify_param(None, ""), True)
		self.assertIs(s3_media.tls_verify_param(0, "/etc/x.crt"), False)
		with self.assertRaisesRegex(s3_media.S3Error, "Không thấy tệp CA"):
			s3_media.tls_verify_param(1, "/khong/co/ca.crt")
		with tempfile.NamedTemporaryFile(suffix=".crt") as fh:
			self.assertEqual(s3_media.tls_verify_param(1, fh.name), fh.name)

	def test_enable_needs_endpoint_bucket_key(self):
		doc = frappe.get_single(SETTINGS)
		doc.enabled = 1
		with self.assertRaisesRegex(frappe.ValidationError, "trước khi bật"):
			doc.save(ignore_permissions=True)

	def test_off_by_default_nothing_moves(self):
		f = self.upload()
		name = self.item(media=f.file_url)
		self.assertFalse(s3_media.enabled())
		self.assertEqual(frappe.db.get_value(ITEM, name, "media"), f.file_url)
		self.assertEqual(self.s3.objects, {})

	def test_check_storage_api_admin_only(self):
		from omni_crm.api import media

		configure(enabled=0)
		self.assertTrue(media.check_storage()["ok"])
		self.assertEqual(self.s3.objects, {})  # object thử đã xóa
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			media.check_storage()


class TestOffload(MediaCase):
	def setUp(self):
		super().setUp()
		configure()

	def test_save_moves_cover_and_extra_images(self):
		cover, extra = self.upload(), self.upload()
		name = self.item(media=cover.file_url, extra=[extra.file_url])
		doc = frappe.get_doc(ITEM, name)
		self.assertTrue(s3_media.is_public_url(doc.media))
		self.assertTrue(s3_media.is_public_url(doc.media_items[0].image))
		self.assertEqual(len(self.s3.objects), 2)
		for f, url in ((cover, doc.media), (extra, doc.media_items[0].image)):
			row = frappe.db.get_value(
				"File", f.name, ["file_url", s3_media.KEY_FIELD, "content_hash"], as_dict=True
			)
			self.assertEqual(row.file_url, url)
			self.assertTrue(row.get(s3_media.KEY_FIELD).startswith(f"omni-content-item/{name}/{f.name}"))
			self.assertIsNone(row.content_hash)
			self.assertFalse(os.path.exists(self.path_of(f.file_url)), "bản trên đĩa phải bị xóa sau commit")
			self.assertEqual(self.s3.objects[row.get(s3_media.KEY_FIELD)][1], "image/png")

	def test_private_and_foreign_files_are_not_moved(self):
		other = {"attached_to_doctype": "User", "attached_to_name": "Administrator"}
		private, foreign, cover_src = (
			self.upload(private=1),
			self.upload(attach=other),
			self.upload(attach=other),
		)
		name = self.item(media=cover_src.file_url, extra=[private.file_url, foreign.file_url])
		doc = frappe.get_doc(ITEM, name)
		self.assertEqual(doc.media_items[0].image, private.file_url, "ảnh riêng tư không bao giờ chuyển")
		self.assertEqual(
			doc.media_items[1].image, foreign.file_url, "ảnh thêm của tài liệu khác không chuyển"
		)
		# Ảnh bìa: Frappe tự tạo dòng File gắn vào bài (attach_files_to_document), bài có bản riêng trên S3;
		# dòng File của tài liệu kia giữ nguyên, tệp trên đĩa còn
		self.assertTrue(s3_media.is_public_url(doc.media))
		self.assertEqual(frappe.db.get_value("File", cover_src.name, "file_url"), cover_src.file_url)
		self.assertTrue(os.path.exists(self.path_of(cover_src.file_url)))
		self.assertTrue(os.path.exists(self.path_of(foreign.file_url)))
		self.assertEqual(len(self.s3.objects), 1)

	def test_shared_url_keeps_disk_until_last_user_moves(self):
		f = self.upload()
		configure(enabled=0)
		a, b = self.item(media=f.file_url), self.item(media=f.file_url)
		configure(enabled=1)
		frappe.get_doc(ITEM, a).save(ignore_permissions=True)
		frappe.db.commit()
		self.assertTrue(s3_media.is_public_url(frappe.db.get_value(ITEM, a, "media")))
		self.assertEqual(frappe.db.get_value(ITEM, b, "media"), f.file_url)
		self.assertTrue(os.path.exists(self.path_of(f.file_url)), "bài b còn dùng tệp trên đĩa")
		s3_media.push_pending()
		self.assertTrue(s3_media.is_public_url(frappe.db.get_value(ITEM, b, "media")))
		self.assertFalse(os.path.exists(self.path_of(f.file_url)))

	def test_svg_and_other_types_stay_on_disk(self):
		svg = self.upload(
			ext=".svg", content=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
		)
		png = self.upload()
		name = self.item(media=svg.file_url, extra=[png.file_url])
		doc = frappe.get_doc(ITEM, name)
		self.assertEqual(doc.media, svg.file_url, "SVG không chuyển: phát inline trên tên miền CRM là XSS")
		self.assertTrue(s3_media.is_public_url(doc.media_items[0].image))
		self.assertEqual(len(self.s3.objects), 1)
		self.assertEqual(next(iter(self.s3.objects.values()))[1], "image/png")
		counts = s3_media.pending_count()
		self.assertEqual((counts["on_disk"], counts["kept_other_type"]), (0, 1))
		s3_media.push_pending()  # không thử lại SVG, không lỗi
		self.assertEqual(frappe.db.get_value(ITEM, name, "media"), svg.file_url)

	def test_s3_down_keeps_image_on_disk_then_job_retries(self):
		self.s3.down = True
		f = self.upload()
		name = self.item(media=f.file_url)
		self.assertEqual(frappe.db.get_value(ITEM, name, "media"), f.file_url)
		self.assertTrue(os.path.exists(self.path_of(f.file_url)))
		s3_media.push_pending()  # vẫn lỗi: không đổi gì
		self.assertEqual(frappe.db.get_value(ITEM, name, "media"), f.file_url)
		self.s3.down = False
		s3_media.push_pending()
		self.assertTrue(s3_media.is_public_url(frappe.db.get_value(ITEM, name, "media")))
		self.assertEqual(s3_media.pending_count()["on_disk"], 0)

	def test_busy_or_soon_targets_are_skipped(self):
		configure(enabled=0)
		f, g = self.upload(), self.upload()
		busy, soon = self.item(media=f.file_url), self.item(media=g.file_url)
		self.target(busy, "Đang xử lý")
		self.target(soon, "Đã lên lịch", minutes=5)
		configure(enabled=1)
		s3_media.push_pending()
		self.assertEqual(frappe.db.get_value(ITEM, busy, "media"), f.file_url)
		self.assertEqual(frappe.db.get_value(ITEM, soon, "media"), g.file_url)
		frappe.db.set_value(
			TARGET, {"content_item": soon}, "scheduled_at", add_to_date(now_datetime(), hours=3)
		)
		frappe.db.commit()
		s3_media.push_pending()
		self.assertTrue(s3_media.is_public_url(frappe.db.get_value(ITEM, soon, "media")))

	def test_score_not_reset_by_move(self):
		f = self.upload()
		configure(enabled=0)
		name = self.item(media=f.file_url)
		frappe.db.set_value(ITEM, name, "quality_score", 90)
		frappe.db.commit()
		configure(enabled=1)
		s3_media.push_pending()
		self.assertEqual(frappe.db.get_value(ITEM, name, "quality_score"), 90)


def fake_request(path: str) -> None:
	"""Request werkzeug thật cho bộ định tuyến website của Frappe (cần environ)."""
	from werkzeug.test import EnvironBuilder
	from werkzeug.wrappers import Request

	frappe.local.request = Request(EnvironBuilder(path=path, method="GET").get_environ())


def tampered(url: str) -> str:
	"""Cùng tệp, chữ ký khác."""
	head, sig = url[: url.rfind("-") + 1], url[url.rfind("-") + 1 :].split(".")[0]
	return head + ("f" * 32 if sig != "f" * 32 else "0" * 32) + ".png"


class TestServeAndPublish(MediaCase):
	def setUp(self):
		super().setUp()
		old_request = getattr(frappe.local, "request", None)
		self.addCleanup(lambda: setattr(frappe.local, "request", old_request))
		configure()
		f = self.upload()
		self.name = self.item(media=f.file_url)
		self.url = frappe.db.get_value(ITEM, self.name, "media")
		self.file = f.name

	def test_signature_is_checked(self):
		self.assertEqual(s3_media.resolve(self.url)[0], self.file)
		self.assertEqual(s3_media.resolve(self.url.lstrip("/"))[0], self.file)
		bad = tampered(self.url)
		self.assertIsNone(s3_media.resolve(bad))
		self.assertIsNone(s3_media.resolve("/omni-media/khongco-" + "0" * 32 + ".png"))
		self.assertIsNone(s3_media.resolve("/files/a.png"))

	def test_publisher_gets_absolute_public_url(self):
		got = content.public_image_url(self.url)
		self.assertTrue(got.startswith(("http://", "https://")))
		self.assertTrue(got.endswith(self.url))
		with self.assertRaisesRegex(PublishValidationError, "Không tìm thấy ảnh"):
			content.public_image_url("/omni-media/khongco-" + "0" * 32 + ".png")

	def test_build_payload_uses_s3_image(self):
		t = self.target(self.name, "Đã lên lịch")
		payload = content.build(t)
		self.assertEqual(len(payload.images), 1)
		self.assertTrue(payload.images[0].endswith(self.url))

	def render(self, path):
		from frappe.website.path_resolver import PathResolver

		frappe.set_user("Guest")
		fake_request(path)
		_endpoint, renderer = PathResolver(path.lstrip("/")).resolve()
		self.assertIsInstance(renderer, s3_media.MediaRenderer)
		return renderer.render()

	def test_guest_gets_image(self):
		res = self.render(self.url)
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.mimetype, "image/png")
		self.assertTrue(res.get_data().startswith(PNG))
		self.assertIn("max-age", res.headers["Cache-Control"])

	def test_only_raster_types_are_served(self):
		key = frappe.db.get_value("File", self.file, s3_media.KEY_FIELD)
		self.s3.objects[key] = (b"<svg/>", "image/svg+xml")  # dù chữ ký đúng
		self.assertEqual(self.render(self.url).status_code, 404)
		self.s3.objects[key] = (b"<html/>", "text/html")
		self.assertEqual(self.render(self.url).status_code, 404)

	def test_bad_signature_404_and_s3_down_503(self):
		bad = tampered(self.url)
		self.assertEqual(self.render(bad).status_code, 404)
		self.s3.down = True
		self.assertEqual(self.render(self.url).status_code, 503)

	def test_full_request_as_guest(self):
		from frappe.website.serve import get_response

		frappe.set_user("Guest")
		fake_request(self.url)
		res = get_response(self.url)
		self.assertEqual(res.status_code, 200)
		self.assertTrue(res.get_data().startswith(PNG))

	def test_delete_file_removes_object(self):
		key = frappe.db.get_value("File", self.file, s3_media.KEY_FIELD)
		self.assertIn(key, self.s3.objects)
		frappe.delete_doc("File", self.file, ignore_permissions=True)
		self.assertNotIn(key, self.s3.objects)


class TestPullBack(MediaCase):
	def setUp(self):
		super().setUp()
		configure()
		f, g = self.upload(), self.upload()
		self.name = self.item(media=f.file_url, extra=[g.file_url])

	def test_dry_run_counts_and_refuses_while_enabled(self):
		self.assertEqual(s3_media.pull_back(item=self.name), {"files": 2, "pulled": 0, "errors": []})
		with self.assertRaisesRegex(s3_media.S3Error, "Tắt"):
			s3_media.pull_back(dry_run=0, item=self.name)

	def test_pull_back_restores_public_files(self):
		configure(enabled=0)
		res = s3_media.pull_back(dry_run=0, item=self.name)
		self.assertEqual(res, {"files": 2, "pulled": 2, "errors": []})
		doc = frappe.get_doc(ITEM, self.name)
		for url in (doc.media, doc.media_items[0].image):
			self.assertTrue(url.startswith("/files/"), url)
			self.assertTrue(os.path.exists(self.path_of(url)))
			row = frappe.db.get_value(
				"File", {"file_url": url}, ["is_private", s3_media.KEY_FIELD], as_dict=True
			)
			self.assertEqual(row.is_private, 0)
			self.assertIsNone(row.get(s3_media.KEY_FIELD))
		self.assertIn("/files/", content.public_image_url(doc.media))  # bộ đăng nhận lại ảnh trên đĩa
		self.assertEqual(len(self.s3.objects), 2, "object trên S3 được giữ lại")


@__import__("unittest").skipUnless(os.environ.get("OMNI_S3_TEST_ENDPOINT"), "cần OMNI_S3_TEST_ENDPOINT")
class TestS3Real(FrappeTestCase):
	"""boto3 thật tới moto_server hoặc MinIO thử: kiểm kho, chuyển ảnh, phát ảnh, kéo về."""

	def setUp(self):
		frappe.set_user("Administrator")
		cleanup()
		self.addCleanup(cleanup)
		old_request = getattr(frappe.local, "request", None)
		self.addCleanup(lambda: setattr(frappe.local, "request", old_request))
		ca = os.environ.get("OMNI_S3_TEST_CA") or ""
		configure(
			endpoint_url=os.environ["OMNI_S3_TEST_ENDPOINT"],
			bucket=os.environ.get("OMNI_S3_TEST_BUCKET", "crm-media"),
			access_key=os.environ.get("OMNI_S3_TEST_KEY", "test"),
			secret_key=os.environ.get("OMNI_S3_TEST_SECRET", "test"),
			ca_bundle=ca,
			verify_tls=1 if ca or os.environ["OMNI_S3_TEST_ENDPOINT"].startswith("https") else 0,
		)

	def test_round_trip(self):
		self.assertTrue(s3_media.check_storage()["ok"])
		case = MediaCase()
		case.n = 0
		f = case.upload()
		name = case.item(media=f.file_url)
		url = frappe.db.get_value(ITEM, name, "media")
		self.assertTrue(s3_media.is_public_url(url))
		res = TestServeAndPublish.render(self, url)
		self.assertEqual(res.status_code, 200)
		self.assertTrue(res.get_data().startswith(PNG))
		frappe.set_user("Administrator")
		disable()
		res = s3_media.pull_back(dry_run=0, item=name)
		self.assertEqual(res["pulled"], 1, res)
		self.assertTrue(frappe.db.get_value(ITEM, name, "media").startswith("/files/"))
