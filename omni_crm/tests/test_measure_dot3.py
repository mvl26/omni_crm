# ruff: noqa: RUF001
"""D3-06: kiểm script đo chỉ số nghiệm thu `tests/acceptance/measure_dot3.py` trên dữ liệu dựng sẵn (rollback cuối test)."""

import json
from datetime import timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.tests.acceptance import measure_dot3 as m

G = "MD3 Giờ làm việc"


class TestMeasureDot3(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.start = now_datetime() - timedelta(hours=1)
		self.end = now_datetime() + timedelta(hours=1)
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": "MD3 Khach"})
		doc.flags.ignore_permissions = True
		with mock.patch("frappe.enqueue"):
			doc.insert()
		self.lead = doc.name
		frappe.get_doc(
			{"doctype": "Omni Question Group", "group_name": G, "always_handoff": 0, "description": "md3"}
		).insert(ignore_permissions=True)

	def draft(self, decision="Sent As Is", ratio=0.0, mode="Draft", **kw) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Bot Draft",
					"lead": self.lead,
					"batch_id": f"md3-{frappe.generate_hash(length=8)}",
					"mode": mode,
					"action": kw.pop("action", "Reply"),
					"decision": decision,
					"question_group": G,
					"draft_text": "x",
					"edit_distance_ratio": ratio,
					**kw,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def quality(self, prefixes=()):
		return m.draft_quality(self.start, self.end, prefixes)

	def test_ratio_hidden_when_sample_too_small(self):
		for _i in range(5):
			self.draft()
		q = self.quality()
		self.assertGreaterEqual(q["decided"], 5)
		if q["decided"] < m.MIN_SAMPLE:
			self.assertIsNone(q["ratio_good"])
			self.assertFalse(q["enough_data"])
		self.assertFalse(q["by_group"][G]["enough_data"])
		self.assertIsNone(q["by_group"][G]["ratio"])

	def test_ratio_uses_eligibility_definition_with_discarded_in_denominator(self):
		before = self.quality()
		# 20 nguyên văn, 8 sửa nhẹ (0,1 còn tính), 4 sửa nặng (0,5), 6 bỏ => đúng 28 / quyết định 38
		for _i in range(20):
			self.draft("Sent As Is")
		for _i in range(8):
			self.draft("Sent Edited", 0.1)
		for _i in range(4):
			self.draft("Sent Edited", 0.5)
		for _i in range(6):
			self.draft("Discarded")
		self.draft("Pending")  # chưa quyết định: không vào mẫu số
		self.draft("Expired")
		after = self.quality()
		self.assertEqual(after["decided"] - before["decided"], 38)
		self.assertEqual(after["good"] - before["good"], 28)
		self.assertEqual((after["pending"] - before["pending"], after["expired"] - before["expired"]), (1, 1))
		g = after["by_group"][G]
		self.assertEqual((g["decided"], g["good"]), (38, 28))
		self.assertEqual(g["ratio"], round(28 / 38, 4))
		self.assertTrue(g["enough_data"])

	def test_shadow_and_auto_modes_are_not_in_draft_quality(self):
		before = self.quality()
		self.draft("Shadow Compared", mode="Shadow")
		self.draft("Sent As Is", mode="Auto")
		self.assertEqual(self.quality()["decided"], before["decided"])

	def test_prefix_excludes_test_leads(self):
		self.draft()
		with_all = self.quality(())["drafts_created"]
		without = self.quality(("md3",))["drafts_created"]
		self.assertEqual(with_all - without, 1)

	def test_serious_errors_counts_critical_wrong_with_note_and_auto_wrong(self):
		before = m.serious_errors(self.start, self.end, ())
		self.draft("Sent As Is", critical=1)
		self.draft("Sent As Is", rating="Sai", rating_note="trả lời sai giờ")
		self.draft("Sent As Is", rating="Sai")  # Sai không ghi chú, không phải tin Auto: không tính
		self.draft("Sent As Is", mode="Auto", rating="Sai")
		after = m.serious_errors(self.start, self.end, ())
		self.assertEqual(after["critical_flagged"] - before["critical_flagged"], 1)
		self.assertEqual(after["wrong_with_note"] - before["wrong_with_note"], 1)
		self.assertEqual(after["auto_sent_rated_wrong"] - before["auto_sent_rated_wrong"], 1)
		self.assertEqual(after["total_serious"] - before["total_serious"], 3)

	def test_bot_self_handled_zero_without_auto(self):
		for _i in range(m.MIN_SAMPLE):
			self.draft("Sent As Is")
		res = m.bot_self_handled(self.start, self.end, ())
		g = res["groups"][G]
		self.assertEqual(g["auto_sent"], 0)
		self.assertEqual(g["ratio_self_handled"], 0)
		self.draft("Sent As Is", mode="Auto")
		self.assertEqual(m.bot_self_handled(self.start, self.end, ())["groups"][G]["auto_sent"], 1)

	def test_nhap_gate_only_real_reviewed_run_counts(self):
		def row(mode, reviewed, gate="nhap"):
			meta = {"meta": {"mode": mode, "reviewed": reviewed, "gate": gate}}
			return frappe._dict(
				name="EVR-1",
				started_at=self.start,
				total_cases=20,
				field_accuracy=0.95,
				action_accuracy=0.92,
				answer_pass_rate=0.9,
				critical_failures=0,
				passed_gate=1,
				result_json=json.dumps(meta),
			)

		for mode, reviewed, counts in (
			("Fake", True, False),
			("Không có client AI", False, False),
			("AI thật", False, False),
			("AI thật", True, True),
		):
			with mock.patch.object(frappe, "get_all", return_value=[row(mode, reviewed)]):
				res = m.nhap_gate()
			self.assertEqual(res["counts_as_measurement"], counts, (mode, reviewed))
			self.assertEqual(res["answer_pass_rate"], 0.9 if mode == "AI thật" else None)
		with mock.patch.object(frappe, "get_all", return_value=[row("AI thật", True, gate="phan_tich")]):
			self.assertIsNone(m.nhap_gate()["run"])  # chỉ cổng nhap mới tính

	def test_table_says_not_enough_data_instead_of_a_verdict(self):
		res = {
			"draft_quality": {"ratio_good": None, "decided": 3, "enough_data": False},
			"serious_errors": {"total_serious": 0},
			"nhap_gate": {"counts_as_measurement": False, "passed_gate": True, "answer_pass_rate": None},
			"first_response_dot1": {"median_min": None},
		}
		t = m._table(res)
		self.assertEqual(t.count("chưa đủ dữ liệu"), 4)
		self.assertNotIn("ĐẠT", t.replace("KHÔNG ĐẠT", ""))
		res["draft_quality"] = {"ratio_good": 0.9, "decided": 40, "enough_data": True}
		res["nhap_gate"] = {"counts_as_measurement": True, "passed_gate": True, "answer_pass_rate": 0.9}
		res["first_response_dot1"] = {"median_min": 20}
		t = m._table(res)
		self.assertIn("| 0.9 | ĐẠT |", t)
		self.assertIn("| 20 | KHÔNG ĐẠT |", t)

	def test_run_prints_json_and_table_on_empty_period(self):
		with mock.patch("builtins.print") as out:
			m.run(str(self.start), str(self.end))
		text = "\n".join(str(c.args[0]) for c in out.call_args_list)
		self.assertIn("draft_quality", text)
		self.assertIn("| Chỉ số | Ngưỡng | Đo được | Kết luận |", text)
