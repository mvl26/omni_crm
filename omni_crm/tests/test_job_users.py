"""V2-01 (E6, ADR 0006): mọi job nền Omni chạy được bằng người dùng Guest như worker RQ thật.

Job do webhook đẩy ra thừa hưởng người dùng Guest; job do sale đẩy ra thừa hưởng người dùng đó; tác vụ theo lịch chạy bằng
Administrator. Test này (1) dò mã nguồn để mọi chỗ `frappe.enqueue(` mới buộc phải đăng ký ở đây, (2) chạy từng điểm vào
bằng Guest.
"""

import re
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import analyze, reply
from omni_crm.channels.telephony import callbacks
from omni_crm.pipeline import outbound, worker
from omni_crm.tests.test_outbound import OutboundTestCase

APP = Path(__file__).resolve().parent.parent

# Điểm vào của job -> tệp chứa lời gọi enqueue. Thêm job mới = thêm dòng ở đây và test chạy bằng Guest bên dưới.
ENQUEUE_SITES = {
	"omni_crm/ingest/gateway.py": 1,  # pipeline.worker.process_identity
	"omni_crm/pipeline/outbound.py": 1,  # pipeline.outbound.send_outbound
	"omni_crm/bot/analyze.py": 1,  # bot.analyze.analyze_batch
	"omni_crm/bot/reply.py": 1,  # bot.reply.reply_batch
	"omni_crm/channels/telephony/callbacks.py": 1,  # channels.telephony.callbacks.process_missed_call
	"omni_crm/prospect/importer.py": 1,  # prospect.importer.run_job (V2-03; test Guest: test_prospect_import::TestJobAndAccess)
}


class TestEnqueueRegistry(FrappeTestCase):
	def test_every_enqueue_call_site_is_registered(self):
		found: dict[str, int] = {}
		for path in APP.rglob("*.py"):
			rel = path.relative_to(APP.parent).as_posix()
			if "/tests/" in rel:
				continue
			n = len(re.findall(r"^\s*frappe\.enqueue\(", path.read_text(encoding="utf-8"), flags=re.M))
			if n:
				found[rel] = n
		self.assertEqual(found, ENQUEUE_SITES)


class TestJobsRunAsGuest(OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")

	def test_process_identity_as_guest(self):
		frappe.set_user("Guest")
		self.assertIn(worker.process_identity("Zalo OA:khong-co-ai", debounce_seconds=0), ("idle", "done"))

	def test_bot_jobs_as_guest_do_not_raise_permission_error(self):
		frappe.set_user("Guest")
		analyze.analyze_batch("khong-co-lo")
		self.assertIsInstance(reply.reply_batch("khong-co-lo"), str)

	def test_missed_call_job_as_guest(self):
		frappe.set_user("Guest")
		callbacks.process_missed_call("OCALL-khong-co", None)  # không có cuộc gọi: không ném PermissionError
		self.assertEqual(frappe.session.user, "Guest")  # trả lại người dùng cũ

	def test_send_outbound_job_as_guest_sandbox(self):
		self.sandbox(True)
		name = self.queue()
		frappe.set_user("Guest")
		with mock.patch("frappe.enqueue"):
			self.assertEqual(outbound.send_outbound(name), "Sent")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Omni Outbound Message", name, "status"), "Sent")
