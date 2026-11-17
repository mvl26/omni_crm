"""K2-core (bot kịch bản v3, ADR 0032): công tắc, kho ô kịch bản, gửi kèm nút, trạng thái, bộ định tuyến, móc Reply.

Mọi lời gọi Zalo đều giả (mock `requests`); không gọi AI thật.
"""

from __future__ import annotations

import json
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.bot import reply
from omni_crm.channels.zalo.adapter import ZaloAdapter
from omni_crm.ingest.events import OutboundRequest
from omni_crm.pipeline import outbound
from omni_crm.script import router, send, state, store, switch
from omni_crm.tests.test_outbound import REQ, OutboundTestCase
from omni_crm.tests.zalo_helpers import response

NODE = "Omni Script Node"
OUT = "Omni Outbound Message"
PFX = "TK2C_"
SETTING_FIELDS = ("script_bot_enabled", "script_test_identities")
SENT = {"error": 0, "data": {"message_id": "zl-btn-1", "quota": {"quota_type": "reply"}}}


def make_node(code: str, text: str = "Chào {ten}", buttons=(), status: str = "Đang dùng", **kw) -> str:
	doc = frappe.get_doc(
		{
			"doctype": NODE,
			"code": code,
			"text": text,
			"status": status,
			"buttons": [{"label": lb, "target": t} for lb, t in buttons],
			**kw,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class K2Case(OutboundTestCase):
	def setUp(self):
		super().setUp()
		self._orig = {f: frappe.db.get_single_value("Omni Settings", f) for f in SETTING_FIELDS}
		self.addCleanup(self._restore)
		self.set_switch(enabled=0, tests="")
		self.sandbox(True)
		self.batch = f"{PFX}{frappe.generate_hash(length=8)}"
		frappe.db.set_value("Omni Inbound Message", self.zalo1, "batch_id", self.batch)
		frappe.db.commit()

	def _restore(self):
		frappe.db.rollback()
		for f, v in self._orig.items():
			frappe.db.set_single_value("Omni Settings", f, v)
		frappe.db.delete(NODE, {"name": ["like", f"{PFX}%"]})
		frappe.db.delete("Omni Script Button", {"parent": ["like", f"{PFX}%"]})
		frappe.db.delete(OUT, {"identity": ["in", [self.ident1, self.ident2]]})
		for k in list(router._handlers):
			if k.startswith(PFX) or k in (router.DEFAULT_KEY, router.FOLLOW_KEY):
				router.unregister(k)
		frappe.db.commit()

	def set_switch(self, enabled: int = 0, tests: str = ""):
		frappe.db.set_single_value("Omni Settings", "script_bot_enabled", enabled)
		frappe.db.set_single_value("Omni Settings", "script_test_identities", tests)
		frappe.db.commit()

	def outs(self, identity=None):
		return frappe.get_all(
			OUT,
			filters={"identity": identity or self.ident1},
			fields=["name", "text", "source", "buttons", "script_node", "status"],
		)

	def send(self, code, ctx=None, identity=None):
		with mock.patch("frappe.enqueue"):
			return send.send_node(identity or self.ident1, code, ctx)


class TestSwitch(K2Case):
	def test_off_nothing_queued_for_non_test_identity(self):
		make_node(f"{PFX}HI")
		self.set_switch(enabled=0, tests=self.ident2)
		self.assertFalse(switch.enabled_for(self.ident1))
		self.assertIsNone(self.send(f"{PFX}HI", {"ten": "Mai"}))
		self.assertIsNone(send.send_text(self.ident1, "câu kho"))
		self.assertEqual(self.outs(), [])

	def test_test_identity_queued(self):
		make_node(f"{PFX}HI")
		self.set_switch(enabled=0, tests=f"{self.ident2}\n{self.ident1}")
		self.assertTrue(switch.is_test(self.ident1))
		name = self.send(f"{PFX}HI", {"ten": "Mai"})
		self.assertTrue(name)
		rows = self.outs()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].text, "Chào Mai")
		self.assertEqual(rows[0].source, "Bot")
		self.assertEqual(rows[0].script_node, f"{PFX}HI")

	def test_global_switch(self):
		self.set_switch(enabled=1, tests="")
		self.assertTrue(switch.enabled_for(self.ident1))
		self.assertFalse(switch.enabled_for(None))

	def test_bot_source_without_draft_still_rejected_outside_script(self):
		self.set_switch(enabled=1)
		with mock.patch("frappe.enqueue"), self.assertRaises(frappe.ValidationError):
			outbound.queue_outbound(OutboundRequest(self.channel.name, self.ident1, "x", None, "Bot"))


