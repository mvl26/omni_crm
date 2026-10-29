"""D2-03: giờ làm việc, việc gọi lại cho cuộc gọi nhỡ, khách ẩn số, đối soát đêm, giám sát."""

from datetime import date, datetime, timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from omni_crm.channels.telephony import callbacks, registry, worker_hooks
from omni_crm.channels.telephony.base import CallEvent
from omni_crm.channels.telephony.fake import FakeProvider
from omni_crm.channels.telephony.stringee import StringeeError
from omni_crm.crm_bridge import task as task_bridge
from omni_crm.monitoring import alerts
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.tests.telephony_helpers import (
	CUSTOMER_E164,
	HOTLINE,
	TelephonyTestCase,
	call,
)
from omni_crm.utils import business_hours as bh

CALL = "Omni Call"
TASK = "CRM Task"
HOURS = bh.parse_hours('{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}')
NO_HOLIDAYS = frozenset()
FRI_HOLIDAY = frozenset({date(2026, 10, 30)})


def dt(*a) -> datetime:
	return datetime(*a)


class TestBusinessHours(FrappeTestCase):
	def add(self, start, minutes, holidays=NO_HOLIDAYS):
		return bh.add_business_minutes(start, minutes, HOURS, holidays)

	def test_friday_1720_plus_30_goes_to_saturday(self):
		self.assertEqual(self.add(dt(2026, 10, 30, 17, 20), 30), dt(2026, 10, 31, 8, 20))

	def test_saturday_half_day_then_monday(self):
		self.assertEqual(self.add(dt(2026, 10, 31, 11, 50), 30), dt(2026, 11, 2, 8, 20))

	def test_overnight_and_inside_hours(self):
		self.assertEqual(self.add(dt(2026, 10, 26, 17, 0), 60), dt(2026, 10, 27, 8, 30))
		self.assertEqual(self.add(dt(2026, 10, 26, 9, 0), 30), dt(2026, 10, 26, 9, 30))

	def test_before_open_and_weekend_start(self):
		self.assertEqual(self.add(dt(2026, 10, 26, 6, 0), 30), dt(2026, 10, 26, 8, 30))
		self.assertEqual(self.add(dt(2026, 11, 1, 10, 0), 30), dt(2026, 11, 2, 8, 30))  # chủ nhật

	def test_spans_many_days(self):
		# thứ hai 08:00 + 600 phút: 570 phút thứ hai, còn 30 phút -> thứ ba 08:30
		self.assertEqual(self.add(dt(2026, 10, 26, 8, 0), 600), dt(2026, 10, 27, 8, 30))

	def test_exact_closing_boundary(self):
		self.assertEqual(self.add(dt(2026, 10, 26, 17, 0), 30), dt(2026, 10, 26, 17, 30))
		self.assertEqual(self.add(dt(2026, 10, 26, 17, 30), 1), dt(2026, 10, 27, 8, 1))

	def test_holiday_is_skipped(self):
		# thứ sáu 30/10 nghỉ: thứ năm 17:20 + 30 -> 10 phút thứ năm, bỏ thứ sáu, thứ bảy 08:20
		self.assertEqual(self.add(dt(2026, 10, 29, 17, 20), 30, FRI_HOLIDAY), dt(2026, 10, 31, 8, 20))
		self.assertFalse(bh.is_business_time(dt(2026, 10, 30, 10, 0), HOURS, FRI_HOLIDAY))
		self.assertTrue(bh.is_business_time(dt(2026, 10, 30, 10, 0), HOURS, NO_HOLIDAYS))

	def test_next_business_start(self):
		self.assertEqual(
			bh.next_business_start(dt(2026, 10, 31, 13, 0), HOURS, NO_HOLIDAYS), dt(2026, 11, 2, 8, 0)
		)
		inside = dt(2026, 10, 26, 10, 15)
		self.assertEqual(bh.next_business_start(inside, HOURS, NO_HOLIDAYS), inside)
		self.assertEqual(
			bh.next_business_start(dt(2026, 10, 26, 18, 0), HOURS, NO_HOLIDAYS), dt(2026, 10, 27, 8, 0)
		)

	def test_parse_holidays_tolerates_garbage(self):
		self.assertEqual(
			bh.parse_holidays('["2027-01-01", "bad", 5, "2027-02-10 00:00"]'),
			{date(2027, 1, 1), date(2027, 2, 10)},
		)
		for raw in (None, "", "{", "{}", '"x"'):
			self.assertEqual(bh.parse_holidays(raw), frozenset())

	def test_settings_holidays_field_default_empty(self):
		meta = frappe.get_meta("Omni Settings").get_field("holidays")
		self.assertEqual((meta.fieldtype, meta.default), ("JSON", "[]"))

	def test_business_seconds_skips_holiday(self):
		whole = bh.business_seconds_between(
			dt(2026, 10, 30, 8, 0), dt(2026, 10, 30, 17, 30), HOURS, NO_HOLIDAYS
		)
		self.assertEqual(whole, 9.5 * 3600)
		self.assertEqual(
			bh.business_seconds_between(dt(2026, 10, 30, 8, 0), dt(2026, 10, 30, 17, 30), HOURS, FRI_HOLIDAY),
			0,
		)


