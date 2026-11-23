"""K2-flow (bot kịch bản v3): nạp seed, bộ chạy action, chào/menu/nút, phân loại tin gõ (bảng 1.2, 2.5, C.3), Kho tri thức.

Bộ phân loại, bộ máy Reply và `tasks` đều giả; không gọi AI/HTTP thật; sandbox bật.
"""

from __future__ import annotations

import json
from unittest import mock

import frappe
from frappe.utils import now_datetime

from omni_crm.bot import reply
from omni_crm.channels.zalo import phone_request
from omni_crm.pipeline import outbound
from omni_crm.script import engine, router, send, state
from omni_crm.script import tasks as script_tasks
from omni_crm.script.flows import collect, menu
from omni_crm.script.flows import handoff as k3_handoff
from omni_crm.script.seed import load as seed_load
from omni_crm.tests.test_k2_core import NODE, OUT, PFX, K2Case, make_node

STATE_FIELDS = (
	"script_node",
	"script_step",
	"script_reask_count",
	"script_greeted_at",
	"script_last_bot_at",
	"script_state",
)
CLASSIFY = "omni_crm.script.classify.classify"


class FlowCase(K2Case):
	"""Nền chung: seed đã nạp, khách thử = ident1, bộ phân loại/handoff/tasks giả, hàm chạy một lượt tin."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_load.load()  # tạo ô còn thiếu (idempotent, như after_migrate)

	def setUp(self):
		super().setUp()
		router.load_flows()
		menu.register_handlers()  # K2Case gỡ handler `*`/FOLLOW sau mỗi test
		self.set_switch(tests=self.ident1)
		self.codes: dict[str, tuple[str, float]] = {}
		p = mock.patch(CLASSIFY, side_effect=lambda text, *a, **k: self.codes.get(text, ("CHUA_RO", 0.0)))
		self.classify = p.start()
		self.addCleanup(p.stop)
		self.handoffs: list = []
		self._old_handoff = engine._actions.get("handoff")
		engine.register_action(
			"handoff", lambda ctx, arg, node: self.handoffs.append((arg, node.get("code")))
		)
		self.addCleanup(self._restore_handoff)
		self.tasks: list[dict] = []
		for target, fake in (
			(script_tasks, ("create_task", self._create_task)),
			(k3_handoff, ("start", self._start)),
		):
			p = mock.patch.object(target, fake[0], side_effect=fake[1])
			p.start()
			self.addCleanup(p.stop)
		p = mock.patch("omni_crm.bot.handoff.bot_active_now", return_value=True)
		p.start()
		self.addCleanup(p.stop)
		self.greeted()
		self._kb_auto = frappe.db.get_single_value("Omni Settings", "script_auto_send_kb_for_test")
		self.addCleanup(self.kb_auto, self._kb_auto)
		self.kb_auto(0)

	def kb_auto(self, value):
		frappe.db.set_single_value("Omni Settings", "script_auto_send_kb_for_test", value)
		frappe.db.commit()

	def _restore_handoff(self):
		if self._old_handoff:
			engine._actions["handoff"] = self._old_handoff
		else:
			engine._actions.pop("handoff", None)

	def _start(self, ctx, service=None, reason="", task_only=False):
		self.handoffs.append(("start", reason))

	def _create_task(self, lead, service, title, description, due=None, priority="Medium"):
		self.tasks.append(
			{"lead": lead, "service": service, "title": title, "description": description, "due": due}
		)
		return f"TASK-{len(self.tasks)}"

	def greeted(self, value: bool = True):
		frappe.db.set_value(
			"Omni Identity", self.ident1, "script_greeted_at", now_datetime() if value else None
		)
		frappe.db.commit()

	def reset(self):
		frappe.db.set_value(
			"Omni Identity", self.ident1, {**dict.fromkeys(STATE_FIELDS), "script_reask_count": 0}
		)
		frappe.db.set_value("CRM Lead", self.lead1, "omni_services", None)
		frappe.db.delete(OUT, {"identity": self.ident1})
		self.handoffs.clear()
		self.greeted()

	def turn(self, *texts, attachments=None, event_type="message", identity=None, lead=None) -> bool:
		"""Một lượt tin khách (nhiều tin = cùng một lượt); trả kết quả `router.handle_batch`."""
		batch = f"{PFX}{frappe.generate_hash(length=10)}"
		for i, text in enumerate(texts):
			name = self.make_inbound(
				lead or self.lead1, identity or self.ident1, "Zalo OA", text, 0, event_type
			)
			frappe.db.set_value(
				"Omni Inbound Message",
				name,
				{
					"batch_id": batch,
					"attachments": json.dumps(attachments or []),
					"received_at": frappe.utils.add_to_date(now_datetime(), seconds=i),
				},
			)
		frappe.db.commit()
		self.last_batch = batch
		with mock.patch("frappe.enqueue"):
			return router.handle_batch(batch)

	def tap(self, code: str) -> bool:
		return self.turn(f"#{code}")

	def say(self, text: str, code: str | None = None, conf: float = 0.9, **kw) -> bool:
		if code:
			self.codes[text] = (code, conf)
		return self.turn(text, **kw)

	def sent(self, identity=None) -> list[str]:
		rows = frappe.get_all(
			OUT,
			filters={"identity": identity or self.ident1},
			fields=["script_node", "creation"],
			order_by="creation asc",
		)
		return [r.script_node for r in rows]

	def texts(self) -> list[str]:
		return frappe.get_all(OUT, filters={"identity": self.ident1}, pluck="text")

	def sstate(self) -> dict:
		return state.get(self.ident1)["script_state"]

	def set_service(self, svc: str):
		state.add_service(self.lead1, svc)
		state.update_state(self.ident1, service=svc)
		frappe.db.commit()


class TestSeedLoad(K2Case):
	def test_create_skip_edit_overwrite(self):
		row = {
			"code": f"{PFX}SEED",
			"title": "T",
			"step": "1",
			"service": "",
			"channel": "Tất cả",
			"status": "Đang dùng",
			"text": "Câu gốc",
			"buttons": [{"label": "Menu", "target": "MENU_CHINH"}],
			"action": "menu",
			"source_ref": "1.1",
			"notes": "",
		}
		with mock.patch.object(seed_load, "read_nodes", return_value=[row]):
			self.assertEqual(seed_load.load()["created"], 1)
			self.assertEqual(seed_load.load(), {"created": 0, "updated": 0, "skipped": 1, "failed": 0})
			changed = {**row, "text": "Câu seed mới", "status": "Tắt"}
			with mock.patch.object(seed_load, "read_nodes", return_value=[changed]):
				self.assertEqual(
					seed_load.load()["skipped"], 1
				)  # ô đã có: không đổi chữ, không đổi trạng thái
				got = frappe.db.get_value(NODE, f"{PFX}SEED", ["text", "status"])
				self.assertEqual(got, ("Câu gốc", "Đang dùng"))
				self.assertEqual(seed_load.load(overwrite=True)["updated"], 1)
				self.assertEqual(frappe.db.get_value(NODE, f"{PFX}SEED", "status"), "Tắt")

	def test_not_called_on_migrate(self):
		import inspect

		from omni_crm import install

		self.assertNotIn("seed", inspect.getsource(install.after_migrate))

	def test_seed_files_include_extra(self):
		names = [p.name for p in seed_load.seed_files()]
		self.assertEqual(names[0], "nodes.json")
		self.assertIn("nodes_k2flow.json", names)
		codes = {n["code"] for n in seed_load.read_nodes()}
		self.assertTrue({"GREETING", "MENU_CHINH", "XACNHAN_KHONG_EMAIL"} <= codes)


class TestEngine(FlowCase):
	def test_service_variant_and_variables(self):
		make_node(f"{PFX}HOI", text="Bản chung {dich_vu}")
		make_node(f"{PFX}HOI_VATTU", text="Bản vật tư {dich_vu}")
		self.set_service("VATTU")
		self.tap(f"{PFX}HOI")
		self.assertEqual(self.sent(), [f"{PFX}HOI_VATTU"])
		self.assertEqual(self.texts(), ["Bản vật tư cung ứng vật tư y tế"])
		self.reset()
		self.set_service("SPD")
		self.tap(f"{PFX}HOI")
		self.assertEqual(self.sent(), [f"{PFX}HOI"])

	def test_navigation_node_and_unknown_action(self):
		make_node(f"{PFX}NAV", text="", action=f"service:SPD;lenh_la:x;need:Demo;then:{PFX}DICH")
		make_node(f"{PFX}DICH", text="Đích {dich_vu}")
		self.tap(f"{PFX}NAV")
		self.assertEqual(self.sent(), [f"{PFX}DICH"])
		row = frappe.db.get_value(
			"CRM Lead", self.lead1, ["omni_services", "omni_journey_need"], as_dict=True
		)
		self.assertEqual((row.omni_services, row.omni_journey_need), ("SPD", "Demo"))
		self.assertEqual(state.get(self.ident1)["script_node"], f"{PFX}DICH")

	def test_missing_variable_not_sent_falls_back(self):
		make_node(f"{PFX}CANEMAIL", text="Gửi tới {email} ạ")
		self.tap(f"{PFX}CANEMAIL")
		self.assertEqual(self.sent(), [])
		self.assertEqual(len(self.handoffs), 1)

	def test_off_node_falls_back_to_handoff_or_menu(self):
		self.tap("TIMHIEU_VATTU")  # ô trống của kịch bản, đang Tắt
		self.assertEqual((self.sent(), len(self.handoffs)), ([], 1))
		self.reset()
		engine._actions.pop("handoff", None)
		self.tap("TIMHIEU_VATTU")
		self.assertEqual(self.sent(), ["MENU_CHINH"])

	def test_goto_quote_by_service(self):
		self.tap("XIN_BAOGIA")
		self.assertEqual(self.sent(), ["BAOGIA_HOISANPHAM"])
		self.reset()
		self.set_service("SUPPLYCORE")
		self.tap("XIN_BAOGIA")
		self.assertEqual(self.sent(), ["BAOGIA_PHANMEM"])


class TestMenu(FlowCase):
	def test_follow_greets_once(self):
		self.greeted(False)
		self.assertTrue(self.turn("", event_type="follow"))
		self.assertEqual(self.sent(), ["GREETING"])
		self.assertTrue(frappe.db.get_value("Omni Identity", self.ident1, "script_greeted_at"))
		self.turn("", event_type="follow")
		self.say("hi")
		self.assertEqual(self.sent(), ["GREETING", "MENU_CHINH"])  # khách cũ: chào ngắn + menu, không gửi lại tin chào dài

	def test_first_message_greeting_or_concrete_question(self):
		self.greeted(False)
		self.say("Alo")
		self.assertEqual(self.sent(), ["GREETING"])
		self.reset()
		self.greeted(False)
		self.say("SPD là gì?", "MENU_SPD")
		self.assertEqual(self.sent(), ["MENU_SPD"])  # 1.2: hỏi cụ thể ngay thì không chào
		self.assertTrue(frappe.db.get_value("Omni Identity", self.ident1, "script_greeted_at"))

	def test_buttons_and_menu_word(self):
		self.tap("MENU_VATTU")
		self.turn("menu")
		self.tap("TIMHIEU_SPD")
		self.assertEqual(self.sent(), ["MENU_VATTU", "MENU_CHINH", "TIMHIEU_SPD", "HOITIEP_SPD"])
		row = frappe.db.get_value(
			"CRM Lead", self.lead1, ["omni_services", "omni_journey_status"], as_dict=True
		)
		self.assertEqual(row.omni_services, "VATTU")
		self.assertEqual(row.omni_journey_status, "Đang tư vấn")

	def test_table_1_2(self):
		rows = [
			("Bên mình có bơm tiêm, găng tay không?", "MENU_VATTU", ["MENU_VATTU"], 0),
			("cần nguồn hàng dự thầu", "MENU_VATTU", ["MENU_VATTU"], 0),
			("Có phần mềm quản lý kho không?", "MENU_PHANMEM", ["MENU_PHANMEM"], 0),
			("đang quản lý bằng Excel", "MENU_PHANMEM", ["MENU_PHANMEM"], 0),
			("SPD là gì?", "MENU_SPD", ["MENU_SPD"], 0),
			("quản lý trọn gói", "MENU_SPD", ["MENU_SPD"], 0),
			("Báo giá", "BAOGIA", ["BAOGIA_CHUARO"], 0),
			("giá bao nhiêu", "BAOGIA", ["BAOGIA_CHUARO"], 0),
			("Cho gặp nhân viên", "GAP_NHANVIEN", [], 1),
			("gọi cho tôi", "GAP_NHANVIEN", [], 1),
			("bạn là bot à?", "BOT_HOI", ["C3_BOT"], 0),  # theo C.3 (CLASSIFY_CODES mâu thuẫn 1)
			("Alo", "CHUA_RO", ["MENU_CHINH"], 0),  # 19/11: khách cũ chỉ chào thì chào lại kèm menu, không nói "chưa rõ ý"
			("hi", "CHUA_RO", ["MENU_CHINH"], 0),
			("abc xyz", "CHUA_RO", ["REASK_CHUARO"], 0),
		]
		for text, code, nodes, handoffs in rows:
			with self.subTest(text=text):
				self.reset()
				self.assertTrue(self.say(text, code))
				self.assertEqual(self.sent(), nodes)
				self.assertEqual(len(self.handoffs), handoffs)

	def test_table_2_5(self):
		rows = [
			("SPD", "Giá sao?", "BAOGIA", ["BAOGIA_SPD"]),
			("SUPPLYCORE", "báo giá giúp", "BAOGIA", ["BAOGIA_PHANMEM"]),
			("VATTU", "báo giá giúp", "BAOGIA", ["BAOGIA_VATTU"]),
			("SPD", "Gửi mình tài liệu", "TAILIEU", ["TAILIEU_SPD"]),
			("VATTU", "có profile không?", "TAILIEU", ["TAILIEU_VATTU"]),
			("SUPPLYCORE", "Cho xem thử", "DEMO", ["DEMO_HINHTHUC"]),
			("SPD", "demo được không?", "DEMO", ["DEMO_HINHTHUC"]),
			# 20/11 (thử thật): khách ở Vật tư GÕ muốn demo -> hỏi muốn xem demo sản phẩm nào (trước: mời gửi danh mục)
			("VATTU", "demo được không?", "DEMO", ["DEMO_CHONSANPHAM"]),
			("VATTU", "thế còn SPD thì sao?", "MENU_SPD", ["MENU_SPD"]),
		]
		for svc, text, code, nodes in rows:
			with self.subTest(svc=svc, text=text):
				self.reset()
				self.set_service(svc)
				self.say(text, code)
				self.assertEqual(self.sent(), nodes)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_services"), "VATTU, SPD")

	def test_file_in_vattu_branch(self):
		self.set_service("VATTU")
		files = [{"url": "https://x.example/danhmuc.xlsx", "name": "danhmuc.xlsx"}]
		self.turn("", attachments=files)
		self.assertEqual(self.sent(), ["DANHMUC_VATTU_DANHAN", "HOI_EMAIL_BAOGIA"])
		st = self.sstate()
		self.assertEqual((st["branch"], st["flow"], st["field"]), ("3b", "collect", "email"))
		self.assertEqual(st["files"][0]["name"], "danhmuc.xlsx")

	def test_kb_answer_auto_send_for_test_identity(self):
		self.set_service("SPD")
		self.kb_auto(1)
		res = {
			"name": "D1",
			"action": "Reply",
			"text": "Dạ, triển khai theo 3 bước ạ.",
			"group": None,
			"flags": "info:a",
		}
		with mock.patch.object(menu, "kb_draft", return_value=res):
			self.say("triển khai mất bao lâu?", "KB_HOI")
		self.assertEqual(self.sent(), ["KB_ANSWER", "HOITIEP_SPD"])
		self.assertEqual(self.handoffs, [])

	def test_kb_not_auto_leaves_draft_flow(self):
		res = {"name": "D1", "action": "Reply", "text": "Dạ có ạ.", "group": None}
		with (
			mock.patch.object(menu, "kb_draft", return_value=res),
			mock.patch.object(menu, "_to_sale_draft", return_value=True) as to_sale,
		):
			self.say("triển khai mất bao lâu?", "KB_HOI")
		to_sale.assert_called_once()
		# luật 9 (20/11): nháp chờ nhân viên duyệt thì khách vẫn nhận câu giữ chân, không im lặng
		self.assertEqual((self.sent()[:1], self.handoffs), (["KB_CHODUYET"], []))

	def test_kb_no_answer_fixed_sentence_handoff_and_logged(self):
		with mock.patch.object(reply, "make_draft", return_value=None) as make:
			self.say("bên em tích hợp HIS được không", "KB_HOI")
		self.assertEqual(make.call_args.kwargs["mode"], "Shadow")
		self.assertEqual(self.sent(), ["KB_CHUYENVIEN"])
		self.assertEqual(self.handoffs, [("", "KB_CHUYENVIEN")])
		logged = frappe.db.get_value("CRM Lead", self.lead1, "omni_unanswered_questions")
		self.assertIn("bên em tích hợp HIS được không", logged)
		self.reset()
		self.say("công nợ thế nào", "CAN_NGUOI")
		self.assertEqual((self.sent(), len(self.handoffs)), (["KB_CHUYENVIEN"], 1))

	def test_other_question_button_then_kb(self):
		self.tap("KHAC_CAUHOI")
		self.assertEqual(self.sstate()["flow"], menu.FLOW_KB)
		with mock.patch.object(menu, "kb_draft", return_value=None) as kb:
			self.turn("câu gì đó")
		kb.assert_called_once()
		self.classify.assert_not_called()
		self.assertEqual(self.sent(), ["KHAC_CAUHOI", "KB_CHUYENVIEN"])

	def test_c3_medical_complaint_unsubscribe(self):
		self.say("thuốc này uống thế nào", "Y_KHOA")
		self.assertEqual((self.sent(), self.handoffs), (["C3_YKHOA", "MENU_CHINH"], []))
		self.reset()
		self.say("làm ăn chán quá", "PHAN_NAN")
		self.assertEqual((self.sent(), len(self.handoffs)), ([], 1))
		self.reset()
		self.say("đừng nhắn nữa", "HUY_DANGKY")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_do_not_contact"), 1)

	def test_k3_handoff_module_hooks(self):
		self.set_service("SPD")
		self.say("làm ăn chán quá", "PHAN_NAN")
		for text in ("a", "b", "c"):
			self.say(text)
		self.assertEqual(
			self.handoffs,
			[("start", k3_handoff.REASON_COMPLAINT), ("start", k3_handoff.REASON_MISUNDERSTOOD)],
		)
		calls = self.classify.call_count
		self.turn("hủy đăng ký")  # câu dừng nhắn rõ ràng: không qua AI, K3 dừng hẳn
		self.assertEqual(self.classify.call_count, calls)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_do_not_contact"), 1)

	def test_khong_can_is_polite_decline_never_stop(self):
		self.assertFalse(k3_handoff.is_stop_phrase("không cần"))
		self.assertTrue(k3_handoff.is_stop_phrase("đừng nhắn nữa"))
		state.set_node(self.ident1, "HOITIEP_SPD", "2")
		frappe.db.commit()
		self.say("không cần", "HUY_DANGKY")
		self.assertEqual(self.sent(), ["KHONG_CAMON"])
		self.reset()
		state.update_state(self.ident1, flow=k3_handoff.FLOW_PHONE, handoff={"task": None})
		frappe.db.commit()
		self.turn("không")  # không cho số: giữ việc chuyển người, gửi câu kết
		self.assertEqual(self.sent(), ["GAP_NV_CAUKET"])
		self.assertIsNone(self.sstate().get("flow"))
		self.assertFalse(frappe.db.get_value("CRM Lead", self.lead1, "omni_do_not_contact"))

	def test_spam_silent_flag_no_do_not_contact(self):
		before = frappe.db.count("Comment", {"reference_name": self.lead1})
		self.assertTrue(self.say("mua follow giá rẻ http://x.example", "SPAM"))
		self.assertEqual(self.sent(), [])
		row = frappe.db.get_value("CRM Lead", self.lead1, ["omni_do_not_contact", "status"], as_dict=True)
		self.assertFalse(row.omni_do_not_contact)
		self.assertNotEqual(row.status, "Junk")
		self.assertEqual(frappe.db.count("Comment", {"reference_name": self.lead1}), before + 1)
		self.say("SPD là gì?", "MENU_SPD")  # lượt sau bot vẫn trả lời bình thường
		self.assertEqual(self.sent(), ["MENU_SPD"])

	def test_then_to_unsendable_node_falls_back(self):
		make_node(f"{PFX}A", text="Câu A", action=f"then:{PFX}OFF")
		make_node(f"{PFX}OFF", text="Câu tắt", status="Tắt")
		self.tap(f"{PFX}A")
		self.assertEqual(self.sent(), [f"{PFX}A"])
		self.assertEqual(self.handoffs, [("task", f"{PFX}OFF")])

	def test_typed_button_label_and_resend_limit(self):
		self.tap("MENU_SPD")
		self.turn("nhan tai lieu")  # gõ đúng chữ trên nút (không dấu) = bấm nút
		self.assertEqual(self.sent(), ["MENU_SPD", "TAILIEU_SPD"])
		self.classify.assert_not_called()
		self.turn("bảng so sánh chi phí")  # 3.1: tài liệu chọn bằng nút hoặc gõ
		self.assertEqual(self.sent()[-1], "HOI_EMAIL_TAILIEU")
		self.assertEqual(self.sstate()["tai_lieu"], "bảng so sánh chi phí")
		self.reset()
		self.tap("MENU_SPD")
		for _i in range(3):  # khách gõ mãi cùng một ý: cùng một ô không gửi lại vô hạn
			self.say("SPD là sao nhỉ", "MENU_SPD")
		self.assertEqual(self.sent().count("MENU_SPD"), 2)
		self.assertGreaterEqual(len(self.handoffs), 1)

	def test_kb_auto_send_refused_on_guard_flag(self):
		self.kb_auto(1)
		res = {
			"name": "D1",
			"action": "Reply",
			"text": "Dạ có ạ.",
			"group": None,
			"flags": "nghi_gia\ninfo:x",
		}
		with (
			mock.patch.object(menu, "kb_draft", return_value=res),
			mock.patch.object(menu, "_to_sale_draft", return_value=False),
		):
			self.say("triển khai mất bao lâu?", "KB_HOI")
		self.assertNotIn("KB_ANSWER", self.sent())
		self.assertEqual(self.sent(), ["KB_CHUYENVIEN"])

	def test_no_thanks_typed_is_not_unsubscribe(self):
		state.set_node(self.ident1, "TL_HOITHEM", "3")
		frappe.db.commit()
		self.say("không cần", "HUY_DANGKY")
		self.assertEqual(self.sent(), ["KHONG_CAMON"])
		self.classify.assert_not_called()

	def test_low_confidence_two_reasks_then_handoff(self):
		self.say("ờ thì", "MENU_SPD", conf=0.3)
		self.say("ờm")
		self.assertEqual((self.sent(), self.handoffs), (["REASK_CHUARO", "REASK_CHUARO"], []))
		self.say("???")
		self.assertEqual(self.sent(), ["REASK_CHUARO", "REASK_CHUARO"])
		self.assertEqual(len(self.handoffs), 1)
		self.assertEqual(state.get(self.ident1)["script_reask_count"], 0)

	def test_switch_off_non_test_identity_old_flow(self):
		self.set_switch(enabled=0, tests=self.ident2)
		with mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"):
			self.assertFalse(self.say("SPD là gì?", "MENU_SPD"))
			self.assertEqual(reply._reply_batch(self.last_batch), "off")  # luồng cũ chạy như trước
		self.assertEqual(self.sent(), [])
		self.classify.assert_not_called()

	def test_duplicate_batch_no_double_send(self):
		self.tap("MENU_SPD")
		with mock.patch("frappe.enqueue"):
			self.assertTrue(router.handle_batch(self.last_batch))
			self.assertEqual(reply._reply_batch(self.last_batch), "script")
		self.assertEqual(self.sent(), ["MENU_SPD"])

	def test_multi_tap_only_last(self):
		self.turn("#MENU_VATTU", "#MENU_PHANMEM", "#MENU_SPD")  # nhiều nút trong một lượt: chỉ nút cuối
		self.assertEqual(self.sent(), ["MENU_SPD"])

	def test_all_sends_go_through_script_send(self):
		seen = []
		real = outbound.queue_outbound

		def spy(req, *a, **k):
			seen.append(send.is_script_sending(req.identity))
			return real(req, *a, **k)

		with mock.patch.object(outbound, "queue_outbound", side_effect=spy):
			self.tap("MENU_PHANMEM")
			self.tap("TIMHIEU_PHANMEM")
		self.assertEqual(seen, [True, True, True])
		rows = frappe.get_all(OUT, filters={"identity": self.ident1}, fields=["source", "script_node"])
		self.assertTrue(all(r.source == "Bot" and r.script_node for r in rows))

	def test_handler_error_after_send_keeps_batch_handled(self):
		with (
			mock.patch.object(engine, "run_actions", side_effect=RuntimeError("x")),
			mock.patch("frappe.enqueue"),
		):
			name = self.make_inbound(self.lead1, self.ident1, "Zalo OA", "#MENU_SPD", 0)
			frappe.db.set_value("Omni Inbound Message", name, "batch_id", f"{PFX}ERR")
			frappe.db.commit()
			with mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"):
				self.assertEqual(
					reply._reply_batch(f"{PFX}ERR"), "script"
				)  # đã gửi tin: lượt thuộc bot kịch bản
		self.assertEqual(self.sent(), ["MENU_SPD"])
		self.assertTrue(router.handle_batch(f"{PFX}ERR"))  # đã gửi tin: luồng cũ không được trả lời thêm


class TestHooks(FlowCase):
	def test_phone_request_skips_script_identity(self):
		doc = frappe.get_doc("Omni Identity", self.ident1)
		channel = frappe.get_doc("Omni Channel", self.channel.name)
		with (
			mock.patch.object(phone_request, "recipient_allowed", return_value=True),
			mock.patch.object(phone_request.routing, "pick_message_kind"),
			mock.patch.object(phone_request, "_refused", return_value=False),
		):
			self.assertIn("bot kịch bản", phone_request.ineligible_reason(doc, channel, manual=False))
			self.assertIsNone(phone_request.ineligible_reason(doc, channel, manual=True))
			self.set_switch(tests="")
			self.assertIsNone(phone_request.ineligible_reason(doc, channel, manual=False))

	def test_enqueue_reply_when_reply_mode_off(self):
		with (
			mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"),
			mock.patch("frappe.enqueue") as enq,
		):
			self.assertTrue(reply.enqueue_reply(self.batch, self.lead1))
			self.set_switch(tests="")
			self.assertFalse(reply.enqueue_reply(self.batch, self.lead1))
		self.assertEqual(enq.call_count, 1)

	def test_collect_registered(self):
		self.assertIs(engine._state_handlers.get("collect"), collect.on_text)
