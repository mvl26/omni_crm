from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.ingest.events import stable_message_id
from omni_crm.ingest.gateway import enqueue_identity, save_inbound
from omni_crm.tests.fake_adapter import make_channel, make_event


class TestGateway(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.channel = make_channel("GW kenh", enabled=1)

	def _flush(self):
		"""Chạy các callback after_commit (không commit thật)."""
		frappe.db.after_commit.run()

	def test_new_message_saved_and_enqueued(self):
		with mock.patch("frappe.enqueue") as enq:
			name = save_inbound(make_event(self.channel.name, "m1"))
			enq.assert_not_called()  # hoãn tới sau commit
			self._flush()
		doc = frappe.get_doc("Omni Inbound Message", name)
		self.assertEqual(doc.status, "New")
		self.assertEqual(doc.identity_key, "Zalo OA:U1")
		self.assertEqual(doc.dedupe_key, f"{self.channel.name}:m1")
		enq.assert_called_once()
		args, kwargs = enq.call_args
		self.assertEqual(args[0], "omni_crm.pipeline.worker.process_identity")
		self.assertEqual(kwargs["queue"], "omni")
		self.assertEqual(kwargs["job_id"], "omni-identity::Zalo OA:U1")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["identity_key"], "Zalo OA:U1")

	def test_duplicates_saved_once_enqueued_once(self):
		with mock.patch("frappe.enqueue") as enq:
			results = [save_inbound(make_event(self.channel.name, "dup")) for _ in range(5)]
			self._flush()
		self.assertIsNotNone(results[0])
		self.assertEqual(results[1:], [None] * 4)
		self.assertEqual(
			frappe.db.count("Omni Inbound Message", {"dedupe_key": f"{self.channel.name}:dup"}), 1
		)
		self.assertEqual(enq.call_count, 1)

	def test_disabled_channel_saves_without_enqueue(self):
		self.channel.enabled = 0
		self.channel.save()
		with mock.patch("frappe.enqueue") as enq:
			name = save_inbound(make_event(self.channel.name, "off"))
			self._flush()
		self.assertTrue(frappe.db.exists("Omni Inbound Message", name))
		enq.assert_not_called()

	def test_enqueue_failure_keeps_message_new(self):
		with (
			mock.patch("frappe.enqueue", side_effect=ConnectionError("redis chet")),
			mock.patch.object(frappe.db, "commit"),
			mock.patch("frappe.log_error") as log,
		):
			name = save_inbound(make_event(self.channel.name, "boom"))
			self._flush()  # không ném lỗi
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", name, "status"), "New")
		log.assert_called_once()
		self.assertNotIn("U1", str(log.call_args))

	def test_enqueue_identity_direct_swallows_error(self):
		with mock.patch("frappe.enqueue", side_effect=ConnectionError), mock.patch("frappe.log_error"):
			enqueue_identity("Zalo OA:x", after_commit=False)  # không ném

	def test_unparsed_event_is_manual_and_not_enqueued(self):
		ev = make_event(self.channel.name, "u1")
		ev = type(ev)(**{**ev.__dict__, "event_type": "unparsed"})
		with mock.patch("frappe.enqueue") as enq:
			name = save_inbound(ev)
			self._flush()
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", name, "status"), "Manual")
		enq.assert_not_called()

	def test_stable_message_id(self):
		a = stable_message_id("ch", "u", "2026-01-01", "hi")
		self.assertEqual(a, stable_message_id("ch", "u", "2026-01-01", "hi"))
		self.assertNotEqual(a, stable_message_id("ch", "u", "2026-01-01", "ho"))
		self.assertNotEqual(stable_message_id("ab", "c"), stable_message_id("a", "bc"))
		self.assertEqual(len(a), 64)
