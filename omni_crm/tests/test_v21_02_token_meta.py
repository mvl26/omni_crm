# ruff: noqa: RUF001
"""V21-02 (F2): kiểm hạn/quyền token Page Meta hằng ngày bằng phản hồi `debug_token` GIẢ (chưa thử với token thật)."""

import logging
import time
from datetime import timedelta
from unittest import mock

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm import flags
from omni_crm.channels.meta_leads import token_check
from omni_crm.channels.meta_leads.client import MetaClient
from omni_crm.channels.meta_leads.errors import PERMANENT, TRANSIENT, MetaError
from omni_crm.monitoring.snapshot import build_snapshot

PREFIX = "TD2102"
APP_ID = "2102210221022102"
APP_SECRET = "td2102-app-secret-khong-that-abcdef"
PAGE_TOKEN = "td2102-page-token-khong-that-" + "z" * 24
DAY = 86400


def cleanup() -> None:
	frappe.db.rollback()
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Error Log", {"method": ["like", "%kiểm token Meta%"]})
	frappe.db.commit()


def make_channel(name: str, **kw):
	doc = frappe.get_doc(
		{
			"doctype": "Omni Channel",
			"channel_name": f"{PREFIX}-{name}",
			"channel_type": "Meta Lead Ads",
			"enabled": 1,
			"meta_page_id": f"{PREFIX}PAGE",
			"meta_app_id": APP_ID,
			"meta_app_secret": APP_SECRET,
			"meta_verify_token": "td2102-verify",
			"meta_page_access_token": PAGE_TOKEN,
			**kw,
		}
	).insert()
	frappe.db.commit()
	return doc.name


def data(**kw) -> dict:
	"""Phản hồi `data` mẫu theo tài liệu Meta: hợp lệ, hết hạn sau 60 ngày, đủ quyền."""
	now = int(time.time())
	return {
		"app_id": APP_ID,
		"is_valid": True,
		"expires_at": now + 60 * DAY,
		"data_access_expires_at": now + 80 * DAY,
		"scopes": ["pages_show_list", "leads_retrieval", "pages_read_engagement"],
		**kw,
	}


class FakeClient:
	def __init__(self, result=None, error: MetaError | None = None):
		self.result, self.error, self.calls = result, error, 0

	def debug_token(self, app_id, app_secret):
		self.calls += 1
		if self.error:
			raise self.error
		return self.result


