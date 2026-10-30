# ruff: noqa: RUF001
"""D3-03: tính WER/chi phí/quyết định (stt/trial.py) và script tests/stt_trial/run_trial.py với FakeSTTProvider
trên thư mục tạm. Không gọi mạng, không dùng ghi âm thật."""

import csv
import json
import struct
import tempfile
import unicodedata
import wave
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import ai_client
from omni_crm.bot.fake_client import FakeAIClient
from omni_crm.stt import registry, trial
from omni_crm.stt.fake import FakeSTTProvider
from omni_crm.tests.stt_trial import run_trial


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> bytes:
	frames = int(seconds * rate)
	with wave.open(str(path), "wb") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(rate)
		w.writeframes(struct.pack("<h", 0) * frames)
	return path.read_bytes()


class TestWER(FrappeTestCase):
	def test_giong_het_la_0(self):
		self.assertEqual(trial.wer("tôi muốn mua găng tay", "tôi muốn mua găng tay"), 0.0)

	def test_bo_dau_cau_va_chu_hoa(self):
		self.assertEqual(trial.wer("Tôi muốn mua, găng tay!", "tôi muốn mua găng tay"), 0.0)

	def test_unicode_nfc_nfd_nhu_nhau(self):
		nfd = unicodedata.normalize("NFD", "tiếng Việt có dấu")
		nfc = unicodedata.normalize("NFC", "tiếng Việt có dấu")
		self.assertNotEqual(nfd, nfc)
		self.assertEqual(trial.wer(nfc, nfd), 0.0)

	def test_dau_thanh_la_loi(self):
		# "mua" khác "múa" là một từ sai, vì WER giữ dấu
		self.assertEqual(trial.wer("tôi mua găng", "tôi múa găng"), 1 / 3)

	def test_thay_xoa_chen(self):
		self.assertEqual(trial.word_errors("a b c d", "a x c d"), trial.WordErrors(1, 0, 0, 4))
		self.assertEqual(trial.word_errors("a b c d", "a c d"), trial.WordErrors(0, 1, 0, 4))
		self.assertEqual(trial.word_errors("a b c d", "a b x c d"), trial.WordErrors(0, 0, 1, 4))
		self.assertEqual(trial.wer("a b c d", "a x c"), 0.5)  # 1 thay + 1 xóa / 4

	def test_wer_co_the_tren_1(self):
		self.assertEqual(trial.wer("a", "a b c"), 2.0)

	def test_rong(self):
		self.assertEqual(trial.wer("a b", ""), 1.0)  # bỏ sót hết
		self.assertIsNone(trial.wer("", "abc"))  # không có bản chuẩn: không đo được
		self.assertIsNone(trial.wer("  ,. ", ""))  # chỉ dấu câu = rỗng
		self.assertIsNone(trial.wer(None, "x"))

	def test_goi_y_gop(self):
		total = trial.pool_wer([trial.WordErrors(1, 0, 0, 4), trial.WordErrors(0, 0, 0, 6)])
		self.assertEqual(total, 0.1)
		self.assertIsNone(trial.pool_wer([]))


class TestCostAndDuration(FrappeTestCase):
	def test_chi_phi_theo_phut(self):
		pricing = {"A": {"per_minute": 0.006, "currency": "USD"}}
		self.assertAlmostEqual(trial.estimate_cost(pricing, "A", 90)["amount"], 0.009)
		self.assertEqual(trial.estimate_cost(pricing, "A", 90)["currency"], "USD")

	def test_thieu_gia_hoac_thoi_luong_la_none(self):
		self.assertIsNone(trial.estimate_cost({}, "A", 90))
		self.assertIsNone(trial.estimate_cost({"A": {"per_minute": 1}}, "A", None))

	def test_bang_gia_sai(self):
		with tempfile.TemporaryDirectory() as tmp:
			p = Path(tmp) / "gia.json"
			p.write_text(json.dumps({"A": {"per_minute": "nhieu"}}))
			with self.assertRaises(ValueError):
				trial.load_pricing(str(p))
			p.write_text(json.dumps({"A": {"per_minute": 0.01, "currency": "VND"}}))
			self.assertEqual(trial.load_pricing(str(p))["A"]["per_minute"], 0.01)

	def test_thoi_luong_wav(self):
		with tempfile.TemporaryDirectory() as tmp:
			p = Path(tmp) / "a.wav"
			write_wav(p, 2.5)
			self.assertAlmostEqual(trial.audio_seconds(p, {}), 2.5, places=2)
			self.assertEqual(trial.audio_seconds(Path(tmp) / "b.mp3", {"duration_seconds": 61}), 61.0)
			self.assertIsNone(trial.audio_seconds(Path(tmp) / "b.mp3", {}))


