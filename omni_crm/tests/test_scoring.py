# ruff: noqa: RUF001
"""D2-06: bot/scoring.py (quy tắc thuần, không cộng trùng, điểm âm, Lead nóng báo một lần, nhập CSV, công tắc,
nối analyze/post_batch, điểm chỉ đọc). Không gọi AI thật."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.api import inbox
from omni_crm.bot import analyze, scoring
from omni_crm.pipeline import worker
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_analyze import AnalyzeTestCase, signals
from omni_crm.tests.test_inbox import SALE1, SALE2

RULES = [
	{"signal": "has_phone", "points": 15, "note": "Có số điện thoại hợp lệ"},
	{"signal": "has_email", "points": 5, "note": "Có email"},
	{"signal": "has_org", "points": 10, "note": "Nêu tên đơn vị"},
	{"signal": "intent_price", "points": 15, "note": "Hỏi giá"},
	{"signal": "intent_purchase", "points": 25, "note": "Muốn đặt hàng"},
	{"signal": "quantity_given", "points": 15, "note": "Nêu số lượng"},
	{"signal": "timeframe_within_30d", "points": 15, "note": "Cần trong 30 ngày"},
	{"signal": "urgency_high", "points": 10, "note": "Khách nói gấp"},
	{"signal": "repeat_contact", "points": 5, "note": "Liên hệ lại"},
	{"signal": "complaint", "points": -10, "note": "Đang khiếu nại"},
]
KEYS = ("lead_scoring_enabled", "hot_lead_threshold")
PHONE = "0912345678"


class ScoringTestCase(AnalyzeTestCase):
	def setUp(self):
		super().setUp()
		# InboxTestCase gieo 2 tin cũ cho lead1 (đủ làm repeat_contact đúng): bỏ để mỗi test tự dựng tín hiệu
		frappe.db.delete("Omni Inbound Message", {"lead": self.lead1})
		frappe.db.commit()
		self.addCleanup(self._restore_scoring)
		self.set_rules(RULES)
		set_settings(lead_scoring_enabled=1, hot_lead_threshold=60)
		self.addCleanup(self._clean_notifications)

	def _restore_scoring(self):
		set_settings(lead_scoring_enabled=0, hot_lead_threshold=60, score_rules=[])

	def _clean_notifications(self):
		frappe.db.rollback()
		frappe.db.delete("Notification Log", {"document_type": "CRM Lead", "subject": ["like", "Lead nóng%"]})
		frappe.db.commit()

	def set_rules(self, rules):
		set_settings(score_rules=[dict(r) for r in rules])

	def lead_set(self, **values):
		frappe.db.set_value("CRM Lead", self.lead1, values)
		frappe.db.commit()

	def score_of(self, lead=None):
		scoring.run_scoring(lead or self.lead1)
		return frappe.db.get_value("CRM Lead", lead or self.lead1, "omni_score")

	def hot_logs(self, user=SALE1):
		return frappe.get_all(
			"Notification Log",
			filters={"document_name": self.lead1, "for_user": user, "subject": ["like", "Lead nóng%"]},
			pluck="name",
		)


class TestComputation(ScoringTestCase):
	def test_five_signal_sets_match_hand_calculation(self):
		cases = [
			({}, 0),
			({"mobile_no": PHONE}, 15),
			({"mobile_no": PHONE, "omni_intent": "Hỏi giá", "omni_quantity": "200 hộp"}, 15 + 15 + 15),
			(
				{
					"mobile_no": PHONE,
					"email": "a@example.com",
					"organization": "BV Hoa",
					"omni_intent": "Đặt hàng",
					"omni_quantity": "10 thùng",
					"omni_urgency": "Cao",
				},
				15 + 5 + 10 + 25 + 15 + 10,
			),
			(
				{
					"mobile_no": None,
					"email": None,
					"organization": None,
					"omni_intent": "Tư vấn",
					"omni_quantity": None,
					"omni_urgency": "Thấp",
				},
				0,
			),  # fmt: skip
		]
		for values, expected in cases:
			self.lead_set(**values)
			score, breakdown = scoring.score_lead(self.lead1)
			self.assertEqual(score, expected, values)
			self.assertEqual(breakdown["total"], expected)
			self.assertEqual(sum(i["points"] for i in breakdown["signals"]), expected)

	def test_invalid_phone_and_email_do_not_count(self):
		self.lead_set(mobile_no="12ab", email="khong-phai-email")
		self.assertEqual(scoring.score_lead(self.lead1)[0], 0)

	def test_no_double_count_when_rescored_or_duplicate_rule_rows(self):
		self.lead_set(mobile_no=PHONE)
		first = scoring.score_lead(self.lead1)[0]
		self.assertEqual(scoring.score_lead(self.lead1)[0], first)
		self.assertEqual(
			scoring.compute({"has_phone": True}, {"has_phone": {"points": 15, "note": ""}}),
			(15, [{"signal": "has_phone", "points": 15, "note": ""}]),
		)

	def test_settings_rejects_duplicate_signal_and_bad_threshold(self):
		with self.assertRaises(frappe.ValidationError):
			set_settings(score_rules=[dict(r) for r in [*RULES, RULES[0]]])
		frappe.db.rollback()
		with self.assertRaises(frappe.ValidationError):
			set_settings(hot_lead_threshold=0)
		frappe.db.rollback()

	def test_complaint_gives_negative_score(self):
		self.lead_set(omni_intent="Khiếu nại")
		score, breakdown = scoring.score_lead(self.lead1)
		self.assertEqual(score, -10)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), -10)
		self.assertEqual([i["signal"] for i in breakdown["signals"]], ["complaint"])

	def test_empty_rules_means_no_scoring(self):
		self.lead_set(mobile_no=PHONE, omni_score=7)
		self.set_rules([])
		self.assertIsNone(scoring.run_scoring(self.lead1))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), 7)

	def test_repeat_contact_counts_batches_within_30_days(self):
		self.say("lần một", minutes_ago=5)
		self.assertFalse(scoring.collect_signals(self.lead1)["repeat_contact"])
		batch = self.say("lần hai", minutes_ago=3)
		self.assertTrue(scoring.collect_signals(self.lead1)["repeat_contact"])
		self.assertEqual(scoring.recent_contacts(self.lead1), 2)
		# nhiều tin cùng một lượt chỉ tính một lần
		name = self.make_inbound(self.lead1, self.ident1, "Zalo OA", "tin nữa", 3)
		frappe.db.set_value("Omni Inbound Message", name, "batch_id", batch)
		self.assertEqual(scoring.recent_contacts(self.lead1), 2)

	def test_old_contacts_do_not_count(self):
		self.say("cũ", minutes_ago=60 * 24 * 40)
		self.say("mới", minutes_ago=3)
		self.assertEqual(scoring.recent_contacts(self.lead1), 1)

	def test_inbound_call_counts_as_contact(self):
		self.say("tin", minutes_ago=3)
		call = frappe.get_doc(
			{
				"doctype": "Omni Call",
				"call_id": f"TD106-{frappe.generate_hash(length=8)}",
				"direction": "Inbound",
				"status": "Missed",
				"started_at": add_to_date(now_datetime(), minutes=-2),
				"lead": self.lead1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: (frappe.db.delete("Omni Call", {"name": call.name}), frappe.db.commit()))
		self.assertEqual(scoring.recent_contacts(self.lead1), 2)
		out = frappe.get_doc("Omni Call", call.name)
		out.direction, out.started_at = "Outbound", out.started_at
		out.db_set("direction", "Outbound")
		self.assertEqual(scoring.recent_contacts(self.lead1), 1)  # gọi ra của sale không phải khách liên hệ

	def test_timeframe_from_latest_bot_run_with_the_key(self):
		batch = self.say("Cần trong tuần sau")
		ev = {"timeframe": "tuần sau", "timeframe_within_30d": True}
		self.analyze(
			batch,
			signals(timeframe="tuần sau", timeframe_within_30d=True, evidence={"timeframe": "tuần sau"}),
		)
		self.assertTrue(scoring.collect_signals(self.lead1)["timeframe_within_30d"], ev)
		# lượt sau không có khóa (không căn cứ): vẫn dùng lượt gần nhất CÓ khóa
		self.fake.rules.clear()  # luật khớp đầu tiên thắng
		self.analyze(self.say("ok"), signals())
		self.assertTrue(scoring.collect_signals(self.lead1)["timeframe_within_30d"])
		# lượt sau nói ngược lại thì thắng
		self.fake.rules.clear()
		self.analyze(
			self.say("để sang năm sau"),
			signals(
				timeframe="sang năm sau", timeframe_within_30d=False, evidence={"timeframe": "sang năm sau"}
			),
		)
		self.assertFalse(scoring.collect_signals(self.lead1)["timeframe_within_30d"])


class TestHotLead(ScoringTestCase):
	def test_notified_once_with_reasons_and_no_pii(self):
		self.lead_set(mobile_no=PHONE, email="khach.bi.mat@example.com", omni_intent="Đặt hàng",
		              omni_quantity="500 hộp", omni_urgency="Cao")  # fmt: skip
		self.assertGreaterEqual(self.score_of(), 60)
		logs = self.hot_logs()
		self.assertEqual(len(logs), 1)
		log = frappe.get_doc("Notification Log", logs[0])
		text = f"{log.subject} {log.email_content}"
		self.assertIn("Lead nóng", log.subject)
		self.assertIn("Muốn đặt hàng", log.email_content)
		for secret in (PHONE, "khach.bi.mat", "500 hộp"):
			self.assertNotIn(secret, text)
		self.assertTrue(
			json.loads(frappe.db.get_value("CRM Lead", self.lead1, "omni_score_breakdown"))["hot_notified_at"]
		)
		self.assertEqual(frappe.db.get_value("Notification Log", logs[0], "document_name"), self.lead1)

	def test_oscillation_around_threshold_does_not_renotify(self):
		hot = {"mobile_no": PHONE, "omni_intent": "Đặt hàng", "omni_quantity": "5 hộp", "omni_urgency": "Cao"}
		self.lead_set(**hot)
		self.score_of()
		self.lead_set(omni_intent="Tư vấn", omni_quantity=None, omni_urgency="Thấp")
		self.assertLess(self.score_of(), 60)
		self.lead_set(**hot)
		self.assertGreaterEqual(self.score_of(), 60)
		self.score_of()
		self.assertEqual(len(self.hot_logs()), 1)

	def test_below_threshold_not_notified(self):
		self.lead_set(mobile_no=PHONE)
		self.score_of()
		self.assertEqual(self.hot_logs(), [])

	def test_threshold_is_configurable(self):
		self.lead_set(mobile_no=PHONE)
		set_settings(hot_lead_threshold=10)
		self.score_of()
		self.assertEqual(len(self.hot_logs()), 1)

	def test_no_recipient_does_not_stamp_so_notifies_later(self):
		self.lead_set(mobile_no=PHONE, lead_owner=None, _assign=None)
		set_settings(hot_lead_threshold=10, assignment_fallback_users="")
		self.score_of()
		self.assertFalse(
			json.loads(frappe.db.get_value("CRM Lead", self.lead1, "omni_score_breakdown")).get(
				"hot_notified_at"
			)
		)
		self.lead_set(lead_owner=SALE1)
		self.score_of()
		self.assertEqual(len(self.hot_logs()), 1)

	def test_notification_failure_does_not_break_scoring(self):
		self.lead_set(mobile_no=PHONE)
		set_settings(hot_lead_threshold=10)
		with mock.patch.object(scoring, "notify_hot", side_effect=RuntimeError("x")):
			self.assertEqual(scoring.run_scoring(self.lead1), 15)  # điểm vẫn ghi, lỗi báo chỉ vào log
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), 15)


class TestSwitchAndWiring(ScoringTestCase):
	def test_switch_off_keeps_old_score_and_does_nothing(self):
		self.lead_set(mobile_no=PHONE)
		self.score_of()
		set_settings(lead_scoring_enabled=0)
		self.lead_set(mobile_no=None, email="a@example.com")
		self.assertIsNone(scoring.run_scoring(self.lead1))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), 15)

	def test_scores_after_analyze_batch(self):
		batch = self.say("Em cần 500 hộp găng tay gấp")
		self.analyze(
			batch,
			signals(
				intent="Hỏi giá",
				quantity="500 hộp",
				urgency="Cao",
				evidence={"quantity": "500 hộp", "urgency": "cần 500 hộp"},
			),
		)
		# 15 (hỏi giá) + 15 (số lượng) + 10 (gấp), chưa có SĐT
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), 40)

	def test_scoring_error_does_not_break_analysis(self):
		batch = self.say("Em cần 500 hộp găng tay")
		with mock.patch.object(scoring, "score_lead", side_effect=RuntimeError("hỏng")):
			out = self.analyze(batch, signals(quantity="500 hộp", evidence={"quantity": "500 hộp"}))
		self.assertEqual(out.omni_quantity, "500 hộp")
		self.assertEqual(self.run_row(batch).status, "Success")

	def test_post_batch_scores_when_bot_off(self):
		set_settings(bot_analyze_enabled=0)
		self.lead_set(mobile_no=PHONE)
		batch = self.say("chào")
		with mock.patch("frappe.enqueue") as enq:
			worker.post_batch_hooks(batch, self.lead1)
		self.assertFalse([c for c in enq.call_args_list if c.args and c.args[0] == analyze.JOB])
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), 15)

	def test_post_batch_does_not_score_twice_when_analysis_enqueued(self):
		batch = self.say("Em cần găng tay")
		with mock.patch("frappe.enqueue"), mock.patch.object(scoring, "run_scoring") as run:
			worker.post_batch_hooks(batch, self.lead1)
		run.assert_not_called()

	def test_merged_lead_scores_the_survivor(self):
		other = self.make_lead("TD106 Gop", SALE1)
		frappe.db.set_value("CRM Lead", self.lead1, "omni_merged_into", other)
		frappe.db.set_value("CRM Lead", other, "mobile_no", PHONE)
		frappe.db.commit()
		scoring.run_scoring(self.lead1)
		self.assertEqual(frappe.db.get_value("CRM Lead", other, "omni_score"), 15)


class TestReadOnlyAndInbox(ScoringTestCase):
	def test_sale_cannot_edit_score_but_system_can(self):
		self.lead_set(mobile_no=PHONE)
		self.score_of()
		self.as_user(SALE1)
		doc = frappe.get_doc("CRM Lead", self.lead1)
		doc.omni_score = 999
		doc.omni_score_breakdown = "{}"
		doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_score"), 15)
		self.assertIn("signals", frappe.db.get_value("CRM Lead", self.lead1, "omni_score_breakdown"))

	def test_new_lead_by_user_cannot_preset_score(self):
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD106 Moi", "omni_score": 99})
		with mock.patch("frappe.enqueue"):
			doc.insert(ignore_permissions=True)
		self.assertFalse(frappe.db.get_value("CRM Lead", doc.name, "omni_score"))

	def test_thread_returns_score_and_reasons_not_raw_json(self):
		self.lead_set(mobile_no=PHONE, omni_intent="Hỏi giá")
		self.score_of()
		self.as_user(SALE1)
		data = inbox.get_thread(self.lead1)
		self.assertEqual(data["score"]["score"], 30)
		self.assertIn("Có số điện thoại hợp lệ (+15)", data["score"]["reasons"])
		self.assertNotIn("omni_score_breakdown", data["lead"])
		self.assertEqual(data["lead"]["omni_score"], 30)

	def test_thread_without_score_is_none(self):
		self.as_user(SALE1)
		self.assertIsNone(inbox.get_thread(self.lead1)["score"])

	def test_other_sale_still_cannot_read_thread(self):
		self.score_of()
		self.as_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			inbox.get_thread(self.lead1)


class TestImportRules(ScoringTestCase):
	def write(self, text, name="bang-diem.csv"):
		d = tempfile.mkdtemp()
		self.addCleanup(lambda: [p.unlink() for p in Path(d).iterdir()] and Path(d).rmdir())
		path = Path(d) / name
		path.write_text(text, encoding="utf-8")
		return str(path)

	def current(self):
		frappe.clear_document_cache("Omni Settings", "Omni Settings")
		return {(r.signal, r.points) for r in frappe.get_doc("Omni Settings").score_rules}

	def test_valid_file_replaces_whole_table(self):
		path = self.write("signal,points,note\nhas_phone,20,SĐT\ncomplaint,-5,Khiếu nại\n")
		out = scoring.import_rules(path)
		self.assertEqual(out["imported"], 2)
		self.assertEqual(out["warnings"], [])
		self.assertEqual(self.current(), {("has_phone", 20), ("complaint", -5)})

	def test_bad_signal_raises_and_keeps_old_table(self):
		before = self.current()
		path = self.write(
			"signal,points,note\nhas_phone,20,ok\nkhong_co,5,sai\nhas_email,abc,sai\nhas_phone,1,trùng\n"
		)
		with self.assertRaises(ValueError) as cm:
			scoring.import_rules(path)
		msg = str(cm.exception)
		self.assertIn("khong_co", msg)
		self.assertIn("abc", msg)
		self.assertIn("trùng", msg)
		self.assertEqual(self.current(), before)

	def test_sample_file_imports_with_warning(self):
		out = scoring.import_rules(str(scoring.SAMPLE_FILE))
		self.assertEqual(out["imported"], 10)
		self.assertTrue(out["warnings"])
		self.assertEqual(self.current(), {(r["signal"], r["points"]) for r in RULES})

	def test_missing_columns_or_file(self):
		for text in ("a,b\n1,2\n", "signal,points,note\n"):
			with self.assertRaises(ValueError):
				scoring.read_rules_csv(self.write(text))
		with self.assertRaises(ValueError):
			scoring.read_rules_csv("/khong/co/tep.csv")

	def test_no_ai_dependency(self):
		self.assertFalse(hasattr(scoring, "ai_client"))  # chấm điểm là quy tắc thuần, không đi qua AI