class TestTokenMeta(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		self.alerts = []
		p = mock.patch.object(token_check, "deliver_now", side_effect=lambda a: self.alerts.append(a) or True)
		p.start()
		self.addCleanup(p.stop)

	def check(self, name, result=None, error=None):
		ch = make_channel(name)
		client = FakeClient(result, error)
		out = token_check.check_channel(ch, client=client)
		return ch, out, frappe.db.get_value("Omni Channel", ch, "*", as_dict=True)

	def test_hop_le(self):
		ch, out, row = self.check("ok", data())
		self.assertEqual(out["status"], "ok")
		self.assertEqual(self.alerts, [])
		self.assertEqual((row.meta_token_valid, row.meta_token_never_expires), (1, 0))
		self.assertTrue(
			row.meta_token_expires_at and row.meta_token_data_expires_at and row.meta_token_checked_at
		)

	def test_sap_het_han(self):
		_, out, row = self.check("sap", data(expires_at=int(time.time()) + 3 * DAY))
		self.assertEqual(out["status"], "alert")
		self.assertEqual(len(self.alerts), 1)
		self.assertIn("còn", self.alerts[0].detail)
		self.assertIn("còn", row.meta_token_note)

	def test_sap_het_han_quyen_du_lieu(self):
		_, out, _row = self.check("sapdl", data(data_access_expires_at=int(time.time()) + 2 * DAY))
		self.assertEqual(out["status"], "alert")
		self.assertIn("Quyền dữ liệu", self.alerts[0].detail)

	def test_ngưỡng_đặt_được(self):
		frappe.db.sql(
			"""insert into tabSingles (doctype, field, value) values ('Omni Settings', 'meta_token_warn_days', '30')
			on duplicate key update value = '30'"""
		)
		self.addCleanup(
			lambda: frappe.db.sql(
				"delete from tabSingles where doctype='Omni Settings' and field='meta_token_warn_days'"
			)
		)
		self.assertEqual(flags.ads_setting("meta_token_warn_days"), 30)
		_, out, _r = self.check("n30", data(expires_at=int(time.time()) + 20 * DAY))
		self.assertEqual(out["status"], "alert")

	def test_mac_dinh_7_khi_chua_luu(self):
		frappe.db.sql("delete from tabSingles where doctype='Omni Settings' and field='meta_token_warn_days'")
		self.assertEqual(flags.ads_setting("meta_token_warn_days"), 7)
		_, out, _r = self.check("n7", data(expires_at=int(time.time()) + 10 * DAY))
		self.assertEqual(out["status"], "ok")

	def test_da_het_han(self):
		_, out, row = self.check("het", data(expires_at=int(time.time()) - DAY))
		self.assertEqual(out["status"], "alert")
		self.assertIn("đã hết hạn", self.alerts[0].detail)

	def test_khong_hop_le(self):
		_, out, row = self.check(
			"invalid", {"is_valid": False, "error": {"code": 190, "message": "Token xyz"}}
		)
		self.assertEqual(out["status"], "alert")
		self.assertEqual(row.meta_token_valid, 0)
		self.assertNotIn("xyz", self.alerts[0].detail)  # không chép nội dung lỗi của Graph

	def test_expires_at_0_khong_het_han(self):
		_, out, row = self.check("sysuser", data(expires_at=0, data_access_expires_at=0))
		self.assertEqual(out["status"], "ok")
		self.assertEqual(self.alerts, [])
		self.assertEqual(row.meta_token_never_expires, 1)
		self.assertIsNone(row.meta_token_expires_at)

	def test_expires_at_0_van_kiem_quyen(self):
		_, out, _r = self.check("sysscope", data(expires_at=0, scopes=["pages_show_list"]))
		self.assertEqual(out["status"], "alert")
		self.assertIn("leads_retrieval", self.alerts[0].detail)

	def test_thieu_quyen(self):
		_, out, row = self.check("scope", data(scopes=["pages_show_list"]))
		self.assertEqual(out["status"], "alert")
		self.assertIn("leads_retrieval", row.meta_token_note)

	def test_graph_loi_tam_thoi_khong_canh_bao_va_thu_lai(self):
		ch = make_channel("tam")
		first = token_check.check_channel(ch, client=FakeClient(error=MetaError(TRANSIENT, "tam")))
		self.assertEqual(first["status"], "transient")
		self.assertEqual(self.alerts, [])
		self.assertIsNone(frappe.db.get_value("Omni Channel", ch, "meta_token_checked_at"))  # chưa ghi đè gì
		with mock.patch.object(token_check, "MetaClient", return_value=FakeClient(data())):
			res = token_check.run()  # lần sau: thử lại
		self.assertEqual(res["transient"], 0)
		self.assertEqual(frappe.db.get_value("Omni Channel", ch, "meta_token_valid"), 1)

	def test_loi_tam_thoi_giu_ket_qua_cu(self):
		ch, _o, _r = self.check("giu", data())
		token_check.check_channel(ch, client=FakeClient(error=MetaError(TRANSIENT, "tam")))
		self.assertEqual(frappe.db.get_value("Omni Channel", ch, "meta_token_valid"), 1)
		self.assertEqual(self.alerts, [])

	def test_loi_vinh_vien_canh_bao(self):
		_, out, _r = self.check("perm", error=MetaError(PERMANENT, "tu choi"))
		self.assertEqual(out["status"], "alert")
		self.assertEqual(len(self.alerts), 1)

	def test_thieu_app_id_bo_qua_khong_goi_graph(self):
		ch = make_channel("noapp", meta_app_id="")
		client = FakeClient(data())
		self.assertEqual(token_check.check_channel(ch, client=client)["status"], "skipped")
		self.assertEqual(client.calls, 0)
		self.assertIn("Chưa kiểm", frappe.db.get_value("Omni Channel", ch, "meta_token_note"))

	def test_cong_tac_tat(self):
		ch = make_channel("off")
		frappe.db.sql(
			"""insert into tabSingles (doctype, field, value) values ('Omni Settings', 'meta_token_check_enabled', '0')
			on duplicate key update value = '0'"""
		)
		self.addCleanup(
			lambda: frappe.db.sql(
				"delete from tabSingles where doctype='Omni Settings' and field='meta_token_check_enabled'"
			)
		)
		with mock.patch.object(token_check, "MetaClient") as client:
			self.assertEqual(token_check.run()["channels"], 0)
		client.assert_not_called()
		self.assertIsNone(frappe.db.get_value("Omni Channel", ch, "meta_token_checked_at"))

	def test_run_bo_qua_kenh_tat_va_da_kiem(self):
		on, off = make_channel("on"), make_channel("off2", enabled=0)
		fake = FakeClient(data())
		with mock.patch.object(token_check, "MetaClient", return_value=fake):
			self.assertEqual(token_check.run()["channels"], 1)
			self.assertEqual(token_check.run()["channels"], 0)  # vừa kiểm xong trong 20 giờ
		self.assertEqual(fake.calls, 1)
		self.assertIsNone(frappe.db.get_value("Omni Channel", off, "meta_token_checked_at"))
		self.assertTrue(frappe.db.get_value("Omni Channel", on, "meta_token_checked_at"))

	def test_hook_lich_chay(self):
		hooks = frappe.get_hooks("scheduler_events")["cron"]
		self.assertIn("omni_crm.channels.meta_leads.token_check.run", [x for v in hooks.values() for x in v])

	def test_snapshot_hien_han_token_meta(self):
		ch, _o, _r = self.check("snap", data(scopes=["x"]))
		row = next(c for c in build_snapshot()["channels"] if c["name"] == ch)
		self.assertTrue(row["meta_token"]["problem"])
		self.assertTrue(row["meta_token"]["expires_at"])
		ch2, _o, _r = self.check("snap2", data(expires_at=0))
		row2 = next(c for c in build_snapshot()["channels"] if c["name"] == ch2)
		self.assertFalse(row2["meta_token"]["problem"])
		self.assertTrue(row2["meta_token"]["never_expires"])

	def test_assess_thuan_khong_chua_token(self):
		a = token_check.assess(data(), now_datetime(), 7)
		self.assertTrue(a.valid and not a.problems)
		self.assertIsNotNone(a.expires_at)
		self.assertFalse(token_check.assess({}, now_datetime(), 7).valid)


class TestDebugTokenClient(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)

	def _resp(self, status, payload):
		r = mock.Mock(status_code=status)
		r.json.return_value = payload
		return r

	def test_goi_dung_cach_va_tra_data(self):
		ch = make_channel("cli")
		with mock.patch("requests.get", return_value=self._resp(200, {"data": data()})) as get:
			out = MetaClient(ch).debug_token(APP_ID, APP_SECRET)
		self.assertTrue(out["is_valid"])
		args, kwargs = get.call_args
		self.assertTrue(args[0].endswith("/debug_token"))
		self.assertEqual(kwargs["params"]["input_token"], PAGE_TOKEN)
		self.assertEqual(kwargs["params"]["access_token"], f"{APP_ID}|{APP_SECRET}")
		self.assertEqual(kwargs["timeout"], 10)
		self.assertNotIn("Authorization", kwargs["headers"])

	def test_loi_mang_khong_lo_token_va_thu_lai_co_gioi_han(self):
		ch = make_channel("net")
		boom = requests.ConnectionError(
			f"https://graph.facebook.com/x?input_token={PAGE_TOKEN}&access_token={APP_ID}|{APP_SECRET}"
		)
		with mock.patch("requests.get", side_effect=boom) as get, mock.patch("time.sleep"):
			with self.assertRaises(MetaError) as cm:
				MetaClient(ch).debug_token(APP_ID, APP_SECRET)
		self.assertEqual(cm.exception.kind, TRANSIENT)
		self.assertEqual(get.call_count, 3)  # 1 + 2 lần thử lại
		for secret in (PAGE_TOKEN, APP_SECRET):
			self.assertNotIn(secret, str(cm.exception))

	def test_5xx_la_tam_thoi_va_token_khong_vao_log_hay_error_log(self):
		ch = make_channel("log")
		frappe.db.set_value("Omni Channel", ch, "meta_token_checked_at", None)
		records = []

		class H(logging.Handler):
			def emit(self, record):
				records.append(record.getMessage())

		handler = H()
		lg = frappe.logger("omni")
		lg.addHandler(handler)
		self.addCleanup(lg.removeHandler, handler)
		with (
			mock.patch("requests.get", return_value=self._resp(503, {"error": {"code": 2}})),
			mock.patch("time.sleep"),
			mock.patch.object(token_check, "deliver_now") as deliver,
		):
			res = token_check.run()
		self.assertEqual(res["transient"], 1)
		deliver.assert_not_called()
		errors = frappe.get_all("Error Log", fields=["error", "method"], limit=50, order_by="creation desc")
		blob = "\n".join(records + [e.error or "" for e in errors])
		for secret in (PAGE_TOKEN, APP_SECRET):
			self.assertNotIn(secret, blob)

	def test_canh_bao_that_khong_chua_bi_mat(self):
		ch = make_channel("mail")
		sent = []
		with (
			mock.patch.object(token_check, "MetaClient", return_value=FakeClient(data(scopes=[]))),
			mock.patch("omni_crm.monitoring.alerts._deliver", side_effect=sent.append),
		):
			token_check.run()
		self.assertEqual(len(sent), 1)
		blob = (
			f"{sent[0].subject}{sent[0].detail}{frappe.db.get_value('Omni Channel', ch, 'meta_token_note')}"
		)
		for secret in (PAGE_TOKEN, APP_SECRET):
			self.assertNotIn(secret, blob)
		frappe.cache().delete_value(f"omni:alert:{token_check.ALERT_KIND}:{ch}")
