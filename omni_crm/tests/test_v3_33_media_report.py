"""ADR 0033, báo cáo "Kho ảnh MinIO": đối chiếu object trên kho S3 với File có `omni_s3_key`, chỉ đọc.

Dùng kho S3 giả của test_v3_33_media_storage (thêm `list_objects`). `TestListReal` chạy với boto3 thật khi có
OMNI_S3_TEST_ENDPOINT như TestS3Real.
"""

from __future__ import annotations

import datetime
import importlib
import os
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.marketing import s3_media
from omni_crm.tests.test_v3_33_media_storage import ITEM, MediaCase, cleanup, configure

report = importlib.import_module("omni_crm.omni_crm.report.kho_ảnh_minio.kho_ảnh_minio")
MODIFIED = datetime.datetime(2026, 11, 20, 3, 28, 59, tzinfo=datetime.timezone.utc)


class ReportCase(MediaCase):
	def setUp(self):
		super().setUp()
		mock.patch.object(s3_media, "list_objects", side_effect=self.fake_list).start()
		configure()

	def fake_list(self, prefix=None, limit=10000):
		self.s3._check()
		prefix = s3_media._prefix() if prefix is None else prefix
		keys = sorted(k for k in self.s3.objects if k.startswith(prefix))[:limit]
		return [{"key": k, "size": len(self.s3.objects[k][0]), "modified": MODIFIED} for k in keys]

	def run_report(self, **filters):
		columns, rows, message, _chart, summary = report.execute(filters)
		return rows, message, {s["label"]: s["value"] for s in summary}

	def offloaded_item(self) -> tuple[str, str, str]:
		"""(bài, File, khóa) của một bài có ảnh bìa đã lên S3."""
		f = self.upload()
		name = self.item(media=f.file_url)
		key = frappe.db.get_value("File", f.name, s3_media.KEY_FIELD)
		self.assertTrue(key, "ảnh phải đã lên S3 khi lưu bài")
		return name, f.name, key


