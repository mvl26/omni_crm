"""K1 (docs/bot_v3_plan.md): sửa lỗi của bot hiện tại trước khi làm kịch bản v3.

Câu lệnh reply v4 quyết định theo tin hiện tại; không tin nào rơi vào im lặng ở chế độ Draft; lý do chuyển người
bằng tiếng Việt; tin gửi từ CRM giữ tên người gửi; bộ kiểm thử chấm ý phải có/không được có. AI và gửi đều giả.
"""

import json
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.api import drafts, inbox
from omni_crm.bot import analyze, guards, handoff, reply
from omni_crm.bot.eval import grading, importer
from omni_crm.monitoring import alerts
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_analyze import signals
from omni_crm.tests.test_bot_reply import G_HOURS, G_PRICE, reply_ok
from omni_crm.tests.test_draft import DraftTestCase
from omni_crm.tests.test_inbox import SALE1

OUT = "Omni Outbound Message"
IN = "Omni Inbound Message"
CASES = Path(__file__).resolve().parents[2] / "eval" / "cases" / "k1-kich-ban-v3.jsonl"


class TestPureHelpers(FrappeTestCase):
	def test_current_turn_mode_from_v4(self):
		self.assertEqual(
			[reply.current_turn_mode(v) for v in ("v1", "v3", "v4", "v10", None, "x")],
			[False, False, True, True, False, False],
		)

	def test_current_turn_block_escapes_and_masks(self):
		block = reply.current_turn_block("<b>SĐT 0912345678</b> spd là gì")
		self.assertTrue(block.startswith("<tin_moi>") and block.endswith("</tin_moi>"))
		self.assertNotIn("<b>", block)
		self.assertNotIn("0912345678", block)

	def test_check_reply_output_rejects_empty_reply(self):
		self.assertIsNotNone(reply.check_reply_output({"action": "reply", "reply_text": "  "}))
		self.assertIsNone(reply.check_reply_output({"action": "handoff", "reply_text": ""}))
		self.assertIsNone(reply.check_reply_output({"action": "reply", "reply_text": "Dạ ạ"}))

	def test_claims_check_asks_repair_once(self):
		items = [{"id": "c1", "answer": "Demo SupplyCore và SPD miễn phí, khoảng 30 phút."}]
		chk = reply.make_reply_check(items)
		bad = {"action": "reply", "reply_text": "Dạ, buổi demo kéo dài 30 phút ạ.", "kb_item_ids": ["c1"]}
		self.assertIn("30 phút", chk(bad) or "")
		self.assertIsNone(chk(bad))  # chỉ nhờ sửa một lần; evaluate vẫn chặn như cũ
		good = {
			"action": "reply",
			"reply_text": "Dạ, demo miễn phí, khoảng 30 phút ạ.",
			"kb_item_ids": ["c1"],
		}
		self.assertIsNone(reply.make_reply_check(items)(good))
		self.assertIsNotNone(reply.make_reply_check(items)({"action": "reply", "reply_text": ""}))

	def test_free_question_is_not_price_topic(self):
		self.assertEqual(guards.sensitive_topics("Demo supply core có mất phí k"), [])
		self.assertIn("hoi_gia", guards.sensitive_topics("báo giá cho anh về phần mềm spd"))

	def test_proposal_request_from_current_text(self):
		for text in ("Cho xin đề xuất dịch vụ bằng văn bản", "gửi mình hợp đồng mẫu nhé"):
			self.assertIn("yeu_cau_de_xuat", guards.sensitive_topics(text), text)
		self.assertNotIn("yeu_cau_de_xuat", guards.sensitive_topics("Miyano đề xuất hãng tương đương à?"))

	def test_reason_in_vietnamese(self):
		self.assertEqual(
			handoff.explain_reason("Chốt chặn: handoff_topic:y_dinh_hoi_gia", "handoff_topic:y_dinh_hoi_gia"),
			"Khách hỏi giá hoặc phí",
		)
		self.assertEqual(
			handoff.explain_reason(
				"Chốt chặn: kb_rong; tra_loi_rong", "kb_rong\ntra_loi_rong\ninfo:ngoai_gio"
			),
			"Kho tri thức chưa có thông tin cho câu hỏi này; Bot không soạn được câu trả lời",
		)
		self.assertEqual(handoff.explain_reason("Khách muốn báo giá", "x"), "Khách muốn báo giá")

	def test_unique_sources_by_document(self):
		items = [
			{"id": "c1", "question": "Câu hỏi thường gặp", "group": ""},
			{"id": "c2", "question": "Hệ thống SPD", "group": ""},
			{"id": "c3", "question": "Câu hỏi thường gặp", "group": ""},
		]
		self.assertEqual([k["id"] for k in drafts._unique_sources(items)], ["c1", "c2"])

	def test_content_check_folds_accents_and_alternatives(self):
		res = grading.content_check(
			"Dạ, demo MIỄN PHÍ khoảng 30 phút ạ", ["mien phi", "30 phút|nửa giờ"], ["triệu"]
		)
		self.assertTrue(res["pass"])
		res = grading.content_check("Giá 2 triệu ạ", ["miễn phí"], ["triệu"])
		self.assertEqual((res["missing"], res["forbidden"]), (["miễn phí"], ["triệu"]))

	def test_by_group_rates(self):
		rows = [
			{"expected_group": "A", "expected_action": "Reply", "case_pass": True},
			{"expected_group": "A", "expected_action": "Reply", "case_pass": False},
			{"expected_group": "", "expected_action": "Handoff", "case_pass": True},
			{"expected_group": "A", "expected_action": "Reply", "case_pass": None},
			{"expected_group": "A", "unmeasured": "x", "case_pass": True},
		]
		out = grading.by_group(rows)
		self.assertEqual(out["A"], {"cases": 2, "passed": 1, "pass_rate": 0.5})
		self.assertEqual(out["(Handoff)"]["pass_rate"], 1.0)

	def test_no_recipient_alert(self):
		from omni_crm.monitoring import snapshot

		snap = snapshot.build_snapshot()
		snap["bot"]["handoff_no_recipient_24h"] = 2
		self.assertIn("chuyen_nguoi_khong_nguoi_nhan", [a.kind for a in alerts.evaluate(snap)])
		snap["bot"]["handoff_no_recipient_24h"] = 0
		self.assertNotIn("chuyen_nguoi_khong_nguoi_nhan", [a.kind for a in alerts.evaluate(snap)])


