# ruff: noqa: RUF001
"""V21-01 (F1): hàng đợi riêng `omni_leads` cho lead quảng cáo; thiếu khai báo thì rơi về `omni`."""

import threading
from contextlib import contextmanager
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.background_jobs import create_job_id, get_queue, get_queues_timeout, get_redis_conn

from omni_crm import queue as omni_queue
from omni_crm.api import webhooks
from omni_crm.ingest import gateway
from omni_crm.ingest.events import InboundEvent
from omni_crm.pipeline import sweeper
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests.ads_helpers import (
	PREFIX,
	SALE,
	AdsCase,
	body,
	call_webhook,
	make_channel,
	make_form,
	make_users,
)
from omni_crm.tests.fake_adapter import make_channel as make_zalo
from omni_crm.tests.test_web_form import FORM, payload

LEADS = {"omni_leads": {"timeout": 300}}
OMNI = {"omni": {"timeout": 300}}
LEAD_TYPES = ("Meta Lead Ads", "Google Lead Form", "Web Form")
OTHER_TYPES = ("Email", "Zalo OA", "Telephony", None, "Loại lạ")


@contextmanager
def workers(*extra: dict):
	"""Đặt khóa `workers` của cấu hình; `get_queues_timeout` của Frappe có lru_cache nên phải xóa cache hai đầu."""
	merged: dict = {}
	for d in extra:
		merged.update(d)
	get_queues_timeout.cache_clear()
	try:
		with mock.patch.dict(frappe.local.conf, {"workers": merged}):
			get_queues_timeout.cache_clear()
			yield
	finally:
		get_queues_timeout.cache_clear()


class TestQueueFor(FrappeTestCase):
	def setUp(self):
		frappe.cache().delete(frappe.cache().make_key(omni_queue.WARN_KEY))
		self.addCleanup(frappe.cache().delete, frappe.cache().make_key(omni_queue.WARN_KEY))

	def test_lead_channels_go_to_omni_leads_when_declared(self):
		with workers(OMNI, LEADS):
			for t in LEAD_TYPES:
				self.assertEqual(omni_queue.queue_for(t), "omni_leads", t)

	def test_other_channels_always_omni(self):
		with workers(OMNI, LEADS):
			for t in OTHER_TYPES:
				self.assertEqual(omni_queue.queue_for(t), "omni", t)

	def test_identity_key_prefix_decides_queue(self):
		with workers(OMNI, LEADS):
			self.assertEqual(omni_queue.queue_for_identity("Web Form:abc"), "omni_leads")
			self.assertEqual(omni_queue.queue_for_identity("Meta Lead Ads:1:2"), "omni_leads")
			self.assertEqual(omni_queue.queue_for_identity("Zalo OA:x"), "omni")
			self.assertEqual(omni_queue.queue_for_identity("khong-co-dau"), "omni")
			self.assertEqual(omni_queue.queue_for_identity(None), "omni")

	def test_missing_declaration_falls_back_to_omni_and_warns_once_per_hour(self):
		with workers(OMNI), mock.patch.object(frappe, "logger") as logger:
			for _ in range(3):
				self.assertEqual(omni_queue.queue_for("Web Form"), "omni")
			self.assertEqual(omni_queue.queue_for("Meta Lead Ads"), "omni")
			warn = logger.return_value.warning
			self.assertEqual(warn.call_count, 1)
			self.assertIn("omni_leads", warn.call_args.args[0])
			ttl = frappe.cache().ttl(frappe.cache().make_key(omni_queue.WARN_KEY))
			self.assertTrue(0 < ttl <= 3600)

	def test_non_lead_channel_never_warns(self):
		with workers(OMNI), mock.patch.object(frappe, "logger") as logger:
			omni_queue.queue_for("Zalo OA")
			logger.return_value.warning.assert_not_called()

	def test_redis_failure_in_warning_does_not_break_routing(self):
		with workers(OMNI), mock.patch.object(frappe, "cache", side_effect=RuntimeError("redis chết")):
			self.assertEqual(omni_queue.queue_for("Web Form"), "omni")


