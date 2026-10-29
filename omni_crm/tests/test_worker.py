import threading
from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.identity import resolver
from omni_crm.pipeline import worker
from omni_crm.pipeline.locks import identity_lock
from omni_crm.pipeline.retry import TransientError
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase
from omni_crm.tests.test_locks import _in_thread

KEY = f"Zalo OA:{PREFIX}U1"


class TestWorker(PipelineTestCase):
	def setUp(self):
		super().setUp()
		# D1-06: Comment liên kết Hộp thư kích hoạt hook CRM `on_comment_insert` -> frappe.enqueue; Redis queue
		# của bench dev đang hỏng nên mock (các test dưới đây kiểm tra thử lại/log, không kiểm tra hàng đợi).
		patcher = mock.patch("frappe.enqueue")
		patcher.start()
		self.addCleanup(patcher.stop)

	def _row(self, name):
		return frappe.db.get_value(
			"Omni Inbound Message",
			name,
			["status", "attempts", "next_attempt_at", "batch_id", "identity", "last_error"],
			as_dict=True,
		)

	def _make_due(self, name):
		frappe.db.set_value(
			"Omni Inbound Message", name, "next_attempt_at", add_to_date(now_datetime(), minutes=-1)
		)
		frappe.db.commit()

	def test_three_new_messages_one_batch_all_done(self):
		names = [self.add_msg(f"w{i}") for i in range(3)]
		with mock.patch.object(worker, "_process_batch", wraps=worker._process_batch) as pb:
			self.assertEqual(process_identity(KEY, 0), "done")
		self.assertEqual(pb.call_count, 1)
		rows = [self._row(n) for n in names]
		self.assertEqual({r.status for r in rows}, {"Done"})
		self.assertEqual(len({r.batch_id for r in rows}), 1)
		self.assertTrue(rows[0].batch_id)
		identity = frappe.db.get_value("Omni Identity", {"identity_key": KEY}, "name")
		self.assertEqual({r.identity for r in rows}, {identity})
		self.assertIsNotNone(frappe.db.get_value("Omni Identity", identity, "last_inbound_at"))

	def test_idle_when_nothing_to_do(self):
		self.assertEqual(process_identity(KEY, 0), "idle")

	def test_message_arriving_during_processing_is_picked_up_next_round(self):
		first = self.add_msg("a1")
		late: list[str] = []
		real = worker._process_batch

		def wrapper(identity_key, msgs, batch_id):
			if not late:
				late.append(self.add_msg("a2"))  # tin đến lúc đang xử lý lượt đầu
			return real(identity_key, msgs, batch_id)

		with mock.patch.object(worker, "_process_batch", side_effect=wrapper) as pb:
			process_identity(KEY, 0)
		self.assertEqual(pb.call_count, 2)
		r1, r2 = self._row(first), self._row(late[0])
		self.assertEqual((r1.status, r2.status), ("Done", "Done"))
		self.assertNotEqual(r1.batch_id, r2.batch_id)

	def test_pending_after_max_rounds_enqueues_continuation_without_commit_flag(self):
		self.add_msg("m0")
		# tin đến sau khi lô đã lấy và hết vòng: còn lại New -> phải enqueue với after_commit=False
		real_pb = worker._process_batch
		extra: list[str] = []

		def wrapper(identity_key, msgs, batch_id):
			if not extra:
				extra.append(self.add_msg("m-x"))
			return real_pb(identity_key, msgs, batch_id)

		with (
			mock.patch.object(worker, "MAX_ROUNDS", 1),
			mock.patch.object(worker, "_process_batch", side_effect=wrapper),
			mock.patch.object(worker, "enqueue_identity") as enq,
		):
			process_identity(KEY, 0)
		enq.assert_called_once()
		self.assertIs(enq.call_args.kwargs["after_commit"], False)
		self.assertTrue(enq.call_args.kwargs["job_suffix"])
		self.assertEqual(self.status_of(extra[0]), ["New"])

	def test_transient_error_twice_then_success(self):
		name = self.add_msg("t1")
		boom = TransientError("mạng")
		with mock.patch.object(worker.timeline, "append", side_effect=boom):
			t0 = now_datetime()
			process_identity(KEY, 0)
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Retry", 1))
		self.assertAlmostEqual((r.next_attempt_at - t0).total_seconds(), 60, delta=5)

		# chưa đến hạn: không tự chạy lại
		with mock.patch.object(worker.timeline, "append", side_effect=boom) as ap:
			self.assertEqual(process_identity(KEY, 0), "idle")
		ap.assert_not_called()

		self._make_due(name)
		with mock.patch.object(worker.timeline, "append", side_effect=boom):
			t1 = now_datetime()
			process_identity(KEY, 0)
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Retry", 2))
		self.assertAlmostEqual((r.next_attempt_at - t1).total_seconds(), 300, delta=5)

		self._make_due(name)
		process_identity(KEY, 0)
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Done", 2))
		self.assertIsNone(r.next_attempt_at)

	def test_three_failures_go_manual(self):
		name = self.add_msg("f1")
		with mock.patch.object(worker.timeline, "append", side_effect=TransientError("x")):
			for _ in range(3):
				process_identity(KEY, 0)
				self._make_due(name)
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Manual", 3))
		with mock.patch.object(worker.timeline, "append") as ap:  # Manual không tự chạy
			process_identity(KEY, 0)
		ap.assert_not_called()

	def test_logic_error_rolls_back_and_is_logged_without_content(self):
		name = self.add_msg("l1")
		frappe.db.set_value("Omni Inbound Message", name, "text", "SDT 0912345678 bí mật")
		frappe.db.commit()
		with (
			mock.patch.object(worker.timeline, "append", side_effect=ValueError("hỏng")),
			mock.patch("frappe.log_error") as log,
		):
			process_identity(KEY, 0)
		# D1-05: resolver commit sau khi gắn Lead (khóa tín hiệu), nên Identity + Lead còn lại sau lỗi ở
		# bước ghi dòng thời gian; lô bị rollback phần còn lại và thử lại sẽ dùng lại đúng chúng.
		self.assertEqual(frappe.db.count("Omni Identity", {"identity_key": KEY}), 1)
		first_lead = frappe.db.get_value("Omni Identity", {"identity_key": KEY}, "lead")
		self.assertTrue(first_lead)
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Retry", 1))
		self.assertIn("loi du lieu", r.last_error)
		self.assertIsNone(r.identity)  # tin chưa được đánh dấu Done/identity (rollback phần của worker)
		log.assert_called_once()
		self.assertNotIn("0912345678", str(log.call_args))

		# tạo lại an toàn: chạy lại thành công, Identity có đúng 1 bản ghi
		self._make_due(name)
		process_identity(KEY, 0)
		self.assertEqual(self._row(name).status, "Done")
		self.assertEqual(frappe.db.count("Omni Identity", {"identity_key": KEY}), 1)

	def test_lock_is_extended_each_round(self):
		"""Mỗi vòng xử lý phải gia hạn khóa (replace_ttl) trước khi chạy lô; không phụ thuộc đồng hồ."""
		from redis.lock import Lock

		self.add_msg("e0")
		real = worker._process_batch
		extra = [1, 2]
		batches: list[int] = []
		extend_calls_at_batch: list[int] = []
		extend_calls: list[tuple] = []
		real_extend = Lock.extend

		def spy_extend(lock, additional_time, replace_ttl=False):
			extend_calls.append((additional_time, replace_ttl))
			return real_extend(lock, additional_time, replace_ttl=replace_ttl)

		def wrapper(identity_key, msgs, batch_id):
			batches.append(1)
			extend_calls_at_batch.append(len(extend_calls))
			if extra:
				extra.pop()
				self.add_msg(f"e-{len(extra)}")
			return real(identity_key, msgs, batch_id)

		with (
			mock.patch.object(Lock, "extend", spy_extend),
			mock.patch.object(worker, "_process_batch", side_effect=wrapper),
		):
			process_identity(KEY, 0)
		self.assertEqual(len(batches), 3)
		# Trước mỗi lô thứ k đã có ít nhất k lần gia hạn.
		self.assertEqual(extend_calls_at_batch, [1, 2, 3])
		self.assertTrue(all(call == (worker.LOCK_TIMEOUT, True) for call in extend_calls), extend_calls)

	def test_lock_lost_between_rounds_stops_and_enqueues_continuation(self):
		from omni_crm.pipeline.locks import lock_key

		first = self.add_msg("g0")
		real = worker._process_batch
		later: list[str] = []

		def wrapper(identity_key, msgs, batch_id):
			if not later:
				later.append(self.add_msg("g1"))
			result = real(identity_key, msgs, batch_id)
			frappe.cache().delete(lock_key(identity_key))  # khóa mất (hết hạn/Redis mất)
			return result

		with (
			mock.patch.object(worker, "_process_batch", side_effect=wrapper) as pb,
			mock.patch.object(worker, "enqueue_identity") as enq,
		):
			process_identity(KEY, 0)
		self.assertEqual(pb.call_count, 1)  # không chạy tiếp khi mất khóa
		self.assertEqual(self.status_of(first, later[0]), ["Done", "New"])
		enq.assert_called_once()
		self.assertIs(enq.call_args.kwargs["after_commit"], False)

	def test_error_log_message_is_redacted(self):
		self.add_msg("x1")
		ch = frappe.get_doc("Omni Channel", self.channel.name)
		ch.zalo_app_secret = "mk-that-su-ngan"
		ch.save()
		frappe.db.commit()
		token = "tok0123456789abcdef0123456789abcdef0123"
		err = ValueError(f"401 for token={token} secret=mk-that-su-ngan")
		with (
			mock.patch.object(worker.timeline, "append", side_effect=err),
			mock.patch("frappe.log_error") as log,
		):
			process_identity(KEY, 0)
		log.assert_called_once()
		text = str(log.call_args)
		self.assertNotIn(token, text)
		self.assertNotIn("mk-that-su-ngan", text)
		self.assertIn("loai_loi=ValueError", text)

	def test_rerun_of_done_message_is_noop(self):
		name = self.add_msg("d1")
		process_identity(KEY, 0)
		batch = self._row(name).batch_id
		with mock.patch.object(worker, "_process_batch") as pb:
			self.assertEqual(process_identity(KEY, 0), "idle")
		pb.assert_not_called()
		self.assertEqual(self._row(name).batch_id, batch)
		self.assertEqual(frappe.db.count("Omni Identity", {"identity_key": KEY}), 1)

	def test_channel_disabled_midway_keeps_new(self):
		name = self.add_msg("c1")
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		self.assertEqual(process_identity(KEY, 0), "idle")
		self.assertEqual(self.status_of(name), ["New"])

	def test_concurrent_calls_only_one_processes(self):
		name = self.add_msg("p1")
		entered, release = threading.Event(), threading.Event()
		real = worker._process_batch

		def slow(identity_key, msgs, batch_id):
			entered.set()
			release.wait(10)
			return real(identity_key, msgs, batch_id)

		with mock.patch.object(worker, "_process_batch", side_effect=slow) as pb:
			t1, res1 = _in_thread(lambda: process_identity(KEY, 0))
			self.assertTrue(entered.wait(10))
			t2, res2 = _in_thread(lambda: process_identity(KEY, 0, lock_wait=1))
			t2.join(10)
			self.assertEqual(res2.get("value"), "busy")
			release.set()
			t1.join(15)
		self.assertEqual(res1.get("value"), "done")
		self.assertEqual(pb.call_count, 1)
		frappe.db.rollback()
		self.assertEqual(self.status_of(name), ["Done"])

	def test_busy_leaves_new_for_holder_recheck(self):
		"""Lượt busy không đổi trạng thái; tin New vẫn còn cho lượt giữ khóa hoặc tác vụ quét."""
		name = self.add_msg("b1")
		with identity_lock(KEY, wait=1):
			self.assertEqual(process_identity(KEY, 0, lock_wait=1), "busy")
		self.assertEqual(self.status_of(name), ["New"])

	def test_debounce_read_from_settings_when_not_given(self):
		self.add_msg("s1")
		real = frappe.db.get_single_value

		def fake(doctype, field, *a, **kw):
			return (
				7
				if (doctype, field) == ("Omni Settings", "debounce_seconds")
				else real(doctype, field, *a, **kw)
			)

		with (
			mock.patch("omni_crm.pipeline.worker.time.sleep") as sl,
			mock.patch.object(frappe.db, "get_single_value", side_effect=fake),
			mock.patch.object(
				worker, "_process_batch", return_value=None
			),  # cô lập khỏi tạo Lead (CRM có thể sleep khi Redis hỏng)
		):
			process_identity(KEY)
		sl.assert_called_once_with(7)

	def test_post_batch_hook_error_does_not_break_done(self):
		name = self.add_msg("h1")
		with (
			mock.patch.object(worker, "post_batch_hooks", side_effect=RuntimeError("x")),
			mock.patch("frappe.log_error"),
		):
			process_identity(KEY, 0)
		self.assertEqual(self.status_of(name), ["Done"])

	def test_resolver_reuses_identity_and_lead_across_batches(self):
		self.add_msg("i1")
		process_identity(KEY, 0)
		self.add_msg("i2")
		process_identity(KEY, 0)
		self.assertEqual(frappe.db.count("Omni Identity", {"identity_key": KEY}), 1)
		lead = frappe.db.get_value("Omni Identity", {"identity_key": KEY}, "lead")
		self.assertTrue(lead)
		rows = frappe.get_all(
			"Omni Inbound Message", filters={"identity_key": KEY}, fields=["status", "lead"]
		)
		self.assertEqual({(r.status, r.lead) for r in rows}, {("Done", lead)})
		self.assertEqual(
			resolver.resolve(
				[
					frappe._dict(
						identity_key=KEY,
						channel_type="Zalo OA",
						external_sender_id="x",
						text=None,
						received_at=None,
					)
				]
			)[1],
			lead,
		)
