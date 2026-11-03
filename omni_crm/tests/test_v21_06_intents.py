# ruff: noqa: RUF001
"""V21-06 (F6): ý định theo bối cảnh nhà thầu (bảng ý định, schema v3, tín hiệu điểm, chốt chặn bằng code, patch ánh xạ,
bộ chấm). Mọi lời gọi AI đi qua FakeAIClient; câu lệnh đang dùng vẫn ghim v1."""

import json
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import ai_client, analyze, auto_send, guards, intents, scoring
from omni_crm.bot.eval import grading
from omni_crm.bot.eval.fake import OracleClient
from omni_crm.bot.prompts import loader
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_reply import ReplyTestCase, reply_ok
from omni_crm.tests.test_scoring import RULES, ScoringTestCase
from omni_crm.tests.test_v2_06_bot_b2b import b2b, b2b_v3

LEAD = "CRM Lead"
PREFIX = "TD2106"
APP_ROOT = Path(__file__).resolve().parents[2]
NEW = ("Hỏi cách vận hành", "Hỏi gói dịch vụ", "Hỏi phí", "Yêu cầu đề xuất", "Khiếu nại", "Khác")
ALWAYS = ("Hỏi phí", "Yêu cầu đề xuất", "Khiếu nại")
# ý định mới -> (tín hiệu B2B đúng, luôn chuyển người) theo bảng F6
TABLE = {
	"Hỏi cách vận hành": ("intent_service_info", False),
	"Hỏi gói dịch vụ": ("intent_service_info", False),
	"Hỏi phí": ("intent_fee", True),
	"Yêu cầu đề xuất": ("intent_proposal", True),
	"Khiếu nại": ("complaint", True),
	"Khác": (None, False),
}
B2B_SIGNALS = ("intent_service_info", "intent_fee", "intent_proposal", "complaint")


class TestIntentTable(FrappeTestCase):
	def test_new_list_is_exactly_the_f6_table(self):
		self.assertEqual(intents.INTENTS_B2B, NEW)
		self.assertEqual(set(TABLE), set(NEW))
		for intent, (signal, handoff) in TABLE.items():
			self.assertEqual(intents.signal_of(intent), signal, intent)
			self.assertEqual(intents.always_handoff(intent), handoff, intent)
		self.assertEqual(intents.ALWAYS_HANDOFF, frozenset(ALWAYS))

	def test_legacy_values_are_understood(self):
		self.assertEqual(
			intents.LEGACY_TO_B2B,
			{"Hỏi giá": "Hỏi phí", "Đặt hàng": "Yêu cầu đề xuất", "Tư vấn": "Hỏi cách vận hành"},
		)
		self.assertEqual(intents.normalize("Hỏi giá"), "Hỏi phí")
		self.assertEqual(intents.normalize("Khác"), "Khác")
		self.assertIsNone(intents.normalize(None))
		self.assertIsNone(intents.normalize(""))
		self.assertEqual(intents.signal_of("Tư vấn"), "intent_service_info")
		for old in ("Hỏi giá", "Đặt hàng", "Khiếu nại"):
			self.assertTrue(intents.always_handoff(old), old)
		for ok in ("Tư vấn", "Khác", None, "giá trị lạ"):
			self.assertFalse(intents.always_handoff(ok), ok)

	def test_select_options_keep_old_and_add_new(self):
		options = [o for o in frappe.get_meta(LEAD).get_field("omni_intent").options.split("\n") if o]
		self.assertEqual(
			options[:5], ["Tư vấn", "Hỏi giá", "Đặt hàng", "Khiếu nại", "Khác"]
		)  # giá trị cũ giữ nguyên
		self.assertEqual(set(options), set(intents.ALL_INTENTS))
		for new in NEW:
			self.assertIn(new, options)

	def test_grading_maps_old_values_to_new(self):
		self.assertTrue(grading.field_match("intent", "Hỏi phí", "Hỏi giá"))  # ca B2B, bot v1
		self.assertTrue(grading.field_match("intent", "Hỏi giá", "Hỏi phí"))  # ca v1, bot v2
		self.assertTrue(grading.field_match("intent", "Yêu cầu đề xuất", "Đặt hàng"))
		self.assertTrue(
			grading.field_match("intent", "Tư vấn", "Hỏi gói dịch vụ")
		)  # Tư vấn khớp cả hai ý định tư vấn mới
		self.assertTrue(grading.field_match("intent", "Hỏi cách vận hành", "Tư vấn"))
		self.assertTrue(grading.field_match("intent", "Khiếu nại", "Khiếu nại"))
		self.assertFalse(grading.field_match("intent", "Hỏi phí", "Hỏi cách vận hành"))
		self.assertFalse(grading.field_match("intent", "Hỏi cách vận hành", "Hỏi gói dịch vụ"))
		self.assertFalse(grading.field_match("intent", "Khiếu nại", "Hỏi phí"))
		self.assertFalse(grading.field_match("intent", "Hỏi phí", None))

	def test_adapt_to_picks_a_value_the_schema_accepts(self):
		legacy, new = analyze.TOOL_V1["input_schema"]["properties"]["intent"]["enum"], list(NEW)
		self.assertEqual(intents.adapt_to("Hỏi phí", legacy), "Hỏi giá")
		self.assertEqual(intents.adapt_to("Hỏi gói dịch vụ", legacy), "Tư vấn")
		self.assertEqual(intents.adapt_to("Hỏi giá", new), "Hỏi phí")
		self.assertEqual(intents.adapt_to("Khác", new), "Khác")


