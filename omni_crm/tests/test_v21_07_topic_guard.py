# ruff: noqa: RUF001
"""V21-07 (F7): chốt chặn chủ đề không bị né bằng cách viết; gia cố `intents.normalize`/`always_handoff`.

Bộ ca ở tests/fixtures/guards/topic_cases.json. 40+ ca lách PHẢI chặn, ca thường PHẢI qua; số đo in ra khi chạy với -v.
Thuần (không DB, không AI, không gửi ra ngoài)."""

import json
import time
import unicodedata
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import guard_text, guards, intents
from omni_crm.bot.guard_text import normalize_for_guard

CASES = json.loads((Path(__file__).parent / "fixtures" / "guards" / "topic_cases.json").read_text("utf-8"))


class TestNormalizeForGuard(FrappeTestCase):
	def test_steps(self):
		n = normalize_for_guard
		self.assertEqual(n("ĐÀM PHÁN"), "dam phan")
		self.assertEqual(n("ph1 d1ch vu"), "phi dich vu")
		self.assertEqual(n("b@o l4nh $"), "bao lanh s")
		self.assertEqual(n("đ à m p h á n"), "damphan")
		self.assertEqual(n("phííííí"), "phii")
		self.assertEqual(n("đàm💬phán"), "damphan")
		self.assertEqual(n("đàm💬phán", sep=" "), "dam phan")
		self.assertEqual(n("đàm​phán"), "damphan")
		self.assertEqual(n("ｄàm ｐhán"), "dam phan")
		self.assertEqual(n("đàm рhá́n"), "dam phan")

	def test_not_str_is_empty(self):
		for bad in (None, 5, ["a"], {"a": 1}, b"abc"):
			self.assertEqual(normalize_for_guard(bad), "")
			self.assertEqual(guards.sensitive_topics(bad), [])
			self.assertFalse(guards.guard_injection(bad)[0])