class TestStore(K2Case):
	def test_render_vars(self):
		make_node(f"{PFX}V", text="Dạ {ten} quan tâm {dich_vu}, hẹn {ngay_lam_viec_tiep_theo}.")
		node = store.get_node(f"#{PFX.lower()}v")
		text, buttons = store.render(node, {"ten": "Mai", "dich_vu": "SPD"})
		self.assertEqual(text, "Dạ Mai quan tâm SPD, hẹn.")
		self.assertEqual(buttons, [])

	def test_off_and_empty_not_sent(self):
		make_node(f"{PFX}OFF", status="Tắt")
		make_node(f"{PFX}EMPTY", text="  ")
		self.set_switch(enabled=1)
		self.assertIsNone(store.render(store.get_node(f"{PFX}OFF"), {}))
		self.assertIsNone(store.render(store.get_node(f"{PFX}EMPTY"), {}))
		self.assertIsNone(store.get_node(f"{PFX}MISSING"))
		self.assertIsNone(self.send(f"{PFX}EMPTY"))
		self.assertIsNone(self.send(f"{PFX}MISSING"))
		self.assertIsNone(send.send_text(self.ident1, "   "))
		self.assertEqual(self.outs(), [])

	def test_review_only_for_test_list(self):
		make_node(f"{PFX}REV", text="Câu chờ duyệt", status="Cần duyệt")
		node = store.get_node(f"{PFX}REV")
		self.set_switch(enabled=1, tests=self.ident2)
		frappe.db.set_value("Omni Identity", self.ident2, "last_inbound_at", now_datetime())
		self.assertIsNone(store.render(node, {"identity": self.ident1}))
		self.assertIsNone(self.send(f"{PFX}REV"))
		self.assertTrue(store.render(node, {"identity": self.ident2}))
		self.assertTrue(self.send(f"{PFX}REV", identity=self.ident2))

	def test_button_validation(self):
		with self.assertRaises(frappe.ValidationError):
			make_node(f"{PFX}LONG", buttons=[("Nhãn này dài hơn hai mươi ký tự", "X")])
		with self.assertRaises(frappe.ValidationError):
			make_node(f"{PFX}SIX", buttons=[(f"N{i}", f"T{i}") for i in range(6)])
		name = make_node(f"{PFX}FIVE", buttons=[(f"N{i}", f"#t{i}") for i in range(5)])
		self.assertEqual(store.get_node(name)["buttons"][0], {"label": "N0", "target": "T0"})
		self.set_switch(enabled=1)
		with mock.patch("frappe.enqueue"):
			self.assertIsNone(send.send_text(self.ident1, "x", [{"label": "y" * 21, "target": "A"}]))


class TestSendButtons(K2Case):
	def test_outbound_row_has_buttons_and_source_bot(self):
		make_node(f"{PFX}MENU", text="Chọn dịch vụ", buttons=[("Vật tư", f"{PFX}VT"), ("SPD", f"{PFX}SPD")])
		self.set_switch(tests=self.ident1)
		name = self.send(f"{PFX}MENU")
		row = frappe.db.get_value(OUT, name, ["source", "script_node", "buttons", "bot_draft"], as_dict=True)
		self.assertEqual(row.source, "Bot")
		self.assertIsNone(row.bot_draft)
		self.assertEqual(row.script_node, f"{PFX}MENU")
		self.assertEqual(
			json.loads(row.buttons),
			[{"label": "Vật tư", "target": f"{PFX}VT"}, {"label": "SPD", "target": f"{PFX}SPD"}],
		)
		self.assertTrue(frappe.db.get_value("Omni Identity", self.ident1, "script_last_bot_at"))

	def test_zalo_template_payload_end_to_end(self):
		make_node(f"{PFX}MENU", text="Chọn dịch vụ", buttons=[("Vật tư", f"{PFX}VT"), ("SPD", f"{PFX}SPD")])
		self.set_switch(tests=self.ident1)
		self.set_token()
		name = self.send(f"{PFX}MENU")
		self.sandbox(False)
		with mock.patch(REQ, side_effect=[response(SENT)]) as http:
			status = outbound.send_outbound(name)
		self.assertEqual(status, "Sent", frappe.db.get_value(OUT, name, "last_error"))
		self.assertEqual(http.call_count, 1)
		body = json.loads(http.call_args.kwargs["data"])
		self.assertEqual(
			body["message"],
			{
				"text": "Chọn dịch vụ",
				"attachment": {
					"type": "template",
					"payload": {
						"buttons": [
							{"title": "Vật tư", "type": "oa.query.show", "payload": f"#{PFX}VT"},
							{"title": "SPD", "type": "oa.query.show", "payload": f"#{PFX}SPD"},
						]
					},
				},
			},
		)
		self.assertEqual(frappe.db.get_value(OUT, name, "external_message_id"), "zl-btn-1")

	def test_plain_text_without_buttons_uses_send_text(self):
		self.set_switch(tests=self.ident1)
		self.set_token()
		with mock.patch("frappe.enqueue"):
			name = send.send_text(self.ident1, "Câu kho tri thức")
		self.sandbox(False)
		with mock.patch(REQ, side_effect=[response(SENT)]) as http:
			self.assertEqual(outbound.send_outbound(name), "Sent")
		self.assertEqual(json.loads(http.call_args.kwargs["data"])["message"], {"text": "Câu kho tri thức"})

	def test_sandbox_no_http(self):
		make_node(f"{PFX}MENU", text="Chọn", buttons=[("A", "A")])
		self.set_switch(tests=self.ident1)
		name = self.send(f"{PFX}MENU")
		with mock.patch(REQ) as http:
			self.assertEqual(outbound.send_outbound(name), "Sent")
		http.assert_not_called()
		self.assertEqual(frappe.db.get_value(OUT, name, "is_sandbox"), 1)

	def test_client_send_buttons_sandbox(self):
		self.set_token()
		with mock.patch(REQ) as http:
			req = OutboundRequest(self.channel.name, self.ident1, "Chọn", None, "Bot")
			with mock.patch("omni_crm.script.send.buttons_of", return_value=[{"label": "A", "target": "A"}]):
				mid = ZaloAdapter().send(req, frappe.get_doc("Omni Channel", self.channel.name))
		http.assert_not_called()
		self.assertTrue(mid.startswith("sandbox:"))

	def test_switch_turned_off_before_send_blocks(self):
		make_node(f"{PFX}HI")
		self.set_switch(tests=self.ident1)
		name = self.send(f"{PFX}HI")
		self.set_switch(tests="")
		self.sandbox(False)
		with mock.patch(REQ) as http:
			self.assertEqual(outbound.send_outbound(name), "Blocked")
		http.assert_not_called()


