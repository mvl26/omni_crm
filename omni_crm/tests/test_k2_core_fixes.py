"""Sửa lỗi nền bot kịch bản v3 sau rà soát độc lập (17/11): chống trả lời hai lần, tin bị chặn, hành trình, khóa hạ tầng.

Mọi lời gọi Zalo đều giả; không gọi AI thật.
"""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.bot import handoff, reply
from omni_crm.channels.zalo.client import BUTTON_TEXT_MAX
from omni_crm.pipeline import outbound
from omni_crm.script import classify, pricing, router, send, state, store
from omni_crm.tests.test_k2_core import NODE, OUT, PFX, K2Case, make_node
from omni_crm.tests.test_outbound import REQ

DRAFT = "Omni Bot Draft"
LOG = "Notification Log"


class FixCase(K2Case):
	def setUp(self):
		super().setUp()
		self.calls: list = []
		self.addCleanup(self._clean_fix)

	def _clean_fix(self):
		frappe.db.rollback()
		frappe.db.delete(DRAFT, {"batch_id": ["like", f"{PFX}%"]})
		frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
		frappe.db.commit()

	def handle(self, batch=None):
		with mock.patch("frappe.enqueue"):
			return router.handle_batch(batch or self.batch)

	def forget(self):
		frappe.cache().delete_value(router._handled_key(self.batch))


class TestDoubleAnswer(FixCase):
	def _send_then_fail(self, ctx):
		self.calls.append(ctx)
		send.send_text(ctx.identity, "Dạ em nhận được rồi ạ")
		raise RuntimeError("lỗi sau khi đã gửi")

	def test_outbound_row_carries_batch(self):
		router.register(router.DEFAULT_KEY, lambda ctx: send.send_text(ctx.identity, "Dạ vâng ạ"))
		self.set_switch(tests=self.ident1)
		self.assertTrue(self.handle())
		self.assertEqual(
			frappe.get_all(OUT, filters={"script_batch": self.batch}, pluck="identity"), [self.ident1]
		)
		self.assertIsNone(frappe.flags.get(send.BATCH_FLAG))

	def test_error_after_send_is_handled_and_handed_off(self):
		router.register(router.DEFAULT_KEY, self._send_then_fail)
		self.set_switch(tests=self.ident1)
		with (
			mock.patch("frappe.enqueue"),
			mock.patch("omni_crm.flags.bot_reply_mode", return_value="Draft"),
			mock.patch.object(reply, "make_draft") as old_flow,
		):
			self.assertEqual(reply._reply_batch(self.batch), "script")
		old_flow.assert_not_called()
		self.assertEqual(len(self.outs()), 1)
		drafts = frappe.get_all(DRAFT, filters={"batch_id": self.batch}, fields=["action", "mode"])
		self.assertEqual([(d.action, d.mode) for d in drafts], [("Handoff", "Draft")])

	def test_error_before_any_send_still_uses_old_flow(self):
		router.register(router.DEFAULT_KEY, mock.Mock(side_effect=RuntimeError("x")))
		self.set_switch(tests=self.ident1)
		with mock.patch("omni_crm.flags.bot_reply_mode", return_value="Off"):
			self.assertEqual(reply._reply_batch(self.batch), "off")
		self.assertFalse(frappe.db.exists(DRAFT, {"batch_id": self.batch}))

	def test_durable_dedupe_without_redis(self):
		router.register(router.DEFAULT_KEY, self._send_then_fail)
		self.set_switch(tests=self.ident1)
		with self.assertRaises(RuntimeError):
			self.handle()
		frappe.db.rollback()
		self.forget()
		self.assertTrue(self.handle())  # đã có tin của lượt: không chạy lại handler
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(len(self.outs()), 1)


class TestSilentDrop(FixCase):
	def _logs(self):
		return frappe.db.count(LOG, {"document_type": "CRM Lead", "document_name": self.lead1})

	def test_long_text_with_buttons_refused(self):
		self.set_switch(tests=self.ident1)
		buttons = [{"label": "Menu", "target": "MENU_CHINH"}]
		with mock.patch("frappe.enqueue"):
			self.assertIsNone(send.send_text(self.ident1, "a" * (BUTTON_TEXT_MAX + 1), buttons))
			self.assertTrue(send.send_text(self.ident1, "a" * BUTTON_TEXT_MAX, buttons))
		self.assertEqual(len(self.outs()), 1)
		with self.assertRaises(frappe.ValidationError):
			make_node(f"{PFX}BIG", text="a" * (BUTTON_TEXT_MAX + 1), buttons=[("Menu", "MENU_CHINH")])
		make_node(f"{PFX}BIGOK", text="a" * (BUTTON_TEXT_MAX + 1))  # không nút: không giới hạn này

	def _blocked(self, change):
		make_node(f"{PFX}HI")
		self.set_switch(tests=self.ident1)
		name = self.send(f"{PFX}HI", {"ten": "Mai"})
		change()
		frappe.db.commit()
		before = self._logs()
		self.sandbox(False)
		with (
			mock.patch(REQ) as http,
			mock.patch.object(handoff, "recipients", return_value=["Administrator"]),
		):
			self.assertEqual(outbound.send_outbound(name), "Blocked")
		http.assert_not_called()
		return self._logs() - before

	def test_blocked_by_pause_notifies_staff(self):
		pause = add_to_date(now_datetime(), hours=1)
		added = self._blocked(
			lambda: frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", pause)
		)
		self.assertEqual(added, 1)

	def test_blocked_by_switch_or_dnc_is_quiet(self):
		self.assertEqual(self._blocked(lambda: self.set_switch(tests="")), 0)
		frappe.db.delete(NODE, {"name": f"{PFX}HI"})
		added = self._blocked(lambda: frappe.db.set_value("CRM Lead", self.lead1, "omni_do_not_contact", 1))
		frappe.db.set_value("CRM Lead", self.lead1, "omni_do_not_contact", 0)
		self.assertEqual(added, 0)


