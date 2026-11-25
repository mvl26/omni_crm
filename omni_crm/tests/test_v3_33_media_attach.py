"""Mức 2 (ADR 0033, tài liệu 33): tệp đính kèm công khai của bài marketing trên kho S3.

Dùng kho S3 giả của `test_v3_33_media_storage` (thêm đọc từng đoạn cho video). Tệp đính kèm = File gắn vào
Omni Content Item qua thanh "Đính kèm" (không có `attached_to_field`).
"""

from __future__ import annotations

import os
import re
from unittest import mock

import frappe

from omni_crm.marketing import kb, planner, s3_media
from omni_crm.tests.test_v3_33_media_storage import (
	ITEM,
	PNG,
	PREFIX,
	FakeS3,
	MediaCase,
	configure,
	disable,
)


def pdf_bytes(tag: str | None = None) -> bytes:
	"""PDF hợp lệ một trang (Frappe đọc PDF để chặn JavaScript), khác nhau theo `tag` để không bị gộp theo hash."""
	from io import BytesIO

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=10, height=10)
	writer.add_metadata({"/Title": tag or frappe.generate_hash(length=8)})
	buf = BytesIO()
	writer.write(buf)
	return buf.getvalue()


class FakeS3Parts(FakeS3):
	"""Thêm đọc một đoạn `bytes=a-b` như MinIO."""

	def get_part(self, key, byte_range):
		body, ctype = self.get(key)
		total = len(body)
		m = re.match(r"^bytes=(\d*)-(\d*)$", byte_range)
		if not m.group(1):
			start, end = max(total - int(m.group(2)), 0), total - 1
		else:
			start = int(m.group(1))
			end = min(int(m.group(2)), total - 1) if m.group(2) else total - 1
		if start >= total:
			raise s3_media.S3RangeError("ngoài tệp")
		return body[start : end + 1], ctype, f"bytes {start}-{end}/{total}"


def fake_request(path: str, headers: dict | None = None) -> None:
	from werkzeug.test import EnvironBuilder
	from werkzeug.wrappers import Request

	frappe.local.request = Request(
		EnvironBuilder(path=path, method="GET", headers=headers or {}).get_environ()
	)


class AttachCase(MediaCase):
	def setUp(self):
		super().setUp()
		self.s3 = FakeS3Parts()
		for name, fn in (
			("put_object", self.s3.put),
			("get_object", self.s3.get),
			("get_object_part", self.s3.get_part),
			("delete_object", self.s3.delete),
		):
			mock.patch.object(s3_media, name, side_effect=fn).start()
		old_request = getattr(frappe.local, "request", None)
		self.addCleanup(lambda: setattr(frappe.local, "request", old_request))
		configure()
		self.name = self.item()
		self.addCleanup(self._drop_comments)

	def _drop_comments(self):
		frappe.db.delete("Comment", {"reference_doctype": ITEM, "reference_name": self.name})
		frappe.db.commit()

	def attach(
		self, ext=".pdf", private=0, content=None, field=None, to=None, file_name=None
	) -> frappe._dict:
		"""Tải một tệp vào thanh "Đính kèm" của bài (hoặc tài liệu `to`)."""
		attach = {"attached_to_doctype": ITEM, "attached_to_name": self.name}
		if to:
			attach = {"attached_to_doctype": to[0], "attached_to_name": to[1]}
		if field:
			attach["attached_to_field"] = field
		body = content if content is not None else pdf_bytes()
		if file_name:
			doc = frappe.get_doc(
				{"doctype": "File", "file_name": file_name, "content": body, "is_private": private, **attach}
			).insert(ignore_permissions=True)
			frappe.db.commit()
			return doc
		return self.upload(private=private, attach=attach, ext=ext, content=body)

	def on_disk(self, file_name: str, private=0) -> bool:
		return os.path.exists(frappe.get_site_path("private" if private else "public", "files", file_name))

	def render(self, path, headers=None):
		from frappe.website.path_resolver import PathResolver

		frappe.set_user("Guest")
		fake_request(path, headers)
		_endpoint, renderer = PathResolver(path.lstrip("/")).resolve()
		self.assertIsInstance(renderer, s3_media.MediaRenderer)
		res = renderer.render()
		frappe.set_user("Administrator")
		return res