class TestEvalCasesFile(FrappeTestCase):
	def test_k1_cases_valid_and_enough(self):
		rows = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
		self.assertGreaterEqual(len(rows), 40)
		for row in rows:
			self.assertEqual(importer.validate_case(row), [], row["case_id"])
			self.assertEqual(row["source"], "Synthetic")
		ids = [r["case_id"] for r in rows]
		self.assertEqual(len(ids), len(set(ids)))
		self.assertTrue(any(r["expected_fields"].get("_phai_co") for r in rows))


class V4TestCase(DraftTestCase):
	def setUp(self):
		super().setUp()
		old = frappe.db.get_single_value("Omni Settings", "reply_prompt_version")
		self.addCleanup(set_settings, reply_prompt_version=old)
		set_settings(reply_prompt_version="v4")

	def analysed(self, text, intent):
		batch = self.say(text)
		self.fake.add_rule(signals(intent=intent), kind="Analyze")
		with mock.patch("frappe.enqueue"):
			analyze.analyze_batch(batch)
		return batch

	def reply_messages(self):
		calls = [c for c in self.fake.calls if c["kind"] == "Reply"]
		return " ".join(str(m.get("content")) for m in calls[-1]["messages"])


class TestCurrentTurnDecision(V4TestCase):
	def test_v4_sends_current_turn_without_summary(self):
		frappe.db.set_value("CRM Lead", self.lead1, "omni_bot_summary", "Khách muốn báo giá phần mềm SPD")
		frappe.db.commit()
		self.make_draft("spd là gì")
		content = self.reply_messages()
		self.assertIn("<tin_moi>spd là gì</tin_moi>", content)
		self.assertNotIn("<tom_tat_lead>", content)

	def test_conversation_price_intent_does_not_block_knowledge_question(self):
		batch = self.analysed("spd là gì", "Hỏi giá")
		self.fake.add_rule(
			reply_ok(reply_text="Dạ, SPD là mô hình quản lý vật tư tập trung ạ."), kind="Reply"
		)
		self.assertEqual(reply.reply_batch(batch), "drafted")
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual(d.action, "Reply", d.guard_flags)
		self.assertNotIn("y_dinh", d.guard_flags or "")

	def test_v3_keeps_old_behaviour(self):
		set_settings(reply_prompt_version="v3")
		batch = self.analysed("Cho hỏi giờ làm việc", "Khiếu nại")
		self.fake.add_rule(reply_ok(), kind="Reply")
		self.assertEqual(reply.reply_batch(batch), "drafted")
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual(d.action, "Handoff")
		self.assertIn("handoff_topic:y_dinh", d.guard_flags)

	def test_complaint_still_caught_from_current_text(self):
		batch = self.analysed("Tôi muốn khiếu nại vì giao hàng trễ", "Khác")
		self.fake.add_rule(reply_ok(), kind="Reply")
		reply.reply_batch(batch)
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual(d.action, "Handoff")
		self.assertIn("khieu_nai", d.guard_flags)

	def test_price_in_current_text_still_handoff(self):
		batch = self.analysed("báo giá cho anh về phần mềm spd", "Khác")
		self.fake.add_rule(reply_ok(question_group=G_HOURS), kind="Reply")
		reply.reply_batch(batch)
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual(d.action, "Handoff")
		self.assertIn("hoi_gia", d.guard_flags)

	def test_empty_reply_retried_once(self):
		batch = self.say("Cho hỏi giờ làm việc")
		self.fake.add_rule(reply_ok(reply_text=""), kind="Reply")
		with mock.patch.object(reply, "check_reply_output", wraps=reply.check_reply_output) as chk:
			reply.reply_batch(batch)
		self.assertGreaterEqual(chk.call_count, 2)  # lần đầu + lần thử lại
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual(d.action, "Handoff")
		self.assertIn(reply.FLAG_AI_FAILED, d.guard_flags)