class CallbackCase(TelephonyTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(self._clean_tasks)
		self.old_fallback = frappe.db.get_single_value("Omni Settings", "callback_fallback_user")
		frappe.db.set_single_value("Omni Settings", "callback_fallback_user", "Administrator")
		frappe.db.commit()
		self.addCleanup(self._restore_settings)

	def _restore_settings(self):
		frappe.db.set_single_value("Omni Settings", "callback_fallback_user", self.old_fallback)
		frappe.db.commit()

	def _clean_tasks(self):
		frappe.db.rollback()
		tasks = [
			t
			for t in frappe.get_all(CALL, filters={"call_id": ["like", "td201-%"]}, pluck="callback_task")
			if t
		]
		frappe.db.delete(
			"ToDo", {"reference_type": TASK, "reference_name": ["in", [str(t) for t in tasks] or [""]]}
		)
		frappe.db.delete(TASK, {"name": ["in", tasks or [""]]})
		frappe.db.delete(
			"Notification Log", {"subject": ["like", "%việc gọi lại quá hạn%"], "for_user": "Administrator"}
		)
		frappe.db.commit()

	def missed(self, cid: str, ended="2026-10-30 09:30:00", **over):
		data = call(
			cid, "Missed", answered_at=None, duration=0, agent_ref=None, started_at=ended, ended_at=ended
		)
		data.update(over)
		self.post(data)
		self.run_worker()
		return frappe.db.get_value(CALL, {"call_id": f"td201-{cid}"}, "name")

	def answered(self, cid: str, started="2026-10-30 10:00:00", **over):
		data = call(cid, started_at=started, answered_at=started, ended_at="2026-10-30 10:05:00", **over)
		self.post(data)
		self.run_worker()
		return frappe.db.get_value(CALL, {"call_id": f"td201-{cid}"}, "name")

	def task_of(self, call_name):
		return frappe.db.get_value(CALL, call_name, "callback_task")


class TestCallbackTask(CallbackCase):
	def test_missed_call_creates_one_task_with_owner_and_due(self):
		name = self.missed("m1", ended="2026-10-30 17:20:00")
		self.assertIsNone(self.task_of(name))  # worker chưa tạo việc: việc làm ở job riêng sau commit
		task = callbacks.process_missed_call(name)
		self.assertTrue(task)
		row = frappe.db.get_value(
			TASK,
			task,
			[
				"title",
				"status",
				"assigned_to",
				"due_date",
				"reference_doctype",
				"reference_docname",
				"description",
			],
			as_dict=True,
		)
		lead = frappe.db.get_value(CALL, name, "lead")
		self.assertEqual(
			(row.reference_doctype, row.reference_docname, row.status), ("CRM Lead", lead, "Todo")
		)
		self.assertEqual(row.assigned_to, "Administrator")
		self.assertEqual(
			get_datetime(row.due_date), dt(2026, 10, 31, 8, 20)
		)  # 17:20 thứ sáu + 30 phút làm việc
		self.assertIn("1 lần", row.description)
		self.assertEqual(self.task_of(name), task)

	def test_assignee_is_lead_owner_when_present(self):
		name = self.missed("m2")
		lead = frappe.db.get_value(CALL, name, "lead")
		frappe.db.set_value("CRM Lead", lead, "lead_owner", "Guest")  # Guest bị tắt: rơi về người dự phòng
		self.assertEqual(
			frappe.db.get_value(TASK, callbacks.process_missed_call(name), "assigned_to"), "Administrator"
		)

	def test_missed_twice_one_task_two_calls(self):
		a = self.missed("m3a", ended="2026-10-30 09:30:00")
		b = self.missed("m3b", ended="2026-10-30 11:15:00")
		ta, tb = callbacks.process_missed_call(a), callbacks.process_missed_call(b)
		self.assertEqual(ta, tb)
		self.assertEqual(frappe.db.count(TASK, {"name": ta}), 1)
		desc = frappe.db.get_value(TASK, ta, "description")
		self.assertIn("2 lần", desc)
		self.assertIn("11:15", desc)
		# chạy lại không đếm thêm
		callbacks.process_missed_call(b)
		self.assertIn("2 lần", frappe.db.get_value(TASK, ta, "description"))

	def test_three_missed_one_task(self):
		names = [self.missed(f"m4{i}", ended=f"2026-10-30 0{i + 1}:00:00") for i in range(3)]
		tasks = {callbacks.process_missed_call(n) for n in names}
		self.assertEqual(len(tasks), 1)
		self.assertIn("3 lần", frappe.db.get_value(TASK, tasks.pop(), "description"))

	def test_answered_call_after_closes_task_either_direction(self):
		name = self.missed("m5", ended="2026-10-30 09:30:00")
		task = callbacks.process_missed_call(name)
		self.answered(
			"m5b",
			started="2026-10-30 10:00:00",
			direction="Outbound",
			**{"from": HOTLINE, "to": CUSTOMER_E164},
		)
		self.assertEqual(frappe.db.get_value(TASK, task, "status"), "Done")
		# gọi nhỡ mới sau đó -> việc mới
		again = self.missed("m5c", ended="2026-10-30 12:00:00")
		self.assertNotEqual(callbacks.process_missed_call(again), task)

	def test_answered_call_before_missed_does_not_close(self):
		self.answered("m6a", started="2026-10-30 08:00:00")
		name = self.missed("m6b", ended="2026-10-30 09:30:00")
		task = callbacks.process_missed_call(name)
		self.assertEqual(frappe.db.get_value(TASK, task, "status"), "Todo")

	def test_unanswered_followup_does_not_close(self):
		task = callbacks.process_missed_call(self.missed("m7", ended="2026-10-30 09:30:00"))
		self.missed("m7b", ended="2026-10-30 10:00:00")
		self.assertEqual(frappe.db.get_value(TASK, task, "status"), "Todo")

	def test_outbound_or_answered_call_makes_no_task(self):
		name = self.answered("m8")
		self.assertIsNone(task_bridge.create_callback_task(name))
		out = self.missed("m8b", direction="Outbound", **{"from": HOTLINE, "to": CUSTOMER_E164})
		self.assertIsNone(task_bridge.create_callback_task(out))

	def test_no_assignee_leaves_task_unassigned(self):
		frappe.db.set_single_value("Omni Settings", "callback_fallback_user", None)
		task = callbacks.process_missed_call(self.missed("m9"))
		lead_owner = frappe.db.get_value(CALL, {"call_id": "td201-m9"}, "lead")
		self.assertTrue(task)
		self.assertEqual(
			frappe.db.get_value(TASK, task, "assigned_to"),
			frappe.db.get_value("CRM Lead", lead_owner, "lead_owner") or None,
		)


class TestVerifyOutsideLock(CallbackCase):
	def test_verify_runs_before_task_and_after_rollback(self):
		name = self.missed("v1")
		order = []

		def fake_verify(call_name, channel_doc):
			order.append(("verify", frappe.db.sql("select 1")[0][0]))
			return True

		real_create = task_bridge.create_callback_task

		def spy_create(c):
			order.append(("create", None))
			return real_create(c)

		with (
			mock.patch.object(worker_hooks, "verify_missed_call", side_effect=fake_verify),
			mock.patch.object(task_bridge, "create_callback_task", side_effect=spy_create),
			mock.patch.object(frappe.db, "rollback", wraps=frappe.db.rollback) as rb,
		):
			callbacks.process_missed_call(name, self.channel.name)
		self.assertEqual([o[0] for o in order], ["verify", "create"])
		self.assertTrue(rb.called)  # không giữ transaction mở khi gọi ra ngoài

	def test_unverified_flag_and_counter_shown_in_snapshot(self):
		name = self.missed("v2")
		provider = FakeProvider()
		provider.call_exists = lambda cid, around, ch: False
		registry.register_provider(provider)
		self.addCleanup(registry.unregister_provider, "Fake")
		with mock.patch("omni_crm.flags.sandbox", return_value=False):
			task = callbacks.process_missed_call(name)
		self.assertTrue(task)  # vẫn tạo việc
		self.assertEqual(frappe.db.get_value(CALL, name, "unverified"), 1)
		self.assertEqual(build_snapshot()["telephony"]["unverified_calls"][self.channel.name], 1)

	def test_verify_error_is_none_and_counts(self):
		name = self.missed("v3")
		provider = FakeProvider()

		def boom(*a):
			raise StringeeError("x")

		provider.call_exists = boom
		registry.register_provider(provider)
		self.addCleanup(registry.unregister_provider, "Fake")
		with mock.patch("omni_crm.flags.sandbox", return_value=False):
			self.assertIsNone(worker_hooks.verify_missed_call(name, self.channel))
		self.assertEqual(frappe.db.get_value(CALL, name, "unverified"), 1)

	def test_sandbox_does_not_call_provider_or_mark(self):
		name = self.missed("v4")
		provider = FakeProvider()
		provider.call_exists = mock.Mock(return_value=False)
		registry.register_provider(provider)
		self.addCleanup(registry.unregister_provider, "Fake")
		with mock.patch("omni_crm.flags.sandbox", return_value=True):
			self.assertIsNone(worker_hooks.verify_missed_call(name, self.channel))
		provider.call_exists.assert_not_called()
		self.assertEqual(frappe.db.get_value(CALL, name, "unverified"), 0)


class TestEnqueueAndSweep(CallbackCase):
	def test_worker_enqueues_job_after_commit_for_missed_only(self):
		self.post(call("e1", "Missed", answered_at=None, duration=0, agent_ref=None))
		with mock.patch("frappe.enqueue") as enq:
			from omni_crm.pipeline.worker import process_identity

			process_identity(f"Telephony:{CUSTOMER_E164}", debounce_seconds=0)
		jobs = [c for c in enq.call_args_list if c.args and c.args[0] == callbacks.JOB]
		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0].kwargs["channel"], self.channel.name)

	def test_answered_call_enqueues_no_callback_job(self):
		self.post(call("e2"))
		with mock.patch("frappe.enqueue") as enq:
			from omni_crm.pipeline.worker import process_identity

			process_identity(f"Telephony:{CUSTOMER_E164}", debounce_seconds=0)
		self.assertFalse([c for c in enq.call_args_list if c.args and c.args[0] == callbacks.JOB])

	def test_enqueue_failure_is_swallowed_and_sweep_recovers(self):
		self.post(call("e3", "Missed", answered_at=None, duration=0, agent_ref=None))
		from omni_crm.pipeline.worker import process_identity

		with mock.patch("frappe.enqueue", side_effect=RuntimeError("redis")):
			process_identity(f"Telephony:{CUSTOMER_E164}", debounce_seconds=0)
		name = frappe.db.get_value(CALL, {"call_id": "td201-e3"}, "name")
		self.assertIsNone(self.task_of(name))
		self.assertEqual(callbacks.sweep_pending(), 0)  # chưa đủ 5 phút
		frappe.db.set_value(
			CALL,
			name,
			"creation",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-10),
			update_modified=False,
		)
		frappe.db.commit()
		self.assertGreaterEqual(callbacks.sweep_pending(), 1)
		self.assertTrue(self.task_of(name))
		self.assertEqual(callbacks.sweep_pending(), 0)  # chạy lại không tạo thêm

	def test_disabled_channel_makes_no_task(self):
		name = self.missed("e4")
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_cache()
		self.assertIsNone(callbacks.process_missed_call(name, self.channel.name))
		self.assertIsNone(self.task_of(name))

	def test_hooks_enabled(self):
		sched = frappe.get_hooks("scheduler_events")["cron"]
		self.assertIn("omni_crm.channels.telephony.registry.reconcile_yesterday", sched["30 1 * * *"])
		self.assertIn("omni_crm.channels.telephony.callbacks.sweep_pending", sched["*/5 * * * *"])


