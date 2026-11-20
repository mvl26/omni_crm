"""Bot kịch bản trả lời nhanh: không chờ gom tin khi bấm nút, không chờ bot phân tích."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import reply
from omni_crm.script import switch


def _rows(*pairs):
	return [frappe._dict(channel_type=c, text=t) for c, t in pairs]


class TestFastDebounce(FrappeTestCase):
	def _run(self, rows, enabled=1, base=5):
		with (
			mock.patch.object(switch.frappe, "get_all", return_value=rows),
			mock.patch.object(switch, "setting", return_value=enabled),
			mock.patch.object(switch.frappe.db, "get_single_value", return_value=base),
			mock.patch.object(switch.frappe.db, "get_value", return_value=None),
		):
			return switch.fast_debounce("zalo:1")

	def test_button_tap_does_not_wait(self):
		self.assertEqual(self._run(_rows(("Zalo OA", "#MENU_SPD"))), 0)

	def test_typed_text_is_gathered_until_the_customer_stops(self):
		# 20/11 (bước 3 "hiểu ngữ cảnh"): tin gõ không còn chờ cố định 1 giây mà đợi khách nhắn xong (script.gather)
		self.assertEqual(self._run(_rows(("Zalo OA", "#MENU_SPD"), ("Zalo OA", "alo"))), switch.GATHER)
		self.assertEqual(self._run(_rows(("Zalo OA", "alo")), base=0), switch.GATHER)

	def test_other_channel_or_script_off_keeps_default(self):
		self.assertIsNone(self._run(_rows(("Email", "#X"))))
		self.assertIsNone(self._run(_rows(("Zalo OA", "#X")), enabled=0))
		self.assertIsNone(self._run([]))

	def test_error_keeps_default(self):
		with mock.patch.object(switch.frappe, "get_all", side_effect=RuntimeError("x")):
			self.assertIsNone(switch.fast_debounce("zalo:1"))


class TestScriptFirst(FrappeTestCase):
	def test_enqueues_script_job_only_for_script_batches(self):
		with (
			mock.patch.object(reply, "_script_enabled_batch", return_value=True),
			mock.patch.object(reply.frappe, "enqueue") as enq,
		):
			self.assertTrue(reply.enqueue_reply("b1", "L1", script_first=True))
		self.assertEqual(enq.call_args.args[0], reply.SCRIPT_FIRST_JOB)
		self.assertEqual(enq.call_args.kwargs["job_id"], "omni-script::b1")
		with (
			mock.patch.object(reply, "_script_enabled_batch", return_value=False),
			mock.patch.object(reply.frappe, "enqueue") as enq,
		):
			self.assertFalse(reply.enqueue_reply("b1", "L1", script_first=True))
		enq.assert_not_called()

	def test_script_first_never_runs_old_flow(self):
		with (
			mock.patch.object(reply, "_script_handled", return_value=False),
			mock.patch.object(reply, "_reply_batch") as old,
		):
			self.assertEqual(reply.script_first_batch("b-none"), "skipped")
		old.assert_not_called()
		with mock.patch.object(reply, "_script_handled", return_value=True):
			self.assertEqual(reply.script_first_batch("b-none"), "script")


class TestChoiceList(FrappeTestCase):
	def test_numbered_list_and_typed_number(self):
		from omni_crm.script import router, send

		buttons = [{"label": "Nhận tài liệu", "target": "A"}, {"label": "Xin báo giá", "target": "B"}]
		text, choices = send.with_choice_list("Anh/chị cần gì ạ?", buttons)
		self.assertIn("1. Nhận tài liệu\n2. Xin báo giá", text)
		self.assertTrue(choices["numbered"])
		st = {"script_state": {"choices": choices}}
		with mock.patch.object(router, "payload_code", side_effect=lambda t, known=None: t.lstrip("#")):
			self.assertEqual(router.typed_choice("2", st), "B")
			self.assertEqual(router.typed_choice(" Số 1. ", st), "A")
			self.assertEqual(router.typed_choice("xin báo giá", st), "B")
			self.assertIsNone(router.typed_choice("3", st))
			self.assertIsNone(router.typed_choice("tôi muốn hỏi giá găng tay", st))
			self.assertIsNone(router.typed_choice("1", {"script_state": {}}))

	def test_labels_with_digits_are_not_numbered(self):
		from omni_crm.script import router, send

		buttons = [{"label": "1", "target": "X1"}, {"label": "2–5", "target": "X2"}, {"label": "Trên 5", "target": "X3"}]
		text, choices = send.with_choice_list("Mấy cơ sở ạ?", buttons)
		self.assertIn("• 2–5", text)
		self.assertNotIn("1. 1", text)
		st = {"script_state": {"choices": choices}}
		with mock.patch.object(router, "payload_code", side_effect=lambda t, known=None: t.lstrip("#")):
			self.assertEqual(router.typed_choice("1", st), "X1")  # khớp nhãn, không phải thứ tự
			self.assertIsNone(router.typed_choice("3", st))
			self.assertEqual(router.typed_choice("trên 5", st), "X3")

	def test_single_button_or_too_long_keeps_text(self):
		from omni_crm.script import send

		one = [{"label": "Menu", "target": "M"}]
		self.assertEqual(send.with_choice_list("Chào", one), ("Chào", None))
		two = [*one, {"label": "Khác", "target": "K"}]
		long_text = "a" * send.BUTTON_TEXT_MAX
		self.assertEqual(send.with_choice_list(long_text, two), (long_text, None))
