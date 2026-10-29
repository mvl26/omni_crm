"""D2-09: kiểm script đo chỉ số nghiệm thu `tests/acceptance/measure_dot2.py` trên dữ liệu dựng sẵn (rollback cuối test)."""

import json
from datetime import datetime, timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.tests.acceptance import measure_dot2 as m

NOW = datetime(2026, 10, 26, 12, 0)
DUE = datetime(2026, 10, 26, 10, 30)
FAR = datetime(2030, 1, 1, 9, 0)


class TestHelpers(FrappeTestCase):
	def test_norm_and_prefix_clause(self):
		self.assertEqual(m._norm(["Găng  tay", "Nitrile"]), "găng tay, nitrile")
		self.assertEqual(m._norm(None), "")
		cond, args = m._prefix_clause("c.call_id", ("TD", "Load"))
		self.assertEqual(args, ["td%", "load%"])
		self.assertEqual(cond.count("not like"), 2)
		self.assertEqual(m._prefix_clause("x", ()), ("1=1", []))

	def test_table_verdicts(self):
		base = {
			"callbacks_on_time": {"ratio_on_time": 0.75},
			"extraction_proxy": {"ratio_kept_proxy": 0.9},
			"eval_gate": {"passed_gate": True, "counts_as_measurement": False, "field_accuracy": None},
		}
		t = m._table(base)
		self.assertIn("| Gọi nhỡ được gọi lại đúng hạn | >= 80% | 0.75 | KHÔNG ĐẠT |", t)
		self.assertIn("| None | chưa đủ dữ liệu |", t)  # bộ đề Fake: không tính là số đo dù passed_gate
		base["callbacks_on_time"]["ratio_on_time"] = 0.8
		base["eval_gate"] = {"passed_gate": True, "counts_as_measurement": True, "field_accuracy": 0.93}
		t = m._table(base)
		self.assertIn("| 0.8 | ĐẠT |", t)
		self.assertIn("| 0.93 | ĐẠT |", t)

	def test_eval_gate_only_real_reviewed_run_counts(self):
		def row(mode, reviewed):
			meta = {"meta": {"mode": mode, "reviewed": reviewed, "gate": "phan_tich"}}
			return frappe._dict(
				name="EVR-1",
				started_at=NOW,
				total_cases=20,
				field_accuracy=0.95,
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
			with mock.patch.object(frappe.db, "get_value", return_value=row(mode, reviewed)):
				res = m.eval_gate()
			self.assertEqual(res["counts_as_measurement"], counts, (mode, reviewed))
			self.assertEqual(res["field_accuracy"], 0.95 if mode == "AI thật" else None)
		with mock.patch.object(frappe.db, "get_value", return_value=None):
			self.assertIsNone(m.eval_gate()["run"])


class TestOnData(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.start = now_datetime() - timedelta(hours=1)
		self.end = now_datetime() + timedelta(hours=1)

	def lead(self, name: str, **kw) -> str:
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": name, **kw})
		doc.flags.ignore_permissions = True
		with mock.patch("frappe.enqueue"):
			doc.insert()
		return doc.name

	def call(
		self, cid: str, status: str, lead: str | None, at: datetime, task: str | None = None, **kw
	) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Call",
					"call_id": f"md2-{cid}",
					"direction": kw.pop("direction", "Inbound"),
					"status": status,
					"lead": lead,
					"started_at": at,
					"ended_at": at,
					"answered_at": at if status == "Answered" else None,
					"callback_task": task,
					**kw,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def task(
		self, lead: str, due: datetime, status: str = "Todo", assigned: str | None = "Administrator"
	) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": f"Gọi lại {lead}",
				"status": status,
				"due_date": due,
				"assigned_to": assigned,
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		return doc.name

	def test_callbacks_on_time_classification(self):
		before = m.callbacks_on_time(self.start, self.end, (), now=NOW)
		miss = datetime(2026, 10, 26, 10, 0)
		# A: nghe máy 10:20 <= hạn 10:30 -> đúng hạn
		a = self.lead("MD2 A")
		self.call("a-ans", "Answered", a, datetime(2026, 10, 26, 10, 20), direction="Outbound")
		self.call("a-miss", "Missed", a, miss, task=self.task(a, DUE))
		# B: nghe máy 11:00 > hạn -> trễ
		b = self.lead("MD2 B")
		self.call("b-ans", "Answered", b, datetime(2026, 10, 26, 11, 0), direction="Outbound")
		self.call("b-miss", "Missed", b, miss, task=self.task(b, DUE))
		# C: còn mở, quá hạn lúc 12:00, không ai gọi -> trễ
		c = self.lead("MD2 C")
		self.call("c-miss", "Missed", c, miss, task=self.task(c, DUE, assigned=None))
		# D: còn mở, chưa đến hạn -> chưa tính
		d = self.lead("MD2 D")
		self.call("d-miss", "Missed", d, miss, task=self.task(d, FAR))
		# E: sale đóng tay trước hạn (hạn xa), không có cuộc gọi ghi nhận -> đúng hạn
		e = self.lead("MD2 E")
		self.call("e-miss", "Missed", e, miss, task=self.task(e, FAR, status="Done"))
		# F: nhỡ có Lead nhưng chưa có việc; G: nhỡ ẩn số (không Lead)
		f = self.lead("MD2 F")
		self.call("f-miss", "Missed", f, miss)
		self.call("g-miss", "Missed", None, miss)
		# cuộc gọi đi nhỡ không phải việc gọi lại
		self.call("h-out", "Missed", f, miss, direction="Outbound")
		after = m.callbacks_on_time(self.start, self.end, (), now=NOW)
		diff = {k: after[k] - before[k] for k in after if isinstance(after[k], int) and k in before}
		self.assertEqual(diff["tasks_in_period"], 5)
		self.assertEqual((diff["on_time"], diff["late"], diff["pending_not_due_yet"]), (2, 2, 1))
		self.assertEqual((diff["closed_by_answered_call"], diff["closed_by_hand_without_call"]), (2, 1))
		self.assertEqual(diff["tasks_unassigned"], 1)
		self.assertEqual(diff["missed_inbound_calls"], 7)
		self.assertEqual(diff["missed_hidden_number_no_task"], 1)
		self.assertEqual(diff["missed_with_lead_but_no_task"], 1)

	def test_callbacks_exclusion_prefix_drops_test_calls(self):
		a = self.lead("MD2 X")
		self.call("x-miss", "Missed", a, datetime(2026, 10, 26, 10, 0), task=self.task(a, DUE))
		with_x = m.callbacks_on_time(self.start, self.end, (), now=NOW)
		without = m.callbacks_on_time(self.start, self.end, ("md2-",), now=NOW)
		self.assertEqual(with_x["tasks_in_period"] - without["tasks_in_period"], 1)

	def run_row(self, lead: str, accepted: dict, status: str = "Success"):
		frappe.get_doc(
			{
				"doctype": "Omni Bot Run",
				"kind": "Analyze",
				"status": status,
				"model": "fake",
				"lead": lead,
				"batch_id": f"md2-{frappe.generate_hash(length=6)}",
				"output_json": json.dumps({"accepted": accepted}),
			}
		).insert(ignore_permissions=True)

	def test_extraction_proxy_counts_kept_and_changed(self):
		before = m.extraction_proxy(self.start, self.end, ())
		lead = self.lead("MD2 P", omni_need="mua găng tay", omni_quantity="100 hộp", omni_intent="Đặt hàng")
		self.run_row(lead, {"need": "mua găng tay", "quantity": "100 hộp", "intent": "Đặt hàng"})
		frappe.db.set_value("CRM Lead", lead, "omni_quantity", "200 hộp")  # sale sửa
		# lượt lỗi không đóng góp
		self.run_row(lead, {"need": "ghi chú lạ"}, status="Error")
		after = m.extraction_proxy(self.start, self.end, ())
		self.assertEqual(after["values_filled_by_bot"] - before["values_filled_by_bot"], 3)
		self.assertEqual(after["values_kept"] - before["values_kept"], 2)
		self.assertEqual(
			after["values_changed_or_cleared_since"] - before["values_changed_or_cleared_since"], 1
		)
		self.assertEqual(after["leads_with_bot_fields"] - before["leads_with_bot_fields"], 1)
		self.assertIn("không phải độ đúng", after["note"])

	def test_run_prints_json_and_table(self):
		with mock.patch("builtins.print") as out:
			m.run(str(self.start), str(self.end))
		text = "\n".join(str(c.args[0]) for c in out.call_args_list)
		self.assertIn("callbacks_on_time", text)
		self.assertIn("| Chỉ số | Ngưỡng | Đo được | Kết luận |", text)