class TestAnonymousCaller(CallbackCase):
	def test_hidden_number_records_call_without_lead_identity_or_task(self):
		leads_before = frappe.db.count("CRM Lead")
		for cid in ("a1", "a2"):
			self.post(
				call(cid, "Missed", answered_at=None, duration=0, agent_ref=None, **{"from": "anonymous"})
			)
		keys = {
			r.name: r
			for r in frappe.get_all(
				"Omni Inbound Message",
				filters={"channel": self.channel.name},
				fields=["identity_key", "status"],
			)
		}
		key = next(iter({r.identity_key for r in keys.values()}))
		self.assertEqual(len({r.identity_key for r in keys.values()}), 1)
		with mock.patch("frappe.enqueue"):
			from omni_crm.pipeline.worker import process_identity

			process_identity(key, debounce_seconds=0)
		rows = self.inbound()
		self.assertEqual({r.status for r in rows}, {"Done"})
		self.assertEqual({r.lead for r in rows}, {None})
		self.assertEqual(frappe.db.count("CRM Lead"), leads_before)
		self.assertFalse(frappe.db.exists("Omni Identity", {"identity_key": key}))
		for cid in ("a1", "a2"):
			oc = frappe.get_doc(CALL, {"call_id": f"td201-{cid}"})
			self.assertEqual((oc.status, oc.lead, oc.callback_task), ("Missed", None, None))
			log = frappe.get_doc("CRM Call Log", oc.crm_call_log)
			self.assertEqual(log.reference_docname, None)
			self.assertIsNone(task_bridge.create_callback_task(oc))
		# chạy lại: không nhân đôi
		frappe.db.set_value("Omni Inbound Message", {"channel": self.channel.name}, "status", "New")
		frappe.db.commit()
		with mock.patch("frappe.enqueue"):
			process_identity(key, debounce_seconds=0)
		self.assertEqual(frappe.db.count(CALL, {"call_id": ["like", "td201-a%"]}), 2)