class TestNoSilentDrop(V4TestCase):
	def notes(self):
		return frappe.db.count("Notification Log", {"document_name": self.lead1})

	def test_ai_error_becomes_handoff_with_notification(self):
		batch = self.say("Cho hỏi giờ làm việc")
		self.fake.faults.extend(["fatal"])
		self.assertEqual(reply.reply_batch(batch), "drafted")
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual((d.action, d.decision), ("Handoff", "Pending"))
		self.assertIn(reply.FLAG_AI_FAILED, d.guard_flags)
		self.assertIn("AI lỗi", d.handoff_reason)
		self.assertTrue(d.handoff_notified_at or d.handoff_logs)

	def test_unexpected_error_becomes_handoff(self):
		batch = self.say("Cho hỏi giờ làm việc")
		with mock.patch.object(reply, "make_draft", side_effect=RuntimeError("hỏng")):
			self.assertEqual(reply.reply_batch(batch), "error")
		(d,) = self.drafts(batch_id=batch)
		self.assertEqual(d.action, "Handoff")
		self.assertIn(reply.FLAG_SYSTEM_ERROR, d.guard_flags)

	def test_rate_limited_becomes_handoff(self):
		batch = self.say("Cho hỏi giờ làm việc")
		with mock.patch.object(reply, "_recent_drafts", return_value=reply.MAX_DRAFTS_PER_HOUR):
			self.assertEqual(reply.reply_batch(batch), "rate_limited")
		(d,) = self.drafts(batch_id=batch)
		self.assertIn(reply.FLAG_RATE_LIMITED, d.guard_flags)

	def test_shadow_mode_still_silent(self):
		set_settings(bot_reply_mode="Shadow")
		batch = self.say("Cho hỏi giờ làm việc")
		self.fake.faults.extend(["fatal"])
		self.assertEqual(reply.reply_batch(batch), "no_result")
		self.assertEqual(self.drafts(batch_id=batch), [])

	def test_paused_stays_silent(self):
		handoff.pause_lead(self.lead1)
		frappe.db.commit()
		batch = self.say("Cho hỏi giờ làm việc")
		self.assertEqual(reply.reply_batch(batch), "paused")
		self.assertEqual(self.drafts(batch_id=batch), [])


