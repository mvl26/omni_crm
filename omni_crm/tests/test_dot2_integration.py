# ruff: noqa: RUF001
"""D2-09: kịch bản tích hợp bắt buộc Đợt 2 (prompt D2-09 mục 2) còn thiếu so với bộ test sẵn có.

Đi hết chuỗi webhook -> lưu -> worker -> Lead -> bot (FakeAIClient) khi cần; chỉ mock mạng ra ngoài. Bảng ánh xạ
C-01...C-04, B-01...B-05 -> tên test nằm ở docs/nghiem-thu/dot-2.md. Kịch bản đã có test khớp đúng kỳ vọng ở các tệp
khác (C-03, đa số sự cố tổng đài) không lặp lại ở đây.
"""

import json
import threading
from unittest import mock

import frappe
from crm.api.activities import get_activities
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from omni_crm.api import inbox
from omni_crm.bot import ai_client, analyze, reply, scoring
from omni_crm.bot.fake_client import FakeAIClient
from omni_crm.channels.telephony import callbacks, calls
from omni_crm.channels.telephony.base import CallEvent
from omni_crm.channels.zalo.adapter import parse_event
from omni_crm.ingest.gateway import save_inbound
from omni_crm.monitoring import alerts
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests import zalo_helpers
from omni_crm.tests.telephony_helpers import CUSTOMER_E164, TelephonyTestCase, call
from omni_crm.tests.telephony_helpers import cleanup as cleanup_telephony
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_analyze import signals
from omni_crm.tests.test_callbacks import CallbackCase
from omni_crm.tests.test_dot1_integration import SignedZaloCase
from omni_crm.tests.test_scoring import RULES
from omni_crm.tests.zalo_helpers import USER, load, response
from omni_crm.utils import business_hours as bh

INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
CALL = "Omni Call"
TASK = "CRM Task"
BOT_RUN = "Omni Bot Run"
DRAFT = "Omni Bot Draft"
GROUP = "Omni Question Group"
ITEM = "Omni Knowledge Item"
SETTINGS_FIELDS = (
	"bot_analyze_enabled",
	"bot_reply_mode",
	"daily_token_budget",
	"lead_scoring_enabled",
	"hot_lead_threshold",
	"assignment_fallback_users",
)
G_HOURS = "D209 Giờ làm việc"
KB1 = "KB-92001"
KB1_ANSWER = "Miyano làm việc từ 8 giờ đến 17 giờ 30 các ngày trong tuần."
PHONE = "0912345678"
PHONE_E164 = "+84912345678"


