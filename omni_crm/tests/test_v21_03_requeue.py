"""V21-03 (F3): phân loại lỗi Meta và tự kéo lại tin Manual (lỗi tạm thời) sau sự cố Graph. Graph API được giả: CHƯA THỬ VỚI META THẬT."""

from datetime import timedelta
from unittest import mock

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from omni_crm import flags
from omni_crm.channels.meta_leads import reconcile
from omni_crm.channels.meta_leads.errors import (
	TAM_THOI,
	TRANSIENT,
	VINH_VIEN,
	MetaError,
	classify_meta_error,
)
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.pipeline.retry import TransientError
from omni_crm.tests.ads_helpers import PAGE_TOKEN, FakeResponse, graph_router, inbound, work
from omni_crm.tests.test_meta_leads import GET, MetaCase
from omni_crm.tests.test_v21_02_token_meta import set_single

SETTING_FIELDS = ("meta_requeue_hours", "meta_requeue_batch", "meta_requeue_disabled")
ENQUEUE = "omni_crm.ingest.gateway.frappe.enqueue"
SLEEP = "omni_crm.channels.meta_leads.client.time.sleep"


def err(code: int, status: int = 400) -> FakeResponse:
	return FakeResponse({"error": {"code": code, "message": "x"}}, status)


class TestClassify(MetaCase):
	def test_network_and_timeout_are_transient(self):
		self.assertEqual(classify_meta_error(requests.Timeout()), TAM_THOI)
		self.assertEqual(classify_meta_error(requests.ConnectionError()), TAM_THOI)
		self.assertEqual(classify_meta_error(TimeoutError()), TAM_THOI)
		self.assertEqual(classify_meta_error(TransientError("x")), TAM_THOI)
		self.assertEqual(classify_meta_error(MetaError(TRANSIENT, "x")), TAM_THOI)

	def test_http_5xx_and_429_are_transient(self):
		for status in (500, 502, 503, 504, 429):
			self.assertEqual(classify_meta_error(FakeResponse({}, status)), TAM_THOI, status)

	def test_rate_limit_codes_are_transient(self):
		for code in (1, 2, 4, 17, 32, 341, 613, 80000, 80001, 80005, 80014):
			self.assertEqual(classify_meta_error(err(code)), TAM_THOI, code)
			self.assertEqual(classify_meta_error({"error": {"code": code}}), TAM_THOI, code)

	def test_token_and_permission_are_permanent(self):
		for code in (190, 102, 10, 200, 210, 299):
			self.assertEqual(classify_meta_error(err(code)), VINH_VIEN, code)
		self.assertEqual(classify_meta_error(FakeResponse({}, 401)), VINH_VIEN)
		self.assertEqual(classify_meta_error(FakeResponse({}, 403)), VINH_VIEN)
		self.assertEqual(classify_meta_error(MetaError("token", "x")), VINH_VIEN)

	def test_missing_lead_and_unknown_are_permanent(self):
		self.assertEqual(classify_meta_error(err(100)), VINH_VIEN)
		self.assertEqual(
			classify_meta_error(FakeResponse({"error": {"code": 100, "error_subcode": 33}}, 400)), VINH_VIEN
		)
		self.assertEqual(classify_meta_error(ValueError("form_lead thiếu dữ liệu")), VINH_VIEN)
		self.assertEqual(classify_meta_error(FakeResponse(None, 400)), VINH_VIEN)
		self.assertEqual(classify_meta_error({"khong": "co loi"}), VINH_VIEN)

	def test_wrapped_exceptions_keep_their_cause(self):
		try:
			try:
				raise MetaError("token", "x")
			except MetaError as e:
				raise ValueError("meta_token") from e
		except ValueError as wrapped:
			self.assertEqual(classify_meta_error(wrapped), VINH_VIEN)
		try:
			try:
				raise MetaError(TRANSIENT, "x")
			except MetaError as e:
				raise TransientError("t") from e
		except TransientError as wrapped:
			self.assertEqual(classify_meta_error(wrapped), TAM_THOI)


