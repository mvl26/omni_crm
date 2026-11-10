"""Nạp file intake: luồng tệp (archive / dead_crm / chua_nap / giữ nguyên) với DB và cổng được thay bằng giả."""

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from omni_crm.intake_files import mapper, runner


class TestRunner(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name)
		d = self.root / "inbox" / "2026" / "11" / "03" / "gmail"
		d.mkdir(parents=True)
		self.d = d
		patches = [
			mock.patch.object(
				runner.frappe,
				"conf",
				{
					"omni_intake_dir": str(self.root),
					"omni_intake_kinds": ["email", "call", "web_form", "messenger"],
				},
			),
			mock.patch.object(runner.frappe, "db", mock.MagicMock()),
			mock.patch.object(runner.frappe, "log_error"),
			mock.patch.object(runner.frappe, "get_traceback", return_value="tb"),
			mock.patch.object(runner, "redact", side_effect=lambda t: t),  # redact đọc meta DB: cần site thật
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def tearDown(self):
		self.tmp.cleanup()

	def put(self, name, data):
		p = self.d / name
		p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
		return p

	def test_tat_khi_chua_cau_hinh(self):
		with mock.patch.object(runner.frappe, "conf", {}):
			self.assertEqual(runner.run()["moi"], 0)

	def test_ket_qua_di_dung_thu_muc(self):
		a, b, c, e = (self.put(f"{n}.json", {}) for n in "abce")
		self.put("hong.json", "{khong phai json")
		results = {"a": "moi", "b": "trung", "c": "chua_ho_tro", "e": "thieu_kenh"}
		with mock.patch.object(runner, "ingest_envelope", side_effect=list(results.values())):
			stats = runner.run()
		self.assertEqual(
			(stats["moi"], stats["trung"], stats["chua_ho_tro"], stats["thieu_kenh"]), (1, 1, 1, 1)
		)
		self.assertEqual(stats["loi"], 1)
		rel = Path("2026/11/03/gmail")
		self.assertTrue((self.root / "archive" / rel / "a.json").exists())
		self.assertTrue((self.root / "archive" / rel / "b.json").exists())
		self.assertTrue((self.root / "chua_nap" / rel / "c.json").exists())
		self.assertTrue(e.exists())  # thiếu kênh: giữ nguyên để nạp lại
		self.assertTrue((self.root / "dead_crm" / rel / "hong.json").exists())
		self.assertTrue((self.root / "dead_crm" / rel / "hong.json.loi.txt").exists())
		self.assertFalse(a.exists() or b.exists() or c.exists())

	def test_loi_mot_file_khong_dung_ca_lo(self):
		self.put("a.json", {})
		self.put("b.json", {})
		with mock.patch.object(runner, "ingest_envelope", side_effect=[RuntimeError("DB"), "moi"]):
			stats = runner.run()
		self.assertEqual((stats["loi"], stats["moi"]), (1, 1))

	def test_mac_dinh_chi_doc_thu_muc_email(self):
		other = self.root / "inbox" / "2026" / "11" / "03" / "web_form"
		other.mkdir(parents=True)
		(other / "f.json").write_text("{}", encoding="utf-8")
		self.put("g.json", {})
		conf = {"omni_intake_dir": str(self.root)}  # không khai omni_intake_kinds
		with (
			mock.patch.object(runner.frappe, "conf", conf),
			mock.patch.object(runner, "ingest_envelope", return_value="moi") as ing,
		):
			self.assertEqual(runner.run()["moi"], 1)
			self.assertEqual(ing.call_count, 1)
		self.assertTrue((other / "f.json").exists())  # thư mục form không bị đọc hay di chuyển

	def test_spool_chi_doc_khong_di_chuyen_va_khong_nap_lai(self):
		a = self.put("a.json", {})
		done = set()
		ledger = mock.MagicMock()
		ledger.get_value.side_effect = lambda k: 1 if k in done else None
		ledger.set_value.side_effect = lambda k, v, expires_in_sec=None: done.add(k)
		real_access = os.access
		with (
			mock.patch.object(runner.frappe, "cache", return_value=ledger),
			mock.patch.object(
				runner.os, "access", side_effect=lambda p, m: False if m == os.W_OK else real_access(p, m)
			),
			mock.patch.object(runner, "_in_window", return_value=True),
			mock.patch.object(runner, "ingest_envelope", return_value="moi") as ing,
		):
			self.assertEqual(runner.run()["moi"], 1)
			self.assertEqual(runner.run()["moi"], 0)  # lần hai: đã có dấu, bỏ qua
			self.assertEqual(ing.call_count, 1)
		self.assertTrue(a.exists())  # không di chuyển được thì để nguyên

	def test_thieu_kenh_o_che_do_chi_doc_duoc_thu_lai(self):
		self.put("a.json", {})
		ledger = mock.MagicMock()
		ledger.get_value.return_value = None
		real_access = os.access
		with (
			mock.patch.object(runner.frappe, "cache", return_value=ledger),
			mock.patch.object(
				runner.os, "access", side_effect=lambda p, m: False if m == os.W_OK else real_access(p, m)
			),
			mock.patch.object(runner, "_in_window", return_value=True),
			mock.patch.object(runner, "ingest_envelope", return_value="thieu_kenh"),
		):
			runner.run()
		ledger.set_value.assert_not_called()  # không ghi dấu: lần sau thử lại khi đã có kênh

	def test_cua_so_ngay(self):
		today = date.today()
		old = self.root / "inbox" / "2020" / "01" / "01" / "gmail" / "x.json"
		new = self.root / "inbox" / f"{today:%Y/%m/%d}" / "gmail" / "x.json"
		with mock.patch.object(runner.frappe, "conf", {"omni_intake_days": 3}):
			self.assertFalse(runner._in_window(self.root, old))
			self.assertTrue(runner._in_window(self.root, new))

	def test_gioi_han_moi_lan(self):
		for i in range(5):
			self.put(f"{i}.json", {})
		with mock.patch.object(runner, "ingest_envelope", return_value="moi"):
			self.assertEqual(runner.run(limit=3)["moi"], 3)
		self.assertEqual(len(list(self.d.glob("*.json"))), 2)

	def test_ingest_envelope_phan_loai(self):
		env = {"schema": "1", "kind": "message", "channel": "messenger"}
		self.assertEqual(runner.ingest_envelope(env), "chua_ho_tro")
		self.assertEqual(runner.ingest_envelope({"schema": "x"}), "bo_qua")
		with mock.patch.object(runner, "resolve_channel", return_value=None):
			self.assertEqual(
				runner.ingest_envelope({"schema": "1", "kind": "email", "event_key": "gmail:x"}), "thieu_kenh"
			)

	def _email_env(self, **over):
		e = {
			"schema": "1",
			"event_key": "gmail:m1@x",
			"channel": "gmail",
			"kind": "email",
			"occurred_at": "2026-11-01T18:01:56+07:00",
			"sender": {"value": "a@b.vn"},
			"identifiers": {"email": "a@b.vn"},
			"content": {"text": "s\n\nxin chào"},
			"raw": {"subject": "s"},
			"source": {"account": "Info@Miyano.com.vn"},
		}
		e.update(over)
		return e

	def test_email_tao_communication_khi_chua_co_duong_khac(self):
		acc = mock.MagicMock(email_id="info@miyano.com.vn", enable_incoming=0)
		acc.name = "TK"
		doc = mock.MagicMock()
		with (
			mock.patch.object(runner.frappe.db, "get_value", return_value=acc),
			mock.patch.object(runner.frappe.db, "exists", side_effect=[None, "COMM-1"]),
			mock.patch.object(runner.email_adapter, "find_channel", return_value="K"),
			mock.patch.object(runner.frappe, "get_doc", return_value=doc) as get_doc,
		):
			self.assertEqual(runner.ingest_envelope(self._email_env()), "moi")
			self.assertEqual(get_doc.call_args.args[0]["email_account"], "TK")
			doc.insert.assert_called_once_with(ignore_permissions=True)
			self.assertEqual(
				runner.ingest_envelope(self._email_env()), "trung"
			)  # đã có Communication cùng Message-ID

	def test_email_bo_qua_khi_crm_tu_keo_hop_thu_hoac_thu_tu_dong(self):
		acc = mock.MagicMock(email_id="info@miyano.com.vn", enable_incoming=1)
		acc.name = "TK"
		with (
			mock.patch.object(runner.frappe.db, "get_value", return_value=acc),
			mock.patch.object(runner.email_adapter, "find_channel", return_value="K"),
			mock.patch.object(runner.frappe, "get_doc") as get_doc,
		):
			self.assertEqual(runner.ingest_envelope(self._email_env()), "bo_qua")  # CRM tự nhận hộp thư này
			self.assertEqual(runner.ingest_envelope(self._email_env(ignored=True)), "bo_qua")
			get_doc.assert_not_called()

	def test_email_thieu_tai_khoan_hoac_kenh(self):
		with mock.patch.object(runner.frappe.db, "get_value", return_value=None):
			self.assertEqual(runner.ingest_envelope(self._email_env()), "thieu_kenh")
		acc = mock.MagicMock(email_id="info@miyano.com.vn", enable_incoming=0)
		acc.name = "TK"
		with (
			mock.patch.object(runner.frappe.db, "get_value", return_value=acc),
			mock.patch.object(runner.email_adapter, "find_channel", return_value=None),
		):
			self.assertEqual(runner.ingest_envelope(self._email_env()), "thieu_kenh")

	def test_ingest_web_form_dung_adapter(self):
		env = {
			"schema": "1",
			"event_key": "web:1",
			"channel": "web_form",
			"kind": "lead_form",
			"sender": {"display_name": "A"},
			"identifiers": {"phone": "+84912345678"},
			"raw": {"fields": {}},
			"source": {"account": "spd"},
		}
		adapter = mock.MagicMock()
		adapter.parse.return_value = []
		with (
			mock.patch.object(runner, "resolve_channel", return_value="W"),
			mock.patch.object(runner.frappe, "get_cached_doc"),
			mock.patch.object(runner, "get_adapter", return_value=adapter),
		):
			self.assertEqual(
				runner.ingest_envelope(env), "bo_qua"
			)  # adapter trả 0 sự kiện (honeypot / form lạ)
		body = json.loads(adapter.parse.call_args.args[0])
		self.assertEqual(body["form"], "spd")
		self.assertTrue(mapper.webform_body(env)["submission_id"] == body["submission_id"])


if __name__ == "__main__":
	unittest.main()
