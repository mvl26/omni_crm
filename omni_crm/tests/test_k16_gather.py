"""Bước 3 "hiểu ngữ cảnh" 20/11: gom tin (đợi khách nhắn xong), huỷ lượt đang chạy khi khách nhắn thêm."""

from datetime import datetime, timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.script import gather, router
from omni_crm.tests.test_k2_flow import FlowCase

T0 = datetime(2026, 11, 20, 10, 0, 0)


def row(text, at):
	return frappe._dict(name="x", text=text, received_at=T0 + timedelta(seconds=at), creation=T0 + timedelta(seconds=at))


class TestQuiet(FrappeTestCase):
	def test_taps_do_not_wait_and_typed_waits_the_base(self):
		self.assertEqual(gather.quiet_needed([row("#MENU_SPD", 0)], 15), 0)
		self.assertEqual(gather.quiet_needed([row("#MENU_SPD", 0), row("alo", 1)], 15), 15)
		self.assertEqual(gather.quiet_needed([], 15), 0)

	def test_unfinished_last_message_waits_longer(self):
		for text in ("Anh muốn hỏi là", "găng tay và", "bên em có bơm tiêm,", "để anh xem…", "với lại..."):
			self.assertEqual(gather.quiet_needed([row(text, 0)], 10), 15, text)
		for text in ("Anh muốn đặt lịch demo", "găng tay nitrile", "ok em"):
			self.assertEqual(gather.quiet_needed([row(text, 0)], 10), 10, text)

	def _wait(self, timeline, base=15):
		"""`timeline`: hàm (giây đã trôi) -> danh sách tin đang chờ. Trả (giây đã đợi, số lần báo chờ)."""
		clock = {"t": 0.0}
		with (
			mock.patch.object(gather, "pending", side_effect=lambda key: timeline(clock["t"])),
			mock.patch.object(gather, "_notice") as notice,
			mock.patch.object(gather.frappe.db, "rollback"),
		):
			waited = gather.wait_quiet(
				"zalo:1",
				base=base,
				sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
				now=lambda: T0 + timedelta(seconds=clock["t"]),
			)
		return waited, notice.call_count

	def test_waits_from_the_last_message_not_the_first(self):
		# tin 1 lúc 0s, tin 2 lúc 8s: phải đợi tới 8 + 15 = 23s
		waited, notices = self._wait(lambda t: [row("Anh quên mất là thứ 6 bận", 0)] + ([row("Đổi sang thứ 7 nhé", 8)] if t >= 8 else []))
		self.assertGreaterEqual(waited, 23)
		self.assertLess(waited, 26)
		self.assertEqual(notices, 1)  # đợi quá 10 giây: báo khách đang xem, đúng một lần

	def test_tap_only_is_immediate_and_total_wait_is_capped(self):
		self.assertEqual(self._wait(lambda t: [row("#MENU_SPD", 0)]), (0.0, 0))
		waited, _n = self._wait(lambda t: [row("a", 0), row("b", t)])  # khách cứ nhắn mãi
		self.assertLessEqual(waited, gather.MAX_WAIT + gather.POLL)

	def test_short_wait_sends_no_notice(self):
		self.assertEqual(self._wait(lambda t: [row("alo", 0)], base=5)[1], 0)


class TestSuperseded(FlowCase):
	def _new_inbound(self, text):
		frappe.get_doc(
			{
				"doctype": "Omni Inbound Message", "dedupe_key": frappe.generate_hash(length=12), "channel": self.channel.name,
				"channel_type": self.channel.channel_type, "event_type": "message", "identity": self.ident1, "lead": self.lead1,
				"identity_key": frappe.db.get_value("Omni Identity", self.ident1, "identity_key"),
				"received_at": frappe.utils.now_datetime(), "text": text, "status": "New",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def test_new_message_while_thinking_cancels_the_turn_and_carries_the_text(self):
		self.greeted()
		first = "Anh quên mất là thứ 6 bận"

		def slow_ai(text, info=None, **kw):
			self._new_inbound("Đổi sang thứ 7 nhé")  # khách nhắn thêm đúng lúc bot đang hỏi AI
			return "CHUA_RO", 0.3

		self.classify.side_effect = slow_ai
		self.turn(first)
		self.assertEqual(self.sent(), [])  # không gửi câu đã soạn cho tin cũ
		self.assertIn(first, self.sstate().get("carry") or "")
		frappe.db.delete("Omni Inbound Message", {"identity": self.ident1, "status": "New"})
		frappe.db.commit()
		seen = {}

		def ai(text, info=None, **kw):
			seen["text"] = text
			return "CHUA_RO", 0.3

		self.classify.side_effect = ai
		self.turn("Đổi sang thứ 7 nhé")
		self.assertIn(first, seen["text"])  # lượt sau đọc cả hai tin như một lượt
		self.assertIn("Đổi sang thứ 7 nhé", seen["text"])
		self.assertFalse(self.sstate().get("carry"))
		self.assertTrue(self.sent())

	def test_no_new_message_means_no_cancel(self):
		self.greeted()
		self.codes["bên em triển khai ra sao"] = ("MENU_SPD", 0.9)
		self.turn("bên em triển khai ra sao")
		self.assertEqual(self.sent(), ["MENU_SPD"])

	def test_check_ignores_button_taps(self):
		self.greeted()
		self._new_inbound("#MENU_SPD")
		ctx = router.RouteContext("b-x", self.ident1, self.lead1, self.channel.name, "alo", None)
		router.check_superseded(ctx)  # không ném lỗi
		frappe.db.delete("Omni Inbound Message", {"identity": self.ident1, "status": "New"})
		frappe.db.commit()