class TestC01C02Telephony(CallbackCase):
	def test_c01_answered_call_creates_lead_call_log_and_shows_in_inbox(self):
		self.assertEqual(self.post(call("c01")), 200)
		self.run_worker()
		(row,) = self.inbound()
		self.assertEqual(row.status, "Done")
		lead = row.lead
		self.assertTrue(lead)
		oc = frappe.get_doc(CALL, {"call_id": "td201-c01"})
		self.assertEqual((oc.lead, oc.status, oc.direction), (lead, "Answered", "Inbound"))
		self.assertTrue(frappe.db.exists("CRM Call Log", oc.crm_call_log))
		self.assertEqual(frappe.db.get_value("CRM Call Log", oc.crm_call_log, "reference_docname"), lead)
		# tab Calls của CRM
		self.assertEqual([a["name"] for a in get_activities(lead)[1]], [oc.crm_call_log])
		# Hộp thư Omni: người phụ trách Lead (Administrator đã là chủ qua ánh xạ nhân viên) thấy dòng cuộc gọi
		thread = inbox.get_thread(lead)
		self.assertEqual([m["doctype"] for m in thread["messages"] if m["doctype"] == CALL], [CALL])
		# và Lead nằm trong danh sách hội thoại
		listed = inbox.list_conversations(page_length=500)
		self.assertIn(lead, [c["lead"] for c in listed["rows"]])
		# cuộc gọi nghe máy: không có việc gọi lại
		self.assertEqual(frappe.db.count(TASK, {"name": ["in", [oc.callback_task or ""]]}), 0)

	def test_c02_three_missed_calls_one_task_due_inside_business_hours(self):
		# thứ sáu 30/10/2026, nhỡ lúc 17:20, 17:25, 17:29 (sát giờ đóng cửa 17:30)
		names = [
			self.missed("c02a", ended="2026-10-30 17:20:00"),
			self.missed("c02b", ended="2026-10-30 17:25:00"),
			self.missed("c02c", ended="2026-10-30 17:29:00"),
		]
		tasks = {callbacks.process_missed_call(n) for n in names}
		self.assertEqual(len(tasks), 1, "3 cuộc nhỡ của một khách phải gộp thành 1 việc")
		(task,) = tasks
		self.assertIn("3 lần", frappe.db.get_value(TASK, task, "description"))
		due = get_datetime(frappe.db.get_value(TASK, task, "due_date"))
		self.assertGreater(due, get_datetime("2026-10-30 17:20:00"))
		self.assertTrue(bh.is_business_time(due), f"hạn {due} phải rơi vào giờ làm việc")
		# hạn tính từ lần nhỡ ĐẦU: 17:20 + 30 phút làm việc = 08:20 thứ bảy (tối hôm thứ sáu đã đóng cửa)
		self.assertEqual(due, get_datetime("2026-10-31 08:20:00"))
		self.assertEqual(frappe.db.get_value(TASK, task, "assigned_to"), "Administrator")
		self.assertEqual(frappe.db.count(TASK, {"name": task}), 1)