class TestJourney(FixCase):
	def _lead(self):
		return frappe.db.get_value("CRM Lead", self.lead1, ["status", "omni_journey_status"], as_dict=True)

	def test_all_mapped_statuses_are_field_options(self):
		self.assertFalse(set(state.JOURNEY_TO_STATUS) - state.journey_options())

	def test_unknown_status_not_written(self):
		state.set_journey(self.lead1, "Chờ báo giá")
		self.assertEqual(self._lead().omni_journey_status, "Chờ báo giá")
		state.set_journey(self.lead1, "Trạng thái lạ")
		self.assertEqual(self._lead().omni_journey_status, "Chờ báo giá")

	def test_status_only_moves_forward(self):
		state.set_journey(self.lead1, "Đã hẹn demo")
		state.set_journey(self.lead1, "Đang tư vấn")
		self.assertEqual(tuple(self._lead().values()), ("Qualified", "Đang tư vấn"))
		state.set_journey(self.lead1, "Chăm sóc lại")
		self.assertEqual(self._lead().status, "Qualified")
		state.set_journey(self.lead1, "Thua")
		self.assertEqual(self._lead().status, "Unqualified")
		state.set_journey(self.lead1, "Đã demo")  # Lead đã thua: bot không mở lại
		self.assertEqual(tuple(self._lead().values()), ("Unqualified", "Đã demo"))

	def test_junk_never_changed(self):
		frappe.db.set_value("CRM Lead", self.lead1, "status", "Junk")
		for journey in ("Đã hẹn demo", "Thua"):
			state.set_journey(self.lead1, journey)
			self.assertEqual(self._lead().status, "Junk")

	def test_context_follows_merged_lead(self):
		with mock.patch.object(router.lead_bridge, "follow_merged", return_value="LEAD-CHINH") as follow:
			ctx = router.build_context(self.batch)
		follow.assert_called_once_with(self.lead1)
		self.assertEqual(ctx.lead, "LEAD-CHINH")


class TestQuoteReviewDefault(FixCase):
	def test_missing_single_row_means_required(self):
		field = pricing.REVIEW_FIELD
		orig = frappe.db.sql("select value from tabSingles where doctype='Omni Settings' and field=%s", field)
		try:
			frappe.db.sql("delete from tabSingles where doctype='Omni Settings' and field=%s", field)
			self.assertTrue(pricing.review_required())
			frappe.db.set_single_value("Omni Settings", field, 0)
			self.assertFalse(pricing.review_required())
			frappe.db.set_single_value("Omni Settings", field, 1)
			self.assertTrue(pricing.review_required())
		finally:
			frappe.db.sql("delete from tabSingles where doctype='Omni Settings' and field=%s", field)
			if orig:
				frappe.db.set_single_value("Omni Settings", field, orig[0][0])
			frappe.db.commit()


class TestInfraKey(FixCase):
	def setUp(self):
		super().setUp()
		router.register(router.DEFAULT_KEY, self.calls.append)

	def test_global_switch_needs_site_config_key(self):
		self.set_switch(enabled=1)
		frappe.conf[send.INFRA_CONF] = None
		self.assertFalse(self.handle())  # luồng cũ chạy
		with mock.patch("frappe.enqueue"):
			self.assertIsNone(send.send_text(self.ident1, "x"))
		self.assertEqual((self.calls, self.outs()), ([], []))
		frappe.conf[send.INFRA_CONF] = 1
		self.assertTrue(self.handle())
		with mock.patch("frappe.enqueue"):
			self.assertTrue(send.send_text(self.ident1, "x"))

	def test_test_identity_works_without_key(self):
		self.set_switch(enabled=1, tests=self.ident1)
		frappe.conf[send.INFRA_CONF] = None
		self.assertTrue(self.handle())
		self.assertEqual(len(self.calls), 1)
		with mock.patch("frappe.enqueue"):
			self.assertTrue(send.send_text(self.ident1, "x"))

	def test_key_closed_blocks_queued_row_at_send_time(self):
		self.set_switch(enabled=1)
		with mock.patch("frappe.enqueue"):
			name = send.send_text(self.ident1, "x")
		frappe.conf[send.INFRA_CONF] = 0
		self.sandbox(False)
		with mock.patch(REQ) as http:
			self.assertEqual(outbound.send_outbound(name), "Blocked")
		http.assert_not_called()


