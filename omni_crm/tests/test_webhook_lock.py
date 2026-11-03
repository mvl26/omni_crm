"""V2-01 (03.10): webhook không treo khi DB bị khóa (docs/10 mục 5: không quá 5 giây). V21-04: hết hạn chờ thì vào hàng dự phòng thay vì 503."""

import shutil
import time
from unittest import mock

import frappe
import pymysql
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import webhooks
from omni_crm.channels.registry import register_adapter, unregister_adapter
from omni_crm.tests.fake_adapter import FakeAdapter, make_channel
from omni_crm.tests.test_webhooks import body_of, fake_request

NAME = "WL kenh"


class TestWebhookLockWait(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()
		frappe.db.delete("Omni Channel", {"channel_name": NAME})
		self.channel = make_channel(NAME, enabled=1)
		frappe.db.commit()  # connection thứ hai chỉ thấy/khóa được dòng đã commit
		self.addCleanup(self._drop)
		register_adapter("Zalo OA", FakeAdapter())
		self.addCleanup(unregister_adapter, "Zalo OA")
		self._old_req = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", self._old_req)
		enq = mock.patch("frappe.enqueue")
		enq.start()
		self.addCleanup(enq.stop)
		frappe.cache().delete_keys("omni:")
		self.other = None

	def _drop(self):
		if self.other:
			self.other.rollback()
			self.other.close()
		frappe.db.rollback()
		frappe.db.delete("Omni Inbound Message", {"channel": NAME})
		frappe.db.delete("Omni Channel", {"channel_name": NAME})
		frappe.db.commit()

	def connect_other(self):
		conf = frappe.conf
		self.other = pymysql.connect(
			user=conf.db_name,
			password=conf.db_password,
			database=conf.db_name,
			host=conf.get("db_host") or "127.0.0.1",
			port=int(conf.get("db_port") or 3306),
			autocommit=False,
		)
		return self.other.cursor()

	def hold_channel_row(self):
		"""Kết nối riêng giữ khóa ghi trên dòng kênh (như admin đang lưu form kênh hoặc làm mới token)."""
		self.connect_other().execute("select name from `tabOmni Channel` where name = %s for update", (NAME,))

	def hold_same_message_key(self):
		"""Kết nối riêng chèn (chưa commit) tin cùng `dedupe_key`: lần chèn của webhook phải chờ khóa khóa duy nhất."""
		self.connect_other().execute(
			"insert into `tabOmni Inbound Message` (name, dedupe_key, identity_key, status, creation, modified)"
			" values (%s, %s, %s, 'New', now(), now())",
			("WL-HOLD-1", f"{NAME}:wl1", "Zalo OA:wl-hold"),
		)

	def call(self):
		frappe.local.request = fake_request(body_of("wl1"))
		frappe.local.form_dict = frappe._dict(ch=NAME)
		frappe.local.response = frappe._dict()
		result = webhooks.zalo()
		return frappe.local.response.get("http_status_code", 200), result

	def lock_wait(self):
		return int(frappe.db.sql("select @@session.innodb_lock_wait_timeout")[0][0])

	def stored(self):
		frappe.db.rollback()  # bỏ snapshot cũ (REPEATABLE READ) để thấy dữ liệu đã commit
		return frappe.db.count("Omni Inbound Message", {"channel": NAME})

	def test_locked_channel_row_still_stores_the_message_and_answers_200_in_seconds(self):
		"""Khóa ở dòng kênh chỉ làm mất dấu giám sát `last_success_at`, không được làm mất tin khách."""
		before = self.lock_wait()
		self.hold_channel_row()
		started = time.monotonic()
		with mock.patch.dict(frappe.conf, {webhooks.LOCK_WAIT_CONF: 1}):
			status, result = self.call()
		elapsed = time.monotonic() - started
		self.assertEqual((status, result["saved"]), (200, 1))
		self.assertLess(elapsed, 5, "phải trả lời trong vài giây, không chờ 50 giây của MariaDB")
		self.assertEqual(self.stored(), 1)  # tin đã commit bền
		self.assertEqual(self.lock_wait(), before)

	def test_locked_message_key_goes_to_spool_200_quickly_then_drain_stores_it_once(self):
		"""V21-04: trước đây 503 (mất tin nếu nhà cung cấp không gửi lại); nay vào hàng dự phòng và được chèn lại."""
		from omni_crm.ingest import spool

		self.addCleanup(shutil.rmtree, spool.spool_dir(), True)
		before = self.lock_wait()
		self.hold_same_message_key()
		started = time.monotonic()
		with (
			mock.patch.dict(frappe.conf, {webhooks.LOCK_WAIT_CONF: 1}),
			mock.patch("omni_crm.ingest.spool.build_entry", wraps=spool.build_entry),
		):
			status, _ = self.call()
		self.assertEqual(status, 200)
		self.assertLess(time.monotonic() - started, 5)
		self.assertEqual(self.stored(), 0)
		self.assertEqual(spool.spool_count(), 1)
		self.assertEqual(self.lock_wait(), before)
		self.other.rollback()  # khóa nhả: tác vụ quét chèn lại
		frappe.cache().delete_keys("omni:")
		with mock.patch("omni_crm.ingest.spool.reverify", return_value=(True, "ok")):
			self.assertEqual(spool.drain()["saved"], 1)
		self.assertEqual((self.stored(), spool.spool_count()), (1, 0))

	def test_without_lock_still_ok(self):
		status, result = self.call()
		self.assertEqual((status, result["saved"]), (200, 1))
		self.assertEqual(self.stored(), 1)
