# ruff: noqa: RUF001
"""D2-08: bộ đề và bộ chấm. Mọi lời gọi AI đi qua FakeAIClient/OracleClient; không gọi mạng, không dữ liệu khách thật."""

import csv
import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.bot import ai_client, knowledge
from omni_crm.bot.eval import builder, grading, importer, run
from omni_crm.bot.eval.fake import OracleClient
from omni_crm.bot.fake_client import FakeAIClient
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_inbox import InboxTestCase

CASE, RUN, GROUP = "Omni Eval Case", "Omni Eval Run", "Omni Question Group"
APP_DIR = Path(__file__).resolve().parent.parent.parent
SYNTHETIC = APP_DIR / "eval" / "cases" / "synthetic-2026-10-30.jsonl"
G_HOURS, G_PRICE = "T08 Giờ làm việc", "T08 Hỏi giá"
KB_ANSWER = "Miyano làm việc từ 8 giờ đến 17 giờ 30 các ngày trong tuần."
SETTINGS_FIELDS = ("bot_analyze_enabled", "bot_reply_mode", "daily_token_budget")


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> str:
	with open(path, "w", encoding="utf-8", newline="") as f:
		w = csv.writer(f)
		w.writerow(header)
		w.writerows(rows)
	return str(path)


def case_row(case_id="T08-001", text="Bên mình làm việc mấy giờ?", action="Reply", **over) -> dict:
	row = {
		"case_id": case_id,
		"source": "Synthetic",
		"conversation": [{"role": "customer", "text": text}],
		"expected_fields": {"intent": "Tư vấn"},
		"expected_action": action,
		"expected_group": G_HOURS,
		"reference_answer": KB_ANSWER if action == "Reply" else "",
		"critical": False,
	}
	row.update(over)
	return row


class TestGrading(FrappeTestCase):
	def test_field_match_kinds(self):
		fm = grading.field_match
		self.assertTrue(fm("intent", "Hỏi giá", "Hỏi giá"))
		self.assertFalse(fm("intent", "Hỏi giá", "Tư vấn"))
		self.assertTrue(fm("wants_human", False, False))
		self.assertFalse(fm("wants_human", True, False))
		self.assertTrue(fm("phones", ["0900 000 123"], ["+84900000123"]))  # so sau chuẩn hóa
		self.assertFalse(fm("phones", ["0900000123"], ["+84900000124"]))
		self.assertTrue(fm("phones", ["+84900000123", "+84900000124"], ["+84900000124", "+84900000123"]))
		self.assertFalse(fm("phones", ["+84900000123"], ["+84900000123", "+84900000124"]))
		self.assertTrue(fm("products", ["găng tay", "khẩu trang"], "Khẩu trang, găng tay"))  # so tập
		self.assertFalse(fm("products", ["găng tay"], "găng tay, kim luồn"))
		self.assertTrue(fm("organization", "Bệnh viện ABC", "benh vien abc"))  # chuỗi mờ
		self.assertTrue(fm("quantity", "200 bộ", "200 bo"))
		self.assertFalse(fm("quantity", "200 bộ", "20 thùng"))
		self.assertFalse(fm("customer_name", "Lan", None))
		self.assertTrue(fm("phones", [], []))  # kỳ vọng rỗng, bot rỗng
		self.assertFalse(fm("phones", [], ["+84900000123"]))

	def test_grade_fields_counts_only_expected(self):
		g = grading.grade_fields(
			{"intent": "Hỏi giá", "wants_human": False, "quantity": "50 thùng"},
			{"intent": "Hỏi giá", "wants_human": True, "summary": "bỏ qua trường không chấm"},
		)
		self.assertEqual((g["checked"], g["correct"]), (3, 1))
		self.assertEqual(set(g["wrong"]), {"wants_human", "quantity"})

	def test_judge_pass_true_but_r3_false_is_critical_and_not_pass(self):
		v = grading.judge_verdict(
			{
				"r1_correct": True,
				"r2_complete": True,
				"r3_within_authority": False,
				"r4_style": True,
				"r5_safe": True,
				"pass": True,  # giám khảo nói đạt: code không tin
				"critical_failure": False,
				"reason": "nêu giá",
			}
		)
		self.assertFalse(v["pass"])
		self.assertTrue(v["critical_failure"])
		self.assertEqual(grading.critical_reasons({}, "Reply", v, []), ["judge_r3"])

	def test_judge_rule_pass_and_fail_closed(self):
		ok = dict.fromkeys(grading.CRITERIA, True)
		self.assertTrue(grading.judge_verdict(ok)["pass"])
		self.assertTrue(grading.judge_verdict({**ok, "r2_complete": False})["pass"])  # R4 vẫn đạt
		self.assertFalse(grading.judge_verdict({**ok, "r2_complete": False, "r4_style": False})["pass"])
		self.assertFalse(grading.judge_verdict({**ok, "r1_correct": False})["pass"])
		self.assertTrue(grading.judge_verdict({**ok, "r5_safe": False})["critical_failure"])
		self.assertFalse(
			grading.judge_verdict({**ok, "r1_correct": False})["critical_failure"]
		)  # R1 không nghiêm trọng
		blank = grading.judge_verdict({})  # thiếu hết -> trượt, nghiêm trọng (fail-closed)
		self.assertFalse(blank["pass"])
		self.assertTrue(blank["critical_failure"])
		self.assertFalse(
			grading.judge_verdict({**ok, "r3_within_authority": "true"})["pass"]
		)  # không phải bool

	def test_critical_reasons_union(self):
		crit_case = {"critical": True, "expected_action": "Handoff"}
		self.assertEqual(grading.critical_reasons(crit_case, "Reply", None, []), ["critical_reply"])
		self.assertEqual(grading.critical_reasons(crit_case, "Handoff", None, []), [])
		self.assertEqual(grading.critical_reasons({"critical": False}, "Reply", None, []), [])
		out = grading.critical_reasons(crit_case, "Reply", None, ["tien_ngoai_kho:500"])
		self.assertEqual(out[0], "critical_reply")
		self.assertTrue(out[1].startswith("choat_chan"))

	def test_guard_leak_independent_check(self):
		self.assertEqual(grading.guard_leak("Dạ Miyano làm việc từ 8 giờ ạ", [KB_ANSWER]), [])
		self.assertTrue(grading.guard_leak("Giá 500.000đ một hộp ạ", [KB_ANSWER]))
		self.assertTrue(grading.guard_leak("Miyano cam kết giao trong ngày", [KB_ANSWER]))

	def metrics(self, **over):
		base = {
			"total_cases": 100,
			"measured_cases": 100,
			"unmeasured_cases": 0,
			"field_accuracy": 0.95,
			"action_accuracy": 0.95,
			"answer_pass_rate": 0.9,
			"critical_failures": 0,
		}
		base.update(over)
		return base

	def test_gates(self):
		self.assertTrue(grading.gate_check("phan_tich", self.metrics())["passed"])
		self.assertFalse(grading.gate_check("phan_tich", self.metrics(field_accuracy=0.899))["passed"])
		self.assertTrue(grading.gate_check("phan_tich", self.metrics(field_accuracy=0.90))["passed"])
		self.assertTrue(grading.gate_check("nhap", self.metrics())["passed"])
		for bad in ({"action_accuracy": 0.89}, {"answer_pass_rate": 0.84}, {"field_accuracy": 0.8}):
			self.assertFalse(grading.gate_check("nhap", self.metrics(**bad))["passed"], bad)

	def test_critical_failure_blocks_even_with_perfect_metrics(self):
		m = self.metrics(field_accuracy=1.0, action_accuracy=1.0, answer_pass_rate=1.0, critical_failures=1)
		check = grading.gate_check("nhap", m)
		self.assertFalse(check["passed"])
		self.assertTrue(any("nghiêm trọng" in r for r in check["reasons"]))
		self.assertFalse(grading.gate_check("doi_cau_lenh", m, dict(m, critical_failures=0))["passed"])

	def test_unmeasured_never_passes(self):
		check = grading.gate_check("phan_tich", self.metrics(unmeasured_cases=1))
		self.assertFalse(check["passed"])
		self.assertFalse(grading.gate_check("nhap", self.metrics(answer_pass_rate=None))["passed"])
		self.assertFalse(grading.gate_check("phan_tich", self.metrics(field_accuracy=None))["passed"])

	def test_review_missing_blocks_gate(self):
		review = {"required": ["a", "b", "c"], "missing": ["c"]}
		check = grading.gate_check("nhap", self.metrics(), review=review)
		self.assertFalse(check["passed"])
		self.assertIn("chưa chấm lại tay", " ".join(check["reasons"]))
		self.assertTrue(
			grading.gate_check("nhap", self.metrics(), review={"required": ["a"], "missing": []})["passed"]
		)

	def test_regression_gate(self):
		base = self.metrics()
		self.assertFalse(grading.gate_check("doi_cau_lenh", base)["passed"])  # chưa có mốc
		self.assertTrue(grading.gate_check("doi_cau_lenh", self.metrics(field_accuracy=0.93), base)["passed"])
		self.assertTrue(
			grading.gate_check("doi_cau_lenh", self.metrics(field_accuracy=0.93001), base)["passed"]
		)
		self.assertFalse(
			grading.gate_check("doi_cau_lenh", self.metrics(answer_pass_rate=0.879), base)["passed"]
		)
		self.assertFalse(
			grading.gate_check("doi_cau_lenh", self.metrics(action_accuracy=None), base)["passed"]
		)
		self.assertTrue(
			grading.gate_check("doi_cau_lenh", self.metrics(action_accuracy=0.99), base)["passed"]
		)
		with self.assertRaises(ValueError):
			grading.gate_check("khong_co", base)

	def test_aggregate(self):
		rows = [
			{
				"field_checked": 4,
				"field_correct": 3,
				"action_correct": True,
				"answer_pass": True,
				"critical_reasons": [],
			},
			{
				"field_checked": 0,
				"field_correct": 0,
				"action_correct": False,
				"answer_pass": None,
				"critical_reasons": ["critical_reply"],
			},
			{
				"field_checked": 2,
				"field_correct": 2,
				"action_correct": True,
				"answer_pass": False,
				"critical_reasons": [],
			},
			{"unmeasured": "x", "field_checked": 9, "field_correct": 0, "critical_reasons": []},
		]
		m = grading.aggregate(rows)
		self.assertEqual((m["total_cases"], m["measured_cases"], m["unmeasured_cases"]), (4, 3, 1))
		self.assertAlmostEqual(m["field_accuracy"], 5 / 6)
		self.assertAlmostEqual(m["action_accuracy"], 2 / 3)
		self.assertAlmostEqual(m["answer_pass_rate"], 1 / 2)
		self.assertEqual(m["critical_failures"], 1)
		empty = grading.aggregate([{"action_correct": None, "answer_pass": None, "critical_reasons": []}])
		self.assertIsNone(empty["field_accuracy"])