class TestState(K2Case):
	def test_state_roundtrip(self):
		state.set_node(self.ident1, "#menu_spd", step="3a.email")
		self.assertEqual(state.bump_reask(self.ident1), 1)
		self.assertEqual(state.bump_reask(self.ident1), 2)
		state.update_state(self.ident1, branch="3a")
		st = state.get(self.ident1)
		self.assertEqual(st["script_node"], "MENU_SPD")
		self.assertEqual(st["script_step"], "3a.email")
		self.assertEqual(st["script_reask_count"], 2)
		self.assertEqual(st["script_state"], {"branch": "3a"})
		state.reset_reask(self.ident1)
		self.assertEqual(state.get(self.ident1)["script_reask_count"], 0)

	def test_add_service(self):
		state.add_service(self.lead1, "spd")
		self.assertEqual(state.add_service(self.lead1, "VATTU"), ["SPD", "VATTU"])
		self.assertEqual(state.add_service(self.lead1, "SPD"), ["SPD", "VATTU"])

	def test_set_journey_maps_crm_status(self):
		state.set_journey(self.lead1, "Đã hẹn demo")
		row = frappe.db.get_value("CRM Lead", self.lead1, ["status", "omni_journey_status"], as_dict=True)
		self.assertEqual((row.status, row.omni_journey_status), ("Qualified", "Đã hẹn demo"))
		state.set_journey(self.lead1, "Không liên hệ")
		row = frappe.db.get_value("CRM Lead", self.lead1, ["status", "omni_do_not_contact"], as_dict=True)
		self.assertEqual((row.status, row.omni_do_not_contact), ("Unqualified", 1))

	def test_set_journey_keeps_converted(self):
		frappe.db.set_value("CRM Lead", self.lead1, "status", "Converted")
		state.set_journey(self.lead1, "Chăm sóc lại")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "status"), "Converted")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead1, "omni_journey_status"), "Chăm sóc lại")


