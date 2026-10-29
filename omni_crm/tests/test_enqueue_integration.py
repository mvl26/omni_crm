"""Tích hợp với Redis queue thật: commit thật -> after_commit -> enqueue thật. Bỏ qua nếu Redis không dùng được."""

import frappe
from frappe.utils.background_jobs import create_job_id, get_queue, get_redis_conn

from omni_crm.ingest.gateway import QUEUE, enqueue_identity, save_inbound
from omni_crm.tests.fake_adapter import make_event
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase

KEY = f"Zalo OA:{PREFIX}INT"


class TestEnqueueIntegration(PipelineTestCase):
	def setUp(self):
		super().setUp()
		try:
			get_redis_conn().ping()
			self.queue = get_queue(QUEUE)
			self.queue.count
		except Exception as e:
			self.skipTest(f"Redis queue không dùng được: {type(e).__name__}")
		self.job_id = create_job_id(f"omni-identity::{KEY}")
		self.addCleanup(self._drop_job)
		self._drop_job()

	def _drop_job(self):
		try:
			job = self.queue.fetch_job(self.job_id)
			if job:
				job.cancel()
				job.delete()
		except Exception:
			pass

	def test_real_commit_triggers_real_enqueue(self):
		save_inbound(make_event(self.channel.name, "int1", sender=f"{PREFIX}INT"))
		self.assertIsNone(self.queue.fetch_job(self.job_id))  # chưa commit: chưa có job
		frappe.db.commit()  # commit thật chạy callback after_commit
		job = self.queue.fetch_job(self.job_id)
		self.assertIsNotNone(job)
		self.assertIn(self.job_id, self.queue.job_ids)
		self.assertEqual(job.kwargs["kwargs"]["identity_key"], KEY)

	def test_second_commit_does_not_duplicate_job(self):
		save_inbound(make_event(self.channel.name, "int2", sender=f"{PREFIX}INT"))
		frappe.db.commit()
		save_inbound(make_event(self.channel.name, "int3", sender=f"{PREFIX}INT"))
		frappe.db.commit()
		self.assertEqual(self.queue.job_ids.count(self.job_id), 1)

	def test_after_commit_false_enqueues_without_commit(self):
		enqueue_identity(KEY, after_commit=False)
		self.assertIsNotNone(self.queue.fetch_job(self.job_id))

	def test_default_after_commit_is_lost_on_rollback(self):
		"""Ghi lại hành vi: after_commit=True mà rollback thì không có job (lý do worker phải dùng False)."""
		enqueue_identity(KEY)
		frappe.db.rollback()
		self.assertIsNone(self.queue.fetch_job(self.job_id))