def ev(cid: str, **over) -> CallEvent:
	base = dict(
		call_id=cid,
		direction="Inbound",
		from_number=CUSTOMER_E164,
		to_number=HOTLINE,
		status="Ended",
		started_at=None,
		answered_at=None,
		ended_at=None,
		duration_seconds=60,
	)
	base.update(over)
	return CallEvent(**base)


class TestReconcile(CallbackCase):
	def setUp(self):
		super().setUp()
		now = frappe.utils.now_datetime()
		self.day = datetime.combine(now.date(), datetime.min.time()) - timedelta(days=1)
		at = lambda h: self.day.replace(hour=h)  # noqa: E731
		self.events = [
			ev("td201-r1", started_at=at(9), answered_at=at(9), ended_at=at(10)),
			ev("td201-r2", started_at=at(11), answered_at=at(11), ended_at=at(12)),
			ev("td201-r3", started_at=at(13), ended_at=at(13), status="Missed", duration_seconds=0),
		]
		self.provider = FakeProvider(self.events)
		registry.register_provider(self.provider)
		self.addCleanup(registry.unregister_provider, "Fake")
		sb = mock.patch("omni_crm.flags.sandbox", return_value=False)
		sb.start()
		self.addCleanup(sb.stop)
		self.deliver = mock.patch("omni_crm.monitoring.alerts.deliver_now")
		self.deliver = self.deliver.start()
		self.addCleanup(mock.patch.stopall)

	def reconcile(self):
		with mock.patch("frappe.enqueue"):
			return registry.reconcile_yesterday()[self.channel.name]

	def test_fills_exactly_two_missing_and_rerun_adds_none(self):
		# r1 đã có Omni Call cuối
		frappe.get_doc(
			{
				"doctype": CALL,
				"call_id": "td201-r1",
				"status": "Answered",
				"direction": "Inbound",
				"raw_events": "[]",
			}
		).insert()
		frappe.db.commit()
		first = self.reconcile()
		self.assertEqual((first["total"], first["existing"], first["filled"]), (3, 1, 2))
		rows = self.inbound()
		self.assertEqual(sorted(r.external_message_id for r in rows), ["td201-r2", "td201-r3"])
		self.assertEqual(
			{r.dedupe_key for r in rows}, {f"{self.channel.name}:td201-r2", f"{self.channel.name}:td201-r3"}
		)
		second = self.reconcile()
		self.assertEqual((second["total"], second["existing"], second["filled"]), (3, 3, 0))
		self.assertEqual(len(self.inbound()), 2)
		cached = frappe.cache().get_value(registry.reconcile_key(self.channel.name))
		self.assertEqual(cached["filled"], 0)

	def test_filled_calls_flow_through_worker(self):
		self.reconcile()
		self.run_worker()
		self.assertEqual(frappe.db.get_value(CALL, {"call_id": "td201-r2"}, "status"), "Answered")
		self.assertEqual(frappe.db.get_value(CALL, {"call_id": "td201-r3"}, "status"), "Missed")

	def test_alert_when_filled_over_one_percent(self):
		self.reconcile()
		kinds = [c.args[0].kind for c in self.deliver.call_args_list]
		self.assertEqual(kinds, ["doi_soat_bu_nhieu"])

	def test_no_alert_when_nothing_filled(self):
		self.reconcile()
		self.deliver.reset_mock()
		self.reconcile()
		self.deliver.assert_not_called()

	def test_provider_error_is_caught_alerted_and_recorded(self):
		def boom(*a):
			raise StringeeError("Call log quá nhiều trang, dừng để tránh cắt cụt kết quả")

		self.provider.list_calls = boom
		result = self.reconcile()
		self.assertEqual(result["error"], "StringeeError")
		self.assertEqual([c.args[0].kind for c in self.deliver.call_args_list], ["doi_soat_loi"])
		self.assertEqual(len(self.inbound()), 0)

	def test_max_pages_raises_in_real_stringee_provider(self):
		from omni_crm.channels.telephony import stringee

		prov = stringee.StringeeProvider()
		n = {"i": 0}

		def page(channel_doc, params):
			n["i"] += 1
			return {"calls": [{"id": f"c{n['i']}", "stop_time": 1}], "search_after": [n["i"]]}

		with (
			mock.patch.object(stringee, "MAX_PAGES", 3),
			mock.patch.object(prov, "_log_page", side_effect=page),
			mock.patch.object(
				stringee, "event_from_log", side_effect=lambda i: ev(i["id"], ended_at=self.day)
			),
			self.assertRaises(StringeeError),
		):
			prov.list_calls(self.day, self.day.replace(hour=23), self.channel)

	def test_sandbox_and_disabled_channel_are_skipped(self):
		with mock.patch("omni_crm.flags.sandbox", return_value=True):
			self.assertEqual(self.reconcile()["skipped"], "sandbox")
		self.assertEqual(len(self.inbound()), 0)
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		with mock.patch("frappe.enqueue"):
			self.assertEqual(registry.reconcile_yesterday(), {})

	def test_snapshot_shows_reconcile_result(self):
		self.reconcile()
		r = build_snapshot()["telephony"]["reconcile"][self.channel.name]
		self.assertEqual((r["total"], r["filled"]), (3, 3))