class TestSchemaV3(FrappeTestCase):
	def schema(self):
		return analyze.TOOL_V3["input_schema"]

	def test_each_new_intent_valid_and_old_or_unknown_invalid(self):
		for intent in NEW:
			self.assertIsNone(ai_client.validate_output(b2b_v3(intent=intent), self.schema()), intent)
		for bad in ("Hỏi giá", "Đặt hàng", "Tư vấn", "Hỏi phí ", "hỏi phí", "", None):
			self.assertIsNotNone(ai_client.validate_output(b2b_v3(intent=bad), self.schema()), bad)

	def test_retail_fields_removed_and_not_required(self):
		props = self.schema()["properties"]
		for gone in ("products", "quantity", "timeframe", "timeframe_within_30d"):
			self.assertNotIn(gone, props)
			self.assertNotIn(gone, self.schema()["required"])
		self.assertIsNone(ai_client.validate_output(b2b_v3(), self.schema()))  # không cần trường bán lẻ

	def test_v1_schema_still_old_enum_and_rejects_new_values(self):
		v1 = analyze.TOOL_V1["input_schema"]
		self.assertEqual(
			v1["properties"]["intent"]["enum"], ["Tư vấn", "Hỏi giá", "Đặt hàng", "Khiếu nại", "Khác"]
		)
		self.assertIsNone(ai_client.validate_output(b2b(intent="Hỏi giá"), v1))
		self.assertIsNotNone(ai_client.validate_output(b2b(intent="Hỏi phí"), v1))

	def test_schema_version_is_bumped_and_recorded(self):
		self.assertEqual(analyze.SCHEMA_V3, "analyze_schema_v3")
		self.assertEqual(analyze.SCHEMA_VERSION, analyze.SCHEMA_V3)
		self.assertNotIn(analyze.SCHEMA_V3, ("analyze_schema_v1", "analyze_schema_v2"))

	def test_prompt_pin_stays_v1_and_prompt_v2_lists_new_intents(self):
		self.assertEqual(loader.PINNED_DEFAULTS["analyze"], "v1")
		self.assertEqual(loader.PINNED_DEFAULTS["reply"], "v1")
		self.assertEqual(loader.pinned_version("analyze"), "v1")
		text = loader.load_prompt("analyze", "v2").text
		for intent in NEW:
			self.assertIn(f'"{intent}"', text)
		self.assertNotIn('"Tư vấn"', text)
		self.assertNotIn('"Hỏi giá"', text)
		self.assertIn("hệ thống quyết định", text)  # chuyển người do code, không do câu lệnh
		self.assertNotIn("v1", [p.version for p in [loader.load_prompt("analyze", "v2")]])

	def test_postprocess_keeps_old_and_new_intents(self):
		for intent in intents.ALL_INTENTS:
			out = analyze.postprocess(b2b_v3(intent=intent), "Chào Miyano")
			self.assertEqual(out["accepted"]["intent"], intent)
		out = analyze.postprocess(b2b_v3(intent="Giá trị lạ"), "Chào Miyano")
		self.assertNotIn("intent", out["accepted"])