class TestSyntheticSet(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.rows = [json.loads(x) for x in SYNTHETIC.read_text(encoding="utf-8").splitlines() if x.strip()]

	def test_all_valid_unique_synthetic(self):
		self.assertEqual([importer.validate_case(r) for r in self.rows if importer.validate_case(r)], [])
		self.assertEqual(len({r["case_id"] for r in self.rows}), len(self.rows))
		self.assertEqual({r["source"] for r in self.rows}, {"Synthetic"})

	def test_composition_matches_readme(self):
		count = Counter(r["case_id"].split("-")[0] for r in self.rows)
		minimum = {"FAQ": 100, "GIA": 25, "KN": 15, "NK": 25, "NGUOI": 10, "INJ": 10, "TT": 20, "YK": 5}
		for key, n in minimum.items():
			self.assertGreaterEqual(count[key], n, key)
		self.assertGreaterEqual(len(self.rows), 200)
		self.assertGreaterEqual(count["GIO"], 1)  # ngoài giờ
		self.assertGreaterEqual(count["SPAM"], 1)  # spam số điện thoại

	def test_critical_and_actions_by_category(self):
		for r in self.rows:
			cat = r["case_id"].split("-")[0]
			if cat in ("GIA", "INJ", "YK"):
				self.assertTrue(r["critical"], r["case_id"])
				self.assertEqual(r["expected_action"], "Handoff", r["case_id"])
			if cat in ("FAQ",):
				self.assertEqual(r["expected_action"], "Reply")
				self.assertTrue(r["reference_answer"])
			if cat in ("KN", "NK", "NGUOI", "SPAM"):
				self.assertEqual(r["expected_action"], "Handoff", r["case_id"])
		self.assertTrue(
			any(r["conversation"][-1].get("at") for r in self.rows if r["case_id"].startswith("GIO"))
		)

	def test_no_real_pii(self):
		text = SYNTHETIC.read_text(encoding="utf-8")
		for m in re.finditer(r"(?<!\d)(?:\+?84|0)[\d .\-]{8,}", text):
			digits = re.sub(r"\D", "", m.group(0))
			self.assertTrue(digits.startswith(("0900000", "84900000")), m.group(0))
		for email in re.findall(r"[\w.+\-]+@[\w.\-]+", text):
			self.assertTrue(email.rstrip(".").endswith("example.com"), email)

	def test_kb_file_has_no_placeholder_and_covers_faq_answers(self):
		rows = list(csv.DictReader(open(APP_DIR / "eval" / "kb-tong-hop.csv", encoding="utf-8")))
		self.assertFalse(knowledge.has_placeholder(*[v for r in rows for v in r.values()]))
		answers = {r["answer"] for r in rows}
		for r in self.rows:
			if r["case_id"].startswith("FAQ"):
				self.assertIn(r["reference_answer"], answers)

	def test_generator_is_deterministic(self):
		import subprocess
		import sys

		with tempfile.TemporaryDirectory() as tmp:
			shutil.copytree(
				APP_DIR / "eval", Path(tmp) / "eval", ignore=shutil.ignore_patterns("reports", "cases")
			)
			(Path(tmp) / "eval" / "cases").mkdir()
			subprocess.run(
				[sys.executable, str(Path(tmp) / "eval" / "sinh_ca_tong_hop.py"), "2026-10-30"],
				check=True,
				capture_output=True,
			)
			self.assertEqual(
				(Path(tmp) / "eval" / "cases" / "synthetic-2026-10-30.jsonl").read_text(encoding="utf-8"),
				SYNTHETIC.read_text(encoding="utf-8"),
			)


class TestImporter(FrappeTestCase):
	def setUp(self):
		self.tmp = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.tmp, True)
		self.addCleanup(self._clean)
		self._clean()
		frappe.get_doc(
			{"doctype": GROUP, "group_name": G_HOURS, "always_handoff": 0, "description": "t"}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def _clean(self):
		frappe.db.rollback()
		frappe.db.delete(CASE, {"name": ["like", "T08-%"]})
		frappe.db.delete(GROUP, {"name": ["like", "T08 %"]})
		frappe.db.commit()

	def write(self, lines: list) -> str:
		path = self.tmp / "c.jsonl"
		path.write_text(
			"\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in lines),
			encoding="utf-8",
		)
		return str(path)

	def test_reports_bad_lines_and_imports_good(self):
		bad_role = case_row("T08-003")
		bad_role["conversation"] = [{"role": "bot", "text": "x"}]
		ends_sale = case_row("T08-004")
		ends_sale["conversation"] = [{"role": "customer", "text": "a"}, {"role": "sale", "text": "b"}]
		lines = [
			case_row("T08-001"),
			"{không phải json",
			bad_role,
			ends_sale,
			case_row("T08-001"),  # trùng case_id trong tệp
			case_row("T08-005", expected_action="Maybe"),
			case_row("T08-006", expected_group="Nhóm không tồn tại"),
			{"case_id": "T08-007"},
			case_row("T08-008", extra_key=1),
			case_row("T08-009", critical="yes"),
			case_row("T08-010", reviewed=False),
			case_row("T08-011", text="x" * (importer.MAX_TEXT + 1)),
			case_row("T08-012"),
		]
		out = importer.import_jsonl(self.write(lines))
		self.assertEqual((out["created"], out["updated"]), (2, 0))
		self.assertEqual(sorted(e["line"] for e in out["errors"]), [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
		by_line = {e["line"]: e["error"] for e in out["errors"]}
		self.assertIn("JSON", by_line[2])
		self.assertIn("trùng", by_line[5])
		self.assertIn("chưa được người duyệt", by_line[11])
		self.assertTrue(frappe.db.exists(CASE, "T08-001"))
		self.assertTrue(frappe.db.exists(CASE, "T08-012"))
		self.assertFalse(frappe.db.exists(CASE, "T08-010"))

	def test_update_export_roundtrip_and_flags(self):
		importer.import_jsonl(self.write([case_row("T08-001", critical=True)]))
		again = importer.import_jsonl(self.write([case_row("T08-001", text="Giờ mở cửa?", active=False)]))
		self.assertEqual((again["created"], again["updated"]), (0, 1))
		doc = frappe.get_doc(CASE, "T08-001")
		self.assertEqual((doc.critical, doc.active), (0, 0))
		out = self.tmp / "out.jsonl"
		self.assertEqual(importer.export_jsonl(str(out)), frappe.db.count(CASE))
		exported = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
		mine = next(r for r in exported if r["case_id"] == "T08-001")
		self.assertEqual(mine["conversation"][0]["text"], "Giờ mở cửa?")
		self.assertFalse(mine["active"])
		self.assertEqual(
			importer.export_jsonl(str(out), only_active=True), frappe.db.count(CASE, {"active": 1})
		)

	def test_synthetic_must_not_contain_real_looking_pii(self):
		for text in (
			"Gọi em số 0903 123 456",
			"Số em +84 912 345 678",
			"mail a@gmail.com nhé",
			"xem https://facebook.com/abc",
			"vào www.cty.vn",
			"trang miyano.vn",
		):
			row = case_row("T08-001", text=text)
			self.assertTrue(importer.validate_case(row), text)
		for text in (
			"Số em 0900 000 123",
			"Hotline 1900 0000",
			"mail lienhe@example.com",
			"Cần 200 bộ trong 2026",
		):
			self.assertEqual(importer.validate_case(case_row("T08-001", text=text)), [], text)
		row = case_row("T08-002", reference_answer="Liên hệ 0903123456")
		self.assertTrue(importer.validate_case(row))
		self.assertEqual(
			importer.validate_case(case_row("T08-003", text="gọi 0903 123 456", source="Real")), []
		)
		path = self.write([case_row("T08-004", text="gọi 0903 123 456")])
		out = importer.import_jsonl(path)
		self.assertEqual(out["created"], 0)
		self.assertIn("số điện thoại", out["errors"][0]["error"])

	def test_unreviewed_allowed_only_explicitly(self):
		path = self.write([case_row("T08-001", reviewed=False)])
		self.assertEqual(importer.import_jsonl(path)["created"], 0)
		self.assertEqual(importer.import_jsonl(path, allow_unreviewed=True)["created"], 1)

	def test_import_synthetic_file(self):
		knowledge.import_groups()
		ids0 = [
			json.loads(x)["case_id"] for x in SYNTHETIC.read_text(encoding="utf-8").splitlines() if x.strip()
		]
		existing = set(frappe.get_all(CASE, filters={"name": ["in", ids0]}, pluck="name"))
		out = importer.import_jsonl(str(SYNTHETIC.relative_to(APP_DIR)))
		self.assertEqual(out["errors"], [])
		ids = [
			json.loads(x)["case_id"] for x in SYNTHETIC.read_text(encoding="utf-8").splitlines() if x.strip()
		]
		self.assertGreaterEqual(len(frappe.get_all(CASE, filters={"name": ["in", ids]})), 220)
		frappe.db.delete(
			CASE, {"name": ["in", [i for i in ids if i not in existing]]}
		)  # chỉ xóa ca do test tạo
		frappe.db.commit()


class TestMasker(FrappeTestCase):
	def test_masks_contacts_names_orgs_consistently(self):
		m = builder.Masker(
			["Trần Thị Hạnh", "Hạnh"], ["Bệnh viện Hoa Sen"], ["0903123456"], ["hanh.tran@benhvien.vn"]
		)
		text = (
			"Chào, em là Hạnh ở Bệnh viện Hoa Sen. SĐT 0903 123 456 hoặc +84 903-123-456, mail hanh.tran@benhvien.vn. "
			"Chị Trần Thị Hạnh gọi lại nhé. CCCD 012345678901"
		)
		out = m.mask(text)
		self.assertEqual(m.leaks(out), [])
		for secret in ("0903", "903-123", "benhvien.vn", "Hoa Sen", "Hạnh", "012345678901"):
			self.assertNotIn(secret, out)
		self.assertEqual(out.count("0900 000 001"), 2)  # cùng số -> cùng số giả
		self.assertIn("khach1@example.com", out)
		self.assertIn("Đơn vị A", out)
		self.assertIn("Khách A", out)

	def test_accent_and_case_insensitive_names(self):
		m = builder.Masker(["Nguyễn Văn Bình"], [], [], [])
		out = m.mask("anh NGUYEN VAN BINH và anh nguyễn văn bình, ông Bình")
		self.assertNotIn("inh", out.replace("Khách", ""))
		self.assertEqual(m.leaks(out), [])

	def test_leaks_detects_source_data(self):
		m = builder.Masker(["Lan"], ["Công ty Sao"], ["0912345678"], ["a@b.vn"])
		self.assertEqual(m.leaks("gọi 0912 345 678"), ["sdt"])
		self.assertEqual(m.leaks("mail a@b.vn"), ["email"])
		self.assertEqual(m.leaks("chị lan nhé"), ["ten"])
		self.assertEqual(m.leaks("không có gì"), [])

	def test_intro_pattern_masks_unknown_name(self):
		m = builder.Masker([], [], [], [])
		out = m.mask("Em là Phạm Quang Huy, cần mua găng tay")
		self.assertNotIn("Huy", out)
		self.assertIn("Khách A", out)


class TestBuilder(InboxTestCase):
	REAL_PHONE = "0903 777 888"
	REAL_EMAIL = "mai.that@congtythat.vn"

	def setUp(self):
		super().setUp()
		self.tmp = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.tmp, True)
		frappe.db.set_value(
			"CRM Lead", self.lead1, {"organization": "Công ty Thật Sự", "mobile_no": "0903777888"}
		)
		frappe.db.commit()

	def history(self):
		self.make_inbound(
			self.lead1,
			self.ident1,
			"Zalo OA",
			f"Em là Mai Zalo bên Công ty Thật Sự, số {self.REAL_PHONE}, mail {self.REAL_EMAIL}. Cho hỏi giờ làm việc",
			90,
		)
		self.make_outbound(
			self.lead1, f"Dạ chị Mai, Miyano làm việc từ 8 giờ ạ. Em là Hà, gọi {self.REAL_PHONE}", 80
		)
		frappe.db.commit()
		return add_to_date(now_datetime(), minutes=-100), add_to_date(now_datetime(), minutes=-60)

	def test_refuses_without_q11(self):
		since, until = self.history()
		with self.assertRaises(frappe.ValidationError):
			builder.build_from_real(since, until, out_dir=self.tmp)
		self.assertEqual(list(Path(self.tmp).iterdir()), [])

	def test_output_has_no_source_pii_and_needs_review(self):
		since, until = self.history()
		with mock.patch.dict(frappe.conf, {ai_client.ALLOW_DATA_CONF: 1}):
			out = builder.build_from_real(since, until, out_dir=self.tmp)
		self.assertGreaterEqual(out["cases"], 1)
		raw = Path(out["path"]).read_text(encoding="utf-8")
		for secret in ("0903", "777 888", "777888", "mai.that", "congtythat", "Thật Sự", "Mai Zalo", "TD106"):
			self.assertNotIn(secret, raw, secret)
		self.assertEqual(Counter(re.findall(r"\d{4} \d{3} \d{3}", raw)).keys() <= {"0900 000 001"}, True)
		rows = [json.loads(x) for x in raw.splitlines()]
		self.assertTrue(all(r["source"] == "Real" and r["reviewed"] is False for r in rows))
		self.assertEqual(rows[0]["conversation"][-1]["role"], "customer")
		self.assertIn("Miyano làm việc từ 8 giờ", rows[0]["reference_answer"])
		self.assertEqual(rows[0]["expected_fields"], {})
		self.assertTrue((Path(self.tmp) / f"{Path(out['path']).stem}.quy-tac-che.md").exists())
		# người chưa duyệt thì importer từ chối
		self.assertEqual(importer.import_jsonl(out["path"])["created"], 0)

	def test_case_with_leftover_is_dropped(self):
		since, until = self.history()

		class Leaky(builder.Masker):
			def mask(self, text):  # không che gì: mô phỏng lỗi che
				return text

		with (
			mock.patch.dict(frappe.conf, {ai_client.ALLOW_DATA_CONF: 1}),
			mock.patch.object(builder, "Masker", Leaky),
		):
			out = builder.build_from_real(since, until, out_dir=self.tmp)
		self.assertEqual(out["cases"], 0)
		self.assertEqual(out["dropped"], {"con_du_lieu_nguon": 1})

	def test_org_prefix_case_insensitive_and_obfuscated_email_dropped(self):
		m = builder.Masker([], [], [], [])
		self.assertNotIn("Hoa Sen", m.mask("bệnh viện Hoa Sen cần hàng"))
		self.assertNotIn("Hoa Sen", m.mask("BỆNH VIỆN Hoa Sen cần hàng"))
		self.assertTrue(builder.OBFUSCATED.search("mail a (at) b (dot) com"))
		self.assertTrue(builder.OBFUSCATED.search("a[at]b.com"))
		self.assertIsNone(builder.OBFUSCATED.search("bình thường"))

	def test_price_in_sale_text_suggests_handoff(self):
		self.assertEqual(builder.suggest_action("Giá 500.000đ một hộp ạ", ["Miyano làm việc"]), "Handoff")
		self.assertEqual(
			builder.suggest_action("Miyano làm việc từ 8 giờ ạ", ["Miyano làm việc từ 8 giờ"]), "Reply"
		)
		self.assertEqual(builder.suggest_action("Dạ để em kiểm tra giá rồi báo", ["x"]), "Handoff")


class RunTestCase(FrappeTestCase):
	def setUp(self):
		self.tmp = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.tmp, True)
		self._orig = {f: frappe.db.get_single_value("Omni Settings", f) for f in SETTINGS_FIELDS}
		self.addCleanup(self._restore)
		self.addCleanup(self._clean)
		self._clean()
		set_settings(bot_analyze_enabled=0, bot_reply_mode="Off", daily_token_budget=10_000_000)
		frappe.cache().delete_keys("omni:ai:")
		for g, h in ((G_HOURS, 0), (G_PRICE, 1)):
			frappe.get_doc(
				{"doctype": GROUP, "group_name": g, "always_handoff": h, "description": f"mô tả {g}"}
			).insert(ignore_permissions=True)
		frappe.db.commit()
		self.kb = write_csv(
			self.tmp / "kb.csv",
			["item_id", "question_group", "question", "answer", "source", "owner_email"],
			[["KB-91001", G_HOURS, "Mấy giờ làm việc?", KB_ANSWER, "", ""]],
		)
		self.groups = write_csv(
			self.tmp / "g.csv",
			["group_name", "always_handoff", "description"],
			[[G_HOURS, "0", "giờ"], [G_PRICE, "1", "giá"]],
		)
		self.fake = OracleClient()
		ai_client.set_client(self.fake)
		self.addCleanup(ai_client.set_client, None)
		for target in (
			mock.patch.object(ai_client, "_sleep"),
			mock.patch.object(run, "REPORT_DIR", str(self.tmp)),
		):
			target.start()
			self.addCleanup(target.stop)
		self.runs: list[str] = []

	def _restore(self):
		set_settings(**{k: v for k, v in self._orig.items() if v is not None})

	def _clean(self):
		frappe.db.rollback()
		frappe.db.delete(CASE, {"name": ["like", "T08-%"]})
		frappe.db.delete(RUN, {"name": ["in", getattr(self, "runs", [])]})
		frappe.db.delete("Omni Bot Run", {"batch_id": ["like", "eval::%::T08-%"]})
		frappe.db.delete(GROUP, {"name": ["like", "T08 %"]})
		frappe.db.commit()

	def add_cases(self, rows: list[dict]) -> None:
		path = self.tmp / "cases.jsonl"
		path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
		out = importer.import_jsonl(str(path))
		assert not out["errors"], out

	def go(self, gate="nhap", **kw):
		name = run.run({"prefix": "T08-"}, gate=gate, kb_file=self.kb, groups_file=self.groups, **kw)
		self.runs.append(name)
		return name, json.loads(frappe.db.get_value(RUN, name, "result_json"))

	def good_set(self, n=20):
		rows = [case_row(f"T08-{i:03d}", f"Bên mình làm việc mấy giờ? ({i})") for i in range(1, n)]
		rows.append(
			case_row(
				f"T08-{n:03d}",
				"Khách cần báo giá găng tay",
				"Handoff",
				expected_group=G_PRICE,
				critical=True,
				expected_fields={"intent": "Hỏi giá"},
			)
		)
		return rows


class TestRun(RunTestCase):
	def test_full_pass_with_oracle_writes_run_and_report(self):
		self.add_cases(self.good_set())
		before = (frappe.db.count("CRM Lead"), frappe.db.count("Omni Bot Draft"))
		name, data = self.go()
		doc = frappe.get_doc(RUN, name)
		self.assertEqual(
			(doc.total_cases, doc.critical_failures, doc.passed_gate), (20, 0, 0)
		)  # Fake: không bao giờ qua cổng
		self.assertEqual(data["meta"]["mode"], "Fake")
		self.assertIs(data["gate"]["applicable"], False)
		self.assertFalse(data["gate"]["passed"])
		self.assertFalse(data["meta"]["reviewed"])
		self.assertEqual(data["metrics"]["field_accuracy"], 1.0)
		self.assertEqual(
			before, (frappe.db.count("CRM Lead"), frappe.db.count("Omni Bot Draft"))
		)  # không ghi CRM
		reports = list(self.tmp.glob("*.md"))
		self.assertEqual(len(reports), 1)
		self.assertTrue(reports[0].name.endswith("-fake.md"), reports[0].name)
		text = reports[0].read_text(encoding="utf-8")
		self.assertIn("KHÔNG ÁP DỤNG (giả lập)", text)
		self.assertNotIn("**QUA**", text)
		self.assertNotIn("100.0%", text)
		self.assertIn("KHÔNG PHẢI điểm của bot", text)
		self.assertIn("CHƯA được người duyệt", text)

	def test_no_switch_needed_but_production_kind_still_gated(self):
		self.add_cases(self.good_set(3))
		self.go()
		self.assertTrue(any(c["kind"] == "Analyze" for c in self.fake.calls))
		self.assertIsNone(  # ngoài bộ đề, công tắc vẫn chặn
			ai_client.call_tool("Analyze", "m", "s", [{"role": "user", "content": "x"}], run.analyze.TOOL)
		)

	def test_critical_failure_blocks_gate_though_metrics_high(self):
		rows = self.good_set()
		rows.append(
			case_row("T08-050", "Mời anh chị ghé văn phòng", "Handoff", critical=True, expected_fields={})
		)
		self.add_cases(rows)
		fake = FakeAIClient()
		fake.add_rule(
			{
				"action": "reply",
				"question_group": G_HOURS,
				"reply_text": KB_ANSWER,
				"kb_item_ids": ["KB-91001"],
				"confidence": 0.9,
			},
			kind="Reply",
			keyword="văn phòng",
		)
		ai_client.set_client(OracleClient())
		oracle = ai_client.get_client()
		real_create = oracle.create

		def create(**kw):  # ca "văn phòng": bot trả lời nhầm; còn lại như Oracle
			if kw["kind"] == "Reply" and "văn phòng" in str(kw["messages"]):
				return fake.create(**kw)
			return real_create(**kw)

		with mock.patch.object(oracle, "create", side_effect=lambda **kw: create(**kw)):
			name, data = self.go()
		m = data["metrics"]
		self.assertGreaterEqual(m["action_accuracy"], 0.9)
		self.assertGreaterEqual(m["answer_pass_rate"], 0.85)
		self.assertEqual(m["critical_failures"], 1)
		self.assertFalse(data["gate"]["passed"])
		self.assertEqual(frappe.db.get_value(RUN, name, "passed_gate"), 0)
		bad = next(r for r in data["cases"] if r["case_id"] == "T08-050")
		self.assertEqual(bad["critical_reasons"], ["critical_reply"])

	def test_judge_pass_but_r3_false_makes_critical(self):
		self.add_cases([case_row("T08-001")])
		judge = FakeAIClient()
		judge.add_rule(
			{
				**dict.fromkeys(grading.CRITERIA, True),
				"r3_within_authority": False,
				"pass": True,
				"critical_failure": False,
				"reason": "hứa hẹn",
			},
			kind="Judge",
		)
		oracle = ai_client.get_client()
		real_create = oracle.create
		with mock.patch.object(
			oracle,
			"create",
			side_effect=lambda **kw: judge.create(**kw) if kw["kind"] == "Judge" else real_create(**kw),
		):
			_, data = self.go()
		row = data["cases"][0]
		self.assertFalse(row["answer_pass"])
		self.assertEqual(row["critical_reasons"], ["judge_r3"])
		self.assertEqual(data["metrics"]["critical_failures"], 1)
		self.assertFalse(data["gate"]["passed"])
		self.assertEqual(data["metrics"]["answer_pass_rate"], 0.0)

	def test_wrong_fields_counted_and_listed(self):
		self.add_cases(
			[
				case_row("T08-001", expected_fields={"intent": "Hỏi giá", "wants_human": False}),
				case_row("T08-002"),
			]
		)
		wrong = FakeAIClient()
		wrong.add_rule(
			{
				"intent": "Tư vấn",
				"wants_human": False,
				"summary": "x",
				"phones": [],
				"emails": [],
				"products": [],
				"evidence": {},
			},
			kind="Analyze",
		)
		oracle = ai_client.get_client()
		real_create = oracle.create
		with mock.patch.object(
			oracle,
			"create",
			side_effect=lambda **kw: wrong.create(**kw) if kw["kind"] == "Analyze" else real_create(**kw),
		):
			_, data = self.go("phan_tich")
		first = data["cases"][0]
		self.assertEqual(first["field_wrong"], {"intent": ["Hỏi giá", "Tư vấn"]})
		self.assertEqual((first["field_checked"], first["field_correct"]), (2, 1))
		self.assertAlmostEqual(data["metrics"]["field_accuracy"], 2 / 3)  # 1 sai trên 3 trường được chấm
		self.assertFalse(data["gate"]["passed"])

	def test_case_without_expected_fields_still_analyzed_for_reply(self):
		"""Chốt chặn dùng intent/wants_human của phân tích như production, nên ca `{}` (vd chèn lệnh) vẫn phân tích."""
		self.add_cases([case_row("T08-001", expected_fields={})])
		_, data = self.go("nhap")
		self.assertEqual([c["kind"] for c in self.fake.calls if c["kind"] == "Analyze"], ["Analyze"])
		self.assertEqual(data["cases"][0]["field_checked"], 0)
		self.fake.calls.clear()
		self.go("phan_tich")  # chỉ phân tích: ca không có trường để chấm thì khỏi gọi
		self.assertEqual(self.fake.calls, [])

	def test_real_case_keeps_reply_text_for_human_regrade(self):
		self.add_cases([case_row("T08-001", source="Real")])
		with mock.patch.dict(frappe.conf, {ai_client.ALLOW_DATA_CONF: 1}):
			name, data = self.go()
		row = data["cases"][0]
		self.assertEqual(row["reply_text"], KB_ANSWER)
		self.assertTrue(row["question"])
		path = run.export_regrade_csv(name, str(self.tmp / "cl.csv"))
		self.assertIn(KB_ANSWER, open(path, encoding="utf-8-sig").read())
		self.assertNotIn(
			"Mấy giờ", next(p for p in self.tmp.glob("*.md")).read_text(encoding="utf-8")
		)  # báo cáo không in lời ca Real

	def test_phan_tich_gate_runs_only_analysis(self):
		self.add_cases(self.good_set(5))
		_, data = self.go("phan_tich", judge=True)
		self.assertEqual({c["kind"] for c in self.fake.calls}, {"Analyze"})
		self.assertIsNone(data["metrics"]["action_accuracy"])
		self.assertEqual(data["metrics"]["field_accuracy"], 1.0)
		self.assertFalse(data["gate"]["passed"])  # Fake không áp dụng cổng

	def test_judge_off_means_answer_not_measured(self):
		self.add_cases(self.good_set(5))
		_, data = self.go(judge=False)
		self.assertIsNone(data["metrics"]["answer_pass_rate"])
		self.assertFalse(data["gate"]["passed"])
		self.assertIn("chưa đo", " ".join(data["gate"]["reasons"]))

	def test_no_client_everything_unmeasured_not_scored(self):
		self.add_cases(self.good_set(5))
		ai_client.set_client(None)
		with mock.patch.object(ai_client, "get_client", return_value=None):
			name, data = self.go()
		self.assertEqual(data["meta"]["mode"], "Không có client AI")
		self.assertEqual(data["metrics"]["unmeasured_cases"], 5)
		self.assertIsNone(data["metrics"]["field_accuracy"])
		self.assertFalse(data["gate"]["passed"])
		self.assertIn("chưa đo", " ".join(data["gate"]["reasons"]))
		self.assertIn(
			"CHƯA ĐO", (self.tmp / next(p.name for p in self.tmp.glob("*.md"))).read_text(encoding="utf-8")
		)

	def test_real_client_needs_explicit_allow(self):
		self.add_cases([case_row("T08-001")])
		self.fake.is_real = True
		with self.assertRaises(frappe.ValidationError):
			self.go()
		_, data = self.go(allow_real=True)  # Synthetic: truyền synthetic=True nên qua cổng Q11
		self.assertEqual(data["metrics"]["unmeasured_cases"], 0)

	def test_q11_blocks_real_cases_not_synthetic(self):
		self.add_cases([case_row("T08-001", source="Real"), case_row("T08-002")])
		self.fake.is_real = True
		with mock.patch.object(ai_client, "get_client", return_value=self.fake):
			_, data = self.go(allow_real=True)
		by_id = {r["case_id"]: r for r in data["cases"]}
		self.assertIn("Skipped Switch", by_id["T08-001"]["unmeasured"])
		self.assertIsNone(by_id["T08-002"]["unmeasured"])
		self.assertEqual(data["metrics"]["unmeasured_cases"], 1)
		self.assertFalse(data["gate"]["passed"])

	def test_budget_stops_remaining_cases(self):
		self.add_cases(self.good_set(4))
		set_settings(daily_token_budget=1)
		_, data = self.go()
		self.assertEqual(data["metrics"]["unmeasured_cases"], 4)
		self.assertTrue(
			all("không chạy" in r["unmeasured"] or "Skipped Budget" in r["unmeasured"] for r in data["cases"])
		)
		self.assertEqual(len([c for c in self.fake.calls]), 0)

	def test_real_cases_scrubbed_from_bot_run(self):
		self.add_cases([case_row("T08-001", source="Real")])
		self.fake.is_real = False
		with mock.patch.dict(frappe.conf, {ai_client.ALLOW_DATA_CONF: 1}):
			self.go()
		rows = frappe.get_all(
			"Omni Bot Run", filters={"batch_id": ["like", "eval::%T08-001"]}, fields=["output_json"]
		)
		self.assertTrue(rows)
		self.assertTrue(all(not r.output_json for r in rows))

	def test_out_of_hours_uses_case_time(self):
		row = case_row("T08-001")
		row["conversation"][-1]["at"] = "2026-11-01 22:15"  # Chủ nhật
		self.add_cases([row, case_row("T08-002")])
		self.go()
		bodies = [c["system"] for c in self.fake.calls if c["kind"] == "Reply"]
		self.assertTrue(any("ngoài giờ" in b for b in bodies))
		self.assertTrue(any("trong giờ" in b for b in bodies))

	def test_doi_cau_lenh_needs_accepted_baseline(self):
		self.add_cases(self.good_set(5))
		_, data = self.go("doi_cau_lenh")
		self.assertFalse(data["gate"]["passed"])
		self.assertIn("mốc", " ".join(data["gate"]["reasons"]))
		name, _d = self.go()
		with self.assertRaises(frappe.ValidationError):  # Fake/bộ đề chưa duyệt không được làm mốc
			run.accept_run(name)

	def mark_real(self, name, data, reviewed_all=True):
		"""Giả lập lần chạy AI thật đã duyệt, đã qua cổng (và chấm lại đủ mẫu nếu reviewed_all)."""
		meta = data["meta"]
		meta["mode"] = "AI thật"
		meta["reviewed"] = True
		if reviewed_all:
			for cid in run.review_status(data["cases"], meta, name)["required"]:
				meta["human_reviews"][cid] = {"pass": True, "critical": False, "note": ""}
		frappe.db.set_value(RUN, name, {"result_json": json.dumps(data), "passed_gate": 1})
		return data

	def test_accept_run_requires_full_regrade_sample(self):
		self.add_cases(self.good_set(10))
		name, data = self.go()
		self.assertGreater(len(run.review_status(data["cases"], data["meta"], name)["required"]), 0)
		self.mark_real(name, data, reviewed_all=False)
		with self.assertRaises(frappe.ValidationError) as cm:
			run.accept_run(name)
		self.assertIn("chấm lại tay", str(cm.exception))
		self.mark_real(name, data)
		run.accept_run(name)  # đủ mẫu thì được chấp nhận
		frappe.db.set_value(
			RUN, name, "result_json", json.dumps({**data, "meta": {**data["meta"], "accepted": False}})
		)

	def test_gate_blocked_until_human_regrade_covers_sample(self):
		self.add_cases(self.good_set(10))
		name, data = self.go()
		check = data["gate"]
		self.assertTrue(check["review"]["missing"])
		self.assertIn("chưa chấm lại tay", " ".join(check["reasons"]))
		self.mark_real(name, data, reviewed_all=False)
		path = self.tmp / "cl.csv"
		run.export_regrade_csv(name, str(path))
		rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
		rows[0].update(ket_luan_nguoi="Dat")
		with open(path, "w", encoding="utf-8-sig", newline="") as f:
			w = csv.DictWriter(f, fieldnames=list(rows[0]))
			w.writeheader()
			w.writerows(rows)
		out = run.import_regrade(name, str(path))  # mới chấm một dòng: cổng ghi chưa đủ (không báo lỗi)
		self.assertTrue(out["gate"]["review"]["missing"] or len(rows) == 1)
		if len(rows) > 1:
			self.assertFalse(out["gate"]["passed"])
			self.assertIn("chưa chấm lại tay", " ".join(out["gate"]["reasons"]))
		for r in rows:
			r.update(ket_luan_nguoi="Dat")
		with open(path, "w", encoding="utf-8-sig", newline="") as f:
			w = csv.DictWriter(f, fieldnames=list(rows[0]))
			w.writeheader()
			w.writerows(rows)
		out = run.import_regrade(name, str(path))
		self.assertEqual(out["gate"]["review"]["missing"], [])
		self.assertEqual(out["gate"]["reasons"], [])  # mode AI thật + đủ mẫu + chỉ số đạt
		self.assertTrue(out["gate"]["passed"])

	def test_regression_baseline_after_accept(self):
		self.add_cases(self.good_set(5))
		name, data = self.go()
		self.mark_real(name, data)
		run.accept_run(name)
		self.assertEqual(run._baseline()["field_accuracy"], 1.0)
		_, again = self.go("doi_cau_lenh")
		joined = " ".join(again["gate"]["reasons"])
		self.assertNotIn("mốc", joined)
		self.assertNotIn("giảm", joined)
		frappe.db.set_value(
			RUN, name, "result_json", json.dumps({**data, "meta": {**data["meta"], "accepted": False}})
		)

	def test_no_client_report_says_not_measured_not_zero(self):
		self.add_cases(self.good_set(5))
		ai_client.set_client(None)
		with mock.patch.object(ai_client, "get_client", return_value=None):
			self.go()
		text = next(p for p in self.tmp.glob("*.md")).read_text(encoding="utf-8")
		self.assertIn("| Lỗi nghiêm trọng | chưa đo |", text)
		overview = text.split("## Tổng quan theo loại")[1]
		self.assertIn("chưa đo", overview)
		self.assertNotRegex(overview, r"\| 0 \|\s*$")
		self.assertNotIn("| 0 |", overview.replace("| 5 | 5 |", ""))

	def test_report_name_real_cases_suffix(self):
		self.add_cases([case_row("T08-001", source="Real")])
		with mock.patch.dict(frappe.conf, {ai_client.ALLOW_DATA_CONF: 1}):
			self.go()
		names = [p.name for p in self.tmp.glob("*.md")]
		self.assertEqual(len(names), 1)
		self.assertTrue(names[0].endswith("-fake-real.md") or names[0].endswith("-real.md"), names[0])

	def test_prefix_does_not_override_case_ids(self):
		self.add_cases([case_row("T08-001"), case_row("T08-002")])
		got = run.load_cases({"case_ids": ["T08-001", "T08-002"], "prefix": "T08-002"})
		self.assertEqual([c["case_id"] for c in got], ["T08-002"])
		self.assertEqual(run.load_cases({"case_ids": ["T08-001"], "prefix": "XX-"}), [])

	def test_case_filter(self):
		self.add_cases([case_row("T08-001"), case_row("T08-002", expected_action="Handoff", critical=True)])
		name = run.run({"critical": 1, "prefix": "T08-"}, kb_file=self.kb, groups_file=self.groups)
		self.runs.append(name)
		self.assertEqual(frappe.db.get_value(RUN, name, "total_cases"), 1)
		with self.assertRaises(frappe.ValidationError):
			run.run({"case_ids": ["T08-NOPE"]}, kb_file=self.kb, groups_file=self.groups)

	def test_bad_gate_rejected(self):
		self.add_cases([case_row("T08-001")])
		with self.assertRaises(frappe.ValidationError):
			run.run({"prefix": "T08-"}, gate="khong_co")

	def test_cli_fake(self):
		self.add_cases([case_row("T08-001")])
		ai_client.set_client(None)
		name = run.run_cli(
			gate="nhap", fake=True, cases={"prefix": "T08-"}, kb_file=self.kb, groups_file=self.groups
		)
		self.runs.append(name)
		self.assertEqual(frappe.db.get_value(RUN, name, "total_cases"), 1)
		self.assertIsNone(ai_client.get_client())  # trả lại client cũ

	def test_synthetic_flag_only_by_case_source(self):
		src = (Path(run.__file__)).read_text(encoding="utf-8")
		self.assertIsNone(re.search(r"synthetic\s*=\s*True", src))  # không bao giờ cố định True
		self.assertEqual(src.count('synthetic=case["source"] == "Synthetic"'), 3)

	def test_eval_modules_never_send_to_customers(self):
		for path in Path(run.__file__).parent.glob("*.py"):
			text = path.read_text(encoding="utf-8")
			for token in (
				"queue_outbound",
				"OutboundRequest",
				"send_outbound",
				"make_draft",
				"_insert_draft",
				"apply_to_lead",
				"enqueue",
				'frappe.get_doc({"doctype": LEAD',
			):
				self.assertNotIn(token, text, f"{path.name}: {token}")


class TestRegrade(RunTestCase):
	def setUp(self):
		super().setUp()
		self.add_cases(self.good_set(31))  # 30 ca Reply + 1 Handoff
		self.name, self.data = self.go()

	def test_sample_is_20pct_plus_judge_failures_and_stable(self):
		path = run.export_regrade_csv(self.name, str(self.tmp / "cl.csv"))
		rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
		self.assertEqual(len(rows), 6)  # 20% của 30 ca đã chấm
		self.assertEqual({r["ly_do_vao_mau"] for r in rows}, {"ngẫu nhiên"})
		self.assertTrue(set(rows[0]) >= {"ket_luan_nguoi", "truot_r3_hoac_r5", "ghi_chu"})
		again = run.export_regrade_csv(self.name, str(self.tmp / "cl2.csv"))
		self.assertEqual(open(path, encoding="utf-8-sig").read(), open(again, encoding="utf-8-sig").read())

	def test_csv_cells_cannot_inject_formulas(self):
		cases = self.data["cases"]
		for r in cases[:2]:
			r["question"] = '=HYPERLINK("http://x")'
			r["reply_text"] = "+cmd|' /C calc'!A0"
			r["judge"] = {**r["judge"], "pass": False, "reason": "@SUM(1)"}
		frappe.db.set_value(RUN, self.name, "result_json", json.dumps({**self.data, "cases": cases}))
		path = run.export_regrade_csv(self.name, str(self.tmp / "cl.csv"))
		for row in csv.DictReader(open(path, encoding="utf-8-sig")):
			for value in row.values():
				self.assertNotIn(value[:1], ("=", "+", "@"), value)
		self.assertIn("'=HYPERLINK", open(path, encoding="utf-8-sig").read())

	def test_judge_failures_always_included(self):
		cases = self.data["cases"]
		for r in cases[:3]:
			r["judge"] = {**r["judge"], "pass": False, "reason": "trượt"}
		frappe.db.set_value(RUN, self.name, "result_json", json.dumps({**self.data, "cases": cases}))
		path = run.export_regrade_csv(self.name, str(self.tmp / "cl.csv"))
		rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
		self.assertEqual(sum(r["ly_do_vao_mau"] == "giám khảo cho trượt" for r in rows), 3)

	def test_human_review_recomputes_final_metrics(self):
		path = self.tmp / "cl.csv"
		run.export_regrade_csv(self.name, str(path))
		rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
		rows[0].update(ket_luan_nguoi="Truot")
		rows[1].update(ket_luan_nguoi="Truot", truot_r3_hoac_r5="co")
		rows[2].update(ket_luan_nguoi="Dat")
		with open(path, "w", encoding="utf-8-sig", newline="") as f:
			w = csv.DictWriter(f, fieldnames=list(rows[0]))
			w.writeheader()
			w.writerows(rows)
		out = run.import_regrade(self.name, str(path))
		self.assertEqual(out["reviewed_cases"], 3)
		self.assertAlmostEqual(out["metrics"]["answer_pass_rate"], 28 / 30)
		self.assertEqual(out["metrics"]["critical_failures"], 1)
		self.assertFalse(out["gate"]["passed"])
		doc = frappe.get_doc(RUN, self.name)
		self.assertEqual((doc.critical_failures, doc.passed_gate), (1, 0))
		self.assertIn("nguoi_cham_r3_r5", frappe.db.get_value(RUN, self.name, "result_json"))

	def test_second_import_can_retract_earlier_flag(self):
		path = self.tmp / "cl.csv"
		run.export_regrade_csv(self.name, str(path))
		rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
		rows[0].update(ket_luan_nguoi="Truot", truot_r3_hoac_r5="co")

		def save(rs):
			with open(path, "w", encoding="utf-8-sig", newline="") as f:
				w = csv.DictWriter(f, fieldnames=list(rs[0]))
				w.writeheader()
				w.writerows(rs)

		save(rows)
		self.assertEqual(run.import_regrade(self.name, str(path))["metrics"]["critical_failures"], 1)
		rows[0].update(ket_luan_nguoi="Dat", truot_r3_hoac_r5="")  # người chấm đổi ý
		save(rows)
		out = run.import_regrade(self.name, str(path))
		self.assertEqual(out["metrics"]["critical_failures"], 0)
		self.assertEqual(out["metrics"]["answer_pass_rate"], 1.0)

	def test_import_rejects_unknown_case(self):
		path = self.tmp / "cl.csv"
		write_csv(path, list(run.REGRADE_COLUMNS), [["KHONG-CO", "", "", "", "", "", "", "Dat", "", ""]])
		with self.assertRaises(frappe.ValidationError):
			run.import_regrade(self.name, str(path))
