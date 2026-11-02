"""V2-01 (08.20, ĐQ-6): câu lệnh bot chỉ nạp phiên bản được GHIM; tệp mới thả vào thư mục không tự có hiệu lực."""

import shutil
import tempfile
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.bot import analyze, reply
from omni_crm.bot.prompts import loader
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_eval import RunTestCase

MARK = "ĐÁNH_DẤU_BẢN_MỚI"
NAMES = ("analyze", "reply", "judge")


def make_dir_with_v2() -> Path:
	"""Thư mục câu lệnh tạm: bản v1 chép từ thật, thêm bản v2 mang dấu hiệu riêng."""
	tmp = Path(tempfile.mkdtemp())
	for name in NAMES:
		shutil.copy(loader.PROMPT_DIR / f"{name}_v1.md", tmp / f"{name}_v1.md")
		(tmp / f"{name}_v2.md").write_text(f"version: v2\n\n{MARK} {name}\n", encoding="utf-8")
	return tmp


class PinCase(FrappeTestCase):
	def setUp(self):
		self.tmp = make_dir_with_v2()
		self.addCleanup(shutil.rmtree, self.tmp, True)
		patcher = mock.patch.object(loader, "PROMPT_DIR", self.tmp)
		patcher.start()
		self.addCleanup(patcher.stop)
		self._pin_orig = {
			f: frappe.db.get_single_value("Omni Settings", f)
			for f in ("analyze_prompt_version", "reply_prompt_version")
		}
		self.addCleanup(self._restore_pins)

	def _restore_pins(self):
		set_settings(**{k: (v or "") for k, v in self._pin_orig.items()})


class TestLoaderPin(PinCase):
	def test_default_is_pinned_v1_even_when_v2_file_exists(self):
		set_settings(analyze_prompt_version="", reply_prompt_version="")
		for name in NAMES:
			self.assertEqual(loader.latest_version(name), "v2")  # tệp mới có mặt...
			self.assertEqual(loader.active_version(name), "v1")  # ...nhưng không được nạp
			p = loader.load_prompt(name)
			self.assertEqual(p.version, "v1")
			self.assertNotIn(MARK, p.text)

	def test_settings_pin_selects_version_and_latest_is_explicit(self):
		set_settings(analyze_prompt_version="v2")
		self.assertEqual(loader.load_prompt("analyze").version, "v2")
		self.assertIn(MARK, loader.load_prompt("analyze").text)
		self.assertEqual(loader.load_prompt("reply").version, "v1")  # ghim độc lập từng câu lệnh
		self.assertEqual(loader.load_prompt("reply", loader.LATEST).version, "v2")

	def test_missing_pinned_file_raises_instead_of_falling_back(self):
		set_settings(analyze_prompt_version="")
		(self.tmp / "analyze_v1.md").rename(self.tmp / "ngoai_v1.md")  # thư mục chỉ còn v2
		self.addCleanup((self.tmp / "ngoai_v1.md").rename, self.tmp / "analyze_v1.md")
		with self.assertRaises(FileNotFoundError):
			loader.load_prompt("analyze")  # không âm thầm lấy v2

	def test_unpinned_name_is_not_auto_selected(self):
		(self.tmp / "zz_v1.md").write_text("version: v1\n\nx\n", encoding="utf-8")
		self.assertIsNone(loader.active_version("zz"))
		with self.assertRaises(FileNotFoundError):
			loader.load_prompt("zz")
		self.assertEqual(loader.load_prompt("zz", "v1").version, "v1")

	def test_settings_rejects_pin_without_file(self):
		with self.assertRaises(frappe.ValidationError):
			set_settings(reply_prompt_version="v9")
		frappe.db.rollback()

	def test_sync_settings_fills_empty_but_never_overwrites_pin(self):
		set_settings(analyze_prompt_version="v2", reply_prompt_version="")
		written = loader.sync_settings()
		self.assertNotIn("analyze", written)  # bản đã ghim giữ nguyên dù có v2
		self.assertEqual(frappe.db.get_single_value("Omni Settings", "analyze_prompt_version"), "v2")
		self.assertEqual(
			written.get("reply"), "v1"
		)  # trống thì điền mặc định đã ghim, không phải số cao nhất
		self.assertEqual(frappe.db.get_single_value("Omni Settings", "reply_prompt_version"), "v1")