class TestMoveAttachments(AttachCase):
	def test_public_pdf_moves_on_upload(self):
		f = self.attach(".pdf")
		self.assertTrue(s3_media.is_public_url(f.file_url), "bản trả về trình tải lên có địa chỉ mới")
		self.assertEqual(frappe.db.get_value("File", f.name, "file_url"), f.file_url)
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		self.assertEqual(key, f"{s3_media._prefix()}{self.name}/{f.name}.pdf")
		self.assertEqual(self.s3.objects[key][1], "application/pdf")
		self.assertTrue(self.s3.objects[key][0].startswith(b"%PDF"))
		self.assertFalse(self.on_disk(f.file_name), "tệp trên đĩa đã xóa sau commit")
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": ITEM, "reference_name": self.name, "comment_type": "Attachment"},
			pluck="content",
		)
		self.assertTrue(comments, "Frappe ghi dòng 'Đã thêm tệp' trên dòng thời gian")
		self.assertTrue(all(f.file_url in c and "/files/" not in c for c in comments), comments)

	def test_kept_on_disk_private_unsafe_field_and_other_doctypes(self):
		private = self.attach(".pdf", private=1)
		self.assertTrue(
			private.file_url.startswith("/private/files/"), "riêng tư không bao giờ lên S3 dạng đọc được"
		)
		svg = self.attach(
			".svg", content=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
		)
		html = self.attach(".html", content=b"<script>alert(1)</script>")
		field = self.attach(".png", content=PNG + b"field", field="media")
		camp = frappe.db.get_value(ITEM, self.name, "campaign")
		other = self.attach(".pdf", to=("Omni Campaign", camp))
		for f in (svg, html, field, other):
			self.assertTrue(f.file_url.startswith("/files/"), f.file_name)
			self.assertFalse(frappe.db.get_value("File", f.name, s3_media.KEY_FIELD))
		self.assertEqual(self.s3.objects, {})
		counts = s3_media.pending_count()
		self.assertEqual(counts["attach_on_disk"], 0)
		self.assertEqual(counts["attach_kept_other_type"], 2, "svg, html")
		self.assertEqual(counts["attach_private"], 1)

	def test_field_upload_left_to_item_save(self):
		f = self.attach(".png", content=PNG + b"bia", field="media")
		self.assertTrue(f.file_url.startswith("/files/"))
		doc = frappe.get_doc(ITEM, self.name)
		doc.media = f.file_url
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertTrue(s3_media.is_public_url(frappe.db.get_value(ITEM, self.name, "media")))

	def test_s3_down_keeps_file_then_job_moves_it(self):
		self.s3.down = True
		frappe.local.message_log = []
		f = self.attach(".mp4", content=b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
		self.assertTrue(f.file_url.startswith("/files/"))
		self.assertTrue(self.on_disk(f.file_name))
		self.assertTrue(any("Tệp vẫn lưu trên máy chủ CRM" in str(m) for m in frappe.local.message_log))
		self.assertEqual(s3_media.pending_count()["attach_on_disk"], 1)
		self.s3.down = False
		s3_media.push_pending()
		url = frappe.db.get_value("File", f.name, "file_url")
		self.assertTrue(s3_media.is_public_url(url))
		self.assertEqual(next(iter(self.s3.objects.values()))[1], "video/mp4")
		self.assertEqual(s3_media.pending_count()["attach_on_disk"], 0)

	def test_too_big_stays_on_disk(self):
		with mock.patch.object(s3_media, "ATTACH_MAX_BYTES", 16):
			f = self.attach(".pdf")
			self.assertTrue(f.file_url.startswith("/files/"))
			self.assertEqual(s3_media.pending_count()["attach_kept_other_type"], 1)
			s3_media.push_pending()
			self.assertTrue(frappe.db.get_value("File", f.name, "file_url").startswith("/files/"))

	def test_off_switch_moves_nothing(self):
		disable()
		f = self.attach(".pdf")
		self.assertTrue(f.file_url.startswith("/files/"))
		s3_media.push_pending()
		self.assertEqual(self.s3.objects, {})


class TestServeAttachments(AttachCase):
	def test_pdf_is_a_download_with_vietnamese_name(self):
		f = self.attach(file_name=f"{PREFIX.lower()}-báo giá {frappe.generate_hash(length=4)}.pdf")
		self.assertTrue(s3_media.is_public_url(f.file_url))
		res = self.render(f.file_url)
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.mimetype, "application/pdf")
		disp = res.headers["Content-Disposition"]
		self.assertTrue(disp.startswith('attachment; filename="'), disp)
		self.assertIn("filename*=UTF-8''", disp)
		self.assertIn("b%C3%A1o%20gi%C3%A1", disp)
		self.assertNotIn("á", disp.split("filename*=")[0], "bản ASCII không có chữ có dấu")
		self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")

	def test_video_plays_inline_and_seeks(self):
		body = bytes(range(100))
		f = self.attach(".mp4", content=body)
		res = self.render(f.file_url)
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.mimetype, "video/mp4")
		self.assertNotIn("Content-Disposition", res.headers)
		self.assertEqual(res.headers["Accept-Ranges"], "bytes")
		self.assertEqual(res.get_data(), body)
		part = self.render(f.file_url, {"Range": "bytes=10-19"})
		self.assertEqual(part.status_code, 206)
		self.assertEqual(part.get_data(), body[10:20])
		self.assertEqual(part.headers["Content-Range"], "bytes 10-19/100")
		self.assertEqual(self.render(f.file_url, {"Range": "bytes=500-"}).status_code, 416)
		self.assertEqual(
			self.render(f.file_url, {"Range": "bytes=1-2,5-6"}).status_code, 200, "nhiều đoạn: trả cả tệp"
		)

	def test_unsafe_content_type_never_served(self):
		f = self.attach(".pdf")
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		self.s3.objects[key] = (b"<html><script>alert(1)</script></html>", "text/html")
		self.assertEqual(self.render(f.file_url).status_code, 404)
		self.s3.objects[key] = (b"<svg/>", "image/svg+xml")
		self.assertEqual(self.render(f.file_url).status_code, 404)

	def test_delete_attachment_removes_object(self):
		f = self.attach(".xlsx", content=b"PK\x03\x04 thu")
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		self.assertIn(key, self.s3.objects)
		frappe.delete_doc("File", f.name, ignore_permissions=True)
		self.assertNotIn(key, self.s3.objects)

	def test_pull_back_restores_attachment_and_timeline_link(self):
		f = self.attach(".pdf")
		disable()
		res = s3_media.pull_back(dry_run=0)
		self.assertEqual(res["errors"], [])
		url = frappe.db.get_value("File", f.name, "file_url")
		self.assertTrue(url.startswith("/files/"))
		self.assertTrue(os.path.exists(frappe.get_site_path("public", url.lstrip("/"))))
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": ITEM, "reference_name": self.name, "comment_type": "Attachment"},
			pluck="content",
		)
		self.assertTrue(any(url in c for c in comments), comments)