class TestEnqueueRouting(FrappeTestCase):
	def enqueue(self, key, **kw):
		with mock.patch("frappe.enqueue") as enq:
			gateway.enqueue_identity(key, after_commit=False, **kw)
		return enq.call_args

	def test_job_id_dedupe_and_method_unchanged_only_queue_differs(self):
		with workers(OMNI, LEADS):
			web = self.enqueue("Web Form:PLT1")
			zalo = self.enqueue("Zalo OA:PLT1")
		self.assertEqual(web.kwargs["queue"], "omni_leads")
		self.assertEqual(zalo.kwargs["queue"], "omni")
		for call, key in ((web, "Web Form:PLT1"), (zalo, "Zalo OA:PLT1")):
			self.assertEqual(call.args[0], "omni_crm.pipeline.worker.process_identity")
			self.assertEqual(call.kwargs["job_id"], f"omni-identity::{key}")
			self.assertTrue(call.kwargs["deduplicate"])
			self.assertEqual(call.kwargs["identity_key"], key)

	def test_follow_up_job_and_without_declaration(self):
		with workers(OMNI, LEADS):
			call = self.enqueue("Google Lead Form:PLT2", job_suffix="ab12")
		self.assertEqual(call.kwargs["queue"], "omni_leads")
		self.assertEqual(call.kwargs["job_id"], "omni-identity::Google Lead Form:PLT2::ab12")
		with workers(OMNI):
			self.assertEqual(self.enqueue("Google Lead Form:PLT2").kwargs["queue"], "omni")

	def test_sweeper_uses_same_routing(self):
		with workers(OMNI, LEADS), mock.patch("frappe.enqueue") as enq:
			sweeper._enqueue_all(["Meta Lead Ads:PLT3", "Email:PLT3"])
		queues = {c.kwargs["identity_key"]: c.kwargs["queue"] for c in enq.call_args_list}
		self.assertEqual(queues, {"Meta Lead Ads:PLT3": "omni_leads", "Email:PLT3": "omni"})

	def test_no_hardcoded_queue_names_left_in_code(self):
		import pathlib
		import re

		root = pathlib.Path(omni_queue.__file__).parent
		bad = []
		for p in root.rglob("*.py"):
			if "tests" in p.parts or p.name == "queue.py":
				continue
			for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
				if re.search(r"""(queue\s*=|QUEUE\s*=)\s*["']omni""", line):
					bad.append(f"{p.name}:{i}")
		self.assertEqual(bad, [])

	def test_real_redis_job_lands_in_omni_leads_queue(self):
		try:
			get_redis_conn().ping()
		except Exception as e:
			self.skipTest(f"Redis queue không dùng được: {type(e).__name__}")
		key = f"Web Form:{PREFIX}RQ"
		job_id = create_job_id(f"omni-identity::{key}")
		with workers(OMNI, LEADS):
			leads_q, omni_q = get_queue("omni_leads"), get_queue("omni")
			self.addCleanup(lambda: (leads_q.fetch_job(job_id) or mock.Mock()).delete())
			gateway.enqueue_identity(key, after_commit=False)
			self.assertIn(job_id, leads_q.job_ids)
			self.assertNotIn(job_id, omni_q.job_ids)


