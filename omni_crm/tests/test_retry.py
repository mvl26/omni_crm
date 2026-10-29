from datetime import timedelta
from unittest import mock

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.pipeline.retry import TransientError, is_transient, mark_retry, redact
from omni_crm.tests.pipeline_helpers import PipelineTestCase


class TestMarkRetry(PipelineTestCase):
	def _row(self, name):
		return frappe.db.get_value(
			"Omni Inbound Message",
			name,
			["status", "attempts", "next_attempt_at", "last_error"],
			as_dict=True,
		)

	def test_schedule_follows_table(self):
		name = self.add_msg("r1")
		t0 = now_datetime()
		mark_retry([frappe._dict(name=name)], TransientError("mạng"))
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Retry", 1))
		self.assertAlmostEqual((r.next_attempt_at - t0).total_seconds(), 60, delta=5)

		mark_retry([frappe._dict(name=name)], TransientError("mạng"))
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Retry", 2))
		self.assertAlmostEqual((r.next_attempt_at - t0).total_seconds(), 300, delta=5)

		mark_retry([frappe._dict(name=name)], TransientError("mạng"))
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Manual", 3))
		self.assertIsNone(r.next_attempt_at)

	def test_custom_settings(self):
		name = self.add_msg("r2")
		with (
			mock.patch("omni_crm.pipeline.retry.max_attempts", return_value=2),
			mock.patch("omni_crm.pipeline.retry.backoff_minutes", return_value=[3]),
		):
			t0 = now_datetime()
			mark_retry([frappe._dict(name=name)], "x")
			self.assertAlmostEqual((self._row(name).next_attempt_at - t0).total_seconds(), 180, delta=5)
			mark_retry([frappe._dict(name=name)], "x")
		self.assertEqual(self._row(name).status, "Manual")

	def test_permanent_hint_marks_error_and_redacts(self):
		name = self.add_msg("r3")
		token = "A" * 40
		mark_retry([frappe._dict(name=name)], f"lỗi token={token}", permanent_hint=True)
		err = self._row(name).last_error
		self.assertNotIn(token, err)
		self.assertIn("loi du lieu", err)

	def test_works_for_outbound_doctype(self):
		out = frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": self.channel.name,
				"text": "x",
				"source": "System",
			}
		).insert()
		frappe.db.commit()
		self.addCleanup(
			lambda: (frappe.db.delete("Omni Outbound Message", {"name": out.name}), frappe.db.commit())
		)
		mark_retry([out], TransientError("x"), doctype="Omni Outbound Message")
		self.assertEqual(frappe.db.get_value("Omni Outbound Message", out.name, "status"), "Retry")


class TestRedactAndClassify(PipelineTestCase):
	def test_redact_long_token(self):
		token = "abcdEFGH1234" * 4  # 48 ký tự
		out = redact(f"Authorization: Bearer {token} lỗi")
		self.assertNotIn(token, out)
		self.assertIn("lỗi", out)
		self.assertNotIn("x" * 5, redact("ngắn thôi"))

	def test_redact_exactly_40_chars(self):
		tok = "0123456789abcdef0123456789abcdef01234567"
		self.assertEqual(len(tok), 40)
		self.assertNotIn(tok, redact(f"x {tok} y"))

	def test_redact_stored_password_value(self):
		ch = frappe.get_doc("Omni Channel", self.channel.name)
		ch.zalo_app_secret = "mat-khau-ngan"  # ngắn hơn 32 ký tự, chỉ nhận ra nhờ giá trị đã lưu
		ch.save()
		frappe.db.commit()
		out = redact("Zalo trả về: mat-khau-ngan là sai")
		self.assertNotIn("mat-khau-ngan", out)
		self.assertIn("Zalo trả về", out)

	def test_redact_phone_and_email(self):
		text = "Lỗi với 0912345678, +84 912 345 678, 0084912345678, 024.3123.4567 và an.nguyen+x@Abc.vn"
		out = redact(text)
		for leak in (
			"0912345678",
			"912 345 678",
			"0084912345678",
			"3123.4567",
			"an.nguyen",
			"abc.vn",
			"Abc.vn",
		):
			self.assertNotIn(leak, out)
		self.assertIn("Lỗi với", out)

	def test_redact_keeps_short_numbers_and_ids(self):
		self.assertEqual(
			redact("batch 20240312 thử 3 lần, mã CRM-LEAD-2026-00012"),
			"batch 20240312 thử 3 lần, mã CRM-LEAD-2026-00012",
		)

	def test_redact_empty(self):
		self.assertEqual(redact(None), "")


class TestClassify(FrappeTestCase):
	def test_transient_types(self):
		for exc in (
			TransientError("x"),
			ConnectionError(),
			TimeoutError(),
			requests.ConnectionError(),
			requests.Timeout(),
			frappe.QueryTimeoutError(),
			frappe.TimestampMismatchError(),
			frappe.QueryDeadlockError(),
		):
			self.assertTrue(is_transient(exc), type(exc))

	def test_other_types(self):
		for exc in (ValueError(), frappe.ValidationError(), KeyError("k")):
			self.assertFalse(is_transient(exc), type(exc))
