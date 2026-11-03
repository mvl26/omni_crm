"""V21-04 (F4): webhook không mất tin khi chờ khóa; hàng dự phòng (ADR 0014)."""

import json
import os
import shutil
import stat
import threading
import time
from types import SimpleNamespace
from unittest import mock

import frappe
import pymysql
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm import hooks
from omni_crm.api import admin, webhooks
from omni_crm.channels.registry import register_adapter, unregister_adapter
from omni_crm.channels.zalo import signature
from omni_crm.ingest import spool
from omni_crm.monitoring import alerts
from omni_crm.tests.fake_adapter import FakeAdapter, make_channel
from omni_crm.tests.test_locks import _in_thread

NAME = "TD2104 kenh"
APP_ID = "app-td2104"
OA_SECRET = "bi-mat-oa-td2104-khong-duoc-vao-spool"
TS = "1700000000000"


def body_of(*ids) -> bytes:
	events = [{"id": i, "from": "U2104", "text": "xin chao 0900000111"} for i in ids]
	return json.dumps({"events": events, "timestamp": TS}).encode()


def request_of(body: bytes, good_mac: bool = True):
	mac = signature.compute_mac(APP_ID, body, TS, OA_SECRET) if good_mac else "0" * 64
	headers = {
		"X-Fake-Signature": FakeAdapter.GOOD_SIGNATURE,
		"X-ZEvent-Signature": f"mac={mac}",
		"Authorization": "Bearer khong-duoc-vao-spool",
		"Cookie": "sid=khong-duoc-vao-spool",
	}
	return SimpleNamespace(
		method="POST", content_length=len(body), get_data=lambda: body, headers=headers, args={"ch": NAME}
	)


def db_gone(*_a, **_k):
	raise pymysql.err.OperationalError(2006, "MySQL server has gone away")


class SpoolCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		self.wipe()
		self.channel = make_channel(NAME, enabled=1, zalo_app_id=APP_ID, zalo_oa_secret_key=OA_SECRET)
		frappe.db.commit()
		self.addCleanup(self.wipe)
		register_adapter("Zalo OA", FakeAdapter())
		self.addCleanup(unregister_adapter, "Zalo OA")
		self._old_req = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", self._old_req)
		for target in ("frappe.enqueue",):
			p = mock.patch(target)
			p.start()
			self.addCleanup(p.stop)
		frappe.cache().delete_keys("omni:")
		self.other = None

	def wipe(self):
		if getattr(self, "other", None):
			self.other.rollback()
			self.other.close()
			self.other = None
		frappe.db.rollback()
		frappe.db.delete("Omni Inbound Message", {"channel": NAME})
		frappe.db.delete("Omni Channel", {"channel_name": NAME})
		frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": NAME})
		frappe.db.delete("Singles", {"doctype": "Omni Settings", "field": "webhook_spool_disabled"})
		frappe.db.commit()
		shutil.rmtree(spool.spool_dir(), ignore_errors=True)

	def call(self, body: bytes, good_mac: bool = True):
		frappe.local.request = request_of(body, good_mac)
		frappe.local.form_dict = frappe._dict(ch=NAME)
		frappe.local.response = frappe._dict()
		result = webhooks.zalo()
		return frappe.local.response.get("http_status_code", 200), result

	def stored(self) -> int:
		frappe.db.rollback()
		return frappe.db.count("Omni Inbound Message", {"channel": NAME})

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


