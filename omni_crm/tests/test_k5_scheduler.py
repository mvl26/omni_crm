"""K5 (bot kịch bản v3): bộ hẹn giờ - đặt/hủy lịch, nhận dòng một lần, điều kiện dừng, khung giờ C.5, chọn kênh 4.2.

Không gọi Zalo/AI thật: sandbox bật, `frappe.enqueue` giả; `tasks` (K3) là module giả.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, time, timedelta
from unittest import mock

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

import omni_crm.script as script_pkg
import omni_crm.script.flows as flows_pkg
from omni_crm.script import scheduler, send, state, timeutil
from omni_crm.tests.test_k2_core import K2Case

SCHED = "Omni Scheduled Message"
NODE = "Omni Script Node"
OUT = "Omni Outbound Message"
LEAD = "CRM Lead"
IDENT = "Omni Identity"
SETTINGS = ("script_fast_timers_for_test", "script_zns_enabled", "script_zns_template_id")
# 17/11/2026 là thứ Ba: 19 = thứ Năm, 20 = thứ Sáu, 21-22 = cuối tuần, 23 = thứ Hai (đặt làm ngày lễ)
HOLIDAY = date(2026, 11, 23)
CFG = ({d: (time(8, 0), time(17, 30)) for d in range(5)}, frozenset({HOLIDAY}))
THU_10 = datetime(2026, 11, 19, 10, 0)
THU_18 = datetime(2026, 11, 19, 18, 0)
SUN_10 = datetime(2026, 11, 22, 10, 0)
HOLIDAY_10 = datetime(2026, 11, 23, 10, 0)
SEED_CODES = (
	"NL_TAILIEU",
	"NL_TAILIEU_VUAROI",
	"NL_BAOGIA",
	"EMAIL_NL_TAILIEU",
	"NHAC3N_B12",
	"NHAC3N_B4",
	"DEMO_NHAC_1NGAY",
	"DEMO_NHAC_1GIO",
	"DEMO_VANGMAT",
	"DEMO_HINHTHUC",
	"DEMO_HT_ONLINE",
	"DEMO_KHUNGGIO",
	"DEMO_GIOKHAC",
	"DEMO_GIOBAN",
	"DEMO_SONGUOI",
	"DEMO_HOI_EMAIL",
	"DEMO_XACNHAN",
	"DEMO_XACNHAN_THUCONG",
	"DEMO_HUY",
	"DEMO_HUY_BAN",
	"DEMO_HUY_CAMON",
	"MENU_VATTU",
	"DEMO_XACNHAN_KHONGEMAIL",
)
SEED_FILES = ("nodes.json", "nodes_k5.json")


def seed_nodes(codes=SEED_CODES) -> list[str]:
	"""Tạo các ô kịch bản thật từ seed nếu site test chưa có; trả danh sách mã vừa tạo (để dọn)."""
	seed = {}
	for filename in SEED_FILES:
		with open(frappe.get_app_path("omni_crm", "script", "seed", filename), encoding="utf-8") as f:
			seed.update({n["code"]: n for n in json.load(f)})
	made = []
	for code in codes:
		if frappe.db.exists(NODE, code):
			continue
		n = seed[code]
		frappe.get_doc({"doctype": NODE, **{k: v for k, v in n.items() if k != "notes"}}).insert(
			ignore_permissions=True
		)
		made.append(code)
	return made


class FakeTasks(types.ModuleType):
	"""Thay `omni_crm.script.tasks` (K3) trong test."""

	def __init__(self):
		super().__init__("omni_crm.script.tasks")
		self.created: list[dict] = []
		self.notes: list[tuple] = []
		self.recipients: dict = {}

	def append_note(self, task, text):
		self.notes.append((task, text))

	def create_task(self, lead, service, title, description, due=None, priority="Medium"):
		self.created.append({"lead": lead, "service": service, "title": title, "description": description})
		return f"TASK-{len(self.created)}"

	def recipient(self, service, role="primary"):
		return self.recipients.get(service)


class K5Case(K2Case):
	def setUp(self):
		super().setUp()
		self._k5_settings = {f: frappe.db.get_single_value("Omni Settings", f) for f in SETTINGS}
		self._k5_nodes = seed_nodes()
		self.addCleanup(self._k5_cleanup)
		for f in SETTINGS:
			frappe.db.set_single_value("Omni Settings", f, None if f.endswith("_id") else 0)
		frappe.db.set_value("Omni Channel", self.channel.name, "zalo_cs_free_hours", 48)
		frappe.db.set_value(
			LEAD, self.lead1, {"email": "td106mai@example.com", "omni_services": "SUPPLYCORE"}
		)
		self.set_switch(enabled=1)
		self.tasks = FakeTasks()
		self.fake_module(script_pkg, "tasks", self.tasks)
		self.alerts = self.start(mock.patch("omni_crm.monitoring.alerts.deliver_now", return_value=True))
		self.start(mock.patch.object(timeutil, "load_config", return_value=CFG))
		self.start(mock.patch("frappe.enqueue"))
		frappe.db.commit()

	def fake_module(self, package, name: str, module):
		"""Thay module con (cả trong sys.modules lẫn thuộc tính của gói, vì `from gói import x` đọc thuộc tính trước)."""
		self.start(mock.patch.dict(sys.modules, {f"{package.__name__}.{name}": module}))
		self.start(mock.patch.object(package, name, module, create=True))

	def start(self, patcher):
		value = patcher.start()
		self.addCleanup(patcher.stop)
		return value

	def _k5_cleanup(self):
		frappe.db.rollback()
		leads = [self.lead1, self.lead2]
		frappe.db.delete(SCHED, {"lead": ["in", leads]})
		frappe.db.delete("Event", {"reference_doctype": LEAD, "reference_docname": ["in", leads]})
		frappe.db.delete("CRM Task", {"reference_doctype": LEAD, "reference_docname": ["in", leads]})
		for code in self._k5_nodes:
			frappe.delete_doc(NODE, code, force=True, ignore_permissions=True)
		for f, v in self._k5_settings.items():
			frappe.db.set_single_value("Omni Settings", f, v)
		frappe.db.commit()

	def now_is(self, at: datetime):
		self.start(mock.patch.object(scheduler, "_now", return_value=at))

	def window_open(self):
		"""Coi như đang trong khung 09:00-16:30 (test chạy được vào bất kỳ giờ nào)."""
		self.start(mock.patch.object(timeutil, "in_proactive_window", return_value=True))

	def add(self, kind="followup_docs", due=None, lead=None, identity=None, node=None, payload=None):
		name = scheduler.schedule(
			kind,
			identity or self.ident1,
			lead or self.lead1,
			due or add_to_date(scheduler._now(), minutes=-1),
			node,
			payload,
		)
		frappe.db.commit()
		return name

	def row(self, name):
		return frappe.db.get_value(
			SCHED, name, ["status", "reason", "due_at", "node", "sent_at"], as_dict=True
		)

	def journey(self, lead=None):
		return frappe.db.get_value(LEAD, lead or self.lead1, "omni_journey_status")

	def chat_expired(self, identity=None):
		frappe.db.set_value(
			IDENT, identity or self.ident1, "last_inbound_at", add_to_date(now_datetime(), hours=-100)
		)
		frappe.db.commit()


class TestSchedule(K5Case):
	def test_dedupe_on_double_schedule(self):
		due = add_to_date(now_datetime(), days=1)
		a = scheduler.schedule("followup_docs", self.ident1, self.lead1, due, "NL_TAILIEU")
		b = scheduler.schedule("followup_docs", self.ident1, self.lead1, due, "NL_TAILIEU")
		self.assertTrue(a)
		self.assertEqual(a, b)
		self.assertEqual(frappe.db.count(SCHED, {"lead": self.lead1}), 1)
		key = frappe.db.get_value(SCHED, a, "dedupe_key")
		self.assertEqual(key, f"followup_docs:{self.lead1}:{get_datetime(due).date().isoformat()}")
		self.assertIsNone(scheduler.schedule("khong_co_loai", self.ident1, self.lead1, due, "X"))

	def test_sent_milestone_not_scheduled_again(self):
		self.window_open()
		name = self.add()
		self.assertEqual(scheduler.run_due().get("Sent"), 1)
		again = scheduler.schedule(
			"followup_docs", self.ident1, self.lead1, frappe.db.get_value(SCHED, name, "due_at"), "NL_TAILIEU"
		)
		self.assertIsNone(again)
		self.assertEqual(len(self.outs()), 1)

	def test_cancel_for_and_reopen(self):
		due = add_to_date(now_datetime(), days=1)
		a = self.add(due=due)
		b = self.add("demo_remind_1h", due=due, payload={"key": "d1"})
		self.assertEqual(scheduler.cancel_for(self.lead1, ["followup_docs"]), 1)
		self.assertEqual(self.row(a).status, "Cancelled")
		self.assertEqual(self.row(b).status, "Pending")
		self.assertEqual(scheduler.cancel_for(self.lead1), 1)
		self.assertEqual(scheduler.cancel_for(None), 0)
		self.assertEqual(self.add(due=due), a)  # đã hủy thì đặt lại dùng lại dòng cũ
		self.assertEqual(self.row(a).status, "Pending")

	def test_customer_reply_cancels_pending(self):
		due = add_to_date(now_datetime(), days=1)
		follow = self.add(due=due)
		silence = self.add("silence_3d", due=due, payload={"key": "k"})
		remind = self.add("demo_remind_1d", due=due, payload={"key": "d"})
		self.assertEqual(scheduler.on_inbound(self.ident1, self.lead1), 2)
		self.assertEqual(self.row(follow).status, "Cancelled")
		self.assertEqual(self.row(silence).status, "Cancelled")
		self.assertEqual(self.row(remind).status, "Pending")  # nhắc lịch demo không bị hủy

	def test_reply_after_scheduling_blocks_at_send_time(self):
		self.window_open()
		name = self.add()
		frappe.db.set_value(
			SCHED, name, "creation", add_to_date(now_datetime(), hours=-5), update_modified=False
		)
		frappe.db.commit()  # tin cuối của khách (10 phút trước) mới hơn lúc đặt lịch
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Cancelled")
		self.assertEqual(self.outs(), [])

	def test_on_docs_sent_next_working_day_in_window(self):
		self.now_is(
			datetime(2026, 11, 20, 17, 0)
		)  # thứ Sáu 17:00 -> thứ Ba 24/11 (thứ Hai là ngày lễ), 16:30
		self.chat_expired()
		frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", datetime(2026, 11, 24, 12, 0))
		name = scheduler.on_docs_sent(self.lead1)
		row = frappe.db.get_value(SCHED, name, ["due_at", "node", "kind"], as_dict=True)
		self.assertEqual(get_datetime(row.due_at), datetime(2026, 11, 24, 16, 30))
		self.assertEqual((row.kind, row.node), ("followup_docs", "NL_TAILIEU"))
		quote = scheduler.on_quote_sent(self.lead1)
		self.assertEqual(
			get_datetime(frappe.db.get_value(SCHED, quote, "due_at")), datetime(2026, 11, 25, 16, 30)
		)

	def test_on_docs_sent_only_when_bot_enabled(self):
		self.set_switch(enabled=0, tests="")
		self.assertIsNone(scheduler.on_docs_sent(self.lead1))
		self.assertEqual(frappe.db.count(SCHED, {"lead": self.lead1}), 0)


class TestRunDue(K5Case):
	def test_window_open_sends_chat(self):
		self.window_open()
		name = self.add()
		self.assertEqual(scheduler.run_due(), {"Sent": 1})
		row = self.row(name)
		self.assertEqual((row.status, row.reason), ("Sent", "Chat"))
		outs = self.outs()
		self.assertEqual(len(outs), 1)
		self.assertEqual(outs[0].script_node, "NL_TAILIEU")
		self.assertEqual(outs[0].source, "Bot")
		self.assertIn("td106mai@example.com", outs[0].text)
		self.assertIn("anh/chị Mai ạ", outs[0].text)  # {ten} chỉ là tên gọi (từ cuối của "TD106 Mai")
		self.assertNotIn("{", outs[0].text)
		self.assertEqual(len(json.loads(outs[0].buttons)), 3)
		self.assertEqual(frappe.db.get_value(IDENT, self.ident1, "script_node"), "NL_TAILIEU")
		self.assertEqual(scheduler.run_due(), {})  # lượt sau không gửi lại
		self.assertEqual(len(self.outs()), 1)

	def test_chat_closed_with_email_creates_staff_task(self):
		self.window_open()
		self.chat_expired()
		name = self.add()
		scheduler.run_due()
		row = self.row(name)
		self.assertEqual(row.status, "Sent")
		self.assertIn("Email", row.reason)
		self.assertEqual(self.outs(), [])
		self.assertEqual(len(self.tasks.created), 1)
		task = self.tasks.created[0]
		self.assertEqual((task["lead"], task["service"]), (self.lead1, "SUPPLYCORE"))
		self.assertIn("phần mềm SupplyCore", task["description"])  # mẫu EMAIL_NL_TAILIEU đã điền biến
		self.assertNotIn("{", task["description"])

	def test_no_channel_skipped_and_nurture(self):
		self.window_open()
		self.chat_expired()
		frappe.db.set_value(LEAD, self.lead1, {"email": None, "mobile_no": None})
		name = self.add()
		self.assertEqual(scheduler.run_due(), {"Skipped": 1})
		row = self.row(name)
		self.assertEqual((row.status, row.reason), ("Skipped", "Hết kênh liên hệ"))
		self.assertEqual(self.journey(), "Chăm sóc lại")
		self.assertEqual(self.outs(), [])
		self.assertEqual(self.tasks.created, [])

	def test_zns_only_creates_nothing(self):
		self.window_open()
		self.chat_expired()
		frappe.db.set_value(LEAD, self.lead1, {"email": None, "mobile_no": "+84912345678"})
		frappe.db.set_single_value("Omni Settings", "script_zns_enabled", 1)
		frappe.db.set_single_value("Omni Settings", "script_zns_template_id", "tpl-thu")
		name = self.add()
		with mock.patch("requests.request", side_effect=AssertionError("không được gọi HTTP")):
			scheduler.run_due()
		row = self.row(name)
		self.assertEqual(row.status, "Skipped")
		self.assertIn("ZNS", row.reason)
		self.assertEqual((self.outs(), self.tasks.created), ([], []))

	def test_nothing_sent_outside_window_or_non_working_day(self):
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		for at, expect in (
			(THU_18, datetime(2026, 11, 20, 9, 0)),  # sau 16:30 -> 09:00 hôm sau
			(SUN_10, datetime(2026, 11, 24, 9, 0)),  # Chủ nhật -> qua ngày lễ thứ Hai -> thứ Ba
			(HOLIDAY_10, datetime(2026, 11, 24, 9, 0)),  # ngày lễ
			(datetime(2026, 11, 19, 8, 30), datetime(2026, 11, 19, 9, 0)),  # trước 09:00
		):
			with mock.patch.object(scheduler, "_now", return_value=at):
				frappe.db.delete(SCHED, {"lead": self.lead1})
				frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", at - timedelta(hours=1))
				name = self.add(due=at - timedelta(minutes=5), payload={"key": at.isoformat()})
				frappe.db.set_value(SCHED, name, "creation", at, update_modified=False)
				frappe.db.commit()
				scheduler.run_due()
				row = self.row(name)
				self.assertEqual((row.status, get_datetime(row.due_at)), ("Pending", expect), at)
		sender.assert_not_called()
		self.assertEqual(self.tasks.created, [])

	def test_demo_1h_reminder_sent_outside_window(self):
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		self.now_is(THU_18)
		demo_at = datetime(2026, 11, 19, 19, 0)
		frappe.db.set_value(LEAD, self.lead1, {"omni_demo_at": demo_at, "omni_demo_mode": "Online qua Teams"})
		frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", THU_18 - timedelta(hours=2))
		payload = {"key": "d", "demo_at": demo_at.isoformat(sep=" ")}
		hour = self.add("demo_remind_1h", due=THU_18, payload=payload)
		other = self.add(
			"followup_quote", due=THU_18, payload={"key": "q"}
		)  # loại khác vẫn phải chờ khung giờ
		frappe.db.set_value(SCHED, other, "creation", THU_18, update_modified=False)
		frappe.db.commit()
		scheduler.run_due()
		self.assertEqual(self.row(hour).status, "Sent")
		self.assertEqual(self.row(other).status, "Pending")
		self.assertEqual(sender.call_count, 1)
		self.assertEqual(sender.call_args.args[1], "DEMO_NHAC_1GIO")
		self.assertEqual(sender.call_args.kwargs["purpose"], "followup")

	def test_two_overlapping_runs_send_once(self):
		self.window_open()
		name = self.add()
		real = scheduler._process
		nested = []

		def process_and_overlap(row_name):
			if not nested:
				nested.append(scheduler.run_due())  # lượt thứ hai chạy chồng khi lượt đầu đang xử lý
			return real(row_name)

		with mock.patch.object(scheduler, "_process", side_effect=process_and_overlap):
			first = scheduler.run_due()
		self.assertEqual(first, {"Sent": 1})
		self.assertEqual(nested, [{}])
		self.assertEqual(len(self.outs()), 1)
		self.assertFalse(scheduler._claim(name))  # dòng đã nhận thì không nhận lại được

	def test_claim_is_single_winner(self):
		name = self.add(due=add_to_date(now_datetime(), minutes=-1))
		self.assertTrue(scheduler._claim(name))
		self.assertFalse(scheduler._claim(name))

	def test_do_not_contact_blocks(self):
		self.window_open()
		frappe.db.set_value(LEAD, self.lead1, "omni_do_not_contact", 1)
		name = self.add()
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Skipped")
		self.assertEqual((self.outs(), self.tasks.created), ([], []))

	def test_paused_bot_blocks(self):
		self.window_open()
		frappe.db.set_value(IDENT, self.ident1, "bot_paused_until", add_to_date(now_datetime(), hours=2))
		name = self.add()
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Skipped")
		self.assertEqual(self.outs(), [])

	def test_no_followup_flag_and_customer_block(self):
		self.window_open()
		frappe.db.set_value(LEAD, self.lead1, "omni_no_bot_followup", 1)
		a = self.add()
		scheduler.run_due()
		self.assertEqual(self.row(a).status, "Skipped")
		frappe.db.set_value(LEAD, self.lead1, {"omni_no_bot_followup": 0, "converted": 1})
		b = self.add("followup_quote", payload={"key": "q"})
		scheduler.run_due()
		self.assertEqual(self.row(b).status, "Skipped")
		self.assertEqual(self.outs(), [])

	def test_noshow_only_when_staff_marked_absent(self):
		self.window_open()
		demo_at = add_to_date(now_datetime(), minutes=-20).replace(microsecond=0)
		frappe.db.set_value(LEAD, self.lead1, {"omni_demo_at": demo_at, "omni_demo_mode": "Online qua Teams"})
		name = self.add("demo_noshow", payload={"key": "n", "demo_at": str(demo_at)})
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Pending")  # chưa đánh dấu vắng: chờ
		self.assertEqual(self.outs(), [])
		frappe.db.set_value(LEAD, self.lead1, "omni_demo_result", "Khách vắng")
		frappe.db.commit()
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Sent")
		self.assertEqual([o.script_node for o in self.outs()], ["DEMO_VANGMAT"])


class TestPostdemoHooks(K5Case):
	"""Giao ước với K6: `postdemo.send_lost_3m`, `postdemo.sales_allowed`, payload làm biến, giữ `lost_3m`."""

	def fake_postdemo(self, allowed=True):
		mod = types.ModuleType("omni_crm.script.flows.postdemo")
		mod.send_lost_3m = mock.Mock(return_value="TASK-3M")
		mod.sales_allowed = mock.Mock(return_value=allowed)
		self.fake_module(flows_pkg, "postdemo", mod)
		return mod

	def test_lost_3m_goes_to_postdemo(self):
		self.window_open()
		mod = self.fake_postdemo()
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		name = self.add("lost_3m")
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Sent")
		mod.send_lost_3m.assert_called_once_with(self.lead1)
		sender.assert_not_called()

	def test_lost_3m_without_zalo_identity_and_no_task(self):
		self.window_open()
		mod = self.fake_postdemo()
		name = scheduler.schedule("lost_3m", None, self.lead1, add_to_date(now_datetime(), minutes=-1), None)
		frappe.db.commit()
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Sent")  # không cần định danh Zalo
		mod.send_lost_3m.return_value = None  # postdemo không tạo việc (Lead không còn phù hợp)
		other = scheduler.schedule(
			"lost_3m", None, self.lead1, add_to_date(now_datetime(), minutes=-1), None, {"key": "lan2"}
		)
		frappe.db.commit()
		scheduler.run_due()
		self.assertEqual(self.row(other).status, "Skipped")

	def test_sales_guard_blocks_sales_kinds_only(self):
		self.window_open()
		mod = self.fake_postdemo(allowed=False)
		self.set_switch(
			enabled=1, tests=self.ident1
		)  # ô nhắc lịch đang "Cần duyệt": chỉ gửi cho danh sách thử
		demo_at = add_to_date(now_datetime(), hours=1).replace(microsecond=0)
		frappe.db.set_value(LEAD, self.lead1, {"omni_demo_at": demo_at, "omni_demo_mode": "Online qua Teams"})
		follow = self.add()
		remind = self.add("demo_remind_1h", payload={"key": "d", "demo_at": str(demo_at)})
		scheduler.run_due()
		self.assertEqual(self.row(follow).status, "Skipped")
		self.assertEqual(self.row(remind).status, "Sent")
		mod.sales_allowed.assert_called_with(self.lead1)
		self.assertEqual([o.script_node for o in self.outs()], ["DEMO_NHAC_1GIO"])

	def test_payload_passed_as_variables(self):
		self.window_open()
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		self.add("followup_docs", payload={"key": "p", "hom_nay_hom_qua": "hôm qua", "tai_lieu": "catalogue"})
		scheduler.run_due()
		values = sender.call_args.args[2]
		self.assertEqual((values["hom_nay_hom_qua"], values["tai_lieu"]), ("hôm qua", "catalogue"))

	def test_cancel_all_keeps_lost_3m_unless_do_not_contact(self):
		due = add_to_date(now_datetime(), days=1)
		lost = self.add("lost_3m", due=due)
		follow = self.add(due=due)
		self.assertEqual(scheduler.cancel_for(self.lead1), 1)
		self.assertEqual((self.row(follow).status, self.row(lost).status), ("Cancelled", "Pending"))
		frappe.db.set_value(LEAD, self.lead1, "omni_do_not_contact", 1)
		self.assertEqual(scheduler.cancel_for(self.lead1), 1)
		self.assertEqual(self.row(lost).status, "Cancelled")
		again = self.add("lost_3m", due=due)
		self.assertEqual(scheduler.cancel_for(self.lead1, ["lost_3m"]), 1)
		self.assertEqual(self.row(again).status, "Cancelled")


class TestReviewFixes(K5Case):
	"""Các lỗi do lượt rà soát độc lập nêu (mục 3, 4, 5, 6, 9, 10)."""

	def _demo_row(self, kind, demo_at, now):
		self.now_is(now)
		frappe.db.set_value(LEAD, self.lead1, {"omni_demo_at": demo_at, "omni_demo_mode": "Online qua Teams"})
		frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", now - timedelta(hours=1))
		return self.add(kind, due=now, payload={"key": kind, "demo_at": demo_at.isoformat(sep=" ")})

	def test_stale_demo_reminders_are_cancelled(self):
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		late_hour = self._demo_row("demo_remind_1h", datetime(2026, 11, 19, 9, 30), THU_10)  # demo đã bắt đầu
		late_day = self._demo_row("demo_remind_1d", datetime(2026, 11, 19, 12, 0), THU_10)  # còn 2 giờ
		scheduler.run_due()
		self.assertEqual(self.row(late_hour).status, "Cancelled")
		self.assertEqual(self.row(late_day).status, "Cancelled")
		sender.assert_not_called()

	def test_row_due_at_window_end_is_sent_when_slightly_late(self):
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		due = datetime(2026, 11, 19, 16, 30)
		for late, expect in ((10, "Sent"), (25, "Pending")):
			now = due + timedelta(minutes=late)
			with mock.patch.object(scheduler, "_now", return_value=now):
				frappe.db.delete(SCHED, {"lead": self.lead1})
				frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", due - timedelta(hours=1))
				name = self.add(due=due, payload={"key": str(late)})
				frappe.db.set_value(SCHED, name, "creation", due, update_modified=False)
				frappe.db.commit()
				scheduler.run_due()
				self.assertEqual(self.row(name).status, expect, late)
		self.assertEqual(sender.call_count, 1)
		self.assertEqual(get_datetime(self.row(name).due_at), datetime(2026, 11, 20, 9, 0))

	def test_saturday_is_never_proactive_even_if_configured(self):
		with_saturday = ({**CFG[0], 5: (time(8, 0), time(12, 0))}, frozenset())
		self.start(mock.patch.object(timeutil, "load_config", return_value=with_saturday))
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		sat = datetime(2026, 11, 21, 10, 0)
		self.now_is(sat)
		frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", sat - timedelta(hours=1))
		name = self.add(due=sat - timedelta(minutes=5))
		frappe.db.set_value(SCHED, name, "creation", sat, update_modified=False)
		frappe.db.commit()
		scheduler.run_due()
		row = self.row(name)
		self.assertEqual((row.status, get_datetime(row.due_at)), ("Pending", datetime(2026, 11, 23, 9, 0)))
		sender.assert_not_called()
		# thứ Sáu 10:00 + 1 ngày làm việc = thứ Hai (không rơi vào thứ Bảy)
		self.assertEqual(
			scheduler.working_days_after(datetime(2026, 11, 20, 10, 0), 1, with_saturday),
			datetime(2026, 11, 23, 10, 0),
		)

	def test_chat_not_queued_falls_back_to_staff_task(self):
		self.window_open()
		self.start(mock.patch.object(send, "send_node", return_value=None))
		name = self.add()
		scheduler.run_due()
		row = self.row(name)
		self.assertEqual(row.status, "Sent")
		self.assertIn("Đã giao nhân viên", row.reason)
		self.assertEqual(len(self.tasks.created), 1)
		self.alerts.assert_not_called()

	def test_chat_only_kind_not_queued_is_failed_with_alert(self):
		self.start(mock.patch.object(send, "send_node", return_value=None))
		name = self._demo_row("demo_remind_1h", datetime(2026, 11, 19, 11, 0), THU_10)
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Failed")
		self.assertEqual(self.tasks.created, [])
		self.alerts.assert_called_once()

	def test_error_after_message_committed_keeps_sent(self):
		self.window_open()
		self.start(mock.patch.object(state, "set_node", side_effect=RuntimeError("lỗi sau khi gửi")))
		name = self.add()
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Sent")
		self.assertEqual(len(self.outs()), 1)

	def test_processing_error_logs_plain_traceback_and_alerts(self):
		self.window_open()
		name = self.add()
		with (
			mock.patch.object(scheduler, "_process", side_effect=RuntimeError("hỏng")),
			mock.patch.object(frappe, "log_error") as log,
		):
			scheduler.run_due()
		self.assertEqual(self.row(name).status, "Failed")
		self.assertIn("RuntimeError", log.call_args.kwargs["message"])
		self.assertNotIn("td106mai@example.com", log.call_args.kwargs["message"])
		self.alerts.assert_called_once()

	def test_reopened_row_compares_reply_against_rearm_time(self):
		self.window_open()
		name = self.add()
		frappe.db.set_value(
			SCHED, name, "creation", add_to_date(now_datetime(), hours=-5), update_modified=False
		)
		scheduler.cancel_for(self.lead1)
		self.assertEqual(self.add(), name)  # mở lại: tin khách 10 phút trước là TRƯỚC lúc mở lại
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Sent")

	def test_no_channel_does_not_downgrade_advanced_lead(self):
		self.window_open()
		self.chat_expired()
		frappe.db.set_value(
			LEAD, self.lead1, {"email": None, "mobile_no": None, "omni_journey_status": "Đã demo"}
		)
		name = self.add("followup_quote", payload={"key": "q"})
		scheduler.run_due()
		self.assertEqual(self.row(name).status, "Skipped")
		self.assertEqual(self.journey(), "Đã demo")


class TestFastTimers(K5Case):
	def test_fast_only_for_test_identities(self):
		frappe.db.set_single_value("Omni Settings", "script_fast_timers_for_test", 1)
		self.set_switch(enabled=1, tests=self.ident1)
		self.assertTrue(scheduler.fast_for(self.ident1))
		self.assertFalse(scheduler.fast_for(self.ident2))
		self.now_is(SUN_10)
		fast = scheduler.on_docs_sent(self.lead1)
		slow = scheduler.on_docs_sent(self.lead2)
		self.assertEqual(
			get_datetime(frappe.db.get_value(SCHED, fast, "due_at")), SUN_10 + timedelta(minutes=1)
		)
		self.assertEqual(
			get_datetime(frappe.db.get_value(SCHED, slow, "due_at")), datetime(2026, 11, 24, 10, 0)
		)

	def test_fast_skips_window_postponement_only_for_test_identity(self):
		frappe.db.set_single_value("Omni Settings", "script_fast_timers_for_test", 1)
		self.set_switch(enabled=1, tests=self.ident1)
		sender = self.start(mock.patch.object(send, "send_node", return_value="OUT-GIA"))
		self.now_is(SUN_10)
		for ident in (self.ident1, self.ident2):
			frappe.db.set_value(IDENT, ident, "last_inbound_at", SUN_10 - timedelta(hours=1))
		a = self.add(due=SUN_10)
		b = self.add(due=SUN_10, lead=self.lead2, identity=self.ident2)
		for name in (a, b):
			frappe.db.set_value(SCHED, name, "creation", SUN_10, update_modified=False)
		frappe.db.set_value(LEAD, self.lead2, "email", "binh@example.com")
		frappe.db.commit()
		scheduler.run_due()
		self.assertEqual(self.row(a).status, "Sent")
		self.assertEqual(self.row(b).status, "Pending")
		self.assertEqual([c.args[0] for c in sender.call_args_list], [self.ident1])

	def test_flag_off_means_no_fast(self):
		self.set_switch(enabled=1, tests=self.ident1)
		self.assertFalse(scheduler.fast_for(self.ident1))


class TestSilence(K5Case):
	def _question(self, days_ago=4):
		code = self.make("TK2C_HOI", step="2", buttons=(("Tài liệu", "TK2C_A"),))
		bot_at = add_to_date(now_datetime(), days=-days_ago)
		frappe.get_doc(
			{
				"doctype": OUT,
				"channel": self.channel.name,
				"identity": self.ident1,
				"lead": self.lead1,
				"text": "Anh/chị cần gì ạ?",
				"source": "Bot",
				"status": "Sent",
				"script_node": code,
				"buttons": json.dumps([{"label": "Tài liệu", "target": "TK2C_A"}]),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			IDENT,
			self.ident1,
			{
				"script_last_bot_at": bot_at,
				"last_inbound_at": add_to_date(bot_at, hours=-1),
				"script_node": code,
				"script_step": "2",
			},
		)
		frappe.db.commit()
		return code

	def make(self, code, **kw):
		from omni_crm.tests.test_k2_core import make_node

		return make_node(code, text="Anh/chị cần gì ạ?", **kw)

	def test_one_reminder_then_nurture_state_untouched(self):
		self.window_open()
		code = self._question()
		scheduler.run_due()  # quét: đặt tin nhắc (đã quá 3 ngày) ; lượt này hoặc lượt sau sẽ gửi
		scheduler.run_due()
		rows = frappe.get_all(
			SCHED, filters={"lead": self.lead1}, fields=["kind", "status", "node", "reason"]
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].kind, rows[0].status, rows[0].node), ("silence_3d", "Sent", "NHAC3N_B12"))
		self.assertEqual(len(self.tasks.created), 1)  # hết hạn chat sau 3 ngày -> việc gửi email
		self.assertIn("phần mềm SupplyCore", self.tasks.created[0]["description"])
		self.assertNotEqual(self.journey(), "Chăm sóc lại")  # gửi nhắc không đổi hành trình của Lead
		self.assertEqual(
			frappe.db.get_value(IDENT, self.ident1, "script_node"), code
		)  # khách quay lại: tiếp chỗ dở
		scheduler.run_due()
		scheduler.run_due()
		self.assertEqual(frappe.db.count(SCHED, {"lead": self.lead1}), 1)  # chỉ nhắc một lần
		self.assertEqual(len(self.tasks.created), 1)

	def test_not_scheduled_when_customer_answered(self):
		self._question()
		frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", now_datetime())
		self.assertIsNone(scheduler.schedule_silence(self.ident1, self.lead1))

	def test_due_three_days_after_bot_message_in_window(self):
		self._question(days_ago=1)
		bot_at = datetime(2026, 11, 19, 20, 0)  # thứ Năm 20:00 -> +3 ngày = Chủ nhật -> thứ Ba 24/11 09:00
		frappe.db.set_value(IDENT, self.ident1, "script_last_bot_at", bot_at)
		frappe.db.set_value(IDENT, self.ident1, "last_inbound_at", bot_at - timedelta(hours=1))
		name = scheduler.schedule_silence(self.ident1, self.lead1, bot_at)
		self.assertEqual(
			get_datetime(frappe.db.get_value(SCHED, name, "due_at")), datetime(2026, 11, 24, 9, 0)
		)