class TestRouter(K2Case):
	def setUp(self):
		super().setUp()
		self.calls: list = []
		router.register(f"{PFX}MENU", self.calls.append)
		router.register(router.DEFAULT_KEY, self.calls.append)

	def test_switch_off_returns_false(self):
		self.assertFalse(router.handle_batch(self.batch))
		self.assertEqual(self.calls, [])

	def test_typed_text_dispatch(self):
		self.set_switch(tests=self.ident1)
		self.assertTrue(router.handle_batch(self.batch))
		ctx = self.calls[0]
		self.assertEqual(
			(ctx.identity, ctx.lead, ctx.text, ctx.payload_code),
			(self.ident1, self.lead1, "hỏi giá máy A", None),
		)
		self.assertTrue(ctx.is_first_contact)
		self.assertTrue(router.handle_batch(self.batch))  # lượt đã xử lý: không gọi lại
		self.assertEqual(len(self.calls), 1)

	def test_payload_code_detection(self):
		make_node(f"{PFX}MENU")
		self.assertEqual(router.payload_code(f"#{PFX}MENU"), f"{PFX}MENU")
		self.assertIsNone(router.payload_code("#KHONGCO"))
		self.assertIsNone(router.payload_code("giá #1 bao nhiêu"))
		self.assertEqual(router.payload_code("#GAP_NHANVIEN"), "GAP_NHANVIEN")
		self.assertEqual(router.payload_code("#MENU"), router.MENU_CODE)
		self.assertEqual(router.payload_code(" Menu "), router.MENU_CODE)
		frappe.db.set_value("Omni Inbound Message", self.zalo1, "text", f"#{PFX}MENU")
		self.set_switch(tests=self.ident1)
		self.assertTrue(router.handle_batch(self.batch))
		self.assertEqual(self.calls[0].payload_code, f"{PFX}MENU")

	def test_stop_conditions(self):
		self.set_switch(tests=self.ident1)
		for setup, undo in (
			(
				lambda: frappe.db.set_value(
					"Omni Identity", self.ident1, "bot_paused_until", add_to_date(now_datetime(), hours=1)
				),
				lambda: frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None),
			),
			(
				lambda: frappe.db.set_value("CRM Lead", self.lead1, "omni_do_not_contact", 1),
				lambda: frappe.db.set_value("CRM Lead", self.lead1, "omni_do_not_contact", 0),
			),
			(
				lambda: frappe.db.set_value("CRM Lead", self.lead1, "status", "Junk"),
				lambda: frappe.db.set_value("CRM Lead", self.lead1, "status", "New"),
			),
		):
			setup()
			batch = f"{PFX}{frappe.generate_hash(length=8)}"
			frappe.db.set_value("Omni Inbound Message", self.zalo1, "batch_id", batch)
			self.assertTrue(router.handle_batch(batch))
			self.assertEqual(self.calls, [])
			self.assertIsNone(send.send_text(self.ident1, "x"))
			undo()
		self.assertEqual(self.outs(), [])

	def test_no_handler_falls_back(self):
		router.load_flows()  # K2-flow: luồng thật đăng ký `*` khi nạp; nạp trước rồi mới gỡ
		router.unregister(router.DEFAULT_KEY)
		self.set_switch(tests=self.ident1)
		self.assertFalse(router.handle_batch(self.batch))

	def test_prefix_handler(self):
		router.unregister(router.DEFAULT_KEY)
		hits = []
		router.register(f"{PFX}FLOW_*", hits.append)
		make_node(f"{PFX}FLOW_A")
		frappe.db.set_value("Omni Inbound Message", self.zalo1, "text", f"#{PFX}FLOW_A")
		self.set_switch(tests=self.ident1)
		self.assertTrue(router.handle_batch(self.batch))
		self.assertEqual(hits[0].payload_code, f"{PFX}FLOW_A")


class TestReplyHook(K2Case):
	def test_script_true_stops_old_flow(self):
		with mock.patch("omni_crm.script.router.handle_batch", return_value=True):
			self.assertEqual(reply._reply_batch(self.batch), "script")

	def test_script_false_runs_old_flow(self):
		with (
			mock.patch("omni_crm.script.router.handle_batch", return_value=False),
			mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"),
		):
			self.assertEqual(reply._reply_batch(self.batch), "off")

	def test_script_error_falls_back_and_logs(self):
		before = frappe.db.count("Error Log", {"method": "Omni: bot kịch bản lỗi, dùng luồng cũ"})
		with (
			mock.patch("omni_crm.script.router.handle_batch", side_effect=RuntimeError("lời khách bí mật")),
			mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"),
		):
			self.assertEqual(reply._reply_batch(self.batch), "off")
		logs = frappe.get_all(
			"Error Log",
			filters={"method": "Omni: bot kịch bản lỗi, dùng luồng cũ"},
			fields=["error"],
			order_by="creation desc",
		)
		self.assertEqual(len(logs), before + 1)
		self.assertNotIn("lời khách bí mật", logs[0].error)

	def test_real_router_switch_off_old_flow(self):
		with mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"):
			self.assertEqual(reply._reply_batch(self.batch), "off")


class TestSendKeepsHandlerWrites(K2Case):
	def test_routing_error_keeps_uncommitted_state(self):
		make_node(f"{PFX}HI")
		self.set_switch(tests=self.ident1)
		self.set_last_inbound(hours_ago=24 * 8)  # ngoài khung gửi: queue_outbound ném RoutingError
		state.set_node(self.ident1, "MENU_SPD")
		self.assertIsNone(self.send(f"{PFX}HI"))
		self.assertEqual(state.get(self.ident1)["script_node"], "MENU_SPD")
		self.assertEqual(self.outs(), [])