class TestReprocessRouting(AdsCase):
	def test_reprocess_uses_same_routing(self):
		from omni_crm.api import admin

		ch = make_channel("Web Form", f"{PREFIX} web")
		doc = frappe.get_doc(
			{
				"doctype": "Omni Inbound Message",
				"dedupe_key": f"{ch.name}:v2101-rp",
				"channel": ch.name,
				"channel_type": "Web Form",
				"event_type": "form_lead",
				"external_message_id": "v2101-rp",
				"external_sender_id": f"{PREFIX}RP",
				"identity_key": f"Web Form:{PREFIX}RP",
				"received_at": now_datetime(),
				"status": "Manual",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		with workers(OMNI, LEADS), mock.patch("frappe.enqueue") as enq:
			self.assertEqual(admin.reprocess([doc.name])["reset"], 1)
			frappe.db.after_commit.run()
		self.assertEqual(enq.call_args.kwargs["queue"], "omni_leads")
		self.assertEqual(enq.call_args.kwargs["job_id"], f"omni-identity::Web Form:{PREFIX}RP")


class TestSameCustomerNoOverlap(AdsCase):
	"""Một khách vừa điền form (omni_leads) vừa nhắn Zalo (omni), hai worker chạy cùng lúc -> đúng một Lead."""

	def test_form_and_zalo_same_phone_concurrent_workers_one_lead(self):
		make_users()
		make_channel("Web Form", f"{PREFIX} web")
		make_form("Website", FORM, "Web form", default_owner=SALE)
		zalo = make_zalo(f"{PREFIX} zalo", enabled=1)
		frappe.db.commit()
		call_webhook(
			webhooks.web_form, body(payload(submission_id="td505-v2101-sub")), args={"ch": f"{PREFIX} web"}
		)
		web_key = frappe.get_all(
			"Omni Inbound Message",
			filters={"external_message_id": "td505-v2101-sub"},
			pluck="identity_key",
		)[0]
		with mock.patch("frappe.enqueue"):
			gateway.save_inbound(
				InboundEvent(
					channel=zalo.name,
					channel_type="Zalo OA",
					event_type="user_info",
					external_message_id="v2101-z1",
					external_sender_id=f"{PREFIX}ZV21",
					received_at=now_datetime(),
					text="Tôi để lại số điện thoại",
					sender_profile={"phone": "0912345611", "display_name": "TD505 Lê Văn Cường"},
				)
			)
			frappe.db.after_commit.run()
		frappe.db.commit()
		zalo_key = f"Zalo OA:{PREFIX}ZV21"
		with workers(OMNI, LEADS):
			self.assertEqual(omni_queue.queue_for_identity(web_key), "omni_leads")
			self.assertEqual(omni_queue.queue_for_identity(zalo_key), "omni")

		site, sites_path = frappe.local.site, frappe.local.sites_path
		results: dict = {}
		barrier = threading.Barrier(2)

		def run(label, key):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			try:
				barrier.wait(timeout=30)
				results[label] = process_identity(key, debounce_seconds=0)
				frappe.db.commit()
			except BaseException as e:
				results[label] = e
				frappe.db.rollback()
			finally:
				frappe.destroy()

		with (
			mock.patch("frappe.enqueue"),
			mock.patch("omni_crm.channels.zalo.worker_hooks.ZaloClient"),
		):
			threads = [
				threading.Thread(target=run, args=("web", web_key)),
				threading.Thread(target=run, args=("zalo", zalo_key)),
			]
			for t in threads:
				t.start()
			for t in threads:
				t.join(timeout=120)
		frappe.db.rollback()
		self.assertEqual(results.get("web"), "done", results)
		self.assertEqual(results.get("zalo"), "done", results)
		rows = frappe.get_all(
			"Omni Inbound Message",
			filters={"identity_key": ["in", [web_key, zalo_key]]},
			fields=["status", "lead", "last_error"],
		)
		self.assertEqual([r.status for r in rows], ["Done", "Done"], rows)
		self.assertEqual(len({r.lead for r in rows}), 1, rows)
		self.assertEqual(frappe.db.count("CRM Lead", {"omni_phone_e164": "+84912345611"}), 1)


class TestLeadQueueMonitoring(FrappeTestCase):
	"""Khai báo `omni_leads` mà không có worker nghe thì lead nằm im: phải có cảnh báo (rà soát V21-01)."""

	def kinds(self, lead_queue):
		from omni_crm.monitoring import alerts
		from omni_crm.tests.test_monitoring import MONDAY_10H, synthetic

		snap = synthetic()
		snap["lead_queue"] = lead_queue
		return [a.kind for a in alerts.evaluate(snap, now=MONDAY_10H)]

	def test_alert_when_declared_has_jobs_and_no_worker(self):
		self.assertIn("omni_leads_khong_worker", self.kinds({"declared": True, "length": 3, "workers": 0}))

	def test_no_alert_when_worker_listening_empty_queue_undeclared_or_redis_unknown(self):
		for lq in (
			{"declared": True, "length": 3, "workers": 2},
			{"declared": True, "length": 0, "workers": 0},
			{"declared": False, "length": None, "workers": None},
			{"declared": True, "length": None, "workers": None},
		):
			self.assertNotIn("omni_leads_khong_worker", self.kinds(lq), lq)
		from omni_crm.monitoring import alerts
		from omni_crm.tests.test_monitoring import MONDAY_10H, synthetic

		self.assertNotIn(
			"omni_leads_khong_worker", [a.kind for a in alerts.evaluate(synthetic(), now=MONDAY_10H)]
		)

	def test_snapshot_reports_real_queue_length_and_zero_workers(self):
		from omni_crm.monitoring.snapshot import _lead_queue

		try:
			get_redis_conn().ping()
		except Exception as e:
			self.skipTest(f"Redis queue không dùng được: {type(e).__name__}")
		with workers(OMNI):
			self.assertEqual(_lead_queue(), {"declared": False, "length": None, "workers": None})
		with workers(OMNI, LEADS):
			got = _lead_queue()
		self.assertTrue(got["declared"])
		self.assertIsInstance(got["length"], int)
		self.assertIsInstance(got["workers"], int)
