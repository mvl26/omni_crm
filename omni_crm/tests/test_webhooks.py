import json
import time
from types import SimpleNamespace
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import webhooks
from omni_crm.channels.registry import register_adapter, unregister_adapter
from omni_crm.tests.fake_adapter import FakeAdapter, make_channel


def fake_request(
	body: bytes = b"", method: str = "POST", sig: str | None = FakeAdapter.GOOD_SIGNATURE, length=None
):
	headers = {"X-Fake-Signature": sig} if sig else {}
	return SimpleNamespace(
		method=method,
		content_length=len(body) if length is None else length,
		get_data=lambda: body,
		headers=headers,
	)


def body_of(*events) -> bytes:
	return json.dumps({"events": [{"id": i, "from": "U9", "text": "hi"} for i in events]}).encode()


class TestWebhooks(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.channel = make_channel("WH kenh", enabled=1)
		register_adapter("Zalo OA", FakeAdapter())
		self.addCleanup(unregister_adapter, "Zalo OA")
		self._old_req = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", self._old_req)
		self.commit = mock.patch.object(frappe.db, "commit")
		self.commit_mock = self.commit.start()
		self.addCleanup(self.commit.stop)
		enq = mock.patch("frappe.enqueue")
		self.enq = enq.start()
		self.addCleanup(enq.stop)
		frappe.cache().delete_keys("omni:")

	def call(self, req, ch="WH kenh"):
		frappe.local.request = req
		frappe.local.form_dict = frappe._dict(ch=ch)
		frappe.local.response = frappe._dict()
		result = webhooks.zalo()
		return frappe.local.response.get("http_status_code", 200), result

	def count(self):
		return frappe.db.count("Omni Inbound Message")

	def test_ok_saves_and_commits(self):
		before = self.count()
		status, res = self.call(fake_request(body_of("a1")))
		self.assertEqual(status, 200)
		self.assertEqual(self.count(), before + 1)
		self.commit_mock.assert_called()
		self.assertIsNotNone(frappe.db.get_value("Omni Channel", self.channel.name, "last_success_at"))

	def test_three_events_three_records(self):
		before = self.count()
		status, res = self.call(fake_request(body_of("e1", "e2", "e3")))
		self.assertEqual(status, 200)
		self.assertEqual(self.count(), before + 3)

	def test_bad_signature_401_no_message(self):
		before = self.count()
		status, _ = self.call(fake_request(body_of("x"), sig="sai"))
		self.assertEqual(status, 401)
		self.assertEqual(self.count(), before)

	def test_unknown_channel_404(self):
		status, _ = self.call(fake_request(body_of("x")), ch="khong-co")
		self.assertEqual(status, 404)
		status, _ = self.call(fake_request(body_of("x")), ch=None)
		self.assertEqual(status, 404)

	def test_wrong_channel_type_404(self):
		frappe.get_doc(
			{"doctype": "Omni Channel", "channel_name": "WH email", "channel_type": "Email"}
		).insert()
		status, _ = self.call(fake_request(body_of("x")), ch="WH email")
		self.assertEqual(status, 404)

	def test_oversize_413(self):
		before = self.count()
		big = b"x" * (2 * 1024 * 1024)
		status, _ = self.call(fake_request(big))
		self.assertEqual(status, 413)
		# content_length bị khai thiếu nhưng body thật lớn
		status, _ = self.call(fake_request(big, length=10))
		self.assertEqual(status, 413)
		self.assertEqual(self.count(), before)

	def test_chunked_body_read_is_bounded(self):
		"""Không Content-Length (chunked): chỉ đọc tối đa MAX+1 byte rồi trả 413."""
		reads = []

		class Stream:
			def read(self, n=-1):
				reads.append(n)
				return b"x" * n

		req = fake_request(b"", length=0)
		req.content_length = None
		req.stream = Stream()
		req.get_data = lambda: self.fail("không được gọi get_data (đọc hết body)")
		status, _ = self.call(req)
		self.assertEqual(status, 413)
		self.assertEqual(reads, [webhooks.MAX_BODY_BYTES + 1])

	def test_last_success_only_for_real_saved_events(self):
		key = ("Omni Channel", self.channel.name, "last_success_at")

		def touched(req):
			frappe.cache().delete_keys("omni:")
			with mock.patch("frappe.db.set_value") as sv:
				self.call(req)
			return any(c.args[:3] == key for c in sv.call_args_list)

		self.assertTrue(touched(fake_request(body_of("ok1"))))
		# trùng hoàn toàn: không lưu gì mới
		self.assertFalse(touched(fake_request(body_of("ok1"))))
		# parse lỗi: chỉ có bản ghi unparsed
		self.assertFalse(touched(fake_request(b"khong phai json")))
		# không có sự kiện nào
		self.assertFalse(touched(fake_request(body_of())))

	def test_get_405(self):
		status, _ = self.call(fake_request(b"", method="GET"))
		self.assertEqual(status, 405)

	def test_parse_error_saves_manual_and_200(self):
		before = self.count()
		status, _ = self.call(fake_request(b"day khong phai json"))
		self.assertEqual(status, 200)
		self.assertEqual(self.count(), before + 1)
		row = frappe.get_all(
			"Omni Inbound Message",
			filters={"event_type": "unparsed"},
			fields=["status", "text", "raw_payload"],
		)[0]
		self.assertEqual(row.status, "Manual")
		self.assertNotIn("day khong phai json", str(row))
		self.enq.assert_not_called()

	def test_error_log_has_no_payload_or_signature(self):
		secret_body = b'{"events": [{"id": "z", "from": "SDT0912345678"}]'
		with mock.patch("frappe.log_error") as log:
			self.call(fake_request(secret_body, sig="CHU-KY-BI-MAT"))
		text = str(log.call_args)
		self.assertNotIn("0912345678", text)
		self.assertNotIn("CHU-KY-BI-MAT", text)
		self.assertIn("do_dai_body", text)

	def test_last_success_written_once_per_minute(self):
		with mock.patch("frappe.db.set_value") as sv:
			self.call(fake_request(body_of("p1")))
			self.call(fake_request(body_of("p2")))
		calls = [
			c
			for c in sv.call_args_list
			if c.args[:3] == ("Omni Channel", self.channel.name, "last_success_at")
		]
		self.assertEqual(len(calls), 1)

	def test_p95_latency(self):
		times = []
		for i in range(200):
			req = fake_request(body_of(f"perf{i}"))
			t = time.perf_counter()
			self.call(req)
			times.append((time.perf_counter() - t) * 1000)
		times.sort()
		p95 = times[int(len(times) * 0.95) - 1]
		print(
			f"\n[do] webhook p95 = {p95:.1f} ms, p50 = {times[100]:.1f} ms, max = {times[-1]:.1f} ms (200 request)"
		)
		if p95 >= 200:
			print(f"[canh bao] p95 {p95:.1f} ms >= 200 ms (may cham?)")
