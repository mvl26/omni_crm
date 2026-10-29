from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.pipeline import sweeper
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase


class TestSweeper(PipelineTestCase):
	def _set(self, name, **values):
		frappe.db.set_value("Omni Inbound Message", name, values, update_modified=False)
		frappe.db.commit()

	def _row(self, name):
		return frappe.db.get_value(
			"Omni Inbound Message", name, ["status", "attempts", "next_attempt_at"], as_dict=True
		)

	def _run(self):
		with mock.patch.object(sweeper, "enqueue_identity") as enq:
			counts = sweeper.requeue_stuck()
		return counts, enq

	def _mine(self, enq):
		"""Các lần enqueue thuộc dữ liệu test (site test có thể còn tin New cũ của người khác)."""
		return [c for c in enq.call_args_list if f":{PREFIX}" in c.args[0]]

	def test_stuck_processing_goes_to_retry(self):
		stuck = self.add_msg("s1")
		fresh = self.add_msg("s2", sender=f"{PREFIX}U2")
		self._set(stuck, status="Processing", processing_started_at=add_to_date(now_datetime(), minutes=-30))
		self._set(fresh, status="Processing", processing_started_at=add_to_date(now_datetime(), minutes=-1))
		counts, _ = self._run()
		r = self._row(stuck)
		self.assertEqual((r.status, r.attempts), ("Retry", 1))
		self.assertEqual(self._row(fresh).status, "Processing")
		self.assertGreaterEqual(counts["stuck"], 1)

	def test_stuck_beyond_max_attempts_goes_manual(self):
		name = self.add_msg("s3")
		self._set(
			name,
			status="Processing",
			attempts=2,
			processing_started_at=add_to_date(now_datetime(), minutes=-30),
		)
		self._run()
		self.assertEqual(self._row(name).status, "Manual")

	def test_retry_due_is_enqueued_without_after_commit(self):
		due = self.add_msg("r1")
		later = self.add_msg("r2", sender=f"{PREFIX}U2")
		self._set(due, status="Retry", attempts=1, next_attempt_at=add_to_date(now_datetime(), minutes=-1))
		self._set(later, status="Retry", attempts=1, next_attempt_at=add_to_date(now_datetime(), minutes=10))
		counts, enq = self._run()
		self.assertEqual(self._mine(enq), [mock.call(f"Zalo OA:{PREFIX}U1", after_commit=False)])
		self.assertGreaterEqual(counts["retry_due"], 1)

	def test_orphan_new_older_than_two_minutes(self):
		old = self.add_msg("n1")
		old_other = self.add_msg("n1b")  # cùng khách: chỉ enqueue 1 lần
		fresh = self.add_msg("n2", sender=f"{PREFIX}U2")
		for n in (old, old_other):
			self._set(n, creation=add_to_date(now_datetime(), minutes=-5))
		counts, enq = self._run()
		self.assertEqual(self._mine(enq), [mock.call(f"Zalo OA:{PREFIX}U1", after_commit=False)])
		self.assertGreaterEqual(counts["new_orphan"], 1)
		self.assertEqual(self._row(fresh).status, "New")

	def test_disabled_channel_not_enqueued(self):
		old = self.add_msg("d1")
		self._set(old, creation=add_to_date(now_datetime(), minutes=-5))
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		_, enq = self._run()
		self.assertEqual(self._mine(enq), [])

	def test_counts_written_to_cache(self):
		self._run()
		counts = frappe.cache().get_value(sweeper.COUNTS_CACHE_KEY, expires=True)
		self.assertEqual(set(counts) >= {"stuck", "retry_due", "new_orphan", "outbound", "checked_at"}, True)

	def test_hook_registered(self):
		hooks = frappe.get_hooks("scheduler_events", app_name="omni_crm")
		self.assertIn("omni_crm.pipeline.sweeper.requeue_stuck", hooks["cron"]["*/5 * * * *"])
