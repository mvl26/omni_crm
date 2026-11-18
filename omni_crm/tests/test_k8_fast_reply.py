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

	def test_typed_text_waits_at_most_one_second(self):
		self.assertEqual(self._run(_rows(("Zalo OA", "#MENU_SPD"), ("Zalo OA", "alo"))), 1)
		self.assertEqual(self._run(_rows(("Zalo OA", "alo")), base=0), 0)

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
