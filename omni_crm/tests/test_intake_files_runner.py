"""Nạp file intake: luồng tệp (archive / dead_crm / chua_nap / giữ nguyên) với DB và cổng được thay bằng giả."""

import json
import tempfile
import unittest
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
			mock.patch.object(runner.frappe, "conf", {"omni_intake_dir": str(self.root)}),
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

	def test_ingest_email_goi_cong_tiep_nhan(self):
		env = {
			"schema": "1",
			"event_key": "gmail:m1@x",
			"channel": "gmail",
			"kind": "email",
			"occurred_at": "2026-11-01T18:01:56+07:00",
			"sender": {"value": "a@b.vn"},
			"identifiers": {"email": "a@b.vn"},
			"content": {"text": "xin chào"},
			"raw": {"subject": "s"},
		}
		with (
			mock.patch.object(runner, "resolve_channel", return_value="K"),
			mock.patch.object(runner.gateway, "save_inbound", side_effect=["OIN-1", None]) as save,
		):
			self.assertEqual(runner.ingest_envelope(env), "moi")
			self.assertEqual(runner.ingest_envelope(env), "trung")  # cổng trả None = đã nhận
		self.assertEqual(save.call_args.args[0].channel, "K")
		self.assertIn("intake", save.call_args.args[0].raw)

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