class TestGuardsByCode(ReplyTestCase):
	"""Hỏi phí, Yêu cầu đề xuất, Khiếu nại luôn chuyển người dù AI trả action reply và dù nhóm không phải always_handoff."""

	def pin_v2(self):
		original = frappe.db.get_single_value("Omni Settings", "analyze_prompt_version")
		self.addCleanup(set_settings, analyze_prompt_version=original or "")
		set_settings(analyze_prompt_version="v2")

	def draft_for(self, intent, result):
		batch = self.say("Cho hỏi giờ làm việc")  # lời khách vô hại: chỉ ý định do bot phân tích mới bắt được
		self.fake.rules.clear()
		self.fake.add_rule(result, kind="Analyze")
		with mock.patch("frappe.enqueue"):
			analyze.analyze_batch(batch)
		self.fake.add_rule(
			reply_ok(), kind="Reply"
		)  # AI trả "reply", nhóm Giờ làm việc không phải always_handoff
		from omni_crm.bot import reply

		self.assertEqual(reply.reply_batch(batch), "drafted")
		(d,) = self.drafts(batch_id=batch)
		frappe.db.delete("Omni Bot Draft", {"lead": self.lead1})  # tránh giới hạn tần suất nháp theo giờ
		frappe.db.commit()
		return d

	def test_new_always_handoff_intents_block_a_reply(self):
		self.pin_v2()
		for intent in ALWAYS:
			d = self.draft_for(intent, b2b_v3(intent=intent))
			self.assertEqual(d.action, "Handoff", intent)
			self.assertIn("handoff_topic:y_dinh_", d.guard_flags, intent)
			self.assertFalse(d.draft_text, intent)
		self.assertEqual(self.bot_outbound(), 0)

	def test_other_new_intents_follow_the_question_group(self):
		self.pin_v2()
		for intent in ("Hỏi cách vận hành", "Hỏi gói dịch vụ", "Khác"):
			d = self.draft_for(intent, b2b_v3(intent=intent))
			self.assertEqual(d.action, "Reply", intent)  # không bị chặn vì ý định
			self.assertNotIn("y_dinh_", d.guard_flags or "", intent)

	def test_legacy_values_from_pinned_v1_still_handoff(self):
		for intent in ("Hỏi giá", "Đặt hàng", "Khiếu nại"):
			d = self.draft_for(intent, b2b(intent=intent))
			self.assertEqual(d.action, "Handoff", intent)

	def test_guard_function_and_run_guards_for_each_intent(self):
		for intent in (*NEW, "Hỏi giá", "Đặt hàng", "Tư vấn"):
			blocked, reason = guards.guard_handoff_topic(False, intent=intent)
			self.assertEqual(blocked, intents.always_handoff(intent), intent)
			if blocked:
				self.assertTrue(reason.startswith("handoff_topic:y_dinh_"), reason)
		self.assertEqual(
			guards.guard_handoff_topic(False, intent="Hỏi giá")[1], "handoff_topic:y_dinh_hoi_gia"
		)
		self.assertEqual(
			guards.guard_handoff_topic(False, intent="Hỏi phí")[1], "handoff_topic:y_dinh_hoi_phi"
		)
		self.assertEqual(
			guards.guard_handoff_topic(False, intent="Yêu cầu đề xuất")[1],
			"handoff_topic:y_dinh_yeu_cau_de_xuat",
		)
		# always_handoff theo nhóm vẫn giữ, độc lập ý định
		self.assertEqual(
			guards.guard_handoff_topic(True, intent="Khác"), (True, "handoff_topic:nhom_luon_chuyen_nguoi")
		)
		self.assertFalse(guards.guard_handoff_topic(False, intent="Khác")[0])

	def test_auto_send_source_refuses_always_handoff_intent(self):
		src = (APP_ROOT / "omni_crm" / "bot" / "auto_send.py").read_text(encoding="utf-8")
		self.assertIn('intents.always_handoff(analysis.get("intent"))', src)
		self.assertTrue(callable(auto_send.on_analysis))