class TestNoLockWait(SpoolCase):
	def test_twenty_threads_same_event_one_record_no_503(self):
		body = body_of("conc1")
		barrier = threading.Barrier(20)

		def work():
			frappe.local.request = request_of(body)
			frappe.local.form_dict = frappe._dict(ch=NAME)
			frappe.local.response = frappe._dict()
			barrier.wait(15)
			webhooks.zalo()
			return frappe.local.response.get("http_status_code", 200)

		runs = [_in_thread(work) for _ in range(20)]
		for t, _ in runs:
			t.join(60)
		statuses = [r.get("value") for _, r in runs]
		self.assertEqual(statuses, [200] * 20, [r.get("error") for _, r in runs])
		self.assertEqual(self.stored(), 1)
		self.assertEqual(spool.spool_count(), 0)

	def test_series_counter_row_locked_by_someone_else_does_not_block_the_webhook(self):
		"""Nguyên nhân cũ: tên `OIN-.YYYY.-.########` lấy bộ đếm `tabSeries` (FOR UPDATE tới commit) nên mọi webhook xếp hàng."""
		self.connect_other().execute("select name from `tabSeries` where name like 'OIN-%%' for update")
		started = time.monotonic()
		status, result = self.call(body_of("series1"))
		self.assertEqual((status, result["saved"]), (200, 1))
		self.assertLess(time.monotonic() - started, 2)
		self.assertEqual(self.stored(), 1)

	def test_new_names_sort_after_old_and_are_unique(self):
		from omni_crm.ingest.gateway import new_inbound_name

		names = [new_inbound_name() for _ in range(2000)]
		self.assertEqual(len(set(names)), 2000)
		self.assertGreater(min(names), "OIN-2026-09999999")
		self.assertEqual({len(n) for n in names}, {len("OIN-2026-") + 19})


class TestSpoolFlow(SpoolCase):
	def hold_same_message_key(self):
		self.connect_other().execute(
			"insert into `tabOmni Inbound Message` (name, dedupe_key, identity_key, status, creation, modified)"
			" values (%s, %s, %s, 'New', now(), now())",
			("TD2104-HOLD", f"{NAME}:lock1", "Zalo OA:td2104-hold"),
		)

	def test_locked_db_goes_to_spool_returns_200_then_reinserted_exactly_once(self):
		self.hold_same_message_key()
		started = time.monotonic()
		with mock.patch.dict(frappe.conf, {webhooks.LOCK_WAIT_CONF: 1}):
			status, result = self.call(body_of("lock1"))
		self.assertEqual(status, 200)
		self.assertLess(time.monotonic() - started, 5)
		self.assertEqual(self.stored(), 0)
		self.assertEqual(spool.spool_count(), 1)
		self.other.rollback()  # khóa nhả
		first = spool.drain()
		self.assertEqual((first["saved"], first["dropped"]), (1, 0))
		self.assertEqual(self.stored(), 1)
		self.assertEqual(spool.spool_count(), 0)
		self.assertEqual(spool.drain()["saved"], 0)
		self.assertEqual(self.stored(), 1)

	def test_lost_connection_goes_to_spool_and_is_reinserted_once_even_if_provider_also_resends(self):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			status, _ = self.call(body_of("gone1", "gone2"))
		self.assertEqual(status, 200)
		self.assertEqual((self.stored(), spool.spool_count()), (0, 1))
		self.assertEqual(self.call(body_of("gone1", "gone2"))[0], 200)  # nhà cung cấp gửi lại, DB đã sống
		self.assertEqual(self.stored(), 2)
		out = spool.drain()
		self.assertEqual((out["saved"], out["dup"]), (0, 1))
		self.assertEqual((self.stored(), spool.spool_count()), (2, 0))

	def test_drain_keeps_lines_while_db_is_down_then_inserts_later(self):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			self.call(body_of("keep1"))
		with mock.patch.object(spool, "_insert_entry", side_effect=db_gone):
			out = spool.drain()
		self.assertEqual(out["kept"], 1)
		self.assertEqual((self.stored(), spool.spool_count()), (0, 1))
		self.assertEqual(spool.drain()["saved"], 1)
		self.assertEqual((self.stored(), spool.spool_count()), (1, 0))

	def test_received_time_is_kept_on_reinsert(self):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			self.call(body_of("time1"))
		entry = next(spool._iter_entries(spool._files()[0]))
		spool.drain()
		got = frappe.db.get_value("Omni Inbound Message", {"channel": NAME}, "received_at")
		self.assertLessEqual(abs((got - frappe.utils.get_datetime(entry["recv"])).total_seconds()), 31)

	def test_both_db_and_spool_broken_gives_503_critical_log_and_immediate_alert(self):
		logger = mock.MagicMock()
		with (
			mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone),
			mock.patch.object(spool, "append", side_effect=spool.SpoolError("OSError")),
			mock.patch("frappe.logger", return_value=logger),
			mock.patch("omni_crm.monitoring.alerts.deliver_now") as deliver,
		):
			status, _ = self.call(body_of("dead1"))
		self.assertEqual(status, 503)
		logger.critical.assert_called_once()
		self.assertNotIn("0900000111", str(logger.critical.call_args))
		deliver.assert_called_once()
		self.assertEqual(deliver.call_args.args[0].kind, "webhook_spool_hong")

	def test_spool_full_by_lines_and_by_bytes_gives_503(self):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			with mock.patch.dict(frappe.conf, {"omni_spool_max_lines": 1}):
				self.assertEqual(self.call(body_of("full1"))[0], 200)
				self.assertEqual(self.call(body_of("full2"))[0], 503)
			self.assertEqual(spool.spool_count(), 1)
			with mock.patch.dict(frappe.conf, {"omni_spool_max_bytes": 100}):
				self.assertEqual(self.call(body_of("full3"))[0], 503)
		self.assertEqual(spool.spool_count(), 1)

	def test_switch_off_keeps_old_behaviour_503_and_writes_nothing(self):
		frappe.db.set_single_value("Omni Settings", "webhook_spool_disabled", 1)
		frappe.db.commit()
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			self.assertEqual(self.call(body_of("off1"))[0], 503)
		self.assertEqual(spool.spool_count(), 0)

	def test_unauthenticated_request_never_reaches_spool(self):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			req = request_of(body_of("noauth"))
			req.headers["X-Fake-Signature"] = "sai"
			frappe.local.request = req
			frappe.local.form_dict = frappe._dict(ch=NAME)
			frappe.local.response = frappe._dict()
			webhooks.zalo()
		self.assertEqual(frappe.local.response["http_status_code"], 401)
		self.assertEqual(spool.spool_count(), 0)

	def test_non_db_error_is_not_spooled(self):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=ValueError("loi code")):
			with self.assertRaises(ValueError):
				self.call(body_of("code1"))
		self.assertEqual(spool.spool_count(), 0)