class TestProductionPathsUsePin(PinCase):
	"""analyze/reply thật nạp câu lệnh ghim: bắt lời gọi call_tool mà không gọi AI."""

	def capture(self, fn, *args, **kwargs):
		seen = {}

		def fake_call(kind, model, system, *a, **kw):
			seen.update(system=system, prompt_version=kw.get("prompt_version"))

		with mock.patch("omni_crm.bot.ai_client.call_tool", side_effect=fake_call):
			fn(*args, **kwargs)
		return seen

	def test_run_analysis_uses_pinned_version(self):
		set_settings(analyze_prompt_version="")
		with mock.patch.object(analyze, "build_conversation", return_value="x"):
			seen = self.capture(analyze.run_analysis, "b-pin", "LEAD-PIN")
		self.assertEqual(seen["prompt_version"], "v1")
		self.assertNotIn(MARK, seen["system"])
		set_settings(analyze_prompt_version="v2")
		with mock.patch.object(analyze, "build_conversation", return_value="x"):
			seen = self.capture(analyze.run_analysis, "b-pin", "LEAD-PIN")
		self.assertEqual(seen["prompt_version"], "v2")
		self.assertIn(MARK, seen["system"])

	def test_make_draft_uses_pinned_version(self):
		set_settings(reply_prompt_version="")
		item = {"id": "KB-1", "answer": "a"}
		kwargs = dict(
			lead="LEAD-PIN",
			identity="ID-PIN",
			channel="CH",
			channel_type="Zalo OA",
			batch_id="b-pin",
			customer_text="x",
			at=now_datetime(),
			check_channel=False,
		)
		with (
			mock.patch.object(reply.knowledge, "kb_items", return_value=[item]),
			mock.patch.object(reply.knowledge, "render_kb", return_value="kb"),
			mock.patch.object(reply, "build_user_message", return_value="x"),
		):
			seen = self.capture(reply.make_draft, **kwargs)
			self.assertEqual(seen["prompt_version"], "v1")
			self.assertNotIn(MARK, seen["system"])
			set_settings(reply_prompt_version="v2")
			seen = self.capture(reply.make_draft, **kwargs)
		self.assertEqual(seen["prompt_version"], "v2")
		self.assertIn(MARK, seen["system"])


class TestEvalUsesPin(PinCase, RunTestCase):
	"""Bộ đề (analyze/reply/giám khảo) chạy bản ghim; so sánh hai bản chỉ khi chỉ định rõ."""

	def setUp(self):
		RunTestCase.setUp(self)
		PinCase.setUp(self)  # PinCase.setUp sau để PROMPT_DIR tạm có hiệu lực
		set_settings(analyze_prompt_version="", reply_prompt_version="")

	def systems(self):
		return "\n".join(c["system"] for c in self.fake.calls)

	def test_default_run_uses_pinned_versions_and_label(self):
		self.add_cases(self.good_set(4))
		_, data = self.go()
		self.assertNotIn(MARK, self.systems())
		self.assertEqual(data["meta"]["prompt_label"], "analyze-v1_reply-v1_judge-v1")

	def test_explicit_versions_compare_without_changing_pin(self):
		self.add_cases(self.good_set(4))
		_, data = self.go(prompt_versions={"analyze": "v2", "reply": "v2", "judge": "v2"})
		self.assertIn(MARK, self.systems())
		self.assertEqual(data["meta"]["prompt_label"], "analyze-v2_reply-v2_judge-v2")
		self.assertEqual(loader.active_version("analyze"), "v1")
		self.assertEqual(frappe.db.get_single_value("Omni Settings", "analyze_prompt_version") or "", "")
