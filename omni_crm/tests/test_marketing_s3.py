"""Ảnh bài đăng trên kho S3 (ADR 0030): đẩy ảnh sau khi lưu bài, đường tải kiểm quyền, job đăng đọc ảnh từ S3, MinIO lỗi
thì giữ ảnh trên đĩa rồi đẩy lại, xóa File thì xóa object, nút kiểm kho. Kho S3 giả trong bộ nhớ; không gọi mạng.

Lớp `TestS3Real` chỉ chạy khi có biến môi trường OMNI_S3_TEST_ENDPOINT (máy chủ S3 thật hoặc giả lập, bucket
OMNI_S3_TEST_BUCKET đã tạo, khóa OMNI_S3_TEST_KEY / OMNI_S3_TEST_SECRET).
"""

from __future__ import annotations

import io
import os
import unittest
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import set_encrypted_password

from omni_crm.api import media as media_api
from omni_crm.marketing import media, s3_media
from omni_crm.tests.test_marketing_hub import (
	PNG,
	SALES,
	_delete_all,
	make_campaign,
	make_post,
	make_users,
	restore_settings,
	set_settings,
	snapshot_settings,
)

POST = "Omni Content Post"
NOBODY = "td30_nobody@example.com"
KEYS = (
	"s3_media_enabled",
	"s3_endpoint_url",
	"s3_bucket",
	"s3_prefix",
	"s3_access_key",
	"s3_region",
	"s3_verify_tls",
	"s3_ca_bundle",
)


class FakeS3:
	"""Kho S3 trong bộ nhớ; `down = True` thì mọi lời gọi lỗi như MinIO không chạy."""

	def __init__(self):
		self.objects: dict[str, tuple[bytes, str]] = {}
		self.down = False

	def _check(self):
		if self.down:
			raise ConnectionError("minio down")

	def put_object(self, Bucket, Key, Body, ContentType):
		self._check()
		self.objects[Key] = (Body, ContentType)

	def get_object(self, Bucket, Key):
		self._check()
		body, ctype = self.objects[Key]
		return {"Body": io.BytesIO(body), "ContentType": ctype}

	def delete_object(self, Bucket, Key):
		self._check()
		self.objects.pop(Key, None)


def upload(name="td30.png", content=PNG):
	"""Tệp vừa tải lên, chưa gắn bản ghi nào (như ô Ảnh của bài chưa lưu)."""
	return frappe.get_doc({"doctype": "File", "file_name": name, "content": content, "is_private": 1}).insert(
		ignore_permissions=True
	)


def _cleanup_files():
	"""Xóa bài td30-*, tệp td30* và dữ liệu td29 dùng chung (chiến dịch)."""
	frappe.db.rollback()
	frappe.set_user("Administrator")
	for name in frappe.get_all(POST, filters={"name": ["like", "td30%"]}, pluck="name"):
		frappe.db.delete("Comment", {"reference_doctype": POST, "reference_name": name})
		frappe.delete_doc(POST, name, force=True, ignore_permissions=True)
	for name in frappe.get_all("File", filters={"file_name": ["like", "td30%"]}, pluck="name"):
		frappe.delete_doc("File", name, force=True, ignore_permissions=True)
	frappe.db.commit()
	_delete_all()