class TestSpoolSafety(SpoolCase):
	def spool_one(self, ident="safe1"):
		with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=db_gone):
			self.assertEqual(self.call(body_of(ident))[0], 200)

	def test_files_are_private_and_permissions_tight(self):
		self.spool_one()
		path = spool.spool_dir()
		site = frappe.get_site_path()
		self.assertTrue(path.startswith(os.path.join(site, "private")))
		self.assertFalse(path.startswith(os.path.join(site, "private", "files")))
		self.assertFalse(path.startswith(os.path.join(site, "public")))
		self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
		for f in os.listdir(path):
			self.assertEqual(stat.S_IMODE(os.stat(os.path.join(path, f)).st_mode), 0o600, f)

	def test_dir_permissions_are_tightened_even_if_it_pre_exists_loose(self):
		os.makedirs(spool.spool_dir(), exist_ok=True)
		os.chmod(spool.spool_dir(), 0o755)
		self.spool_one()
		self.assertEqual(stat.S_IMODE(os.stat(spool.spool_dir()).st_mode), 0o700)

	def test_no_secret_or_auth_header_in_spool_line(self):
		self.spool_one()
		text = open(os.path.join(spool.spool_dir(), spool.ACTIVE), encoding="utf-8").read()
		for secret in (OA_SECRET, "khong-duoc-vao-spool", "Authorization", "Cookie", "sid="):
			self.assertNotIn(secret, text)
		entry = json.loads(text)
		self.assertEqual(set(entry), {"v", "id", "ch", "ct", "recv", "hdr", "raw", "seal"})
		self.assertEqual(set(entry["hdr"]), {"x-zevent-signature"})
		conf_key = str(frappe.conf.get("encryption_key") or "x-none")
		self.assertNotIn(conf_key, text)

	def test_tampered_payload_is_dropped_and_log_has_no_payload(self):
		self.spool_one("tamper1")
		path = os.path.join(spool.spool_dir(), spool.ACTIVE)
		entry = json.loads(open(path).read())
		import base64

		entry["raw"] = base64.b64encode(body_of("tamper-evil")).decode()
		open(path, "w").write(json.dumps(entry) + "\n")
		logger = mock.MagicMock()
		with mock.patch("frappe.logger", return_value=logger):
			out = spool.drain()
		self.assertEqual((out["dropped"], out["saved"]), (1, 0))
		self.assertEqual((self.stored(), spool.spool_count()), (0, 0))
		logged = str(logger.warning.call_args_list)
		self.assertIn("seal_sai", logged)
		self.assertNotIn("0900000111", logged)
		self.assertNotIn("tamper", logged)

	def test_wrong_provider_signature_with_valid_seal_is_dropped(self):
		body = body_of("badsig1")
		req = request_of(body, good_mac=False)
		spool.append(spool.build_entry(NAME, "Zalo OA", body, req.headers))
		out = spool.drain()
		self.assertEqual((out["dropped"], out["saved"]), (1, 0))
		self.assertEqual((self.stored(), spool.spool_count()), (0, 0))

	def test_missing_signature_and_foreign_seal_and_garbage_lines_are_dropped(self):
		body = body_of("junk1")
		spool.append(spool.build_entry(NAME, "Zalo OA", body, {}))  # thiếu chữ ký
		forged = spool.build_entry(NAME, "Zalo OA", body, request_of(body).headers)
		forged["seal"] = "0" * 64
		spool.append(forged)
		with open(os.path.join(spool.spool_dir(), spool.ACTIVE), "ab") as f:
			f.write(b"khong phai json\n")
		self.assertEqual(spool.spool_count(), 3)
		out = spool.drain()
		self.assertEqual((out["dropped"], out["saved"]), (3, 0))
		self.assertEqual((self.stored(), spool.spool_count()), (0, 0))

	def test_expired_lines_are_removed_but_fresh_ones_kept_while_db_down(self):
		body = body_of("old1")
		headers = request_of(body).headers
		old = spool.build_entry(NAME, "Zalo OA", body, headers, add_to_date(now_datetime(), days=-15))
		fresh = spool.build_entry(NAME, "Zalo OA", body_of("new1"), request_of(body_of("new1")).headers)
		spool.append(old)
		spool.append(fresh)
		with (
			mock.patch.object(spool, "_insert_entry", side_effect=db_gone),
			mock.patch("omni_crm.monitoring.alerts.deliver_now") as deliver,
		):
			out = spool.drain()
		self.assertEqual((out["expired"], out["kept"]), (1, 2))
		self.assertEqual(spool.spool_count(), 1)
		self.assertEqual(deliver.call_args.args[0].kind, "webhook_spool_het_han")

	def test_concurrent_appends_keep_every_line_valid(self):
		def put(i):
			spool.append(spool.build_entry(NAME, "Zalo OA", body_of(f"c{i}"), {}))

		runs = [_in_thread(lambda i=i: put(i)) for i in range(20)]
		for t, res in runs:
			t.join(20)
			self.assertNotIn("error", res)
		entries = list(spool._iter_entries(spool._files()[0]))
		self.assertEqual(len(entries), 20)
		self.assertTrue(all(e is not None for e in entries))

	def test_count_and_oldest_age(self):
		self.assertEqual((spool.spool_count(), spool.oldest_age_seconds()), (0, None))
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}, add_to_date(now_datetime(), minutes=-12)))
		self.assertEqual(spool.spool_count(), 1)
		self.assertGreaterEqual(spool.oldest_age_seconds(), 12 * 60)
		self.assertTrue(spool.stats()["stale"])

	def test_alert_when_a_message_sits_more_than_ten_minutes(self):
		snap = {"spool": {"count": 2, "oldest_age_seconds": 11 * 60, "failed_at": None}}
		kinds = [a.kind for a in alerts.evaluate(self._base_snapshot(snap))]
		self.assertIn("webhook_spool_cu", kinds)
		snap["spool"]["oldest_age_seconds"] = 9 * 60
		kinds = [a.kind for a in alerts.evaluate(self._base_snapshot(snap))]
		self.assertNotIn("webhook_spool_cu", kinds)

	def _base_snapshot(self, extra):
		from omni_crm.monitoring.snapshot import build_snapshot

		snap = build_snapshot()
		snap.update(extra)
		return snap

	def test_snapshot_exposes_spool(self):
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		from omni_crm.monitoring.snapshot import build_snapshot

		self.assertEqual(build_snapshot()["spool"]["count"], 1)