class TestScoringIntents(ScoringTestCase):
	def setUp(self):
		super().setUp()
		scoring.import_rules(str(scoring.SAMPLE_FILE_B2B), merge=True)

	def sig(self):
		return scoring.collect_signals(self.lead1)

	def test_each_new_intent_gives_the_right_signal_only(self):
		for intent, (signal, _handoff) in TABLE.items():
			self.lead_set(omni_intent=intent)
			got = {s for s in B2B_SIGNALS if self.sig()[s]}
			self.assertEqual(got, {signal} if signal else set(), intent)

	def test_old_values_still_understood_before_the_patch(self):
		for old, new in intents.LEGACY_TO_B2B.items():
			self.lead_set(omni_intent=old)
			after_old = {s for s in B2B_SIGNALS if self.sig()[s]}
			self.lead_set(omni_intent=new)
			self.assertEqual(after_old, {s for s in B2B_SIGNALS if self.sig()[s]}, old)
		self.lead_set(omni_intent="Khiếu nại")
		self.assertTrue(self.sig()["complaint"])

	def test_recent_analyses_old_and_new_both_count(self):
		batch = self.say("Phí dịch vụ bao nhiêu")
		self.analyze(batch, b2b(intent="Hỏi giá"))  # kết quả cũ của câu lệnh v1
		batch2 = self.say("Cảm ơn nhé")
		self.analyze(batch2, b2b(intent="Khác"))
		self.assertTrue(self.sig()["intent_fee"])
		self.lead_set(omni_intent=None)
		self.assertTrue(self.sig()["intent_fee"])
		out = frappe.db.get_value("Omni Bot Run", {"lead": self.lead1, "kind": "Analyze"}, "output_json")
		self.assertIn("Hỏi giá", out)  # lịch sử không bị viết lại

	def test_score_identical_before_and_after_mapping_with_b2b_table(self):
		for old, new in intents.LEGACY_TO_B2B.items():
			self.lead_set(omni_intent=old, mobile_no="0912345678")
			before = scoring.score_lead(self.lead1)[1]["signals"]
			self.lead_set(omni_intent=new)
			after = scoring.score_lead(self.lead1)[1]["signals"]
			self.assertEqual(before, after, old)

	def test_score_identical_before_and_after_mapping_with_v1_table(self):
		self.set_rules(RULES)  # bảng điểm v1: intent_price / intent_purchase vẫn hoạt động sau patch
		for old, new in intents.LEGACY_TO_B2B.items():
			self.lead_set(omni_intent=old, mobile_no="0912345678")
			before = scoring.score_lead(self.lead1)[0]
			self.lead_set(omni_intent=new)
			self.assertEqual(scoring.score_lead(self.lead1)[0], before, old)
		self.lead_set(omni_intent="Hỏi phí")
		self.assertTrue(self.sig()["intent_price"])
		self.lead_set(omni_intent="Yêu cầu đề xuất")
		self.assertTrue(self.sig()["intent_purchase"])