class TestReadMovedFiles(AttachCase):
	"""Việc V8: kho kiến thức và trợ lý soạn bài đọc được tệp đã rời đĩa."""

	def test_kb_reads_from_s3(self):
		body = "Bảng giá SPD thử".encode()
		f = self.attach(".txt", content=body)
		self.assertTrue(s3_media.is_public_url(f.file_url))
		name, data = kb.file_bytes(f.file_url)
		self.assertEqual(data, body)
		self.assertTrue(name.endswith(".txt"))
		self.s3.down = True
		with self.assertRaisesRegex(ValueError, "Kho ảnh S3"):
			kb.file_bytes(f.file_url)

	def test_planner_attachment_of_moved_image(self):
		f = self.attach(".png", content=PNG + b"planner")
		self.assertTrue(s3_media.is_public_url(f.file_url))
		att = planner._attachment(f.file_url)
		self.assertEqual(att["kind"], "image")
		block = planner._image_block(att)
		self.assertIsNotNone(block)
		self.assertEqual(block["source"]["media_type"], "image/png")


@__import__("unittest").skipUnless(os.environ.get("OMNI_S3_TEST_ENDPOINT"), "cần OMNI_S3_TEST_ENDPOINT")
class TestS3RealAttach(AttachCase):
	"""boto3 thật tới moto_server hoặc MinIO thử: tệp đính kèm, đọc từng đoạn (Range), đoạn ngoài tệp (InvalidRange)."""

	def setUp(self):
		super().setUp()
		mock.patch.stopall()  # bỏ kho giả: dùng boto3 thật
		ca = os.environ.get("OMNI_S3_TEST_CA") or ""
		configure(
			endpoint_url=os.environ["OMNI_S3_TEST_ENDPOINT"],
			bucket=os.environ.get("OMNI_S3_TEST_BUCKET", "crm-media"),
			access_key=os.environ.get("OMNI_S3_TEST_KEY", "test"),
			secret_key=os.environ.get("OMNI_S3_TEST_SECRET", "test"),
			ca_bundle=ca,
			verify_tls=1 if ca or os.environ["OMNI_S3_TEST_ENDPOINT"].startswith("https") else 0,
		)
		try:
			s3_media._client().create_bucket(Bucket=s3_media._bucket())
		except Exception:
			pass  # đã có

	def test_video_range_and_pdf_download(self):
		body = bytes(range(256)) * 4
		video = self.attach(".mp4", content=body)
		self.assertTrue(s3_media.is_public_url(video.file_url))
		full = self.render(video.file_url)
		self.assertEqual((full.status_code, full.mimetype), (200, "video/mp4"))
		self.assertEqual(full.get_data(), body)
		part = self.render(video.file_url, {"Range": "bytes=100-199"})
		self.assertEqual(part.status_code, 206)
		self.assertEqual(part.get_data(), body[100:200])
		self.assertEqual(part.headers["Content-Range"], f"bytes 100-199/{len(body)}")
		self.assertEqual(self.render(video.file_url, {"Range": "bytes=99999-"}).status_code, 416)
		pdf = self.attach(".pdf")
		res = self.render(pdf.file_url)
		self.assertEqual((res.status_code, res.mimetype), (200, "application/pdf"))
		self.assertTrue(res.headers["Content-Disposition"].startswith("attachment;"))
		keys = [frappe.db.get_value("File", f.name, s3_media.KEY_FIELD) for f in (video, pdf)]
		for f in (video, pdf):
			frappe.delete_doc("File", f.name, ignore_permissions=True)
		listed = {o["key"] for o in s3_media.list_objects()}
		self.assertFalse(set(keys) & listed, "xóa File thì xóa object")