class TestSchedulerAndErase(SpoolCase):
	def test_drain_runs_every_minute(self):
		self.assertIn("omni_crm.ingest.spool.drain", hooks.scheduler_events["cron"]["* * * * *"])

	def test_drain_without_spool_does_nothing_cheaply(self):
		self.assertEqual(spool.drain(), {"saved": 0, "dup": 0, "dropped": 0, "kept": 0, "expired": 0})

	def test_erase_reports_pending_spool_and_leaves_the_spool_untouched(self):
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD2104 xoa"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(lambda: (frappe.db.rollback(), frappe.db.delete("CRM Lead", {"name": lead.name})))
		with mock.patch("omni_crm.api.admin.require_omni_admin"):
			out = admin.erase_customer_data(lead.name, "yeu cau xoa du lieu cua khach")
		self.assertEqual(out["spool_pending"], 1)
		self.assertEqual(spool.spool_count(), 1)
		frappe.db.rollback()


class TestReviewFixes(SpoolCase):
	def test_db_error_codes_only_transient_ones_go_to_spool(self):
		for code in (2002, 2003, 2006, 2013, 1040, 1203, 1205, 1213):
			self.assertTrue(webhooks.is_db_unavailable(pymysql.err.OperationalError(code, "x")), code)
		for code in (1054, 1045, 1146, 1064):
			self.assertFalse(webhooks.is_db_unavailable(pymysql.err.OperationalError(code, "x")), code)
		self.assertTrue(webhooks.is_db_unavailable(pymysql.err.InterfaceError(0, "closed")))

	def test_schema_or_permission_error_is_not_swallowed_into_the_spool(self):
		for code in (1054, 1045):
			err = pymysql.err.OperationalError(code, "loi schema/quyen")
			with mock.patch("omni_crm.api.webhooks.gateway.save_inbound", side_effect=err):
				with self.assertRaises(pymysql.err.OperationalError):
					self.call(body_of(f"schema{code}"))
			self.assertEqual(spool.spool_count(), 0)

	def test_google_key_and_captcha_token_are_scrubbed_before_the_disk(self):
		import base64

		secret = json.dumps({"form_id": "F1", "google_key": "khoa-bi-mat-google", "lead_id": "L1"}).encode()
		entry = spool.build_entry(NAME, "Google Lead Form", secret, {})
		raw = base64.b64decode(entry["raw"])
		self.assertNotIn(b"khoa-bi-mat-google", raw)
		self.assertNotIn(b"google_key", raw)
		self.assertEqual(json.loads(raw), {"form_id": "F1", "lead_id": "L1"})
		self.assertEqual(spool.reverify(entry, mock.Mock(channel_type="Google Lead Form"))[0], True)
		web = json.dumps({"x": 1, "captcha_token": "tok"}).encode()
		self.assertNotIn(b"tok", base64.b64decode(spool.build_entry(NAME, "Web Form", web, {})["raw"]))
		self.assertEqual(spool.scrub_payload("Zalo OA", b"khong json"), b"khong json")

	def test_append_repairs_a_torn_last_line(self):
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		path = os.path.join(spool.spool_dir(), spool.ACTIVE)
		with open(path, "ab") as f:
			f.write(b'{"v":1,"id":"cat-do')  # lần ghi trước bị cắt, không có \n
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		lines = open(path, "rb").read().split(b"\n")
		self.assertEqual(lines[-1], b"")
		entries = list(spool._iter_entries(path))
		self.assertEqual([e is None for e in entries], [False, True, False])

	def test_preexisting_loose_file_is_tightened_to_0600(self):
		os.makedirs(spool.spool_dir(), exist_ok=True)
		path = os.path.join(spool.spool_dir(), spool.ACTIVE)
		open(path, "wb").close()
		os.chmod(path, 0o666)
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

	def test_orphan_work_tmp_is_removed_after_the_retention_but_not_before(self):
		os.makedirs(spool.spool_dir(), exist_ok=True)
		old = os.path.join(spool.spool_dir(), "spool.1.old.work.tmp")
		new = os.path.join(spool.spool_dir(), "spool.2.new.work.tmp")
		for p in (old, new):
			open(p, "wb").close()
		past = time.time() - 15 * 86400
		os.utime(old, (past, past))
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))  # để drain không thoát sớm
		with mock.patch.object(spool, "_insert_entry", side_effect=db_gone):
			spool.drain()
		self.assertFalse(os.path.exists(old))
		self.assertTrue(os.path.exists(new))

	def test_web_form_cannot_use_more_than_half_the_quota(self):
		with mock.patch.dict(frappe.conf, {"omni_spool_max_lines": 4}):
			for i in range(2):
				spool.append(spool.build_entry(NAME, "Web Form", b"{}", {}))
			with self.assertRaises(spool.SpoolFull):
				spool.append(spool.build_entry(NAME, "Web Form", b"{}", {}))
			for i in range(2):  # còn chỗ cho kênh có chữ ký
				spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
			with self.assertRaises(spool.SpoolFull):
				spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		with mock.patch.dict(frappe.conf, {"omni_spool_max_bytes": 3000}):
			shutil.rmtree(spool.spool_dir())
			big = b'{"p":"' + b"a" * 600 + b'"}'
			spool.append(spool.build_entry(NAME, "Web Form", big, {}))
			with self.assertRaises(spool.SpoolFull):
				spool.append(spool.build_entry(NAME, "Web Form", big, {}))
			spool.append(spool.build_entry(NAME, "Zalo OA", big, {}))

	def test_counter_does_not_reread_the_spool_and_heals_after_drain(self):
		for i in range(3):
			spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		with mock.patch.object(
			spool, "_count_lines", side_effect=AssertionError("không được đọc lại cả hàng")
		):
			spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		self.assertEqual(spool._read_meta()["lines"], 4)
		with mock.patch.object(spool, "_insert_entry", return_value="drop:kenh_khong_con"):
			spool.drain()
		self.assertEqual((spool.spool_count(), spool._read_meta()["lines"]), (0, 0))

	def test_oldest_age_stops_at_first_valid_line_and_stats_computes_it_once(self):
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}, add_to_date(now_datetime(), minutes=-30)))
		spool.append(spool.build_entry(NAME, "Zalo OA", b"{}", {}))
		with mock.patch.object(spool, "oldest_age_seconds", wraps=spool.oldest_age_seconds) as spy:
			out = spool.stats()
		self.assertEqual(spy.call_count, 1)
		self.assertGreaterEqual(out["oldest_age_seconds"], 29 * 60)
		self.assertTrue(out["stale"])