class TestC04CallThenZalo(TelephonyTestCase):
	"""Khách gọi trước (số 0912345678), sau đó nhắn Zalo gửi form có đúng số đó: cùng một Lead."""

	def setUp(self):
		zalo_helpers.cleanup()
		super().setUp()
		self.addCleanup(self._cleanup_c04)
		self.zalo = zalo_helpers.make_channel(
			f"{zalo_helpers.PREFIX} kenh", enabled=1, zalo_app_id=zalo_helpers.APP_ID, zalo_oa_secret_key="x"
		)
		frappe.db.commit()

	def _cleanup_c04(self):
		frappe.db.rollback()
		leads = frappe.get_all("CRM Lead", filters={"omni_phone_e164": PHONE_E164}, pluck="name")
		cleanup_telephony()
		frappe.db.delete("Omni Identity", {"identity_key": f"Telephony:{PHONE_E164}"})
		frappe.db.commit()
		for lead in leads:  # Lead do cuộc gọi tạo, nếu Zalo cleanup chưa xóa
			frappe.db.delete("Omni Duplicate Candidate", {"lead_a": lead})
			frappe.db.delete("Omni Duplicate Candidate", {"lead_b": lead})
			frappe.db.delete("Comment", {"reference_name": lead})
			frappe.db.delete("Communication", {"reference_name": lead})
		zalo_helpers.cleanup()
		for lead in leads:
			if frappe.db.exists("CRM Lead", lead):
				frappe.delete_doc(
					"CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True
				)
		frappe.db.commit()

	def test_c04_call_then_zalo_form_with_same_phone_is_one_lead(self):
		self.post(call("c04", **{"from": PHONE}))
		self.run_worker(PHONE_E164)
		(row,) = self.inbound()
		call_lead = row.lead
		self.assertTrue(call_lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", call_lead, "omni_phone_e164"), PHONE_E164)
		leads_before = frappe.db.count("CRM Lead", {"omni_phone_e164": PHONE_E164})
		# Zalo: form thông tin (fixture user_submit_info có SĐT 0912345678)
		event = parse_event(self.zalo, load("user_submit_info"))
		with mock.patch("frappe.enqueue"):
			zalo_msg = save_inbound(event)
			frappe.db.after_commit.run()
		frappe.db.commit()
		with (
			mock.patch("frappe.enqueue"),
			mock.patch("omni_crm.flags.sandbox", return_value=True),
			mock.patch(
				"omni_crm.channels.zalo.client.requests.request",
				return_value=response({"error": -201, "message": "loi"}),
			),
		):
			process_identity(f"Zalo OA:{USER}", debounce_seconds=0)
		self.assertEqual(frappe.db.get_value(INBOUND, zalo_msg, "status"), "Done")
		self.assertEqual(frappe.db.get_value(INBOUND, zalo_msg, "lead"), call_lead)
		self.assertEqual(frappe.db.count("CRM Lead", {"omni_phone_e164": PHONE_E164}), leads_before)
		self.assertEqual(leads_before, 1)
		self.assertEqual(
			frappe.db.count("Omni Duplicate Candidate", {"lead_a": call_lead})
			+ frappe.db.count("Omni Duplicate Candidate", {"lead_b": call_lead}),
			0,
			"cùng SĐT mạnh: phải nối thẳng, không cần nghi trùng",
		)
		# cả hai định danh trỏ về một Lead
		self.assertEqual(
			{
				frappe.db.get_value("Omni Identity", {"identity_key": f"Telephony:{PHONE_E164}"}, "lead"),
				frappe.db.get_value("Omni Identity", {"identity_key": f"Zalo OA:{USER}"}, "lead"),
			},
			{call_lead},
		)


class BotE2E(SignedZaloCase):
	"""Webhook Zalo ký thật -> worker -> hậu xử lý -> bot (FakeAIClient). Mọi enqueue được thu lại, job chạy tay."""

	def setUp(self):
		super().setUp()
		self._orig = {f: frappe.db.get_single_value("Omni Settings", f) for f in SETTINGS_FIELDS}
		self.addCleanup(self._restore)
		self.addCleanup(self._clean_bot_data)
		set_settings(
			bot_analyze_enabled=1,
			bot_reply_mode="Off",
			daily_token_budget=10_000_000,
			lead_scoring_enabled=1,
			hot_lead_threshold=60,
			assignment_fallback_users="Administrator",
			score_rules=[dict(r) for r in RULES],
		)
		self.fake = FakeAIClient()
		ai_client.set_client(self.fake)
		self.addCleanup(ai_client.set_client, None)
		self.deliver = mock.patch.object(ai_client, "deliver_now", return_value=True).start()
		mock.patch.object(ai_client, "_sleep").start()
		self.addCleanup(mock.patch.stopall)
		frappe.cache().delete_keys("omni:ai:")
		self.seq = 0

	def _restore(self):
		frappe.db.rollback()
		values = {k: v for k, v in self._orig.items() if v is not None}
		set_settings(**{**values, "score_rules": [], "lead_scoring_enabled": 0})
		frappe.cache().delete_keys("omni:ai:")

	def _clean_bot_data(self):
		frappe.db.rollback()
		batches = [
			b for b in frappe.get_all(INBOUND, filters={"channel": self.channel.name}, pluck="batch_id") if b
		]
		leads = [
			x for x in frappe.get_all(INBOUND, filters={"channel": self.channel.name}, pluck="lead") if x
		]
		frappe.db.delete(BOT_RUN, {"batch_id": ["in", batches or [""]]})
		frappe.db.delete(DRAFT, {"batch_id": ["in", batches or [""]]})
		frappe.db.delete("Notification Log", {"document_name": ["in", leads or [""]]})
		frappe.db.delete(ITEM, {"name": ["like", "KB-92%"]})
		frappe.db.delete(GROUP, {"name": ["like", "D209 %"]})
		frappe.db.commit()

	def turn(self, text: str) -> tuple[str, str, list]:
		"""Khách gửi một tin: webhook ký -> worker. Trả (batch_id, lead, các job đã enqueue)."""
		self.seq += 1
		self.assertEqual(self.post(self.text_event(f"d209-{self.seq}", text=text)), 200)
		with (
			mock.patch("frappe.enqueue") as enq,
			mock.patch(
				"omni_crm.channels.zalo.client.requests.request",
				return_value=response({"error": -201, "message": "loi"}),
			),
		):
			process_identity(self.key(), debounce_seconds=0)
		last = frappe.get_all(
			INBOUND,
			filters={"channel": self.channel.name},
			fields=["batch_id", "lead", "status"],
			order_by="creation desc",
			limit_page_length=1,
		)[0]
		self.assertEqual(last.status, "Done")
		return last.batch_id, last.lead, enq.call_args_list

	@staticmethod
	def job_batches(calls: list, name: str) -> list[str]:
		return [c.kwargs["batch_id"] for c in calls if c.args and name in str(c.args[0])]

	def lead_row(self, lead: str):
		return frappe.db.get_value(
			"CRM Lead",
			lead,
			[
				"omni_need",
				"omni_products",
				"omni_quantity",
				"omni_timeframe",
				"omni_urgency",
				"omni_intent",
				"omni_score",
				"omni_bot_summary",
			],  # fmt: skip
			as_dict=True,
		)

	def hot_logs(self, lead: str) -> list:
		return frappe.get_all(
			"Notification Log",
			filters={"document_name": lead, "subject": ["like", "Lead nóng%"]},
			pluck="name",
		)


ORDER_TEXT = "Mình cần mua 500 hộp găng tay nitrile, cần gấp trong tuần này nhé"


def order_signals(**over) -> dict:
	base = signals(
		need="mua găng tay nitrile",
		products=["găng tay nitrile"],
		quantity="500 hộp",
		timeframe="trong tuần này",
		timeframe_within_30d=True,
		urgency="Cao",
		intent="Đặt hàng",
		evidence={
			"need": "cần mua 500 hộp găng tay nitrile",
			"products": "găng tay nitrile",
			"quantity": "500 hộp",
			"timeframe": "trong tuần này",
			"urgency": "cần gấp",
		},
	)
	base.update(over)
	return base


class TestB01B02AnalyzeAndScore(BotE2E):
	def test_b01_b02_fields_filled_with_evidence_sale_edit_kept_hot_once(self):
		self.fake.add_rule(order_signals(), kind="Analyze")
		batch, lead, jobs = self.turn(ORDER_TEXT)
		self.assertEqual(self.job_batches(jobs, "analyze"), [batch], "worker phải đẩy đúng 1 job phân tích")
		analyze.analyze_batch(batch)
		row = self.lead_row(lead)
		self.assertEqual(
			(row.omni_need, row.omni_products, row.omni_quantity, row.omni_urgency, row.omni_intent),
			("mua găng tay nitrile", "găng tay nitrile", "500 hộp", "Cao", "Đặt hàng"),
		)
		self.assertEqual(
			frappe.db.get_value(BOT_RUN, {"batch_id": batch, "kind": "Analyze"}, "status"), "Success"
		)
		# B-02: điểm theo bảng: đặt hàng 25 + số lượng 15 + trong 30 ngày 15 + gấp 10 = 65, vượt 60: báo đúng 1 lần
		self.assertEqual(row.omni_score, 65)
		self.assertEqual(len(self.hot_logs(lead)), 1)
		# sale sửa số lượng bằng tay qua form Lead
		doc = frappe.get_doc("CRM Lead", lead)
		doc.omni_quantity = "600 hộp (sale chốt)"
		doc.flags.ignore_permissions = True
		doc.flags.ignore_email_validation = True
		with mock.patch("frappe.enqueue"):
			doc.save()
		frappe.db.commit()
		# khách nhắn tiếp, bot lại trích số lượng khác -> KHÔNG ghi đè trường sale đã sửa
		self.fake.rules.clear()
		self.fake.add_rule(
			order_signals(
				quantity="800 hộp",
				evidence={**order_signals()["evidence"], "quantity": "800 hộp"},
			),
			kind="Analyze",
		)
		batch2, lead2, _jobs = self.turn("Thực ra mình lấy 800 hộp, cần gấp trong tuần này nhé")
		self.assertEqual(lead2, lead)
		analyze.analyze_batch(batch2)
		self.assertEqual(self.lead_row(lead).omni_quantity, "600 hộp (sale chốt)")
		self.assertIn(
			"omni_quantity", json.loads(frappe.db.get_value(BOT_RUN, {"batch_id": batch2}, "output_json"))["skipped_manual"]
		)  # fmt: skip
		# điểm vẫn trên ngưỡng nhưng không báo lần hai
		self.assertEqual(len(self.hot_logs(lead)), 1)

	def test_b01_fabricated_value_without_evidence_not_written(self):
		self.fake.add_rule(
			order_signals(quantity="9999 thùng", evidence={**order_signals()["evidence"], "quantity": "9999 thùng"}),
			kind="Analyze",
		)  # fmt: skip
		batch, lead, _j = self.turn(ORDER_TEXT)
		analyze.analyze_batch(batch)
		self.assertFalse(self.lead_row(lead).omni_quantity)
		self.assertEqual(
			self.lead_row(lead).omni_products, "găng tay nitrile"
		)  # trường có căn cứ vẫn được điền


class TestB03Shadow(BotE2E):
	def setUp(self):
		super().setUp()
		frappe.get_doc(
			{"doctype": GROUP, "group_name": G_HOURS, "always_handoff": 0, "description": "Giờ làm việc"}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": ITEM,
				"item_id": KB1,
				"question_group": G_HOURS,
				"question": "Mấy giờ làm việc?",
				"answer": KB1_ANSWER,
				"active": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		set_settings(bot_reply_mode="Shadow")

	def test_b03_shadow_draft_pending_and_nothing_sent_to_customer(self):
		self.fake.add_rule(signals(summary="Khách hỏi giờ làm việc."), kind="Analyze")
		self.fake.add_rule(
			{
				"action": "reply",
				"question_group": G_HOURS,
				"reply_text": "Dạ Miyano làm việc từ 8 giờ đến 17 giờ 30 các ngày trong tuần ạ.",
				"kb_item_ids": [KB1],
				"ask_contact_info": False,
				"confidence": 0.9,
				"handoff_reason": None,
			},
			kind="Reply",
		)
		before = frappe.db.count(OUTBOUND)
		with mock.patch("omni_crm.channels.zalo.client.requests.request") as http:
			batch, lead, _jobs = self.turn("Cho mình hỏi Miyano làm việc mấy giờ?")
			with mock.patch("frappe.enqueue") as enq:
				analyze.analyze_batch(batch)  # sau phân tích, bot trả lời được đẩy ra job nền
			self.assertEqual(self.job_batches(enq.call_args_list, "reply"), [batch])
			self.assertEqual(reply.reply_batch(batch), "drafted")
			(d,) = frappe.get_all(
				DRAFT, filters={"batch_id": batch}, fields=["mode", "action", "decision", "draft_text"]
			)
			self.assertEqual((d.mode, d.action, d.decision), ("Shadow", "Reply", "Pending"))
			self.assertEqual(frappe.db.count(OUTBOUND, {"source": "Bot"}), 0)
			self.assertEqual(frappe.db.count(OUTBOUND), before, "Shadow không được tạo bất kỳ tin ra nào")
			self.assertEqual(frappe.db.count(OUTBOUND, {"lead": lead}), 0)
			self.assertEqual(http.call_count, 0, "không gọi Zalo")


class TestB04BudgetExhausted(BotE2E):
	def test_b04_budget_exhausted_bot_stops_channel_keeps_running(self):
		set_settings(daily_token_budget=1)
		frappe.cache().delete_keys("omni:ai:")
		self.fake.add_rule(order_signals(), kind="Analyze")
		batches = []
		for i in range(3):  # 3 khách nhắn liên tiếp
			batch, lead, jobs = self.turn(f"{ORDER_TEXT} ({i})")
			analyze.analyze_batch(batch)
			batches.append(batch)
			shown = [a for a in get_activities(lead)[0] if a["activity_type"] == "communication"]
			self.assertEqual(len(shown), i + 1, "kênh vẫn chạy: Lead và dòng thời gian vẫn đủ")
		runs = frappe.get_all(BOT_RUN, filters={"batch_id": ["in", batches]}, pluck="status")
		self.assertEqual(sorted(runs), ["Skipped Budget"] * 3)
		self.assertEqual(self.fake.calls, [], "hết hạn mức: không gọi AI")
		self.assertFalse(self.lead_row(lead).omni_quantity)
		self.assertEqual(self.deliver.call_count, 1, "cảnh báo hết hạn mức đúng 1 lần/ngày")
		self.assertEqual(
			{
				r.status
				for r in frappe.get_all(INBOUND, filters={"channel": self.channel.name}, fields=["status"])
			},
			{"Done"},
		)
		# bật lại hạn mức: bot chạy tiếp
		set_settings(daily_token_budget=10_000_000)
		frappe.cache().delete_keys("omni:ai:")
		batch, lead, _j = self.turn(ORDER_TEXT)
		analyze.analyze_batch(batch)
		self.assertEqual(frappe.db.get_value(BOT_RUN, {"batch_id": batch}, "status"), "Success")


class TestB05AIFailsEverywhere(BotE2E):
	def test_b05_ai_fails_100_percent_pipeline_done_breaker_opens_alert_raised(self):
		set_settings(bot_reply_mode="Shadow")
		self.fake.faults = ["fatal"] * 1000  # mọi lần gọi đều lỗi
		batches = []
		for i in range(8):
			batch, lead, _j = self.turn(f"Cho mình hỏi giá găng tay lần {i}")
			analyze.analyze_batch(batch)
			batches.append(batch)
		rows = frappe.get_all(INBOUND, filters={"channel": self.channel.name}, fields=["status", "lead"])
		self.assertEqual(len(rows), 8)
		self.assertEqual({r.status for r in rows}, {"Done"}, "AI lỗi không được làm hỏng lượt xử lý tin")
		self.assertEqual(len({r.lead for r in rows}), 1)
		statuses = [s for s in frappe.get_all(BOT_RUN, filters={"batch_id": ["in", batches]}, pluck="status")]
		self.assertEqual(statuses.count("Error"), ai_client.BREAKER_FAILURES)
		self.assertEqual(set(statuses) - {"Error", "Skipped Switch"}, set())
		self.assertTrue(ai_client.breaker_open(), "ngắt mạch phải mở")
		self.assertEqual(frappe.db.count(DRAFT, {"lead": lead}), 0, "AI lỗi: không có nháp")
		self.assertEqual(frappe.db.count(OUTBOUND, {"lead": lead}), 0)
		self.assertFalse(self.lead_row(lead).omni_need)
		# cảnh báo bật từ số liệu giám sát thật. `ai_loi_nhieu` đo tỉ lệ trong 24 giờ nên bị pha loãng khi đã có
		# nhiều lần gọi tốt trước đó (lượt bị ngắt mạch không tính là lỗi): phải có cảnh báo riêng cho ngắt mạch
		snap = build_snapshot()
		self.assertTrue(snap["bot"]["breaker_open"])
		self.assertIn("ai_ngat_mach", {a.kind for a in alerts.evaluate(snap)})
		# điểm Lead vẫn chấm được khi AI lỗi (không phụ thuộc AI)
		self.assertIsNotNone(scoring.run_scoring(lead))


class TestBreakerAlertRule(FrappeTestCase):
	def snap(self, **bot) -> dict:
		return {
			"queue": {"new": 0, "manual": 0, "oldest_new_age_seconds": None},
			"timing": {"count": 0, "p95": None},
			"outbound": {"failed_last_hour": 0},
			"duplicates": {"open": 0},
			"channels": [],
			"switches": {"sandbox_mode": 0},
			"bot": bot,
		}

	def test_breaker_open_alerts_even_when_error_ratio_is_diluted(self):
		diluted = {"usage_pct": 1.0, "runs_24h": 700, "errors_24h": 5, "breaker_open": True}
		kinds = {a.kind for a in alerts.evaluate(self.snap(**diluted))}
		self.assertEqual(kinds, {"ai_ngat_mach"})
		self.assertEqual(alerts.evaluate(self.snap(**{**diluted, "breaker_open": False})), [])
		self.assertEqual(
			alerts.evaluate(self.snap(usage_pct=1.0, runs_24h=3, errors_24h=0)), []
		)  # khóa vắng: bình thường


class TestConcurrentCallCreate(TelephonyTestCase):
	"""Tải cuộc gọi D2-09: hai worker tạo Omni Call cho hai call_id KHÁC nhau cùng lúc không được bế tắc (deadlock).

	`SELECT ... FOR UPDATE` trên khóa duy nhất chưa tồn tại giữ khóa khoảng (gap lock) tương thích nhau, rồi cả hai
	INSERT chờ nhau: InnoDB báo lỗi 1213 cho một bên (đo được ~8% tin cuộc gọi phải thử lại ở tải 10 cuộc/giây).
	"""

	def _event(self, cid: str) -> CallEvent:
		return CallEvent(
			call_id=f"td201-{cid}",
			direction="Inbound",
			from_number=CUSTOMER_E164,
			to_number="+842873001234",
			status="Ended",
		)

	def test_two_workers_creating_different_calls_do_not_deadlock(self):
		site, sites_path = frappe.local.site, frappe.local.sites_path
		channel_name = self.channel.name
		barrier = threading.Barrier(2)
		real = calls._locked_name
		errors: list[BaseException] = []

		def hooked(call_id: str):
			result = real(call_id)  # đã giữ khóa khoảng nếu hàng chưa có
			try:
				barrier.wait(timeout=5)  # đảm bảo cả hai đã tra xong trước khi INSERT
			except threading.BrokenBarrierError:
				pass
			return result

		def other_worker():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			try:
				frappe.set_user("Administrator")
				calls.get_or_create(frappe.get_doc("Omni Channel", channel_name), self._event("dl2"))
				frappe.db.commit()
			except BaseException as e:
				errors.append(e)
				frappe.db.rollback()
			finally:
				frappe.destroy()

		with mock.patch.object(calls, "_locked_name", side_effect=hooked):
			thread = threading.Thread(target=other_worker)
			thread.start()
			try:
				calls.get_or_create(self.channel, self._event("dl1"))
				frappe.db.commit()
			except BaseException as e:
				errors.append(e)
				frappe.db.rollback()
			thread.join(timeout=60)
		self.assertEqual(errors, [], f"bế tắc khi tạo Omni Call song song: {errors}")
		self.assertEqual(frappe.db.count(CALL, {"call_id": ["in", ["td201-dl1", "td201-dl2"]]}), 2)

	def test_existing_call_is_locked_and_returned_and_race_on_insert_is_handled(self):
		ev = self._event("dl3")
		first = calls.get_or_create(self.channel, ev)
		self.assertEqual(calls.get_or_create(self.channel, ev), first)  # đã có: khóa dòng và trả lại
		# hàng chưa thấy trong ảnh chụp (yêu cầu kia vừa commit) -> INSERT đụng khóa duy nhất -> khóa và trả hàng đã có
		real_get = frappe.db.get_value
		hidden = {"n": 0}

		def get_value(doctype, filters=None, *a, **kw):
			if doctype == CALL and kw.get("for_update") is None and not hidden["n"]:
				hidden["n"] += 1
				return None
			return real_get(doctype, filters, *a, **kw)

		with mock.patch.object(frappe.db, "get_value", side_effect=get_value):
			self.assertEqual(calls.get_or_create(self.channel, ev), first)
		self.assertEqual(frappe.db.count(CALL, {"call_id": "td201-dl3"}), 1)
