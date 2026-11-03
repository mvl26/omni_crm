# ruff: noqa: RUF001
"""V21-08 (F9): lệnh kiểm cấu hình trước phát hành `omni_crm.ops.preflight` — chỉ đọc, từng mục đạt/không đạt, production nghiêm hơn staging.

Mọi thay đổi dữ liệu trong test chỉ nằm trong transaction và bị ROLLBACK ở tearDown (không commit); tệp sao lưu và hàng dự phòng
nằm ở thư mục tạm. Không gọi mạng; Redis chỉ dùng ở hai test riêng (Redis tạm từ biến môi trường FRAPPE_REDIS_QUEUE, và một máy chủ
TCP giả không bao giờ trả lời để chứng minh có timeout).
"""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from datetime import timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import set_encrypted_password

from omni_crm.channels.meta_leads.errors import TOKEN_NOTE_NEVER, TOKEN_NOTE_OK
from omni_crm.ingest import spool
from omni_crm.ops import preflight
from omni_crm.ops.preflight import CAN_NGUOI_KIEM, DAT, KHONG_DAT

PREFIX = "TD2108"
SECRET_META = "td2108-META-TOKEN-bi-mat-" + "m" * 30
SECRET_APP = "td2108-APP-SECRET-bi-mat-" + "a" * 30
SECRET_ZALO = "td2108-ZALO-REFRESH-bi-mat-" + "z" * 30
SECRET_WEBHOOK = "td2108-WEBHOOK-bi-mat-" + "w" * 30
EMAIL_ONCALL = "td2108.nguoi.truc@example.com"
EMAILS = [EMAIL_ONCALL]


