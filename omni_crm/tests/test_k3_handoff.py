"""K3 (bot kịch bản v3): gặp nhân viên trong/ngoài giờ, 15 phút chưa ai nhận, dừng hẳn, giao việc, API trang Lead.

Ngày dùng trong test (năm 2026): 17/11 thứ Ba, 18/11 thứ Tư, 19/11 thứ Năm (giả định là ngày lễ ở một test),
20/11 thứ Sáu, 21/11 thứ Bảy, 23/11 thứ Hai. Giờ làm việc truyền qua `timeutil.load_config` (không phụ thuộc site).
Mọi lời gọi gửi đều giả (sandbox); không gọi AI.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import frappe
from frappe.utils import get_datetime, now_datetime

from omni_crm.api import script as script_api
from omni_crm.bot import handoff as bot_handoff
from omni_crm.script import lead_ui, router, send, state, tasks
from omni_crm.script.flows import handoff
from omni_crm.script.router import RouteContext
from omni_crm.tests.test_k2_core import K2Case
from omni_crm.tests.test_outbound import SALE1, SALE2
from omni_crm.utils.business_hours import parse_hours

NODE = "Omni Script Node"
TASK = "CRM Task"
OUT = "Omni Outbound Message"
LEAD = "CRM Lead"
WEEKDAYS = (parse_hours({"mon-fri": "08:00-17:30"}), frozenset())
WITH_HOLIDAY = (WEEKDAYS[0], frozenset({date(2026, 11, 19)}))
PROD = (parse_hours({"mon-fri": "08:00-17:30", "sat": "08:00-12:00"}), frozenset())  # lịch như máy chủ thật
SEED = Path(handoff.__file__).resolve().parents[1] / "seed" / "nodes.json"
CODES = (
	"GAP_NHANVIEN",
	"GAP_NV_TRONGGIO",
	"GAP_NV_NGOAIGIO",
	"GAP_NV_GOIDIEN",
	"GAP_NV_GOIDIEN_XACNHAN",
	"GAP_NV_GOIDIEN_XACNHAN_TRONGGIO",
	"GAP_NV_NHANTIN",
	"GAP_NV_CAUKET",
	"GAP_NV_HOIDICHVU",
	"GAP_NV_VATTU",
	"GAP_NV_PHANMEM",
	"GAP_NV_SPD",
	"GAP_NV_KHAC",
	"SAI_SDT",
	"C4_HUYDANGKY",
	"C4_XOADULIEU",
)


def dt(day: int, hh: int, mm: int = 0) -> datetime:
	return datetime(2026, 11, day, hh, mm)


def seed_nodes() -> dict:
	nodes = json.loads(SEED.read_text(encoding="utf-8"))
	nodes += json.loads(SEED.with_name("nodes_k3.json").read_text(encoding="utf-8"))
	return {n["code"]: n for n in nodes}


class K3Case(K2Case):
	def setUp(self):
		super().setUp()
		self._made_nodes: list[str] = []
		self.addCleanup(self._k3_cleanup)
		seed = seed_nodes()
		for code in CODES:
			if frappe.db.exists(NODE, code):
				continue
			n = seed[code]
			frappe.get_doc(
				{
					"doctype": NODE,
					"code": code,
					"text": n.get("text") or "",
					"status": n.get("status") or "Đang dùng",
					"action": n.get("action") or "",
					"step": n.get("step") or "",
					"buttons": [{"label": b["label"], "target": b["target"]} for b in n.get("buttons") or []],
				}
			).insert(ignore_permissions=True)
			self._made_nodes.append(code)
		self.set_switch(enabled=0, tests=self.ident1)  # chỉ danh sách thử
		self.rows: list[dict] = []
		self.real_rows = tasks._recipient_rows
		self.patch("omni_crm.script.tasks._recipient_rows", side_effect=lambda: list(self.rows))
		self.patch("omni_crm.script.timeutil.load_config", return_value=WEEKDAYS)
		self.enqueue = self.patch("frappe.enqueue")
		self.at(dt(17, 10))
		router.load_flows()
		frappe.db.commit()

	def patch(self, target: str, **kw):
		p = mock.patch(target, **kw)
		m = p.start()
		self.addCleanup(p.stop)
		return m

	def at(self, when: datetime):
		"""Đặt "bây giờ" cho luồng chuyển người và hạn việc."""
		for mod in ("omni_crm.script.flows.handoff", "omni_crm.script.tasks"):
			p = mock.patch(f"{mod}.now_datetime", return_value=when)
			p.start()
			self.addCleanup(p.stop)
		self.now = when

	def _k3_cleanup(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		leads = [self.lead1, self.lead2]
		task_names = frappe.get_all(TASK, filters={"reference_docname": ["in", leads]}, pluck="name")
		if task_names:
			frappe.db.delete("ToDo", {"reference_type": TASK, "reference_name": ["in", task_names]})
			frappe.db.delete(TASK, {"name": ["in", task_names]})
		frappe.db.delete("Notification Log", {"document_type": LEAD, "document_name": ["in", leads]})
		for code in self._made_nodes:
			frappe.db.delete("Omni Script Button", {"parent": code})
			frappe.db.delete(NODE, {"name": code})
		frappe.db.commit()

	# --- tiện ích ---
	def inbound(self, text: str) -> str:
		"""Một lượt tin mới của khách ident1; trả batch_id."""
		batch = f"TK3_{frappe.generate_hash(length=8)}"
		name = self.make_inbound(self.lead1, self.ident1, "Zalo OA", text, 0)
		frappe.db.set_value("Omni Inbound Message", name, "batch_id", batch)
		frappe.db.commit()
		return batch

	def ctx(self, text: str = "", code: str | None = None) -> RouteContext:
		return RouteContext(
			batch_id="TK3",
			identity=self.ident1,
			lead=self.lead1,
			channel=self.channel.name,
			text=text,
			payload_code=code,
			state=state.get(self.ident1),
		)

	def sent(self) -> list:
		return frappe.get_all(
			OUT,
			filters={"identity": self.ident1, "source": "Bot"},
			fields=["script_node", "text", "status", "buttons"],
			order_by="creation asc",
		)

	def codes(self) -> list[str]:
		return [r.script_node for r in self.sent()]

	def lead_tasks(self) -> list:
		return frappe.get_all(
			TASK,
			filters={"reference_doctype": LEAD, "reference_docname": self.lead1},
			fields=["name", "title", "assigned_to", "due_date", "priority", "description", "status"],
		)

	def set_services(self, value: str | None):
		frappe.db.set_value(LEAD, self.lead1, "omni_services", value)
		frappe.db.commit()

	def hstate(self) -> dict:
		return state.get(self.ident1)["script_state"]


class TestRecipient(K3Case):
	def test_pick_recipient_pure(self):
		rows = [
			{"service": "SPD", "primary_user": "a@x.vn", "backup_user": "b@x.vn"},
			{"service": "VATTU", "primary_user": None, "backup_user": "c@x.vn"},
			{"service": "KHAC", "primary_user": None, "backup_user": None},
		]
		self.assertEqual(tasks.pick_recipient(rows, "SPD"), "a@x.vn")
		self.assertEqual(tasks.pick_recipient(rows, "SPD", "backup"), "b@x.vn")
		self.assertEqual(tasks.pick_recipient(rows, "VATTU"), "c@x.vn")  # người chính trống → người thay
		self.assertIsNone(tasks.pick_recipient(rows, "KHAC"))
		self.assertIsNone(tasks.pick_recipient(rows, "SUPPLYCORE"))
		self.assertIsNone(tasks.pick_recipient([], "SPD"))
		# dòng đầu không có người dùng được (trống/bị khóa) → xét dòng sau cùng dịch vụ
		later = [*rows, {"service": "KHAC", "primary_user": "d@x.vn", "backup_user": None}]
		self.assertEqual(tasks.pick_recipient(later, "KHAC"), "d@x.vn")

	def test_service_alias_and_unknown(self):
		self.rows = [{"service": "SUPPLYCORE", "primary_user": SALE2, "backup_user": None}]
		self.assertEqual(tasks.recipient("PHANMEM"), SALE2)
		self.assertIsNone(tasks.recipient("LA"))
		self.assertIsNone(tasks.recipient(None))

	def test_table_beats_lead_owner_then_owner_then_none(self):
		self.rows = [{"service": "SPD", "primary_user": SALE2, "backup_user": None}]
		self.assertEqual(tasks.assignee_for(self.lead1, "SPD"), SALE2)
		self.assertEqual(
			tasks.assignee_for(self.lead1, "VATTU"), SALE1
		)  # không có dòng → người phụ trách Lead
		frappe.db.set_value(LEAD, self.lead1, {"lead_owner": None, "_assign": None})
		self.assertIsNone(tasks.assignee_for(self.lead1, "VATTU"))

	def test_reads_real_settings_table(self):
		row = frappe.get_doc(
			{
				"doctype": tasks.RECIPIENT_ROW,
				"parent": "Omni Settings",
				"parenttype": "Omni Settings",
				"parentfield": tasks.RECIPIENT_FIELD,
				"idx": 99,
				"service": "DEMO_SPD",
				"primary_user": SALE2,
			}
		)
		row.db_insert()
		try:
			rows = self.real_rows()
			mine = [r for r in rows if r["service"] == "DEMO_SPD" and r["primary_user"] == SALE2]
			self.assertEqual(len(mine), 1)
			self.assertEqual(tasks.pick_recipient(mine, "DEMO_SPD"), SALE2)
		finally:
			frappe.db.delete(tasks.RECIPIENT_ROW, {"name": row.name})
			frappe.db.commit()


class TestCreateTask(K3Case):
	def test_assigned_per_table_with_due(self):
		self.rows = [{"service": "SPD", "primary_user": SALE2, "backup_user": None}]
		name = tasks.create_task(self.lead1, "SPD", "TK3 việc", "dòng 1\n<b>dòng 2</b>")
		row = frappe.db.get_value(
			TASK,
			name,
			["assigned_to", "due_date", "description", "reference_docname", "status"],
			as_dict=True,
		)
		self.assertEqual(row.assigned_to, SALE2)
		self.assertEqual(get_datetime(row.due_date), dt(17, 17, 30))
		self.assertEqual(row.reference_docname, self.lead1)
		self.assertIn("&lt;b&gt;", row.description)  # nội dung được thoát HTML
		self.assertEqual(frappe.session.user, "Administrator")

	def test_repeat_request_refreshes_open_task(self):
		self.rows = [{"service": "SPD", "primary_user": SALE2, "backup_user": None}]
		name = tasks.create_task(self.lead1, "SPD", "TK3 việc", "tóm tắt lần một")
		self.at(dt(17, 18))  # khách yêu cầu lại sau giờ chốt: hạn dời sang ngày làm việc kế tiếp
		with mock.patch("omni_crm.crm_bridge.notify.crm_notify") as bell:
			self.assertEqual(tasks.create_task(self.lead1, "SPD", "TK3 việc", "tóm tắt lần hai"), name)
		self.assertEqual(len(self.lead_tasks()), 1)
		row = frappe.db.get_value(TASK, name, ["due_date", "description"], as_dict=True)
		self.assertEqual(get_datetime(row.due_date), dt(18, 17, 30))
		self.assertIn("tóm tắt lần một", row.description)
		self.assertIn("tóm tắt lần hai", row.description)
		self.assertEqual(bell.call_args.args[:2], (self.lead1, [SALE2]))

	def test_no_recipient_unassigned_and_logged(self):
		frappe.db.set_value(LEAD, self.lead1, {"lead_owner": None, "_assign": None})
		with (
			mock.patch("omni_crm.monitoring.alerts.deliver_now") as alert,
			mock.patch("frappe.log_error") as log,
		):
			name = tasks.create_task(self.lead1, "VATTU", "TK3 không ai nhận", "x")
		self.assertTrue(name)
		self.assertFalse(frappe.db.get_value(TASK, name, "assigned_to"))
		self.assertEqual(alert.call_count, 1)
		self.assertEqual(alert.call_args.args[0].kind, tasks.ALERT_KIND)
		self.assertIn("không có người nhận", log.call_args.kwargs["title"])

	def test_missing_lead(self):
		self.assertIsNone(tasks.create_task("KHONG-CO", "SPD", "x", "y"))
		self.assertIsNone(tasks.create_task(None, "SPD", "x", "y"))


class TestSummary(K3Case):
	def test_five_lines_mask_pii(self):
		self.set_services("VATTU, SPD")
		self.make_inbound(
			self.lead1, self.ident1, "Zalo OA", "gọi em 0912 345 678 hoặc mail mai.tran@example.com nhé", 0
		)
		frappe.db.commit()
		text = tasks.summary(self.lead1, self.ident1)
		lines = text.split("\n")
		self.assertEqual(len(lines), 5)
		self.assertIn("TD106 Mai", lines[0])
		self.assertIn("Cung ứng vật tư y tế", lines[1])
		self.assertIn("Hệ thống SPD", lines[1])
		self.assertNotIn("345", text)
		self.assertNotIn("mai.tran", text)
		self.assertIn("m***@example.com", lines[3])
		self.assertIn("Zalo OA", lines[4])
		self.assertIn("hạn nhắn miễn phí", lines[4])

	def test_mask_pii_pure(self):
		out = tasks.mask_pii("sđt +84912345678, a@b.vn, năm 2026, 5 hộp")
		self.assertNotIn("912345", out)
		self.assertNotIn("a@b.vn", out)
		self.assertIn("2026", out)
		self.assertIn("5 hộp", out)

	def test_button_code_not_used_as_last_message(self):
		self.make_inbound(self.lead1, self.ident1, "Zalo OA", "#GAP_NHANVIEN", 0)
		frappe.db.commit()
		self.assertNotIn("#GAP", tasks.summary(self.lead1, self.ident1))


class TestInHours(K3Case):
	def test_button_in_hours_assigns_tasks_and_pauses(self):
		self.set_services("SPD")
		self.rows = [{"service": "SPD", "primary_user": SALE2, "backup_user": None}]
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NHANVIEN")))
		rows = self.sent()
		self.assertEqual([r.script_node for r in rows], ["GAP_NV_TRONGGIO"])
		self.assertEqual(rows[0].status, "Sent")  # câu chờ đã đi trước khi bot bị tạm dừng
		self.assertIn("Miyano đã báo nhân viên tư vấn", rows[0].text)
		found = self.lead_tasks()
		self.assertEqual(len(found), 1)
		self.assertEqual(found[0].assigned_to, SALE2)
		self.assertEqual(get_datetime(found[0].due_date), dt(17, 17, 30))
		self.assertIn("Khách muốn gặp nhân viên", found[0].description)
		self.assertTrue(
			frappe.db.exists(
				"ToDo", {"reference_type": LEAD, "reference_name": self.lead1, "allocated_to": SALE2}
			)
		)
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))
		info = self.hstate()["handoff"]
		self.assertEqual((info["mode"], info["assignee"], info["service"]), ("in_hours", SALE2, "SPD"))

		# khách nhắn tiếp trong lúc tạm dừng: bot im lặng
		self.assertTrue(send.blocked_reason(self.ident1))
		self.assertTrue(router.handle_batch(self.inbound("còn ai không ạ")))
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NHANVIEN")))
		self.assertEqual(self.codes(), ["GAP_NV_TRONGGIO"])
		self.assertEqual(len(self.lead_tasks()), 1)

	def test_auto_handoff_reason_priority(self):
		self.set_services("VATTU")
		handoff.start(self.ctx("tôi rất bực"), reason=handoff.REASON_COMPLAINT)
		found = self.lead_tasks()
		self.assertEqual(found[0].priority, "High")
		self.assertEqual(found[0].assigned_to, SALE1)  # bảng trống → người phụ trách Lead
		self.assertIn("phàn nàn", found[0].description)
		self.assertNotIn("tôi rất bực", found[0].title)

	def test_stale_handoff_state_ignored_after_bot_resumed(self):
		self.set_services("SUPPLYCORE")
		self.rows = [
			{"service": "SUPPLYCORE", "primary_user": SALE1, "backup_user": None},
			{"service": "SPD", "primary_user": SALE2, "backup_user": None},
		]
		handoff.start(self.ctx())
		state.update_state(self.ident1, handoff={**self.hstate()["handoff"], "escalated": 1})
		bot_handoff.resume_lead(
			self.lead1
		)  # mở bằng đường cũ (không qua "Trả lại cho bot"): trạng thái cũ còn nằm lại
		self.set_services("SUPPLYCORE, SPD")
		handoff.start(self.ctx())
		info = self.hstate()["handoff"]
		self.assertEqual((info["assignee"], info["service"], info["escalated"]), (SALE2, "SPD", 0))
		self.assertEqual(len(self.lead_tasks()), 1)  # việc đang mở được làm mới, không tạo trùng

	def test_pause_uses_handoff_pause_hours(self):
		self.set_services("SPD")
		with mock.patch.object(bot_handoff, "pause_hours", return_value=24):
			handoff.start(self.ctx())
		until = get_datetime(frappe.db.get_value("Omni Identity", self.ident1, "bot_paused_until"))
		self.assertLess(abs((until - now_datetime()).total_seconds() - 24 * 3600), 120)

	def test_waiting_sentence_not_sent_yet_delays_pause(self):
		self.set_services("SPD")
		with (
			mock.patch("omni_crm.pipeline.outbound.send_outbound", return_value="Queued"),
			mock.patch("frappe.log_error"),
		):
			handoff.start(self.ctx())
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))  # chưa dừng: lần thử lại không bị chặn
		self.assertEqual(self.hstate()["pause_pending"], 1)
		self.assertEqual(len(self.lead_tasks()), 1)  # việc và thông báo vẫn đã tạo
		self.assertEqual(handoff.finish_pending_pauses(), 1)  # câu chờ đi được rồi mới dừng
		self.assertEqual(self.sent()[0].status, "Sent")
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))
		self.assertNotIn("pause_pending", self.hstate())

	def test_identity_without_lead_alerts(self):
		ctx = self.ctx()
		ctx.lead = None
		with mock.patch.object(tasks, "warn_no_lead") as warn:
			handoff.start(ctx, "SPD")
			self.assertIsNone(handoff.request_data_deletion(ctx))
		self.assertEqual(warn.call_count, 2)
		self.assertEqual(warn.call_args.args[0], self.ident1)
		self.assertEqual(self.lead_tasks(), [])

	def test_switch_off_sends_nothing(self):
		self.set_switch(enabled=0, tests="")
		handoff.start(self.ctx())
		self.assertEqual(self.sent(), [])
		self.assertEqual(self.lead_tasks(), [])
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))

	def test_engine_action_after_node_sent(self):
		self.set_services("SPD")
		send.send_node(self.ident1, "GAP_NV_TRONGGIO", {})
		handoff.action_handoff(self.ctx(), "", {"code": "GAP_NV_TRONGGIO"})
		handoff.action_pause_bot(self.ctx(), "", {"code": "GAP_NV_TRONGGIO"})
		self.assertEqual(self.codes(), ["GAP_NV_TRONGGIO"])  # không gửi lặp câu chờ
		self.assertEqual(self.sent()[0].status, "Sent")
		self.assertEqual(len(self.lead_tasks()), 1)
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))


class TestAfterHours(K3Case):
	def _after(self, when: datetime, cfg=WEEKDAYS):
		self.at(when)
		self.patch("omni_crm.script.timeutil.load_config", return_value=cfg)
		self.set_services("VATTU")
		self.notify = self.patch("omni_crm.script.flows.handoff._notify")
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NHANVIEN")))
		return self.sent(), self.lead_tasks()

	def test_evening_appointment_buttons_task_next_day_no_pause(self):
		rows, found = self._after(dt(17, 18))
		self.assertEqual([r.script_node for r in rows], ["GAP_NV_NGOAIGIO"])
		self.assertIn("sáng mai", rows[0].text)
		self.assertEqual(
			[b["target"] for b in json.loads(rows[0].buttons)], ["GAP_NV_NHANTIN", "GAP_NV_GOIDIEN"]
		)
		self.assertEqual(get_datetime(found[0].due_date), dt(18, 17, 30))
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))  # ngoài giờ bot vẫn tư vấn
		self.assertEqual(self.notify.call_args.args[1], [SALE1])  # người nhận được báo cả khi ngoài giờ
		self.assertIsNone(send.blocked_reason(self.ident1))
		self.assertEqual(self.hstate()["handoff"]["mode"], "after_hours")

	def test_weekend(self):
		rows, found = self._after(dt(21, 10))
		self.assertIn("sáng thứ Hai 23/11", rows[0].text)
		self.assertEqual(get_datetime(found[0].due_date), dt(23, 17, 30))

	def test_holiday_eve(self):
		rows, found = self._after(dt(18, 18), WITH_HOLIDAY)
		self.assertIn("sáng thứ Sáu 20/11", rows[0].text)
		self.assertEqual(get_datetime(found[0].due_date), dt(20, 17, 30))

	def test_chat_here(self):
		self._after(dt(17, 18))
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NV_NHANTIN")))
		self.assertEqual(self.codes(), ["GAP_NV_NGOAIGIO", "GAP_NV_NHANTIN", "GAP_NV_CAUKET"])
		self.assertIn("nhắn tin", self.lead_tasks()[0].description)

	def test_call_me_invalid_then_valid_phone(self):
		_rows, found = self._after(dt(17, 18))
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NV_GOIDIEN")))
		self.assertEqual(self.codes()[-1], "GAP_NV_GOIDIEN")
		self.assertEqual(self.hstate()["flow"], handoff.FLOW_PHONE)

		self.assertTrue(handoff.on_phone_typed(self.ctx("số em là 12345")))
		self.assertEqual(self.codes()[-1], "SAI_SDT")
		self.assertEqual(self.hstate()["flow"], handoff.FLOW_PHONE)

		self.assertTrue(handoff.on_phone_typed(self.ctx("0912 345 678 nhé")))
		rows = self.sent()
		self.assertEqual([r.script_node for r in rows][-2:], ["GAP_NV_GOIDIEN_XACNHAN", "GAP_NV_CAUKET"])
		self.assertIn("0912345678", rows[-2].text)
		self.assertIn("sáng mai", rows[-2].text)
		st = self.hstate()
		self.assertNotIn("flow", st)
		self.assertEqual(st["handoff"]["phone"], "+84912345678")
		self.assertIn("0912345678", frappe.db.get_value(TASK, found[0].name, "description"))
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))

	def test_two_invalid_numbers_gives_up(self):
		self._after(dt(17, 18))
		handoff.on_call_me(self.ctx(code="GAP_NV_GOIDIEN"))
		self.assertTrue(handoff.on_phone_typed(self.ctx("abc")))
		self.assertFalse(handoff.on_phone_typed(self.ctx("vẫn không phải số")))
		self.assertNotIn("flow", self.hstate())

	def test_task_only_in_hours_does_not_pause(self):
		self.set_services("SPD")
		handoff.start(self.ctx(), task_only=True)
		self.assertEqual(self.codes(), ["GAP_NV_NGOAIGIO"])
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))
		self.assertEqual(len(self.lead_tasks()), 1)


class TestUnknownService(K3Case):
	def test_ask_once_then_button(self):
		self.set_services(None)
		self.rows = [{"service": "SUPPLYCORE", "primary_user": SALE2, "backup_user": None}]
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NHANVIEN")))
		self.assertEqual(self.codes(), ["GAP_NV_HOIDICHVU"])
		self.assertEqual(self.hstate()["flow"], handoff.FLOW_SERVICE)
		self.assertEqual(self.lead_tasks(), [])
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NV_PHANMEM")))
		self.assertEqual(self.codes(), ["GAP_NV_HOIDICHVU", "GAP_NV_TRONGGIO"])
		self.assertEqual(self.lead_tasks()[0].assigned_to, SALE2)
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_services"), "SUPPLYCORE")

	def test_typed_instead_of_choosing_goes_to_other_row(self):
		self.set_services(None)
		self.rows = [{"service": "KHAC", "primary_user": SALE2, "backup_user": None}]
		handoff.start(self.ctx())
		self.assertTrue(handoff.on_service_typed(self.ctx("tôi muốn hỏi việc khác")))
		self.assertEqual(self.codes(), ["GAP_NV_HOIDICHVU", "GAP_NV_TRONGGIO"])  # không hỏi lại
		self.assertEqual(self.lead_tasks()[0].assigned_to, SALE2)
		self.assertEqual(self.hstate()["handoff"]["service"], "KHAC")


class TestUnanswered(K3Case):
	def test_decision_pure(self):
		ok = dict(staff_replied=False, escalated=False)
		self.assertFalse(handoff.should_escalate(9.9, **ok))  # 20/11: mốc 10 phút (trước là 15)
		self.assertTrue(handoff.should_escalate(10, **ok))
		self.assertFalse(handoff.should_escalate(60, staff_replied=True, escalated=False))
		self.assertFalse(handoff.should_escalate(60, staff_replied=False, escalated=True))
		self.assertTrue(handoff.should_escalate(5, minutes=5, **ok))

	def test_wait_counts_only_current_business_window(self):
		wait = handoff.business_wait_minutes
		self.assertEqual(wait(dt(17, 10), dt(17, 10, 15), WEEKDAYS), 15)
		self.assertEqual(wait(None, dt(17, 10), WEEKDAYS), 0)
		# chuyển lúc 17:20, hết giờ 17:30: tối và sáng hôm sau trước giờ mở cửa không tính
		self.assertEqual(wait(dt(17, 17, 20), dt(17, 17, 29), WEEKDAYS), 9)
		self.assertEqual(wait(dt(17, 17, 20), dt(17, 20), WEEKDAYS), 0)
		self.assertEqual(wait(dt(17, 17, 20), dt(18, 8, 0), WEEKDAYS), 0)
		self.assertEqual(wait(dt(17, 17, 20), dt(18, 8, 14), WEEKDAYS), 14)
		self.assertEqual(wait(dt(17, 17, 20), dt(18, 8, 15), WEEKDAYS), 15)
		self.assertFalse(
			handoff.should_escalate(
				wait(dt(17, 17, 20), dt(18, 8, 0), WEEKDAYS), staff_replied=False, escalated=False
			)
		)
		# qua ngày lễ 19/11
		self.assertEqual(wait(dt(18, 17, 25), dt(19, 10), WITH_HOLIDAY), 0)
		self.assertEqual(wait(dt(18, 17, 25), dt(20, 8, 10), WITH_HOLIDAY), 10)

	def test_wait_production_calendar_with_saturday_morning(self):
		wait = handoff.business_wait_minutes
		self.assertEqual(wait(dt(20, 17, 20), dt(21, 8, 10), PROD), 10)  # thứ Sáu 17:20 → thứ Bảy 08:10
		self.assertEqual(wait(dt(20, 17, 20), dt(21, 8, 15), PROD), 15)
		self.assertEqual(wait(dt(21, 11, 50), dt(21, 12, 0), PROD), 10)  # thứ Bảy đóng cửa 12:00
		self.assertEqual(wait(dt(21, 11, 50), dt(21, 12, 30), PROD), 0)
		self.assertEqual(wait(dt(21, 11, 50), dt(22, 9), PROD), 0)  # Chủ nhật
		self.assertEqual(wait(dt(21, 11, 50), dt(23, 8, 14), PROD), 14)  # thứ Hai
		self.assertEqual(wait(dt(21, 11, 50), dt(23, 8, 15), PROD), 15)

	def _handoff_now(self):
		"""Chuyển người với mốc bắt đầu là giờ thật (để so với bot_paused_until và tin của sale)."""
		p = mock.patch("omni_crm.script.flows.handoff.now_datetime", side_effect=now_datetime)
		p.start()
		self.addCleanup(p.stop)
		self.patch("omni_crm.script.timeutil.in_hours", return_value=True)
		# phút chờ = phút đồng hồ (lịch làm việc đã kiểm ở các test hàm thuần)
		self.patch(
			"omni_crm.script.flows.handoff.business_wait_minutes",
			side_effect=lambda started, now, cfg=None: (now - started).total_seconds() / 60,
		)
		self.set_services("SPD")
		self.rows = [
			{"service": "SPD", "primary_user": SALE1, "backup_user": None},
			{"service": "QUAN_LY", "primary_user": SALE2, "backup_user": None},
		]
		handoff.start(self.ctx())
		frappe.db.commit()

	def test_after_10_minutes_asks_phone_and_notifies_manager(self):
		# 20/11 (người phụ trách quyết): mốc chờ nhân viên là 10 phút (trước là 15)
		self._handoff_now()
		self.assertEqual(handoff.check_unanswered(now_datetime() + timedelta(minutes=8))["escalated"], 0)
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))
		with mock.patch.object(handoff, "_notify") as notify:
			out = handoff.check_unanswered(now_datetime() + timedelta(minutes=11))
		self.assertEqual(out["escalated"], 1)
		self.assertEqual(notify.call_args.args[1], [SALE2])
		self.assertEqual(self.codes(), ["GAP_NV_TRONGGIO", "GAP_NV_GOIDIEN"])
		self.assertEqual(self.hstate()["flow"], handoff.FLOW_PHONE)
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))
		with mock.patch.object(handoff, "_notify") as notify:
			handoff.check_unanswered(now_datetime() + timedelta(minutes=40))
		notify.assert_not_called()  # chỉ báo một lần
		# khách cho số khi nhân viên đang trong giờ: không hẹn "ngày làm việc kế tiếp"
		self.assertTrue(handoff.on_phone_typed(self.ctx("0912345678")))
		rows = self.sent()
		self.assertEqual(
			[r.script_node for r in rows][-2:], ["GAP_NV_GOIDIEN_XACNHAN_TRONGGIO", "GAP_NV_CAUKET"]
		)
		self.assertIn("0912345678", rows[-2].text)
		self.assertNotIn("sáng", rows[-2].text)

	def test_staff_replied_no_escalation(self):
		self._handoff_now()
		name = frappe.db.get_value(OUT, {"identity": self.ident1, "script_node": "GAP_NV_TRONGGIO"}, "name")
		later = now_datetime() + timedelta(minutes=2)  # coi như sale trả lời sau khi được chuyển
		frappe.db.set_value(OUT, name, {"source": "Sale", "creation": later}, update_modified=False)
		frappe.db.commit()
		self.assertEqual(handoff.check_unanswered(now_datetime() + timedelta(minutes=30))["escalated"], 0)
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))

	def test_switch_off_never_runs(self):
		self._handoff_now()
		self.set_switch(enabled=0, tests="")
		with mock.patch.object(handoff, "_notify") as notify:
			out = handoff.check_unanswered(now_datetime() + timedelta(minutes=30))
		self.assertEqual(out["escalated"], 0)
		notify.assert_not_called()
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))
		self.assertEqual(self.codes(), ["GAP_NV_TRONGGIO"])
		self.assertFalse(self.hstate()["handoff"]["escalated"])

	def test_bot_resumes_even_when_nothing_could_be_sent(self):
		# 20/11 (người phụ trách quyết): quá 10 phút chưa ai trả lời thì bot LUÔN hoạt động lại (trước: giữ tạm dừng)
		self._handoff_now()
		with (
			mock.patch.object(handoff.send, "send_node", return_value=None),
			mock.patch.object(handoff, "_notify") as notify,
		):
			out = handoff.check_unanswered(now_datetime() + timedelta(minutes=30))
		self.assertEqual(out["escalated"], 1)
		self.assertEqual(notify.call_args.args[1], [SALE2])  # quản lý vẫn được báo
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))
		self.assertNotIn("flow", self.hstate())

	def test_known_phone_gets_apology_and_menu_not_phone_question(self):
		frappe.db.set_value("CRM Lead", self.lead1, "mobile_no", "0912345678", update_modified=False)
		self._handoff_now()
		with mock.patch.object(handoff, "_notify"):
			out = handoff.check_unanswered(now_datetime() + timedelta(minutes=11))
		self.assertEqual(out["escalated"], 1)
		self.assertEqual(self.codes(), ["GAP_NV_TRONGGIO", "MENU_CHINH"])
		self.assertIn("chưa phản hồi kịp", self.sent()[-1].text)
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))
		self.assertNotIn("flow", self.hstate())


class TestStop(K3Case):
	def test_unsubscribe_sets_do_not_contact_and_cancels(self):
		order: list[str] = []
		from omni_crm.script import scheduler as real_scheduler

		fake = mock.Mock()
		fake.cancel_for = mock.Mock(side_effect=lambda lead, *a, **kw: order.append("cancel") or 2)
		real_send = send.send_node

		def spy(*a, **kw):
			order.append("send")
			return real_send(*a, **kw)

		with (
			mock.patch.object(real_scheduler, "cancel_for", fake.cancel_for),
			mock.patch.object(handoff.send, "send_node", side_effect=spy),
		):
			self.assertTrue(router.handle_batch(self.inbound("#C4_HUYDANGKY")))
		self.assertEqual(fake.cancel_for.call_args[0][0], self.lead1)
		self.assertEqual(order[0], "cancel")  # hủy tin hẹn giờ trước, rồi mới gửi câu ghi nhận
		rows = self.sent()
		self.assertEqual([r.script_node for r in rows], ["C4_HUYDANGKY"])
		self.assertEqual(rows[0].status, "Sent")  # câu ghi nhận đi trước khi chặn
		row = frappe.db.get_value(
			LEAD, self.lead1, ["omni_do_not_contact", "omni_journey_status"], as_dict=True
		)
		self.assertEqual(row.omni_do_not_contact, 1)
		self.assertEqual(row.omni_journey_status, "Không liên hệ")
		# sau đó mọi tin kịch bản bị chặn
		self.assertTrue(send.blocked_reason(self.ident1))
		self.assertIsNone(send.send_node(self.ident1, "GAP_NV_CAUKET", {}))
		# khách chủ động nhắn lại: bot kịch bản không trả lời, lượt về luồng cũ để thành nháp cho nhân viên
		self.assertFalse(router.handle_batch(self.inbound("#GAP_NHANVIEN")))
		self.assertEqual(self.codes(), ["C4_HUYDANGKY"])

	def test_flag_set_even_when_confirmation_or_journey_fails(self):
		with (
			mock.patch.object(handoff.send, "send_node", side_effect=RuntimeError("gửi lỗi")),
			mock.patch.object(
				handoff.state, "set_journey", side_effect=frappe.ValidationError("thiếu lý do")
			),
			mock.patch("frappe.log_error") as log,
		):
			handoff.on_stop(self.ctx(code="C4_HUYDANGKY"))  # không ném lỗi
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_do_not_contact"), 1)
		self.assertEqual(log.call_count, 2)
		self.assertTrue(send.blocked_reason(self.ident1))

	def test_stop_phrase_while_waiting_for_phone_or_service(self):
		handoff.ask_phone(self.ident1, {})
		self.assertTrue(handoff.on_phone_typed(self.ctx("thôi đừng nhắn nữa")))
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_do_not_contact"), 1)
		self.assertEqual(self.codes(), ["GAP_NV_GOIDIEN", "C4_HUYDANGKY"])
		self.assertNotIn("flow", self.hstate())

	def test_stop_phrase_instead_of_choosing_service(self):
		self.set_services(None)
		handoff.start(self.ctx())
		self.assertTrue(handoff.on_service_typed(self.ctx("hủy đăng ký")))
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_do_not_contact"), 1)
		self.assertEqual(self.codes(), ["GAP_NV_HOIDICHVU", "C4_HUYDANGKY"])
		self.assertEqual(self.lead_tasks(), [])

	def test_scheduler_missing_is_tolerated(self):
		with mock.patch.dict(sys.modules, {"omni_crm.script.scheduler": None}):
			self.assertEqual(handoff.cancel_scheduled(self.lead1), 0)

	def test_stop_phrases(self):
		for text in ("đừng nhắn nữa nhé", "Hủy đăng ký", "huỷ đăng ký giúp tôi", "STOP"):
			self.assertTrue(handoff.is_stop_phrase(text), text)
		for text in ("Không cần", "không cần gấp, gửi báo giá giúp", "tôi cần tư vấn", "", None):
			self.assertFalse(handoff.is_stop_phrase(text), text)

	def test_data_deletion_node_off_still_creates_task(self):
		self.rows = [{"service": "TUAN_THU", "primary_user": SALE2, "backup_user": None}]
		self.assertTrue(router.handle_batch(self.inbound("#C4_XOADULIEU")))
		if frappe.db.get_value(NODE, "C4_XOADULIEU", "status") == "Tắt":
			# 19/11: ô hướng dẫn đang Tắt thì khách vẫn nhận câu ghi nhận (không im lặng)
			self.assertEqual([r["script_node"] for r in self.sent()], ["C4_XOADULIEU_GHINHAN"])
		found = self.lead_tasks()
		self.assertEqual(len(found), 1)
		self.assertEqual(found[0].assigned_to, SALE2)
		self.assertEqual(found[0].priority, "High")
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))

	def test_is_customer(self):
		self.assertFalse(handoff.is_customer(self.lead1))
		self.assertFalse(handoff.is_customer(None))
		frappe.db.set_value(LEAD, self.lead1, "converted", 1)
		self.assertTrue(handoff.is_customer(self.lead1))


class TestLeadApi(K3Case):
	def call(self, user: str, fn, *args, **kw):
		frappe.set_user(user)
		try:
			return fn(*args, **kw)
		finally:
			frappe.set_user("Administrator")

	def test_return_to_bot_resumes_and_clears(self):
		self.set_services("SPD")
		handoff.start(self.ctx())
		self.assertTrue(bot_handoff.is_bot_paused(self.ident1))
		status = self.call(SALE1, script_api.bot_status, self.lead1)
		self.assertEqual(status["bot"], "paused")
		self.assertTrue(status["paused_until"])
		res = self.call(SALE1, script_api.return_to_bot, self.lead1)
		self.assertEqual(res["status"], "Resumed")
		self.assertEqual(res["bot"], "on")
		self.assertFalse(bot_handoff.is_bot_paused(self.ident1))
		self.assertNotIn("handoff", self.hstate())
		self.assertIsNone(send.blocked_reason(self.ident1))
		# bot nhận lại lượt tin kế tiếp
		self.assertTrue(router.handle_batch(self.inbound("#GAP_NHANVIEN")))
		self.assertEqual(self.codes().count("GAP_NV_TRONGGIO"), 2)

	def test_permission_denied_without_lead_access(self):
		calls = (
			(script_api.return_to_bot, (self.lead1,)),
			(script_api.bot_status, (self.lead1,)),
			(script_api.mark_docs_sent, (self.lead1,)),
			(script_api.mark_quote_sent, (self.lead1,)),
			(script_api.set_demo_result, (self.lead1, "Đã demo")),
			(script_api.set_no_bot_followup, (self.lead1, 1)),
		)
		for fn, args in calls:
			with self.assertRaises(frappe.PermissionError, msg=fn.__name__):
				self.call(SALE2, fn, *args)
		row = frappe.db.get_value(
			LEAD, self.lead1, ["omni_docs_sent_at", "omni_demo_result", "omni_no_bot_followup"], as_dict=True
		)
		self.assertFalse(row.omni_docs_sent_at or row.omni_demo_result or row.omni_no_bot_followup)

	def test_bot_status_chat_window(self):
		status = self.call(SALE1, script_api.bot_status, self.lead1)
		self.assertEqual(status["bot"], "on")
		zalo = next(w for w in status["chat_windows"] if w["identity"] == self.ident1)
		hours = frappe.db.get_value("Omni Channel", self.channel.name, "zalo_cs_free_hours")
		last = frappe.db.get_value("Omni Identity", self.ident1, "last_inbound_at")
		self.assertEqual(zalo["deadline"], get_datetime(last) + timedelta(hours=hours))
		self.assertTrue(zalo["open"])

	def test_marks_and_lazy_calls(self):
		sched = types.ModuleType("omni_crm.script.scheduler")
		sched.on_docs_sent, sched.on_quote_sent, sched.cancel_for = mock.Mock(), mock.Mock(), mock.Mock()
		post = types.ModuleType("omni_crm.script.flows.postdemo")
		post.on_demo_result = mock.Mock()
		mods = {"omni_crm.script.scheduler": sched, "omni_crm.script.flows.postdemo": post}
		with mock.patch.dict(sys.modules, mods):
			self.call(SALE1, script_api.mark_docs_sent, self.lead1)
			self.call(SALE1, script_api.mark_quote_sent, self.lead1)
			self.call(SALE1, script_api.set_demo_result, self.lead1, "Đã demo", "khách hài lòng")
			self.call(SALE1, script_api.set_no_bot_followup, self.lead1, 1)
			with self.assertRaises(frappe.ValidationError):
				self.call(SALE1, script_api.set_demo_result, self.lead1, "Sai")
		sched.on_docs_sent.assert_called_once_with(self.lead1)
		sched.on_quote_sent.assert_called_once_with(self.lead1)
		sched.cancel_for.assert_called_once_with(self.lead1)
		post.on_demo_result.assert_called_once_with(self.lead1, "Đã demo")
		row = frappe.db.get_value(
			LEAD,
			self.lead1,
			[
				"omni_docs_sent_at",
				"omni_quote_sent_at",
				"omni_demo_result",
				"omni_demo_note",
				"omni_no_bot_followup",
				"omni_journey_status",
			],
			as_dict=True,
		)
		self.assertTrue(row.omni_docs_sent_at and row.omni_quote_sent_at)
		self.assertEqual(
			(row.omni_demo_result, row.omni_demo_note, row.omni_no_bot_followup),
			("Đã demo", "khách hài lòng", 1),
		)
		self.assertEqual(row.omni_journey_status, "Đã demo")

	def test_marks_work_without_other_modules(self):
		res = self.call(SALE1, script_api.mark_docs_sent, self.lead1)
		self.assertEqual(res["omni_journey_status"], "Đã gửi tài liệu")
		self.assertEqual(frappe.db.get_value(LEAD, self.lead1, "omni_journey_status"), "Đã gửi tài liệu")

	def test_field_edit_triggers_same_logic(self):
		doc = frappe.get_doc(LEAD, self.lead1)
		doc.omni_demo_result = "Khách vắng"
		doc.omni_docs_sent_at = now_datetime()
		doc._doc_before_save = frappe.get_doc(LEAD, self.lead1)
		with (
			mock.patch.object(lead_ui, "_call") as call,
			mock.patch("omni_crm.flags.is_system_write", return_value=False),
		):
			lead_ui.on_lead_fields_changed(doc)
		called = [c.args[:2] for c in call.call_args_list]
		self.assertIn(("scheduler", "on_docs_sent"), called)
		self.assertIn(("flows.postdemo", "on_demo_result"), called)
		with mock.patch.object(lead_ui, "_call") as call:  # hệ thống ghi (job/bot): bỏ qua
			with mock.patch("omni_crm.flags.is_system_write", return_value=True):
				lead_ui.on_lead_fields_changed(doc)
		call.assert_not_called()
		with (
			mock.patch.object(lead_ui, "_call") as call,
			mock.patch("omni_crm.flags.is_system_write", return_value=False),
		):
			same = frappe.get_doc(LEAD, self.lead1)  # không trường hành trình nào đổi: không đọc gì thêm
			same._doc_before_save = frappe.get_doc(LEAD, self.lead1)
			with mock.patch.object(lead_ui, "_script_lead") as lookup:
				lead_ui.on_lead_fields_changed(same)
			lookup.assert_not_called()
			self.set_switch(enabled=0, tests="")  # Lead không chạy bot kịch bản: bỏ qua
			lead_ui.on_lead_fields_changed(doc)
			call.assert_not_called()
			self.set_switch(enabled=0, tests=self.ident1)
			with (
				mock.patch.object(lead_ui, "after_docs_sent", side_effect=RuntimeError("lỗi")),
				mock.patch("frappe.log_error") as log,
			):
				lead_ui.on_lead_fields_changed(doc)  # không bao giờ ném lỗi ra ngoài
			self.assertEqual(log.call_count, 1)

	def test_hook_wired(self):
		hooks = frappe.get_hooks("doc_events")["CRM Lead"]["on_update"]
		self.assertIn("omni_crm.script.lead_ui.on_lead_fields_changed", hooks)
		self.assertIn("omni_crm.crm_bridge.lead.track_manual_edits", hooks)
		cron = frappe.get_hooks("scheduler_events")["cron"]["*/1 * * * *"]  # 20/11: kiểm mỗi phút để bot mở lại đúng 10 phút
		self.assertIn("omni_crm.script.flows.handoff.check_unanswered", cron)

	def test_layout_section_pure(self):
		layout = [{"name": "contact", "columns": [{"fields": ["email", "omni_services"]}]}]
		self.assertTrue(lead_ui.add_section(layout))
		fields = layout[-1]["columns"][0]["fields"]
		self.assertEqual(layout[-1]["label"], "Hành trình bot")
		self.assertNotIn("omni_services", fields)  # trường đã có ở phần khác thì không lặp
		self.assertIn("omni_journey_status", fields)
		self.assertFalse(lead_ui.add_section(layout))