class TestDiscover(FrappeTestCase):
	def test_thu_muc_trong_repo_bi_tu_choi(self):
		repo_file = Path(trial.__file__).resolve()
		with self.assertRaises(ValueError):
			trial.assert_outside_repo(repo_file.parent)
		trial.assert_outside_repo(Path(tempfile.gettempdir()))

	def test_doc_ban_chuan_va_meta(self):
		with tempfile.TemporaryDirectory() as tmp:
			d = Path(tmp)
			write_wav(d / "c1.wav")
			(d / "c1.txt").write_text("Tôi muốn mua găng tay.", encoding="utf-8")
			(d / "c1.json").write_text(json.dumps({"stereo": True, "synthetic": True}))
			write_wav(d / "c2.wav")
			(d / "ghi-chu.md").write_text("bỏ qua")
			recs = trial.discover_recordings(d)
			self.assertEqual([r.name for r in recs], ["c1", "c2"])
			self.assertEqual(recs[0].reference, "Tôi muốn mua găng tay.")
			self.assertTrue(recs[0].stereo and recs[0].synthetic)
			self.assertIsNone(recs[1].reference)
			self.assertFalse(recs[1].synthetic)
			self.assertEqual(recs[0].mime, "audio/wav")


class TestReviewAndDecision(FrappeTestCase):
	def _write(self, tmp, rows):
		p = Path(tmp) / "r.csv"
		with open(p, "w", newline="", encoding="utf-8-sig") as f:
			w = csv.DictWriter(f, fieldnames=trial.REVIEW_COLUMNS)
			w.writeheader()
			for r in rows:
				w.writerow({**dict.fromkeys(trial.REVIEW_COLUMNS, ""), **r})
		return str(p)

	def test_tong_hop_danh_dau(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = self._write(
				tmp,
				[
					{"provider": "A", "recording": "c1", "field": "need", "correct": "Đúng"},
					{"provider": "A", "recording": "c1", "field": "products", "correct": "sai"},
					{"provider": "A", "recording": "c2", "field": "need", "correct": "đúng"},
					{"provider": "A", "recording": "c2", "field": "intent", "correct": ""},
					{"provider": "B", "recording": "c1", "field": "need", "correct": "Đúng"},
				],
			)
			s = trial.summarize_review(path)
			self.assertEqual(s["A"], {"correct": 2, "wrong": 1, "unmarked": 1, "accuracy": 2 / 3})
			self.assertEqual(s["B"]["accuracy"], 1.0)

	def test_khong_co_danh_dau_la_none(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = self._write(tmp, [{"provider": "A", "field": "need"}])
			self.assertIsNone(trial.summarize_review(path)["A"]["accuracy"])

	def test_cell_chong_cong_thuc(self):
		self.assertEqual(trial.safe_cell("=1+1"), "'=1+1")
		self.assertEqual(trial.safe_cell("bình thường"), "bình thường")

	def test_quyet_dinh_ba_trang_thai(self):
		th = trial.Thresholds()
		self.assertEqual((th.wer_max, th.accuracy_min, th.min_references), (0.25, 0.80, 5))
		# chưa đo: thiếu bản chuẩn
		self.assertEqual(trial.decide(None, 0.9, 0, th)["status"], "CHƯA ĐO")
		# chưa đo: thiếu đánh dấu
		self.assertEqual(trial.decide(0.2, None, 5, th)["status"], "CHƯA ĐO")
		# chưa đo: ít hơn 5 bản chuẩn dù WER thấp
		self.assertEqual(trial.decide(0.1, 0.9, 4, th)["status"], "CHƯA ĐO")
		self.assertEqual(trial.decide(0.25, 0.80, 5, th)["status"], "ĐẠT")  # biên: bằng ngưỡng thì đạt
		self.assertEqual(trial.decide(0.26, 0.95, 5, th)["status"], "KHÔNG ĐẠT")
		self.assertEqual(trial.decide(0.10, 0.79, 5, th)["status"], "KHÔNG ĐẠT")

	def test_ket_luan_chung(self):
		th = trial.Thresholds()
		per = {"A": trial.decide(0.2, 0.9, 5, th), "B": trial.decide(0.5, 0.9, 5, th)}
		self.assertEqual(trial.overall(per)["branch"], "D3-04")
		self.assertEqual(trial.overall({"B": per["B"]})["branch"], "HOÃN D3-04")
		unknown = trial.decide(None, None, 0, th)
		self.assertEqual(trial.overall({"B": per["B"], "C": unknown})["branch"], "CHƯA ĐO")
		self.assertEqual(trial.overall({})["branch"], "CHƯA ĐO")


class TestRunTrial(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.tmp = tempfile.TemporaryDirectory()
		root = Path(self.tmp.name)
		self.audio_dir, self.out_dir = root / "ghi_am", root / "ket_qua"
		self.audio_dir.mkdir()
		self.audio = {}
		for i, (name, ref) in enumerate(
			(("c1", "tôi muốn mua găng tay"), ("c2", "cho tôi báo giá khẩu trang"), ("c3", None))
		):
			self.audio[name] = write_wav(self.audio_dir / f"{name}.wav", 60.0, rate=8000 + 100 * i)
			if ref:
				(self.audio_dir / f"{name}.txt").write_text(ref, encoding="utf-8")
		self.fake = FakeSTTProvider(
			transcripts={
				self.audio["c1"]: "Tôi muốn mua găng tay.",  # đúng
				self.audio["c2"]: "cho tôi báo giá khẩu",  # thiếu 1 từ / 5
			},
			name="FakeA",
		)
		registry.register_provider(self.fake)
		self.pricing = root / "gia.json"
		self.pricing.write_text(json.dumps({"FakeA": {"per_minute": 0.5, "currency": "USD"}}))
		self.addCleanup(registry.unregister_provider, "FakeA")
		self.addCleanup(self.tmp.cleanup)

	def run_it(self, **kw):
		args = {
			"audio_dir": str(self.audio_dir),
			"out_dir": str(self.out_dir),
			"providers": ["FakeA"],
			"pricing_file": str(self.pricing),
			"analyze": False,
		}
		args.update(kw)
		return run_trial.run(**args)

	def test_wer_thoi_gian_chi_phi(self):
		summary = self.run_it()
		a = summary["providers"]["FakeA"]
		self.assertEqual(a["recordings"], 3)
		self.assertEqual(a["failed"], 0)
		self.assertEqual(a["references"], 2)
		self.assertEqual(a["wer"], 1 / 11)  # 1 từ thiếu / (5 + 6 từ chuẩn)
		self.assertAlmostEqual(a["cost"]["amount"], 1.5)  # 3 phút x 0,5
		self.assertGreaterEqual(a["avg_seconds"], 0)
		self.assertEqual(summary["decision"]["branch"], "CHƯA ĐO")  # chỉ 2 bản chuẩn, chưa có đánh dấu
		self.assertEqual(self.fake.calls[0]["mime"], "audio/wav")

	def test_luu_json_va_csv_ngoai_repo(self):
		self.run_it()
		saved = json.loads((self.out_dir / "ket-qua.json").read_text(encoding="utf-8"))
		rows = {(r["recording"], r["provider"]): r for r in saved["results"]}
		self.assertEqual(rows[("c1", "FakeA")]["text"], "Tôi muốn mua găng tay.")
		self.assertEqual(rows[("c1", "FakeA")]["wer"], 0.0)
		self.assertIsNone(rows[("c3", "FakeA")]["wer"])
		self.assertTrue((self.out_dir / "bang-tong-hop.csv").exists())

	def test_khong_co_nha_cung_cap_thi_bao_ro(self):
		with mock.patch.object(registry, "available_names", return_value=[]):
			with self.assertRaises(ValueError):
				self.run_it(providers=None)

	def test_tu_choi_thu_muc_trong_repo(self):
		repo_dir = Path(trial.__file__).resolve().parent
		with self.assertRaises(ValueError):
			self.run_it(out_dir=str(repo_dir / "kq"))
		with self.assertRaises(ValueError):
			self.run_it(audio_dir=str(repo_dir))

	def test_loi_mot_tep_khong_dung_ca_lo(self):
		self.fake.faults = ["fatal"]
		summary = self.run_it()
		a = summary["providers"]["FakeA"]
		self.assertEqual((a["recordings"], a["failed"]), (3, 1))

	def test_q11_chan_nha_cung_cap_that_voi_ghi_am_khach(self):
		real = FakeSTTProvider(name="RealFake")
		real.is_real = True
		registry.register_provider(real)
		self.addCleanup(registry.unregister_provider, "RealFake")
		conf = frappe.local.conf
		old = conf.get("omni_ai_allow_customer_data")
		conf["omni_ai_allow_customer_data"] = 0
		self.addCleanup(
			lambda: conf.__setitem__("omni_ai_allow_customer_data", old)
			if old is not None
			else conf.pop("omni_ai_allow_customer_data", None)
		)
		summary = self.run_it(providers=["RealFake"])
		self.assertEqual(real.calls, [])  # không có byte nào rời máy
		self.assertEqual(summary["providers"]["RealFake"]["failed"], 3)
		self.assertEqual(summary["providers"]["RealFake"]["blocked"], 3)
		# ghi âm gắn nhãn tổng hợp thì được qua
		for n in ("c1", "c2", "c3"):
			(self.audio_dir / f"{n}.json").write_text(json.dumps({"synthetic": True}))
		summary = self.run_it(providers=["RealFake"])
		self.assertEqual(len(real.calls), 3)
		self.assertEqual(summary["providers"]["RealFake"]["failed"], 0)

	def test_xuat_bang_danh_dau_va_tong_hop(self):
		accepted = {
			"need": "mua găng tay",
			"intent": "Đặt hàng",
			"wants_human": False,
			"products": "găng tay",
		}
		with mock.patch.object(run_trial, "analyze_transcript", return_value={"accepted": accepted}) as m:
			summary = self.run_it(analyze=True)
		self.assertEqual(m.call_count, 3)
		path = self.out_dir / "bang-danh-dau.csv"
		with open(path, newline="", encoding="utf-8-sig") as f:
			rows = list(csv.DictReader(f))
		self.assertEqual(len(rows), 3 * 4)
		self.assertEqual({r["correct"] for r in rows}, {""})
		self.assertEqual(set(rows[0]), set(trial.REVIEW_COLUMNS))
		self.assertIn("review_csv", summary)

	def test_phan_tich_qua_q11_voi_client_gia(self):
		# văn bản của ghi âm KHÔNG gắn nhãn tổng hợp thì client thật bị chặn: kết quả là "chưa đo", không phải số
		from omni_crm.tests.test_ai_client import set_settings

		real_ai = FakeAIClient()
		real_ai.is_real = True
		settings = frappe.get_doc("Omni Settings")
		old_budget = settings.daily_token_budget
		set_settings(daily_token_budget=1_000_000)
		ai_client.set_client(real_ai)
		conf = frappe.local.conf
		old = conf.get("omni_ai_allow_customer_data")
		conf["omni_ai_allow_customer_data"] = 0
		try:
			out = run_trial.analyze_transcript("tôi muốn mua găng tay", synthetic=False)
			self.assertIn("unmeasured", out)
			self.assertEqual(real_ai.calls, [])
			out2 = run_trial.analyze_transcript("tôi muốn mua găng tay", synthetic=True)
			self.assertEqual(len(real_ai.calls), 1)
			self.assertIn("accepted", out2)
		finally:
			ai_client.set_client(None)
			if old is not None:
				conf["omni_ai_allow_customer_data"] = old
			else:
				conf.pop("omni_ai_allow_customer_data", None)
			set_settings(daily_token_budget=old_budget)
			frappe.db.delete("Omni Bot Run", {"batch_id": ["like", "stt-trial::%"]})
			frappe.db.commit()