class TestOverdueMonitor(CallbackCase):
	def make_overdue(self):
		name = self.missed("o1")
		task = callbacks.process_missed_call(name)
		frappe.db.set_value(
			TASK, task, "due_date", frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-2)
		)
		frappe.db.commit()
		return task

	def test_snapshot_counts_overdue_and_lists_assignee(self):
		before = build_snapshot()["telephony"]["callbacks_overdue"]
		task = self.make_overdue()
		tel = build_snapshot()["telephony"]
		self.assertEqual(tel["callbacks_overdue"], before + 1)
		self.assertIn("Administrator", tel["callbacks_overdue_users"])
		frappe.db.set_value(TASK, task, "status", "Done")
		self.assertEqual(build_snapshot()["telephony"]["callbacks_overdue"], before)

	def test_alert_raised_for_assignee_and_admins(self):
		self.make_overdue()
		found = [a for a in alerts.evaluate(build_snapshot()) if a.kind == "goi_lai_qua_han"]
		self.assertEqual(len(found), 1)
		self.assertIn("Administrator", found[0].users)

	def test_no_alert_when_none_overdue(self):
		snap = build_snapshot()
		snap["telephony"]["callbacks_overdue"] = 0
		self.assertFalse([a for a in alerts.evaluate(snap) if a.kind == "goi_lai_qua_han"])

	def test_assignee_gets_notification_log_and_dedupe(self):
		alert = alerts.Alert(
			"goi_lai_qua_han", "t", "Có việc gọi lại quá hạn", "x", email=False, users=("Administrator",)
		)
		frappe.cache().delete_keys("omni:alert:goi_lai_qua_han")
		with mock.patch("frappe.sendmail"):
			self.assertTrue(alerts.deliver_now(alert))
			self.assertFalse(alerts.deliver_now(alert))  # chống lặp 30 phút
		self.assertEqual(
			frappe.db.count(
				"Notification Log", {"for_user": "Administrator", "subject": "Có việc gọi lại quá hạn"}
			),
			1,
		)