class TestTopicCases(FrappeTestCase):
	def test_case_counts(self):
		self.assertGreaterEqual(len(CASES["evade"]), 40)
		self.assertGreaterEqual(len(CASES["normal"]), 20)
		self.assertGreaterEqual(len(CASES["evade"]) + len(CASES["normal"]), 60)

	def test_evasions_are_all_blocked(self):
		missed = [c["id"] + " " + c["text"] for c in CASES["evade"] if not guards.sensitive_topics(c["text"])]
		blocked = len(CASES["evade"]) - len(missed)
		print(f"\n[V21-07] ca lách chặn được: {blocked}/{len(CASES['evade'])}; lọt: {missed}")
		self.assertEqual(missed, [])

	def test_handoff_topic_guard_blocks_evasions(self):
		for c in CASES["evade"]:
			with self.subTest(c["id"]):
				self.assertTrue(guards.guard_handoff_topic(False, None, False, c["text"])[0], c["text"])

	def test_normal_sentences_pass(self):
		wrong = [
			c["id"] + " " + c["text"]
			for c in CASES["normal"]
			if guards.sensitive_topics(c["text"]) or guards.guard_injection(c["text"])[0]
		]
		print(
			f"\n[V21-07] câu thường qua: {len(CASES['normal']) - len(wrong)}/{len(CASES['normal'])}; chặn nhầm: {wrong}"
		)
		self.assertEqual(wrong, [])

	def test_benign_phrase_does_not_hide_the_rest_of_the_sentence(self):
		self.assertEqual(guards.sensitive_topics("hai bên thỏa thuận lịch hẹn"), [])
		self.assertTrue(guards.sensitive_topics("thỏa thuận lịch hẹn rồi đàm phán giá"))
		self.assertTrue(guards.sensitive_topics("100 phần trăm hài lòng, giờ cho tôi xem chiết khấu"))
		self.assertTrue(guards.sensitive_topics("phần trăm hoa hồng là bao nhiêu"))
		self.assertTrue(guards.sensitive_topics("thoa thuan khung"))

	def test_injection_evasions_blocked_and_normal_pass(self):
		for c in CASES["injection_evade"]:
			with self.subTest(c["id"]):
				self.assertTrue(guards.guard_injection(c["text"])[0], c["text"])
		for c in CASES["injection_normal"]:
			with self.subTest(c["id"]):
				self.assertFalse(guards.guard_injection(c["text"])[0], c["text"])

	def test_guard_never_sends_only_handoffs(self):
		"""Chốt chặn chỉ trả (bị chặn, lý do); lý do bắt đầu bằng handoff_topic, không có đường nào tạo tin ra."""
		blocked, reason = guards.guard_handoff_topic(False, None, False, "đ à m p h á n")
		self.assertTrue(blocked)
		self.assertTrue(reason.startswith("handoff_topic:"))

	def test_speed_under_5ms_for_2000_chars(self):
		text = ("Xin chào, tôi cần hỏi về hồ sơ năng lực và lịch làm việc của bên anh. " * 40)[:2000]
		self.assertEqual(len(text), 2000)
		runs = 30
		best = []
		for _ in range(runs):
			guards._forms_of.cache_clear()
			guard_text._clean_str.cache_clear()
			t0 = time.perf_counter()
			guards.sensitive_topics(text)
			guards.guard_injection(text)
			best.append((time.perf_counter() - t0) * 1000)
		best.sort()
		median = best[runs // 2]
		print(
			f"\n[V21-07] normalize + match 2000 ký tự (bộ nhớ đệm xóa): trung vị {median:.2f} ms, tối đa {best[-1]:.2f} ms"
		)
		self.assertLess(median, 5)


class TestIntentHardening(FrappeTestCase):
	def test_variants_of_fee_all_hand_off(self):
		fee = "Hỏi phí"
		for v in (
			fee, " Hỏi phí\n", "HỎI PHÍ", "hỏi phí", "Hỏi  phí", "\tHỏi phí ", "hoi phi", "HOI PHI",
			unicodedata.normalize("NFD", fee), unicodedata.normalize("NFD", fee.upper()),
			"Hỏi​ phí", "﻿Hỏi phí", "Hỏi phí",
		):  # fmt: skip
			with self.subTest(repr(v)):
				self.assertEqual(intents.normalize(v), fee)
				self.assertTrue(intents.always_handoff(v))

	def test_legacy_variants_map_and_hand_off(self):
		for v, canon in (
			("hỏi giá", "Hỏi phí"),
			(" ĐẶT HÀNG ", "Yêu cầu đề xuất"),
			("khiếu nại", "Khiếu nại"),
		):
			self.assertEqual(intents.normalize(v), canon)
			self.assertTrue(intents.always_handoff(v))
		self.assertEqual(intents.normalize("TƯ VẤN"), "Hỏi cách vận hành")
		self.assertFalse(intents.always_handoff("tư vấn"))

	def test_unknown_values_do_not_hand_off(self):
		for v in ("giá trị lạ", "Hỏi phí thêm", "phí", "Hỏi phí.", "Other"):
			self.assertFalse(intents.always_handoff(v), v)
		self.assertEqual(intents.normalize("giá trị lạ"), "giá trị lạ")

	def test_non_str_never_raises(self):
		for v in (
			["Hỏi phí"],
			{"Hỏi phí": 1},
			("Hỏi phí",),
			5,
			1.5,
			True,
			b"Hoi phi",
			object(),
			None,
			"",
			"  \n",
		):
			self.assertIsNone(intents.normalize(v), repr(v))
			self.assertFalse(intents.always_handoff(v), repr(v))
			self.assertIsNone(intents.signal_of(v))

	def test_group_lookup_keys_are_unique(self):
		"""Bỏ dấu không làm hai ý định trùng khóa (nếu trùng, tra cứu dự phòng sẽ sai)."""
		keys = {intents._unaccent(intents._fold(v)) for v in intents.ALL_INTENTS}
		self.assertEqual(len(keys), len(intents.ALL_INTENTS))