class TestMapOldIntentsPatch(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(self.cleanup)
		self.cleanup()

	def cleanup(self):
		frappe.db.rollback()
		for name in frappe.get_all(LEAD, filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name"):
			frappe.delete_doc(LEAD, name, force=True, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	def lead(self, tag, intent, **extra) -> str:
		name = (
			frappe.get_doc({"doctype": LEAD, "first_name": f"{PREFIX} {tag}"})
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value(LEAD, name, {"omni_intent": intent, **extra})
		return name

	def intent_of(self, name):
		return frappe.db.get_value(LEAD, name, "omni_intent")

	def test_dry_run_real_run_and_rerun(self):
		from omni_crm.patches.v21 import map_old_intents

		base = map_old_intents.execute()  # nền của site, dry run
		leads = {
			"gia": self.lead("gia", "Hỏi giá"),
			"gia2": self.lead("gia2", "Hỏi giá"),
			"dat": self.lead("dat", "Đặt hàng"),
			"tuvan": self.lead("tuvan", "Tư vấn"),
			"kn": self.lead("kn", "Khiếu nại"),
			"khac": self.lead("khac", "Khác"),
			"moi": self.lead("moi", "Hỏi phí"),
			"trong": self.lead("trong", None),
			"dongy": self.lead("dongy", "Hỏi giá", converted=1),  # đã chuyển Deal: không động
			"gop": self.lead(
				"gop", "Đặt hàng", omni_merged_into=self.lead("dich", None)
			),  # đã gộp: không động
			"mat": self.lead("mat", "Tư vấn", status="Junk"),  # Lost: không động
			"tay": self.lead("tay", "Hỏi giá", omni_manual_fields=json.dumps(["omni_intent"])),  # sale tự sửa
		}
		frappe.db.commit()
		snapshot = {k: self.intent_of(v) for k, v in leads.items()}

		dry = map_old_intents.execute()
		self.assertTrue(dry["dry_run"])
		self.assertEqual(
			{k: dry["mapped"][k] - base["mapped"][k] for k in dry["mapped"]},
			{"Hỏi giá": 2, "Đặt hàng": 1, "Tư vấn": 1},
		)
		self.assertEqual(dry["skipped_manual"] - base["skipped_manual"], 1)
		self.assertEqual({k: self.intent_of(v) for k, v in leads.items()}, snapshot)  # dry run không ghi gì

		real = map_old_intents.execute(dry_run=False)
		self.assertFalse(real["dry_run"])
		self.assertEqual(real["mapped"], dry["mapped"])
		got = {k: self.intent_of(v) for k, v in leads.items()}
		self.assertEqual(got["gia"], "Hỏi phí")
		self.assertEqual(got["gia2"], "Hỏi phí")
		self.assertEqual(got["dat"], "Yêu cầu đề xuất")
		self.assertEqual(got["tuvan"], "Hỏi cách vận hành")
		for untouched in ("kn", "khac", "moi", "trong", "dongy", "gop", "mat", "tay"):
			self.assertEqual(got[untouched], snapshot[untouched], untouched)

		second = map_old_intents.execute(dry_run=False)  # chạy lại không đổi thêm
		self.assertEqual(second["changed"], 0)
		self.assertEqual({k: self.intent_of(v) for k, v in leads.items()}, got)
		self.assertEqual(map_old_intents.execute(dry_run="false")["changed"], 0)  # --kwargs đưa chuỗi

	def test_manual_edit_marker_is_kept_and_modified_not_touched(self):
		from omni_crm.patches.v21 import map_old_intents

		name = self.lead("gia", "Hỏi giá")
		frappe.db.commit()
		modified = frappe.db.get_value(LEAD, name, "modified")
		map_old_intents.execute(dry_run=False)
		self.assertEqual(self.intent_of(name), "Hỏi phí")
		self.assertEqual(frappe.db.get_value(LEAD, name, "modified"), modified)
		self.assertFalse(frappe.db.get_value(LEAD, name, "omni_manual_fields"))

	def test_not_registered_in_patches_txt(self):
		patches = (APP_ROOT / "omni_crm" / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("map_old_intents", patches)

	def test_dry_run_is_default_signature(self):
		import inspect

		from omni_crm.patches.v21 import map_old_intents

		self.assertIs(inspect.signature(map_old_intents.execute).parameters["dry_run"].default, True)


class TestEvalCases(FrappeTestCase):
	def cases(self):
		path = APP_ROOT / "eval" / "cases" / "b2b-2026-11-02.jsonl"
		return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

	def test_b2b_cases_use_only_new_intents(self):
		seen = {c["expected_fields"].get("intent") for c in self.cases()} - {None}
		self.assertTrue(seen <= set(NEW), seen)
		self.assertTrue({"Hỏi phí", "Yêu cầu đề xuất", "Khiếu nại", "Hỏi cách vận hành", "Khác"} <= seen)

	def test_fee_cases_expect_fee_and_proposal_case_expects_proposal(self):
		by_id = {c["case_id"]: c for c in self.cases()}
		for i in range(1, 21):
			self.assertEqual(by_id[f"B2B-PHI-{i:03d}"]["expected_fields"]["intent"], "Hỏi phí")
		self.assertEqual(by_id["B2B-DX-001"]["expected_fields"]["intent"], "Yêu cầu đề xuất")
		self.assertTrue(by_id["B2B-PHI-001"]["critical"])

	def test_oracle_returns_an_intent_valid_for_each_schema(self):
		oracle = OracleClient()
		oracle.prepare(
			{"conversation": [{"role": "customer", "text": "x"}], "expected_fields": {"intent": "Hỏi giá"}},
			[],
		)
		self.assertEqual(oracle._analyze(analyze.TOOL_V3)["intent"], "Hỏi phí")  # ca v1, câu lệnh v2
		oracle.prepare(
			{"conversation": [{"role": "customer", "text": "x"}], "expected_fields": {"intent": "Hỏi phí"}},
			[],
		)
		self.assertEqual(oracle._analyze(analyze.TOOL_V1)["intent"], "Hỏi giá")  # ca B2B, câu lệnh v1
		for tool in (analyze.TOOL_V1, analyze.TOOL_V3):
			for case in self.cases():
				oracle.prepare(case, [])
				out = oracle._analyze(tool)
				if tool is analyze.TOOL_V3:
					self.assertIsNone(ai_client.validate_output(out, tool["input_schema"]), case["case_id"])