class RequeueCase(MetaCase):
	def setUp(self):
		super().setUp()
		for f in SETTING_FIELDS:
			set_single(self, f, "")  # đảm bảo dọn dù test không đặt
		frappe.db.sql(
			"delete from tabSingles where doctype='Omni Settings' and field in %s", (SETTING_FIELDS,)
		)
		frappe.db.commit()
		self.patches = [mock.patch(SLEEP), mock.patch(ENQUEUE)]
		self.enqueue = self.patches[1].start()
		self.patches[0].start()
		self.addCleanup(lambda: [p.stop() for p in self.patches])

	def make_manual(self, fail, form_id_suffix=""):
		"""Webhook rồi chạy worker 3 lần khi Graph lỗi `fail` -> tin Manual (hết lượt thử lại thật). Trả tên tin."""
		self.post()
		row = inbound(self.channel.name)[0]
		for i in range(3):
			if i:  # đẩy hạn thử lại về quá khứ (COMMIT: worker rollback đầu mỗi vòng)
				frappe.db.set_value(
					"Omni Inbound Message",
					row.name,
					"next_attempt_at",
					add_to_date(now_datetime(), minutes=-1),
					update_modified=False,
				)
				frappe.db.commit()
			with mock.patch(GET, side_effect=graph_router(fail=fail)):
				work(row.identity_key)
		doc = frappe.db.get_value(
			"Omni Inbound Message", row.name, ["status", "error_kind", "attempts"], as_dict=True
		)
		self.assertEqual(doc.status, "Manual")
		return row.name

	def reconcile(self, **graph):
		with mock.patch(GET, side_effect=graph_router(**graph)):
			return reconcile.reconcile_channel(self.channel.name)

	def doc(self, name):
		return frappe.db.get_value(
			"Omni Inbound Message",
			name,
			["status", "error_kind", "requeue_count", "attempts", "error_alerted", "lead"],
			as_dict=True,
		)