class S3Case(FrappeTestCase):
	def setUp(self):
		self.s3 = FakeS3()
		patcher = mock.patch.object(s3_media, "_client", return_value=self.s3)
		patcher.start()
		self.addCleanup(patcher.stop)  # chạy SAU _restore (LIFO): dọn dữ liệu vẫn đi vào kho giả
		_cleanup_files()
		self._saved = snapshot_settings(KEYS)
		self.addCleanup(self._restore)
		make_users()
		if not frappe.db.exists("User", NOBODY):
			frappe.get_doc(
				{"doctype": "User", "email": NOBODY, "first_name": "TD30", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		make_campaign()
		set_settings(
			s3_media_enabled=1,
			s3_endpoint_url="https://minio.test:9000",
			s3_bucket="crm-media",
			s3_prefix="omni-content-post/",
			s3_access_key="td30-key",
		)
		frappe.db.commit()

	def _restore(self):
		_cleanup_files()
		restore_settings(self._saved)
		frappe.db.commit()

	def _post_with_image(self, code="td30-b01"):
		f = upload()
		local = f.get_full_path()
		doc = make_post(code, image=f.file_url, publish_mode="Đăng tay")
		frappe.db.commit()
		return frappe.get_doc(POST, doc.name), frappe.get_doc("File", f.name), local


class TestOffload(S3Case):
	def test_disabled_keeps_image_on_disk(self):
		set_settings(s3_media_enabled=0)
		doc, f, local = self._post_with_image()
		self.assertTrue(doc.image.startswith("/private/files/"))
		self.assertEqual(self.s3.objects, {})
		self.assertTrue(os.path.exists(local))

	def test_new_image_moves_to_s3_and_local_copy_removed(self):
		doc, f, local = self._post_with_image()
		key = f"omni-content-post/td30-b01/{f.name}.png"
		self.assertEqual(doc.image, s3_media.download_url(f.name))
		self.assertEqual((f.file_url, f.get(s3_media.KEY_FIELD)), (doc.image, key))
		self.assertEqual(self.s3.objects[key], (PNG, "image/png"))
		self.assertFalse(os.path.exists(local))
		# job đăng bài đọc ảnh từ S3
		self.assertEqual(media.read_image(doc.name, doc.image), ("td30.png", PNG, "image/png"))
		# lưu lại bài (sửa tiêu đề) không đẩy lại, không lỗi
		doc.title = "Tiêu đề mới"
		doc.save(ignore_permissions=True)
		self.assertEqual(len(self.s3.objects), 1)

	def test_storage_down_keeps_disk_copy_then_hourly_job_pushes(self):
		self.s3.down = True
		doc, f, local = self._post_with_image()
		self.assertTrue(doc.image.startswith("/private/files/"))
		self.assertTrue(os.path.exists(local))
		s3_media.push_pending()  # vẫn lỗi: không đổi gì
		self.assertTrue(frappe.db.get_value(POST, doc.name, "image").startswith("/private/files/"))
		self.s3.down = False
		s3_media.push_pending()
		self.assertTrue(s3_media.is_download_url(frappe.db.get_value(POST, doc.name, "image")))
		self.assertFalse(os.path.exists(local))

	def test_shared_disk_file_not_removed(self):
		f = upload()
		other = frappe.get_doc(
			{
				"doctype": "File",
				"file_url": f.file_url,
				"file_name": "td30.png",
				"is_private": 1,
				"attached_to_doctype": "Omni Campaign",
				"attached_to_name": "TD29 Chiến dịch SPD",
			}
		).insert(ignore_permissions=True)
		local = f.get_full_path()
		make_post("td30-b01", image=f.file_url, publish_mode="Đăng tay")
		frappe.db.commit()
		self.assertTrue(os.path.exists(local))
		self.assertEqual(frappe.db.get_value("File", other.name, "file_url"), f.file_url)

	def test_deleting_file_deletes_object(self):
		doc, f, local = self._post_with_image()
		frappe.delete_doc("File", f.name, ignore_permissions=True)
		frappe.db.commit()
		self.assertEqual(self.s3.objects, {})


class TestDownloadAndCheck(S3Case):
	def test_download_checks_post_permission(self):
		doc, f, local = self._post_with_image()
		media_api.post_image(f.name)
		self.assertEqual(frappe.local.response["filecontent"], PNG)
		self.assertEqual(frappe.local.response["display_content_as"], "inline")
		frappe.set_user(SALES)  # Sales User đọc được bài
		media_api.post_image(f.name)
		frappe.set_user(NOBODY)
		with self.assertRaises(frappe.PermissionError):
			media_api.post_image(f.name)
		frappe.set_user("Administrator")
		# tải lên lại đúng ảnh đã chuyển sang S3 (Frappe gộp tệp trùng nội dung): vẫn tải được, thành tệp riêng trên đĩa
		plain = upload("td30-khac.png")
		self.assertTrue(plain.file_url.startswith("/private/files/"))
		self.assertTrue(os.path.exists(plain.get_full_path()))
		with self.assertRaises(frappe.DoesNotExistError):
			media_api.post_image(plain.name)

	def test_check_storage(self):
		set_settings(s3_media_enabled=0)  # kiểm TRƯỚC khi bật: chỉ cần endpoint và bucket
		self.assertTrue(media_api.check_storage()["ok"])
		set_settings(s3_media_enabled=1)
		self.assertTrue(media_api.check_storage()["ok"])
		self.assertEqual(self.s3.objects, {})  # object thử đã xóa
		self.s3.down = True
		with self.assertRaises(frappe.ValidationError):
			media_api.check_storage()
		frappe.set_user(SALES)
		with self.assertRaises(frappe.PermissionError):
			media_api.check_storage()
		frappe.set_user("Administrator")


class TestPullBack(S3Case):
	"""Quay lui: kéo ảnh từ S3 về đĩa máy chủ CRM, giữ object trên S3."""

	def test_dry_run_counts_only_and_refuses_while_enabled(self):
		doc, f, local = self._post_with_image()
		self.assertEqual(s3_media.pull_back(post=doc.name), {"files": 1, "pulled": 0, "errors": []})
		self.assertEqual(frappe.db.get_value(POST, doc.name, "image"), s3_media.download_url(f.name))
		with self.assertRaises(s3_media.S3Error):
			s3_media.pull_back(dry_run=0, post=doc.name)  # còn bật lưu S3: từ chối

	def test_pull_back_restores_disk_file_and_post_image(self):
		doc, f, local = self._post_with_image()
		key = f.get(s3_media.KEY_FIELD)
		set_settings(s3_media_enabled=0)
		frappe.db.commit()
		self.assertEqual(
			s3_media.pull_back(dry_run=0, post=doc.name), {"files": 1, "pulled": 1, "errors": []}
		)
		f.reload()
		self.assertTrue(f.file_url.startswith("/private/files/td30"))
		self.assertFalse(f.get(s3_media.KEY_FIELD))
		with open(f.get_full_path(), "rb") as fh:
			self.assertEqual(fh.read(), PNG)
		self.assertEqual(frappe.db.get_value(POST, doc.name, "image"), f.file_url)
		self.assertIn(key, self.s3.objects)  # object giữ lại, xóa tay sau khi kiểm
		self.assertEqual(media.read_image(doc.name, f.file_url)[1], PNG)  # job đăng đọc từ đĩa
		self.assertEqual(s3_media.pull_back(dry_run=0, post=doc.name)["files"], 0)  # chạy lại: không còn gì

	def test_storage_down_reports_error_and_changes_nothing(self):
		doc, f, local = self._post_with_image()
		set_settings(s3_media_enabled=0)
		frappe.db.commit()
		self.s3.down = True
		result = s3_media.pull_back(dry_run=0, post=doc.name)
		self.assertEqual((result["files"], result["pulled"], len(result["errors"])), (1, 0, 1))
		self.assertIn(f.name, result["errors"][0])
		self.assertEqual(frappe.db.get_value("File", f.name, "file_url"), s3_media.download_url(f.name))


class TestSettingsDefaults(FrappeTestCase):
	def test_verify_tls_defaults_on_until_saved_off(self):
		self.assertEqual(
			[s3_media.verify_tls(v) for v in (None, "", "0", 0, "1", 1)],
			[True, True, False, False, True, True],
		)

	def test_tls_verify_param_ca_bundle(self):
		ca = frappe.get_site_path("private", "td30-ca.pem")
		with open(ca, "w") as fh:
			fh.write("-----BEGIN CERTIFICATE-----\n")
		self.addCleanup(os.remove, ca)
		self.assertIs(s3_media.tls_verify_param(None, ""), True)
		self.assertIs(s3_media.tls_verify_param("1", "  "), True)
		self.assertIs(s3_media.tls_verify_param("0", ca), False)
		self.assertEqual(s3_media.tls_verify_param("1", f" {ca} "), ca)
		with self.assertRaises(s3_media.S3Error):
			s3_media.tls_verify_param("1", "/khong/co/ca.pem")

	def test_client_passes_ca_bundle_and_missing_ca_is_clear_error(self):
		snap = snapshot_settings(KEYS)
		self.addCleanup(restore_settings, snap)
		ca = frappe.get_site_path("private", "td30-ca.pem")
		with open(ca, "w") as fh:
			fh.write("-----BEGIN CERTIFICATE-----\n")
		self.addCleanup(os.remove, ca)
		set_settings(
			s3_endpoint_url="https://minio.lan:9000", s3_bucket="crm-media", s3_verify_tls=1, s3_ca_bundle=ca
		)
		with mock.patch("boto3.client") as client:
			s3_media._client()
		self.assertEqual(client.call_args.kwargs["verify"], ca)
		set_settings(s3_ca_bundle="/khong/co/ca.pem")
		with self.assertRaises(s3_media.S3Error) as cm:
			s3_media.put_object("x", b"x", "text/plain")
		self.assertIn("/khong/co/ca.pem", str(cm.exception))

	def test_check_needs_endpoint_and_bucket(self):
		snap = snapshot_settings(KEYS)
		self.addCleanup(restore_settings, snap)
		set_settings(s3_media_enabled=1, s3_endpoint_url="", s3_bucket="crm-media")
		self.assertFalse(s3_media.configured())
		with self.assertRaises(frappe.ValidationError):
			media_api.check_storage()

	def test_patch_writes_defaults_only_for_unsaved_fields(self):
		from omni_crm.patches.v3 import set_marketing_settings_defaults as patch

		snap = snapshot_settings(tuple(patch.DEFAULTS))
		self.addCleanup(restore_settings, snap)
		restore_settings({k: None for k in patch.DEFAULTS})  # như site vừa migrate: chưa có dòng nào
		set_settings(s3_region="ap-southeast-1")  # người dùng đã đặt: giữ nguyên
		patch.execute()
		got = snapshot_settings(tuple(patch.DEFAULTS))
		self.assertEqual(got["s3_verify_tls"], "1")
		self.assertEqual(got["content_late_window_minutes"], "30")
		self.assertEqual(got["s3_prefix"], "omni-content-post/")
		self.assertEqual(got["s3_region"], "ap-southeast-1")


@unittest.skipUnless(os.environ.get("OMNI_S3_TEST_ENDPOINT"), "cần máy chủ S3 thử (OMNI_S3_TEST_ENDPOINT)")
class TestS3Real(FrappeTestCase):
	"""Chạy với boto3 thật tới một máy chủ S3 (MinIO hoặc giả lập): ghi, đọc, xóa, và một bài có ảnh đi trọn vòng."""

	def setUp(self):
		_cleanup_files()
		self._saved = snapshot_settings(KEYS)
		self.addCleanup(self._restore)
		make_campaign()
		set_settings(
			s3_media_enabled=1,
			s3_endpoint_url=os.environ["OMNI_S3_TEST_ENDPOINT"],
			s3_bucket=os.environ.get("OMNI_S3_TEST_BUCKET", "crm-media"),
			s3_prefix="td30-real/",
			s3_access_key=os.environ.get("OMNI_S3_TEST_KEY", "test"),
			# OMNI_S3_TEST_CA: tệp CA nội bộ -> bật kiểm TLS như production; không có thì tắt kiểm (máy thử http)
			s3_verify_tls=1 if os.environ.get("OMNI_S3_TEST_CA") else 0,
			s3_ca_bundle=os.environ.get("OMNI_S3_TEST_CA", ""),
		)
		set_encrypted_password(
			"Omni Settings", "Omni Settings", os.environ.get("OMNI_S3_TEST_SECRET", "test"), "s3_secret_key"
		)
		frappe.db.commit()

	def _restore(self):
		_cleanup_files()
		restore_settings(self._saved)
		frappe.db.commit()

	def test_round_trip(self):
		self.assertTrue(s3_media.check_storage()["ok"])
		f = upload()
		make_post("td30-b01", image=f.file_url, publish_mode="Đăng tay")
		frappe.db.commit()
		doc = frappe.get_doc(POST, "td30-b01")
		self.assertTrue(s3_media.is_download_url(doc.image))
		self.assertEqual(media.read_image(doc.name, doc.image)[1], PNG)
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		frappe.delete_doc("File", f.name, ignore_permissions=True)
		frappe.db.commit()
		with self.assertRaises(s3_media.S3Error):
			s3_media.get_object(key)

	def test_pull_back_round_trip(self):
		f = upload()
		make_post("td30-b01", image=f.file_url, publish_mode="Đăng tay")
		frappe.db.commit()
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		self.assertTrue(key)
		set_settings(s3_media_enabled=0)
		frappe.db.commit()
		self.assertEqual(s3_media.pull_back(dry_run=0, post="td30-b01")["pulled"], 1)
		url = frappe.db.get_value("File", f.name, "file_url")
		self.assertEqual(frappe.db.get_value(POST, "td30-b01", "image"), url)
		self.assertEqual(media.read_image("td30-b01", url)[1], PNG)
		self.assertEqual(s3_media.get_object(key)[0], PNG)  # object vẫn còn trên S3
		s3_media.delete_object(key)