class PreflightCase(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()
		# nền sạch: tắt mọi kênh/biểu mẫu có sẵn của site test (chỉ trong transaction, ROLLBACK ở tearDown)
		frappe.db.sql("update `tabOmni Channel` set enabled = 0, zalo_skip_signature = 0")
		frappe.db.sql("update `tabOmni Lead Form` set enabled = 0")
		self.tmp = tempfile.mkdtemp(prefix="td2108-")
		self.backup_dir = os.path.join(self.tmp, "backups")
		os.makedirs(self.backup_dir)
		self.spool_dir = os.path.join(self.tmp, "spool-chua-tao")  # cố ý không tạo
		self.patches = [
			mock.patch.object(preflight, "_backup_dir", lambda: self.backup_dir),
			mock.patch.object(spool, "spool_dir", lambda: self.spool_dir),
			mock.patch.dict(frappe.conf),
		]
		for p in self.patches:
			p.start()
		for key in ("developer_mode", "omni_auto_send_allowed", "omni_ai_allow_customer_data"):
			frappe.conf.pop(key, None)
		self.addCleanup(self._cleanup)

	def _cleanup(self):
		for p in reversed(self.patches):
			p.stop()
		frappe.db.rollback()

	# ---- dựng điều kiện
	def conf(self, **kw):
		for k, v in kw.items():
			if v is None:
				frappe.conf.pop(k, None)
			else:
				frappe.conf[k] = v

	def single(self, **kw):
		for k, v in kw.items():
			frappe.db.sql("delete from tabSingles where doctype='Omni Settings' and field=%s", k)
			if v is not None:
				frappe.db.sql(
					"insert into tabSingles (doctype, field, value) values ('Omni Settings', %s, %s)",
					(k, str(v)),
				)

	def channel(self, ctype, name, **kw):
		extra = {"zalo_app_id": "td2108-app"} if ctype == "Zalo OA" else {}
		doc = frappe.get_doc(
			{
				"doctype": "Omni Channel",
				"channel_name": f"{PREFIX} {name}",
				"channel_type": ctype,
				"enabled": 1,
				**extra,
				**kw,
			}
		).insert(ignore_permissions=True)
		return doc

	def run_check(self, key, target="production"):
		result = preflight.check(target)
		row = next(r for r in result["muc"] if r["muc"] == key)
		return row["ket_qua"], row

	def assertItem(self, key, target, expected, msg=""):
		got, row = self.run_check(key, target)
		self.assertEqual(
			got, expected, f"{key}/{target}: {got} != {expected}; {row['chi_tiet']} {msg}".strip()
		)
		if expected != DAT:
			self.assertTrue(row["cach_sua"], f"{key}: mục không đạt phải có cách sửa")
		return row

	def both(self, key, expected_prod, expected_staging):
		self.assertItem(key, "production", expected_prod)
		self.assertItem(key, "staging", expected_staging)


class TestCommonItems(PreflightCase):
	def test_developer_mode(self):
		self.conf(developer_mode=1)
		self.both("developer_mode", KHONG_DAT, KHONG_DAT)
		self.conf(developer_mode=0)
		self.both("developer_mode", DAT, DAT)
		self.conf(developer_mode=None)
		self.both("developer_mode", DAT, DAT)

	def test_zalo_skip_signature_on_any_channel_even_disabled(self):
		self.both("zalo_skip_signature", DAT, DAT)
		self.channel("Zalo OA", "skip", enabled=0, zalo_skip_signature=1)
		self.both("zalo_skip_signature", KHONG_DAT, KHONG_DAT)

	def test_sandbox_mode(self):
		self.single(sandbox_mode=1)
		self.both("sandbox_mode", CAN_NGUOI_KIEM, DAT)
		self.single(sandbox_mode=0)
		self.both("sandbox_mode", DAT, KHONG_DAT)
		row = self.assertItem("sandbox_mode", "production", DAT)
		self.assertIn("chủ đích", row["chi_tiet"])

	def test_scheduler(self):
		self.conf(pause_scheduler=1)
		self.both("scheduler", KHONG_DAT, KHONG_DAT)
		self.conf(pause_scheduler=None)
		with mock.patch("frappe.utils.scheduler.is_scheduler_inactive", return_value=False):
			self.both("scheduler", DAT, DAT)
		with mock.patch("frappe.utils.scheduler.is_scheduler_inactive", return_value=True):
			self.both("scheduler", KHONG_DAT, KHONG_DAT)

	def test_alert_recipients(self):
		self.single(alert_recipients="")
		self.both("alert_recipients", KHONG_DAT, KHONG_DAT)
		self.single(alert_recipients="khong-phai-email")
		self.both("alert_recipients", KHONG_DAT, KHONG_DAT)
		self.single(alert_recipients=f"{EMAIL_ONCALL}\nkhong-phai-email")
		self.both("alert_recipients", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		self.single(alert_recipients=f"{EMAIL_ONCALL}, b.td2108@example.com")
		self.both("alert_recipients", DAT, DAT)

	def test_business_hours(self):
		self.single(business_hours=None)  # chưa từng lưu: dùng giờ mặc định chưa ai xác nhận
		self.both("business_hours", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		for bad in ("", "{", "[]", '{"mon-fri":"25:00-26:00"}', '{"xyz":"08:00-17:30"}'):
			self.single(business_hours=bad)
			self.both("business_hours", KHONG_DAT, KHONG_DAT)
		self.single(business_hours='{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}')
		self.both("business_hours", DAT, DAT)

	def test_holidays_reminder(self):
		self.single(holidays=None)
		self.both("holidays", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		self.single(holidays="[]")
		self.both("holidays", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		self.single(holidays='["2020-01-01"]')
		self.both("holidays", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		future = str((now_datetime() + timedelta(days=60)).date())
		self.single(holidays=json.dumps([future]))
		self.both("holidays", DAT, DAT)

	def test_prompt_pin_is_informational_only(self):
		from omni_crm.bot.prompts import loader

		with mock.patch.object(loader, "pinned_version", return_value="v2"):
			self.both("prompt_ghim", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		with mock.patch.object(
			loader, "pinned_version", side_effect=lambda name: loader.PINNED_DEFAULTS[name]
		):
			self.both("prompt_ghim", DAT, DAT)
		with mock.patch.object(loader, "pinned_version", side_effect=RuntimeError("x")):
			self.assertNotEqual(self.run_check("prompt_ghim")[0], KHONG_DAT)


class TestDecisionsAndBot(PreflightCase):
	def test_auto_send_key(self):
		self.single(q1_allowed=0)
		self.conf(omni_auto_send_allowed=None)
		self.both("omni_auto_send_allowed", DAT, DAT)
		self.conf(omni_auto_send_allowed=0)
		self.both("omni_auto_send_allowed", DAT, DAT)
		self.conf(omni_auto_send_allowed=1)
		self.both("omni_auto_send_allowed", KHONG_DAT, KHONG_DAT)
		self.single(q1_allowed=1)  # Q1 đã chốt: production chấp nhận, staging vẫn không
		self.both("omni_auto_send_allowed", DAT, KHONG_DAT)

	def test_q11_matches_conf_both_directions(self):
		for q11, conf, prod in ((0, None, DAT), (0, 1, KHONG_DAT), (1, None, KHONG_DAT), (1, 1, DAT)):
			self.single(q11_allowed=q11)
			self.conf(omni_ai_allow_customer_data=conf)
			self.assertItem("omni_ai_allow_customer_data", "production", prod, f"q11={q11} conf={conf}")
		# staging: khóa phải vắng, bất kể Q11
		self.single(q11_allowed=1)
		self.conf(omni_ai_allow_customer_data=1)
		self.assertItem("omni_ai_allow_customer_data", "staging", KHONG_DAT)
		self.conf(omni_ai_allow_customer_data=None)
		self.assertItem("omni_ai_allow_customer_data", "staging", DAT)

	def test_q11_never_saved_means_not_allowed(self):
		self.single(q11_allowed=None, q1_allowed=None)
		self.conf(omni_ai_allow_customer_data=1)
		self.assertItem("omni_ai_allow_customer_data", "production", KHONG_DAT)
		self.conf(omni_auto_send_allowed=1)
		self.assertItem("omni_auto_send_allowed", "production", KHONG_DAT)

	def test_bot_analyze_gate(self):
		past = str(add_to_date(now_datetime(), days=-1))
		future = str(add_to_date(now_datetime(), days=30))
		self.single(bot_analyze_enabled=1, gate_phan_tich_passed_at=None)
		self.both("bot_analyze_enabled", KHONG_DAT, DAT)
		self.single(gate_phan_tich_passed_at=future)  # ngày tương lai không phải đã đạt
		self.both("bot_analyze_enabled", KHONG_DAT, DAT)
		self.single(gate_phan_tich_passed_at=past)
		self.both("bot_analyze_enabled", DAT, DAT)
		self.single(bot_analyze_enabled=0, gate_phan_tich_passed_at=None)
		self.both("bot_analyze_enabled", DAT, DAT)

	def test_bot_reply_gate(self):
		past = str(add_to_date(now_datetime(), days=-1))
		for mode in ("Shadow", "Draft"):
			self.single(bot_reply_mode=mode, gate_nhap_passed_at=None)
			self.both("bot_reply_mode", KHONG_DAT, DAT)
			self.single(gate_nhap_passed_at=past)
			self.both("bot_reply_mode", DAT, DAT)
		self.single(bot_reply_mode="Off", gate_nhap_passed_at=None)
		self.both("bot_reply_mode", DAT, DAT)
		self.single(bot_reply_mode="Rac", gate_nhap_passed_at=None)  # giá trị lạ coi như Off (giống flags)
		self.both("bot_reply_mode", DAT, DAT)

	def test_current_site_state_shape_fails_on_production(self):
		"""Hiện trạng omni.test theo F9: Draft + phân tích bật, chưa có cổng -> production KHÔNG ĐẠT ở cả hai mục bot."""
		self.single(
			bot_analyze_enabled=1,
			bot_reply_mode="Draft",
			gate_phan_tich_passed_at=None,
			gate_nhap_passed_at=None,
		)
		self.assertItem("bot_analyze_enabled", "production", KHONG_DAT)
		self.assertItem("bot_reply_mode", "production", KHONG_DAT)
		self.assertEqual(preflight.check("production")["ket_qua"], KHONG_DAT)

	def test_gate_and_decision_fields_exist_and_are_admin_only(self):
		meta = frappe.get_meta("Omni Settings")
		for f in ("q1_allowed", "q11_allowed", "gate_phan_tich_passed_at", "gate_nhap_passed_at"):
			self.assertIsNotNone(meta.get_field(f), f)
		self.assertEqual({p.role for p in meta.permissions if p.write}, {"Omni Admin"})
		self.assertEqual(cint_default(meta, "q1_allowed"), 0)
		self.assertEqual(cint_default(meta, "q11_allowed"), 0)


def cint_default(meta, field):
	return int(meta.get_field(field).default or 0)


class TestWorkers(PreflightCase):
	def declare(self, *names):
		"""`get_queues_timeout` có lru_cache theo tiến trình nên patch hàm, không đổi frappe.conf."""
		patcher = mock.patch(
			"frappe.utils.background_jobs.get_queues_timeout",
			return_value={"short": 300, "default": 300, "long": 1500, **{n: 300 for n in names}},
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_worker_counts_and_declaration(self):
		self.declare("omni", "omni_leads")
		with mock.patch.object(preflight, "_workers_for", return_value=2):
			self.both("worker_omni", DAT, DAT)
			self.both("worker_omni_leads", DAT, DAT)
		with mock.patch.object(
			preflight, "_workers_for", side_effect=lambda q: 0 if q == "omni_leads" else 1
		):
			self.both("worker_omni", DAT, DAT)
			self.both("worker_omni_leads", KHONG_DAT, KHONG_DAT)
		with mock.patch.object(preflight, "_workers_for", return_value=0):
			self.both("worker_omni", KHONG_DAT, KHONG_DAT)

	def test_undeclared_queue_fails(self):
		self.declare("omni")
		with mock.patch.object(preflight, "_workers_for", return_value=3):
			self.both("worker_omni_leads", KHONG_DAT, KHONG_DAT)
			row = self.assertItem("worker_omni_leads", "production", KHONG_DAT)
		self.assertIn("workers", row["chi_tiet"])

	def test_redis_unreachable_needs_human(self):
		self.declare("omni", "omni_leads")
		with mock.patch.object(preflight, "_workers_for", side_effect=ConnectionError("redis down")):
			row = self.assertItem("worker_omni", "production", CAN_NGUOI_KIEM)
		self.assertIn("ConnectionError", row["chi_tiet"])
		self.assertNotIn("redis down", row["chi_tiet"])

	def test_hung_redis_times_out_instead_of_hanging(self):
		"""Máy chủ nhận kết nối rồi không trả lời (giống Redis đang nạp RDB hỏng): phải CAN_NGUOI_KIEM trong vài giây."""
		server = socket.socket()
		server.bind(("127.0.0.1", 0))
		server.listen(5)
		held = []
		stop = threading.Event()

		def accept():
			server.settimeout(0.2)
			while not stop.is_set():
				with contextlib.suppress(OSError):
					held.append(server.accept()[0])

		t = threading.Thread(target=accept, daemon=True)
		t.start()
		self.addCleanup(lambda: (stop.set(), t.join(2), [c.close() for c in held], server.close()))
		self.declare("omni", "omni_leads")
		self.conf(redis_queue=f"redis://127.0.0.1:{server.getsockname()[1]}")
		started = time.monotonic()
		got, _row = self.run_check("worker_omni")
		self.assertEqual(got, CAN_NGUOI_KIEM)
		self.assertLess(time.monotonic() - started, preflight.REDIS_TIMEOUT * 3 + 5)

	def test_real_redis_counts_registered_workers(self):
		"""Với Redis tạm (FRAPPE_REDIS_QUEUE đặt riêng, không phải cổng dùng chung): worker RQ đăng ký thật được đếm đúng hàng đợi."""
		if not os.environ.get("FRAPPE_REDIS_QUEUE") or ":11000" in os.environ["FRAPPE_REDIS_QUEUE"]:
			self.skipTest("cần Redis tạm riêng qua FRAPPE_REDIS_QUEUE")
		from frappe.utils.background_jobs import generate_qname
		from rq import Queue, Worker

		self.declare("omni", "omni_leads")
		conn = preflight._redis_connection()
		self.addCleanup(conn.close)
		self.assertEqual(preflight._workers_for("omni"), 0)
		self.assertItem("worker_omni", "production", KHONG_DAT)
		worker = Worker(
			[Queue(generate_qname("omni"), connection=conn)], connection=conn, name="td2108-worker"
		)
		worker.register_birth()
		self.addCleanup(worker.register_death)
		self.assertEqual(preflight._workers_for("omni"), 1)
		self.assertEqual(preflight._workers_for("omni_leads"), 0)
		self.both("worker_omni", DAT, DAT)
		self.both("worker_omni_leads", KHONG_DAT, KHONG_DAT)


class TestChannelsAndTokens(PreflightCase):
	def meta(self, **kw):
		base = {
			"meta_app_id": "1234",
			"meta_app_secret": SECRET_APP,
			"meta_page_access_token": SECRET_META,
			"webhook_token": SECRET_WEBHOOK,
			"meta_token_checked_at": now_datetime(),
			"meta_token_valid": 1,
			"meta_token_note": TOKEN_NOTE_OK,
		}
		return self.channel("Meta Lead Ads", "meta", **{**base, **kw})

	def test_token_meta(self):
		self.both("token_meta", DAT, DAT)  # không có kênh Meta bật
		now = now_datetime()
		ch = self.meta(meta_token_checked_at=None)
		self.both("token_meta", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)  # chưa kiểm lần nào
		cases = [
			({"meta_token_valid": 0}, KHONG_DAT, KHONG_DAT),
			({"meta_token_never_expires": 1, "meta_token_expires_at": None}, DAT, DAT),
			({"meta_token_expires_at": now + timedelta(days=30)}, DAT, DAT),
			(
				{"meta_token_expires_at": now + timedelta(days=3)},
				KHONG_DAT,
				CAN_NGUOI_KIEM,
			),  # production nghiêm hơn
			({"meta_token_expires_at": now - timedelta(days=1)}, KHONG_DAT, KHONG_DAT),
			(
				{
					"meta_token_expires_at": now + timedelta(days=60),
					"meta_token_data_expires_at": now + timedelta(days=2),
				},
				KHONG_DAT,
				CAN_NGUOI_KIEM,
			),
			(
				{
					"meta_token_expires_at": now + timedelta(days=30),
					"meta_token_checked_at": now - timedelta(days=5),
				},
				CAN_NGUOI_KIEM,
				CAN_NGUOI_KIEM,
			),
		]
		base = {
			"meta_token_checked_at": now,
			"meta_token_valid": 1,
			"meta_token_never_expires": 0,
			"meta_token_expires_at": None,
			"meta_token_data_expires_at": None,
			"meta_token_note": TOKEN_NOTE_OK,
		}
		for values, prod, staging in cases:
			frappe.db.set_value("Omni Channel", ch.name, {**base, **values})
			self.assertItem("token_meta", "production", prod, str(values))
			self.assertItem("token_meta", "staging", staging, str(values))

	def test_token_meta_disabled_channel_ignored(self):
		self.meta(enabled=0, meta_token_valid=0)
		self.both("token_meta", DAT, DAT)

	def zalo(self, **kw):
		return self.channel(
			"Zalo OA", "zalo", zalo_refresh_token=SECRET_ZALO, zalo_app_secret=SECRET_APP, **kw
		)

	def test_token_zalo(self):
		self.both("token_zalo", DAT, DAT)  # không có kênh
		now = now_datetime()
		ch = self.zalo(zalo_token_expires_at=now + timedelta(hours=20))
		self.both("token_zalo", DAT, DAT)
		frappe.db.set_value("Omni Channel", ch.name, "zalo_token_expires_at", None)
		self.both("token_zalo", KHONG_DAT, KHONG_DAT)
		frappe.db.set_value("Omni Channel", ch.name, "zalo_token_expires_at", now - timedelta(hours=3))
		self.single(sandbox_mode=1)  # sandbox bỏ qua làm mới: hết hạn là bình thường, cần người xem
		self.both("token_zalo", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)
		self.single(sandbox_mode=0)  # tác vụ làm mới chạy mà vẫn hết hạn: hỏng
		self.both("token_zalo", KHONG_DAT, KHONG_DAT)
		frappe.db.set_value(
			"Omni Channel",
			ch.name,
			{"zalo_token_expires_at": now + timedelta(hours=20), "last_error": "lỗi gần nhất"},
		)
		self.both("token_zalo", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)

	def test_token_zalo_without_refresh_token(self):
		self.channel("Zalo OA", "norefresh", zalo_token_expires_at=now_datetime() + timedelta(hours=5))
		self.both("token_zalo", KHONG_DAT, KHONG_DAT)

	def test_zalo_package_strict_on_production_only(self):
		self.both("zalo_oa_package", DAT, DAT)  # không có kênh
		ch = self.zalo(zalo_token_expires_at=now_datetime() + timedelta(hours=5))
		for package, prod in (
			("Chưa xác nhận", KHONG_DAT),
			("Cơ bản", CAN_NGUOI_KIEM),
			("Tiêu chuẩn", CAN_NGUOI_KIEM),
			("Tăng trưởng", DAT),
			("Toàn diện", DAT),
		):
			frappe.db.set_value("Omni Channel", ch.name, "zalo_oa_package", package)
			self.assertItem("zalo_oa_package", "production", prod, package)
			self.assertItem("zalo_oa_package", "staging", DAT, package)


class TestLeadForms(PreflightCase):
	def form(self, platform, name, **kw):
		return frappe.get_doc(
			{
				"doctype": "Omni Lead Form",
				"platform": platform,
				"external_form_id": f"{PREFIX}-{name}",
				"form_name": f"{PREFIX} {name}",
				"enabled": 1,
				**kw,
			}
		).insert(ignore_permissions=True)

	def test_no_ad_channel_is_ok(self):
		self.both("lead_form", DAT, DAT)

	def test_enabled_ad_channel_needs_enabled_form(self):
		self.channel("Web Form", "web")
		self.both("lead_form", KHONG_DAT, KHONG_DAT)
		form = self.form("Website", "w1", enabled=0)
		self.both("lead_form", KHONG_DAT, KHONG_DAT)
		frappe.db.set_value("Omni Lead Form", form.name, "enabled", 1)
		self.both("lead_form", KHONG_DAT, KHONG_DAT)  # có form bật nhưng chưa có người phụ trách
		frappe.db.set_value("Omni Lead Form", form.name, "default_owner", "Administrator")
		self.both("lead_form", DAT, DAT)

	def test_platform_must_match_channel_type(self):
		self.channel("Meta Lead Ads", "meta2")
		self.form("Google", "g1", default_owner="Administrator")  # sai nền tảng: không tính cho kênh Meta
		self.both("lead_form", KHONG_DAT, KHONG_DAT)
		self.form("Meta", "m1", default_owner="Administrator")
		self.both("lead_form", DAT, DAT)

	def test_every_enabled_form_needs_owner(self):
		self.channel("Web Form", "web2")
		self.form("Website", "ok", default_owner="Administrator")
		self.form("Website", "thieu")
		self.both("lead_form", KHONG_DAT, KHONG_DAT)

	def test_owner_must_be_usable_and_rule_must_exist_and_be_enabled(self):
		self.channel("Web Form", "web3")
		form = self.form("Website", "o1", default_owner="Administrator")
		frappe.db.set_value("User", "Administrator", "enabled", 0)  # người phụ trách bị khóa
		self.both("lead_form", KHONG_DAT, KHONG_DAT)
		frappe.db.set_value("User", "Administrator", "enabled", 1)
		self.both("lead_form", DAT, DAT)
		frappe.db.set_value(
			"Omni Lead Form", form.name, {"default_owner": None, "assignment_rule": "td2108-khong-ton-tai"}
		)
		self.both("lead_form", KHONG_DAT, KHONG_DAT)
		rule = frappe.get_doc(
			{
				"doctype": "Assignment Rule",
				"name": f"{PREFIX} rule",
				"document_type": "CRM Lead",
				"assign_condition": "status == 'New'",
				"rule": "Round Robin",
				"priority": 1,
				"disabled": 1,
				"users": [{"user": "Administrator"}],
				"assignment_days": [
					{"day": d} for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
				],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Omni Lead Form", form.name, "assignment_rule", rule.name)
		self.both("lead_form", KHONG_DAT, KHONG_DAT)  # luật đang tắt
		frappe.db.set_value("Assignment Rule", rule.name, "disabled", 0)
		self.both("lead_form", DAT, DAT)


class TestSpoolAndBackup(PreflightCase):
	def write_spool(self, n=2):
		os.makedirs(self.spool_dir)
		recv = str(now_datetime() - timedelta(minutes=5))
		with open(os.path.join(self.spool_dir, spool.ACTIVE), "w") as f:
			for i in range(n):
				f.write(json.dumps({"id": str(i), "recv": recv}) + "\n")

	def test_spool_must_be_empty(self):
		self.both("hang_du_phong_webhook", DAT, DAT)
		self.assertFalse(os.path.exists(self.spool_dir), "kiểm không được tạo thư mục hàng dự phòng")
		self.write_spool(3)
		row = self.assertItem("hang_du_phong_webhook", "production", KHONG_DAT)
		self.assertIn("3 tin", row["chi_tiet"])
		self.assertItem("hang_du_phong_webhook", "staging", KHONG_DAT)

	def test_spool_empty_dir_ok_and_disabled_switch_reminds(self):
		os.makedirs(self.spool_dir)
		self.both("hang_du_phong_webhook", DAT, DAT)
		self.single(webhook_spool_disabled=1)
		self.both("hang_du_phong_webhook", CAN_NGUOI_KIEM, CAN_NGUOI_KIEM)

	def backup(self, hours_old, name="20261103_000000-omni_test-database.sql.gz"):
		path = os.path.join(self.backup_dir, name)
		with open(path, "wb") as f:
			f.write(b"x")
		ts = time.time() - hours_old * 3600
		os.utime(path, (ts, ts))

	def test_backup_within_24h_required_on_production_only(self):
		self.both("sao_luu_24h", KHONG_DAT, DAT)  # chưa có bản nào
		self.backup(30)
		self.both("sao_luu_24h", KHONG_DAT, DAT)  # production nghiêm hơn staging
		self.backup(2, "20261103_220000-omni_test-database.sql.gz")
		self.both("sao_luu_24h", DAT, DAT)

	def test_backup_ignores_other_files(self):
		with open(os.path.join(self.backup_dir, "site_config_backup.json"), "w") as f:
			f.write("{}")
		self.assertItem("sao_luu_24h", "production", KHONG_DAT)


class TestSafetyAndContract(PreflightCase):
	def messy_state(self):
		"""Dựng cấu hình có đủ kiểu dữ liệu nhạy cảm để thử lộ bí mật/PII."""
		self.single(
			alert_recipients="\n".join(EMAILS),
			sandbox_mode=0,
			bot_analyze_enabled=1,
			bot_reply_mode="Draft",
		)
		set_encrypted_password("Omni Settings", "Omni Settings", SECRET_APP, "ai_api_key")
		self.conf(developer_mode=1, omni_ai_allow_customer_data=1, omni_auto_send_allowed=1)
		now = now_datetime()
		self.channel(
			"Meta Lead Ads",
			"m",
			meta_app_id="1234",
			meta_app_secret=SECRET_APP,
			meta_page_access_token=SECRET_META,
			webhook_token=SECRET_WEBHOOK,
			meta_token_checked_at=now,
			meta_token_valid=1,
			meta_token_note=TOKEN_NOTE_OK,
			meta_token_expires_at=now + timedelta(days=2),
		)
		self.channel(
			"Zalo OA", "z", zalo_refresh_token=SECRET_ZALO, zalo_app_secret=SECRET_APP, zalo_skip_signature=1
		)
		self.channel("Web Form", "w")
		frappe.get_doc(
			{
				"doctype": "Omni Lead Form",
				"platform": "Meta",
				"external_form_id": f"{PREFIX}-x",
				"form_name": f"{PREFIX} x",
				"enabled": 1,
				"default_owner": "Administrator",
			}
		).insert(ignore_permissions=True)
		os.makedirs(self.spool_dir)
		with open(os.path.join(self.spool_dir, spool.ACTIVE), "w") as f:
			f.write(json.dumps({"id": "1", "recv": str(now), "raw": EMAIL_ONCALL}) + "\n")

	def guarded(self):
		"""Mọi đường ghi/gửi/đẩy việc quen thuộc đều nổ nếu bị gọi."""
		boom = AssertionError("preflight không được ghi/gửi")
		stack = contextlib.ExitStack()
		for target in (
			"frappe.log_error",
			"frappe.sendmail",
			"frappe.enqueue",
			"frappe.db.commit",
			"frappe.db.set_value",
			"frappe.db.set_single_value",
		):
			stack.enter_context(mock.patch(target, side_effect=boom))
		return stack

	def test_read_only_no_db_writes_no_new_files(self):
		self.messy_state()
		frappe.db.sql("select 1")
		writes_before = frappe.db.transaction_writes
		counts = lambda: {  # noqa: E731
			t: frappe.db.sql(f"select count(*) from `tab{t}`")[0][0]
			for t in ("Error Log", "Version", "Omni Alert Log", "Communication")
			if frappe.db.table_exists(t)
		}
		before = counts()
		callbacks_before = list(frappe.db.after_commit._functions)  # do chính phần dựng dữ liệu của test
		tree_before = sorted(os.listdir(self.tmp))
		with self.guarded(), contextlib.redirect_stdout(io.StringIO()):
			for target in ("production", "staging"):
				preflight.run(target, strict=False)
		self.assertEqual(frappe.db.transaction_writes, writes_before)
		self.assertEqual(list(frappe.db.after_commit._functions), callbacks_before)
		self.assertEqual(counts(), before)
		self.assertEqual(sorted(os.listdir(self.tmp)), tree_before)

	def test_no_secrets_or_pii_in_output(self):
		self.messy_state()
		buf = io.StringIO()
		with contextlib.redirect_stdout(buf):
			result = preflight.run("production", strict=False)
			preflight.run("staging", strict=False)
		everything = buf.getvalue() + json.dumps(result, ensure_ascii=False)
		for secret in (
			SECRET_META,
			SECRET_APP,
			SECRET_ZALO,
			SECRET_WEBHOOK,
			EMAIL_ONCALL,
			"Administrator@",
			"admin@example.com",
		):
			self.assertNotIn(secret, everything)
		self.assertIn("alert_recipients có 1 người nhận", everything)

	def test_exception_in_one_item_does_not_leak_or_abort(self):
		def boom(ctx):
			raise RuntimeError(f"token={SECRET_META}")

		with mock.patch.object(
			preflight, "CHECKS", [("hong", boom), ("developer_mode", preflight._developer_mode)]
		):
			result = preflight.check("production")
		self.assertEqual([r["ket_qua"] for r in result["muc"]], [CAN_NGUOI_KIEM, DAT])
		self.assertNotIn(SECRET_META, json.dumps(result))
		self.assertIn("RuntimeError", result["muc"][0]["chi_tiet"])

	def test_result_shape_and_all_items_present(self):
		result = preflight.check("staging")
		self.assertEqual(result["target"], "staging")
		keys = [r["muc"] for r in result["muc"]]
		for needed in (
			"developer_mode",
			"zalo_skip_signature",
			"omni_auto_send_allowed",
			"omni_ai_allow_customer_data",
			"bot_analyze_enabled",
			"bot_reply_mode",
			"sandbox_mode",
			"scheduler",
			"worker_omni",
			"worker_omni_leads",
			"alert_recipients",
			"business_hours",
			"holidays",
			"lead_form",
			"token_meta",
			"token_zalo",
			"zalo_oa_package",
			"hang_du_phong_webhook",
			"sao_luu_24h",
		):
			self.assertIn(needed, keys)
		self.assertEqual(len(keys), len(set(keys)))
		for row in result["muc"]:
			self.assertIn(row["ket_qua"], (DAT, KHONG_DAT, CAN_NGUOI_KIEM))
			self.assertTrue(row["chi_tiet"])
		self.assertEqual(sum(result["dem"].values()), len(keys))

	def test_invalid_target_rejected_before_any_check(self):
		called = []
		with mock.patch.object(preflight, "CHECKS", [("x", lambda ctx: called.append(1) or (DAT, "x", ""))]):
			for bad in ("dev", "", None, "Production"):
				with self.assertRaises(ValueError):
					preflight.check(bad)
		self.assertEqual(called, [])

	def test_production_is_stricter_than_staging(self):
		self.messy_state()
		self.single(q11_allowed=0, q1_allowed=0)
		self.backup_dir_old()
		prod, stg = preflight.check("production"), preflight.check("staging")
		failing = lambda r: {x["muc"] for x in r["muc"] if x["ket_qua"] == KHONG_DAT}  # noqa: E731
		self.assertGreater(len(failing(prod)), len(failing(stg)))
		self.assertTrue(
			{"sao_luu_24h", "zalo_oa_package", "bot_analyze_enabled", "bot_reply_mode"} <= failing(prod)
		)
		self.assertFalse(
			{"sao_luu_24h", "zalo_oa_package", "bot_analyze_enabled", "bot_reply_mode"} & failing(stg)
		)

	def backup_dir_old(self):
		path = os.path.join(self.backup_dir, "20261101_000000-omni_test-database.sql.gz")
		with open(path, "wb") as f:
			f.write(b"x")
		ts = time.time() - 72 * 3600
		os.utime(path, (ts, ts))

	# ---- mã thoát
	def test_run_raises_system_exit_only_when_a_item_fails(self):
		self.single(bot_analyze_enabled=1, gate_phan_tich_passed_at=None)
		with contextlib.redirect_stdout(io.StringIO()):
			with self.assertRaises(preflight.PreflightFailed) as cm:
				preflight.run("production")
			self.assertIsInstance(cm.exception, SystemExit)
			self.assertNotIsInstance(
				cm.exception, Exception
			)  # bench execute bọc `except Exception` rồi eval -> NameError
			self.assertIn("bot_analyze_enabled", str(cm.exception))
			self.assertEqual(cm.exception.result["target"], "production")
			result = preflight.run("production", strict=False)  # không raise, vẫn trả dict
		self.assertEqual(result["ket_qua"], KHONG_DAT)

	def test_run_does_not_raise_when_only_needs_human(self):
		canned = {
			"target": "production",
			"ket_qua": CAN_NGUOI_KIEM,
			"dem": {DAT: 1, KHONG_DAT: 0, CAN_NGUOI_KIEM: 1},
			"muc": [
				{"muc": "a", "ket_qua": DAT, "chi_tiet": "x", "cach_sua": ""},
				{"muc": "b", "ket_qua": CAN_NGUOI_KIEM, "chi_tiet": "y", "cach_sua": "z"},
			],
		}
		with (
			mock.patch.object(preflight, "check", return_value=canned),
			contextlib.redirect_stdout(io.StringIO()),
		):
			self.assertEqual(preflight.run("production"), canned)

	def test_process_exit_code_through_real_interpreter(self):
		"""Tiến trình thật: có KHONG_DAT -> mã 1 và thông điệp ở stderr; không có -> mã 0. (Không cần DB.)"""
		script = textwrap.dedent(
			"""
			import sys
			from unittest import mock
			from omni_crm.ops import preflight
			bad = sys.argv[1] == "bad"
			row = {"muc": "m", "ket_qua": "KHONG_DAT" if bad else "DAT", "chi_tiet": "c", "cach_sua": "s"}
			res = {"target": "production", "ket_qua": row["ket_qua"], "dem": {"DAT": int(not bad), "KHONG_DAT": int(bad), "CAN_NGUOI_KIEM": 0}, "muc": [row]}
			try:
				with mock.patch.object(preflight, "check", return_value=res):
					preflight.run("production")
			except Exception:
				sys.exit(99)  # giống bench execute: nếu bị `except Exception` bắt thì lỗi này lộ ra
			print("OK-het")
			"""
		)
		env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
		bad = subprocess.run(
			[sys.executable, "-c", script, "bad"], capture_output=True, text=True, env=env, timeout=120
		)
		good = subprocess.run(
			[sys.executable, "-c", script, "good"], capture_output=True, text=True, env=env, timeout=120
		)
		self.assertEqual(bad.returncode, 1, bad.stderr[-500:])
		self.assertIn("KHÔNG ĐẠT", bad.stderr)
		self.assertNotIn("OK-het", bad.stdout)
		self.assertEqual(good.returncode, 0, good.stderr[-500:])
		self.assertIn("OK-het", good.stdout)


class TestReviewFixes(PreflightCase):
	"""Sửa theo review độc lập V21-08 (N1-N4, G1-G3)."""

	def meta_ok(self, **kw):
		base = {
			"meta_app_id": "1234",
			"meta_app_secret": SECRET_APP,
			"meta_page_access_token": SECRET_META,
			"webhook_token": SECRET_WEBHOOK,
			"meta_token_checked_at": now_datetime(),
			"meta_token_valid": 1,
			"meta_token_expires_at": now_datetime() + timedelta(days=60),
		}
		return self.channel("Meta Lead Ads", "mr", **{**base, **kw})

	def test_n1_meta_note_with_problem_fails_even_if_valid_and_far_expiry(self):
		ch = self.meta_ok(meta_token_note=TOKEN_NOTE_OK)
		self.both("token_meta", DAT, DAT)
		frappe.db.set_value("Omni Channel", ch.name, "meta_token_note", TOKEN_NOTE_NEVER)
		self.both("token_meta", DAT, DAT)
		for note in (
			"Không kiểm được token (Graph từ chối: mã 190)",
			"Thiếu quyền leads_retrieval",
			"",
			None,
		):
			frappe.db.set_value("Omni Channel", ch.name, "meta_token_note", note)
			self.both("token_meta", KHONG_DAT, KHONG_DAT)

	def test_n2_spool_unreadable_or_truncated_is_not_empty(self):
		os.makedirs(self.spool_dir)
		path = os.path.join(self.spool_dir, spool.ACTIVE)
		with open(path, "w") as f:  # một dòng cụt không có \n: spool_count() = 0
			f.write('{"id": "1", "recv": "2026-11-03 10:00:00"')
		self.assertEqual(spool.spool_count(), 0)
		self.both("hang_du_phong_webhook", KHONG_DAT, KHONG_DAT)
		with open(path, "w") as f:
			f.write(json.dumps({"id": "1"}) + "\n")
		os.chmod(path, 0)  # không đọc được: spool_count() nuốt lỗi và trả 0
		self.addCleanup(os.chmod, path, 0o600)
		if os.access(path, os.R_OK):
			self.skipTest("chạy bằng root: chmod 0 vẫn đọc được")
		self.assertEqual(spool.spool_count(), 0)
		self.both("hang_du_phong_webhook", KHONG_DAT, KHONG_DAT)
		os.chmod(path, 0o600)
		open(path, "w").close()  # rỗng thật: đạt
		self.both("hang_du_phong_webhook", DAT, DAT)
		with open(os.path.join(self.spool_dir, "spool.1.work"), "w") as f:
			f.write("x")
		self.both("hang_du_phong_webhook", KHONG_DAT, KHONG_DAT)

	def test_n3_unsaved_field_matches_runtime_not_doctype_default(self):
		self.single(sandbox_mode=None)  # chưa có dòng: runtime thấy 0 (tắt), không phải default JSON = 1
		self.assertEqual(int(frappe.get_doc("Omni Settings").sandbox_mode or 0), 0)
		self.assertItem("sandbox_mode", "staging", KHONG_DAT)
		self.assertItem("sandbox_mode", "production", DAT)
		self.single(
			q1_allowed=None, q11_allowed=None, gate_nhap_passed_at=None, gate_phan_tich_passed_at=None
		)
		self.conf(omni_ai_allow_customer_data=1)
		self.assertItem("omni_ai_allow_customer_data", "production", KHONG_DAT)  # chưa lưu = chưa cho phép
		self.single(bot_reply_mode=None, bot_analyze_enabled=None)
		self.both("bot_reply_mode", DAT, DAT)

	def test_n4_critical_items_that_crash_fail_on_production_only(self):
		def boom(ctx):
			raise RuntimeError("x")

		for key in sorted(preflight.CRITICAL):
			with mock.patch.object(preflight, "CHECKS", [(key, boom)]):
				self.assertEqual(preflight.check("production")["muc"][0]["ket_qua"], KHONG_DAT, key)
				self.assertEqual(preflight.check("staging")["muc"][0]["ket_qua"], CAN_NGUOI_KIEM, key)
		with mock.patch.object(preflight, "CHECKS", [("worker_omni", boom)]):
			self.assertEqual(preflight.check("production")["muc"][0]["ket_qua"], CAN_NGUOI_KIEM)
		names = {k for k, _f in preflight.CHECKS}
		self.assertTrue(preflight.CRITICAL <= names)

	def test_g1_developer_mode_truthy_like_frappe(self):
		for value in ("true", "0", 1, True):  # bool(): chuỗi không rỗng là bật, đúng cách Frappe đọc
			self.conf(developer_mode=value)
			self.both("developer_mode", KHONG_DAT, KHONG_DAT)
		for value in (0, "", False, None):
			self.conf(developer_mode=value)
			self.both("developer_mode", DAT, DAT)

	def test_g2_bad_target_is_system_exit_with_message(self):
		with self.assertRaises(SystemExit) as cm:
			preflight.run("dev")
		self.assertIn("staging", str(cm.exception))
		self.assertNotIsInstance(cm.exception, Exception)

	def test_g3_empty_backup_file_does_not_count(self):
		path = os.path.join(self.backup_dir, "20261103_220000-omni_test-database.sql.gz")
		open(path, "wb").close()
		self.assertItem("sao_luu_24h", "production", KHONG_DAT)
		with open(path, "wb") as f:
			f.write(b"x")
		self.assertItem("sao_luu_24h", "production", DAT)