class TestRecordKind(RequeueCase):
	def test_graph_5xx_goes_manual_as_tam_thoi(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		self.assertEqual(self.doc(name).error_kind, TAM_THOI)

	def test_bad_token_goes_manual_as_vinh_vien(self):
		name = self.make_manual((401, {"error": {"code": 190}}))
		self.assertEqual(self.doc(name).error_kind, VINH_VIEN)

	def test_missing_lead_goes_manual_as_vinh_vien(self):
		name = self.make_manual((400, {"error": {"code": 100, "error_subcode": 33}}))
		self.assertEqual(self.doc(name).error_kind, VINH_VIEN)

	def test_error_text_does_not_leak_token(self):
		name = self.make_manual((500, {"error": {"code": 2, "message": PAGE_TOKEN}}))
		self.assertNotIn(PAGE_TOKEN, frappe.db.get_value("Omni Inbound Message", name, "last_error") or "")


class TestRequeue(RequeueCase):
	def test_graph_back_requeue_creates_the_lead_without_duplicates(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		before = frappe.db.count("Omni Inbound Message", {"channel": self.channel.name})
		stats = self.reconcile()
		self.assertEqual((stats["requeued"], stats["permanent"], stats["exhausted"]), (1, 0, 0))
		d = self.doc(name)
		self.assertEqual((d.status, d.requeue_count, d.attempts), ("New", 1, 0))
		# vào lại hàng đợi qua queue_for (lead quảng cáo -> omni khi omni_leads chưa khai báo), job_id/deduplicate như cũ
		kw = self.enqueue.call_args.kwargs
		self.assertEqual(kw["job_id"], f"omni-identity::{inbound(self.channel.name)[0].identity_key}")
		self.assertTrue(kw["deduplicate"])
		self.assertEqual(kw["queue"], "omni")
		with mock.patch(GET, side_effect=graph_router()):
			work(inbound(self.channel.name)[0].identity_key)
		d = self.doc(name)
		self.assertEqual(d.status, "Done")
		self.assertTrue(d.lead)
		self.assertEqual(frappe.db.count("Omni Inbound Message", {"channel": self.channel.name}), before)
		self.assertEqual(frappe.db.count("CRM Lead", {"facebook_lead_id": "TD505LG1"}), 1)
		again = self.reconcile()  # lượt sau: không còn gì để kéo, không tạo thêm
		self.assertEqual((again["requeued"], again["created"]), (0, 0))
		self.assertEqual(frappe.db.count("CRM Lead", {"facebook_lead_id": "TD505LG1"}), 1)

	def test_uses_leads_queue_when_declared(self):
		self.make_manual((500, {"error": {"code": 2}}))
		with mock.patch("omni_crm.queue.leads_queue_declared", return_value=True):
			self.reconcile()
		self.assertEqual(self.enqueue.call_args.kwargs["queue"], "omni_leads")

	def test_graph_still_down_does_not_burn_a_requeue(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		stats = self.reconcile(fail=(500, {"error": {"code": 2}}))
		self.assertEqual((stats["errors"], stats["requeued"]), (1, 0))
		d = self.doc(name)
		self.assertEqual((d.status, d.requeue_count), ("Manual", 0))

	def test_stops_and_alerts_after_three_requeues(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		frappe.db.set_value("Omni Inbound Message", name, "requeue_count", 3, update_modified=False)
		frappe.db.commit()
		with mock.patch.object(reconcile, "deliver_now") as alert:
			stats = self.reconcile()
			self.reconcile()
		self.assertEqual((stats["requeued"], stats["exhausted"]), (0, 1))
		self.assertEqual(self.doc(name).status, "Manual")
		self.assertEqual(alert.call_count, 1)  # tin chỉ được báo một lần
		self.assertEqual(self.doc(name).error_alerted, 1)

	def test_permanent_errors_are_not_requeued_and_alert_is_aggregated(self):
		name = self.make_manual((401, {"error": {"code": 190}}))
		extra = [
			frappe.copy_doc(frappe.get_doc("Omni Inbound Message", name), ignore_no_copy=False)
			for _i in range(2)
		]
		for i, doc in enumerate(extra):
			doc.dedupe_key = f"{self.channel.name}:TD505X{i}"
			doc.status, doc.error_kind = "Manual", VINH_VIEN
			doc.insert(ignore_permissions=True)
		frappe.db.commit()
		with mock.patch.object(reconcile, "deliver_now") as alert:
			stats = self.reconcile()
		self.assertEqual((stats["requeued"], stats["permanent"]), (0, 3))
		self.assertEqual(alert.call_count, 1)  # MỘT cảnh báo cho 3 tin
		sent = alert.call_args.args[0]
		self.assertEqual(sent.kind, "meta_loi_vinh_vien")
		self.assertIn("3 tin", sent.detail)
		self.assertNotIn(PAGE_TOKEN, sent.subject + sent.detail)
		self.assertEqual(self.doc(name).status, "Manual")
		self.assertFalse(self.enqueue.called)
		with mock.patch.object(reconcile, "deliver_now") as alert2:  # giờ sau: đã báo rồi, không báo lại
			self.reconcile()
		self.assertEqual(alert2.call_count, 0)

	def test_outside_window_not_requeued(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		frappe.db.set_value(
			"Omni Inbound Message",
			name,
			"creation",
			now_datetime() - timedelta(hours=73),
			update_modified=False,
		)
		frappe.db.commit()
		self.assertEqual(self.reconcile()["requeued"], 0)
		set_single(self, "meta_requeue_hours", "100")  # đặt được: nới cửa sổ thì kéo lại
		self.assertEqual(self.reconcile()["requeued"], 1)

	def test_batch_limit(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		for i in range(2):
			doc = frappe.copy_doc(frappe.get_doc("Omni Inbound Message", name))
			doc.dedupe_key = f"{self.channel.name}:TD505B{i}"
			doc.status = "Manual"
			doc.insert(ignore_permissions=True)
		frappe.db.commit()
		set_single(self, "meta_requeue_batch", "2")
		self.assertEqual(self.reconcile()["requeued"], 2)
		self.assertEqual(self.reconcile()["requeued"], 1)

	def test_switch_off(self):
		name = self.make_manual((500, {"error": {"code": 2}}))
		set_single(self, "meta_requeue_disabled", "1")
		self.assertFalse(flags.meta_requeue_enabled())
		stats = self.reconcile()
		self.assertEqual(stats["requeued"], 0)
		self.assertEqual(self.doc(name).status, "Manual")

	def test_defaults_when_settings_never_saved(self):
		self.assertTrue(flags.meta_requeue_enabled())
		self.assertEqual(flags.ads_setting("meta_requeue_hours"), 72)
		self.assertEqual(flags.ads_setting("meta_requeue_batch"), 200)
		set_single(self, "meta_requeue_hours", "0")
		self.assertEqual(flags.ads_setting("meta_requeue_hours"), 72)

	def test_run_result_and_snapshot(self):
		self.make_manual((500, {"error": {"code": 2}}))
		with mock.patch(GET, side_effect=graph_router()):
			total = reconcile.run()
		self.assertEqual(total["requeued"], 1)
		snap = build_snapshot()["meta_requeue"]
		self.assertEqual(snap["last_run"]["requeued"], 1)
		self.assertEqual(snap["requeued_total"], 1)
		self.assertEqual(snap["manual_tam_thoi"], 0)