class TestRouterStops(FixCase):
	def setUp(self):
		super().setUp()
		router.register(router.DEFAULT_KEY, self.calls.append)
		self.set_switch(tests=self.ident1)

	def test_sale_already_replied_is_silent(self):
		with mock.patch.object(reply, "sale_replies_after", return_value=[{"text": "sale"}]) as after:
			self.assertTrue(self.handle())
		self.assertEqual(after.call_args.args[0], self.lead1)
		self.assertEqual((self.calls, self.outs()), ([], []))

	def test_stale_drafts_expired_before_handler(self):
		with mock.patch.object(reply, "expire_stale_for_lead") as expire:
			self.assertTrue(self.handle())
		expire.assert_called_once_with(self.lead1)
		self.assertEqual(len(self.calls), 1)


class TestClassifierRules(FixCase):
	def test_bare_khong_can_depends_on_pending_question(self):
		self.assertEqual(classify.classify_by_rules("Dạ không cần ạ")[0], "HUY_DANGKY")
		self.assertEqual(classify.classify_by_rules("không cần", {"script_node": ""})[0], "HUY_DANGKY")
		self.assertIsNone(classify.classify_by_rules("không cần", {"script_node": "TL_HOI_EMAIL"}))
		for text in ("stop", "đừng nhắn tin nữa"):  # yêu cầu dừng rõ ràng: vẫn nhận kể cả khi bot đang hỏi
			self.assertEqual(
				classify.classify_by_rules(text, {"script_node": "TL_HOI_EMAIL"})[0], "HUY_DANGKY"
			)

	def test_huy_dang_ky_something_else_is_not_unsubscribe(self):
		for text in ("hủy đăng ký demo thứ 5", "em muốn hủy đăng ký lịch hẹn", "hủy đăng ký đơn hôm qua"):
			hit = classify.classify_by_rules(text)
			self.assertNotEqual(hit and hit[0], "HUY_DANGKY", text)
		for text in ("hủy đăng ký", "hủy đăng ký nhận tin giúp em", "cho mình hủy theo dõi"):
			self.assertEqual(classify.classify_by_rules(text)[0], "HUY_DANGKY", text)


class TestWantsHumanTurnMode(FixCase):
	def _wants(self, text: str, turn_mode: bool) -> bool:
		with (
			mock.patch.object(reply, "current_turn_mode", return_value=turn_mode),
			mock.patch.object(reply.knowledge, "kb_items", return_value=[{"name": "KB-1"}]),
			mock.patch.object(reply.knowledge, "render_kb", return_value=""),
			mock.patch.object(reply, "make_reply_check", return_value=None),
			mock.patch.object(reply.ai_client, "call_tool", return_value=None),
			mock.patch.object(reply, "_fallback_handoff", return_value="D") as fallback,
		):
			reply.make_draft(
				lead=self.lead1,
				identity=self.ident1,
				channel=self.channel.name,
				channel_type="Zalo OA",
				batch_id=self.batch,
				customer_text=text,
				wants_human=True,  # phân tích cả hội thoại nói khách từng đòi gặp người
				check_channel=False,
				mode="Draft",
			)
		return bool(fallback.call_args.args[3])

	def test_v4_uses_current_text_only(self):
		self.assertFalse(self._wants("cho em hỏi giờ làm việc", turn_mode=True))
		self.assertTrue(self._wants("cho tôi gặp nhân viên tư vấn", turn_mode=True))
		self.assertTrue(self._wants("cho em hỏi giờ làm việc", turn_mode=False))


class TestNodeAndRender(FixCase):
	def test_code_normalised_into_name(self):
		name = make_node(f" #{PFX.lower()}low ")
		self.assertEqual(name, f"{PFX}LOW")
		self.assertEqual(frappe.db.get_value(NODE, name, "code"), name)

	def test_missing_variable_not_sent(self):
		make_node(
			f"{PFX}VAR", text="Dạ anh/chị {ten}, bên em gửi {tai_lieu} ạ.", buttons=[("Xem {tai_lieu}", "X")]
		)
		node = store.get_node(f"{PFX}VAR")
		self.assertIsNone(store.render(node, {"ten": "Mai"}))
		self.assertIsNone(store.render(node, {"ten": "Mai", "tai_lieu": "  "}))
		text, buttons = store.render(node, {"tai_lieu": "catalogue"})
		self.assertEqual(text, "Dạ anh/chị, bên em gửi catalogue ạ.")
		self.assertEqual(buttons[0]["label"], "Xem catalogue")
		self.set_switch(tests=self.ident1)
		self.assertIsNone(self.send(f"{PFX}VAR", {"ten": "Mai"}))
		self.assertEqual(self.outs(), [])