class TestReport(ReportCase):
	def test_matched_row(self):
		name, file_name, key = self.offloaded_item()
		rows, message, summary = self.run_report()
		self.assertIsNone(message)
		row = next(r for r in rows if r["key"] == key)
		self.assertEqual(row["status"], report.OK)
		self.assertEqual(row["content_item"], name)
		self.assertEqual(row["title"], "Bài thử ảnh")
		self.assertEqual(row["file"], file_name)
		self.assertEqual(row["used"], 1)
		self.assertEqual(row["view_url"], frappe.db.get_value(ITEM, name, "media"))
		self.assertTrue(row["view_url"].startswith("/omni-media/"))
		self.assertGreater(row["size_kb"], 0)
		self.assertIsNone(row["modified"].tzinfo)
		self.assertEqual(row["note"], "")
		self.assertEqual(summary["Tệp trên MinIO"], 1)
		self.assertEqual(summary[report.OK], 1)
		self.assertEqual(summary[report.MISSING], 0)
		self.assertEqual(summary[report.ORPHAN], 0)

	def test_missing_object(self):
		name, file_name, key = self.offloaded_item()
		del self.s3.objects[key]
		rows, _m, summary = self.run_report()
		row = next(r for r in rows if r["key"] == key)
		self.assertEqual(row["status"], report.MISSING)
		self.assertEqual(row["content_item"], name)
		self.assertIsNone(row["view_url"], "object không còn thì không đưa liên kết xem")
		self.assertIsNone(row["size_kb"])
		self.assertEqual(summary[report.MISSING], 1)

	def test_orphan_object_and_deleted_item(self):
		key = f"{s3_media._prefix()}khongcobai/abc123.png"
		self.s3.objects[key] = (b"x" * 2048, "image/png")
		rows, _m, summary = self.run_report()
		row = next(r for r in rows if r["key"] == key)
		self.assertEqual(row["status"], report.ORPHAN)
		self.assertIsNone(row["content_item"])
		self.assertIsNone(row["file"])
		self.assertIsNone(row["view_url"])
		self.assertEqual(row["size_kb"], 2.0)
		self.assertIn("khongcobai", row["note"])
		self.assertEqual(summary[report.ORPHAN], 1)

	def test_objects_outside_prefix_are_ignored(self):
		self.s3.objects["khac/ngoai.png"] = (b"x", "image/png")
		rows, _m, summary = self.run_report()
		self.assertFalse([r for r in rows if r["key"] == "khac/ngoai.png"])
		self.assertEqual(summary["Tệp trên MinIO"], 0)

	def test_filters_keep_summary_of_whole_store(self):
		name, _f, key = self.offloaded_item()
		self.s3.objects[f"{s3_media._prefix()}{name}/mocoi.png"] = (b"x", "image/png")
		rows, _m, summary = self.run_report(status=report.ORPHAN)
		self.assertEqual([r["status"] for r in rows], [report.ORPHAN])
		self.assertEqual(summary[report.OK], 1, "ô tổng hợp tính trên cả kho, không theo bộ lọc")
		rows, _m, _s = self.run_report(content_item=name)
		self.assertEqual(len(rows), 2)
		self.assertTrue(all(r["content_item"] == name for r in rows))

	def test_unused_file_is_noted(self):
		name, file_name, key = self.offloaded_item()
		frappe.db.set_value(ITEM, name, "media", None, update_modified=False)
		rows, _m, _s = self.run_report()
		row = next(r for r in rows if r["key"] == key)
		self.assertEqual(row["status"], report.OK)
		self.assertEqual(row["used"], 0)
		self.assertIn("Không ô ảnh nào", row["note"])

	def test_pending_counts_in_summary(self):
		configure(enabled=0)
		before = self.run_report()[2]["Còn trên đĩa, chờ chuyển"]
		f = self.upload()
		self.item(media=f.file_url)
		_rows, _m, summary = self.run_report()
		self.assertEqual(summary["Còn trên đĩa, chờ chuyển"], before + 1)

	def test_store_down_shows_error(self):
		self.s3.down = True
		with self.assertRaises(frappe.ValidationError):
			report.execute({})

	def test_not_configured(self):
		frappe.db.sql("delete from tabSingles where doctype=%s", s3_media.SETTINGS)
		frappe.clear_document_cache(s3_media.SETTINGS, s3_media.SETTINGS)
		_c, rows, message, _chart, summary = report.execute({})
		self.assertEqual(rows, [])
		self.assertIn("Chưa cấu hình", message)

	def test_requires_admin_role(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			report.execute({})

	def test_report_record_is_restricted(self):
		if not frappe.db.exists("Report", "Kho ảnh MinIO"):
			self.skipTest("chưa migrate báo cáo")
		roles = set(frappe.get_all("Has Role", {"parent": "Kho ảnh MinIO", "parenttype": "Report"}, pluck="role"))
		self.assertEqual(roles, {"Omni Admin", "System Manager"})


class FakePaginator:
	def __init__(self, pages):
		self.pages = pages

	def paginate(self, **kwargs):
		self.kwargs = kwargs
		return iter(self.pages)


class TestListObjects(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup()
		self.addCleanup(cleanup)
		self.addCleanup(mock.patch.stopall)
		configure()

	def client(self, pages):
		paginator = FakePaginator(pages)
		client = mock.Mock()
		client.get_paginator.return_value = paginator
		mock.patch.object(s3_media, "_client", return_value=client).start()
		return client, paginator

	def test_pages_prefix_and_limit(self):
		obj = lambda k: {"Key": k, "Size": 5, "LastModified": MODIFIED}  # noqa: E731
		client, paginator = self.client([{"Contents": [obj("a"), obj("b")]}, {}, {"Contents": [obj("c")]}])
		got = s3_media.list_objects()
		client.get_paginator.assert_called_once_with("list_objects_v2")
		self.assertEqual(paginator.kwargs, {"Bucket": "crm-media", "Prefix": "omni-content-item/"})
		self.assertEqual([o["key"] for o in got], ["a", "b", "c"])
		self.assertEqual(got[0], {"key": "a", "size": 5, "modified": MODIFIED})
		self.assertEqual([o["key"] for o in s3_media.list_objects(limit=2)], ["a", "b"])

	def test_error_is_wrapped(self):
		client = mock.Mock()
		client.get_paginator.side_effect = RuntimeError("bum")
		mock.patch.object(s3_media, "_client", return_value=client).start()
		with self.assertRaisesRegex(s3_media.S3Error, "liệt kê ảnh"):
			s3_media.list_objects()


@__import__("unittest").skipUnless(os.environ.get("OMNI_S3_TEST_ENDPOINT"), "cần OMNI_S3_TEST_ENDPOINT")
class TestListReal(FrappeTestCase):
	"""boto3 thật: ghi hai object, liệt kê thấy đúng khóa và kích thước."""

	def setUp(self):
		frappe.set_user("Administrator")
		cleanup()
		self.addCleanup(cleanup)
		ca = os.environ.get("OMNI_S3_TEST_CA") or ""
		configure(
			endpoint_url=os.environ["OMNI_S3_TEST_ENDPOINT"],
			bucket=os.environ.get("OMNI_S3_TEST_BUCKET", "crm-media"),
			access_key=os.environ.get("OMNI_S3_TEST_KEY", "test"),
			secret_key=os.environ.get("OMNI_S3_TEST_SECRET", "test"),
			ca_bundle=ca,
			verify_tls=1 if ca or os.environ["OMNI_S3_TEST_ENDPOINT"].startswith("https") else 0,
		)

	def test_list(self):
		base = f"{s3_media._prefix()}_thu-liet-ke-{frappe.generate_hash(length=6)}/"
		keys = [f"{base}a.png", f"{base}b.png"]
		for k in keys:
			s3_media.put_object(k, b"12345", "image/png")
		try:
			got = s3_media.list_objects(prefix=base)
			self.assertEqual([o["key"] for o in got], keys)
			self.assertEqual({o["size"] for o in got}, {5})
			self.assertIsNotNone(got[0]["modified"].tzinfo)
		finally:
			for k in keys:
				s3_media.delete_object(k)