class TestDraftPanelText(V4TestCase):
	def test_thread_shows_vietnamese_reason(self):
		self.make_draft(
			"Cho hỏi giờ làm việc",
			reply_ok(question_group=G_PRICE, kb_item_ids=[]),
		)
		frappe.set_user(SALE1)
		try:
			(item,) = drafts.thread_drafts(self.lead1)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(item["action"], "Handoff")
		self.assertNotIn("handoff_topic", item["handoff_reason"])
		self.assertNotIn("Chốt chặn", item["handoff_reason"])


class TestCrmSentLabel(DraftTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(self._clean_k1)

	def _clean_k1(self):
		frappe.db.rollback()
		frappe.db.delete(OUT, {"external_message_id": ["like", "k1%"]})
		frappe.db.delete(IN, {"dedupe_key": ["like", "k1-echo-%"]})
		frappe.db.commit()

	def _out(self, ext, sent_at, sent_by=None, source="Sale"):
		return (
			frappe.get_doc(
				{
					"doctype": OUT,
					"channel": self.channel.name,
					"identity": self.ident1,
					"lead": self.lead1,
					"text": "Dạ Miyano chào anh ạ",
					"source": source,
					"sent_by": sent_by,
					"status": "Sent",
					"external_message_id": ext,
					"sent_at": sent_at,
				}
			)
			.insert(ignore_permissions=True, ignore_mandatory=True)
			.name
		)

	def _echo(self, ext, at):
		return (
			frappe.get_doc(
				{
					"doctype": IN,
					"dedupe_key": f"k1-echo-{ext}",
					"channel": self.channel.name,
					"channel_type": "Zalo OA",
					"event_type": "oa_echo",
					"external_message_id": ext,
					"identity": self.ident1,
					"lead": self.lead1,
					"received_at": at,
					"text": "Dạ Miyano chào anh ạ",
					"status": "Done",
				}
			)
			.insert(ignore_permissions=True, ignore_mandatory=True)
			.name
		)

	def thread_items(self, ext):
		frappe.set_user(SALE1)
		try:
			data = inbox.get_thread(self.lead1)
		finally:
			frappe.set_user("Administrator")
		names = set(frappe.get_all(OUT, filters={"external_message_id": ext}, pluck="name"))
		names |= set(frappe.get_all(IN, filters={"external_message_id": ext}, pluck="name"))
		return [i for i in data["messages"] if i.get("name") in names]

	def test_crm_sent_message_keeps_sender(self):
		sent = add_to_date(now_datetime(), seconds=-30)
		out = self._out("k1crm001", sent, sent_by=SALE1)
		self._echo("k1crm001", add_to_date(sent, seconds=1))
		frappe.db.commit()
		(item,) = self.thread_items("k1crm001")
		self.assertEqual(item["name"], out)
		self.assertNotEqual(item["sender"], "Nhân viên (ngoài hệ thống)")

	def test_outside_message_keeps_echo_label(self):
		at = add_to_date(now_datetime(), seconds=-20)
		self._out("k1oa001", at)  # Outbound do echo tạo ra: sent_at = thời điểm echo
		echo = self._echo("k1oa001", at)
		frappe.db.commit()
		(item,) = self.thread_items("k1oa001")
		self.assertEqual((item["name"], item["sender"]), (echo, "Nhân viên (ngoài hệ thống)"))
