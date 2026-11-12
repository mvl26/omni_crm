"""Thư sale gửi từ CRM đi ngay: hook Email Queue đẩy job sau commit; job gửi đúng một lần, an toàn khi flush chạy sau.

`EmailQueue.send` tự commit trạng thái nên dữ liệu test được commit thật: dọn thủ công (tiền tố TDFE).
"""

from unittest import mock

import frappe
from frappe.core.doctype.communication.email import make
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from omni_crm.crm_bridge import fast_email as fe

ACCOUNT = "TDFE gui ngay"
MAIL = "tdfe.sale@example.org"
CUSTOMER = "tdfe.khach@example.org"


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	leads = frappe.get_all("CRM Lead", filters={"first_name": ["like", "TDFE%"]}, pluck="name") or [""]
	queues = frappe.get_all("Email Queue", filters={"sender": ["like", f"%{MAIL}%"]}, pluck="name") or [""]
	frappe.db.delete("Email Queue Recipient", {"parent": ["in", queues]})
	frappe.db.delete("Email Queue", {"name": ["in", queues]})
	frappe.db.delete("Communication", {"reference_name": ["in", leads]})
	for lead in leads:
		if lead:
			frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Email Account", {"name": ACCOUNT})
	frappe.db.commit()


class TestFastEmail(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": ACCOUNT,
				"email_id": MAIL,
				"enable_incoming": 0,
				"enable_outgoing": 1,
				"smtp_server": "localhost",
				"no_smtp_authentication": 1,
			}
		).insert(ignore_permissions=True)
		self.lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "TDFE Khách", "email": CUSTOMER}
		).insert(ignore_permissions=True)
		self.addCleanup(setattr, frappe.flags, "mute_emails", frappe.flags.mute_emails)
		frappe.flags.mute_emails = False
		conf = mock.patch.dict(frappe.conf, {"mute_emails": 0})
		conf.start()
		self.addCleanup(conf.stop)

	def queue_for_lead(self) -> str:
		"""Email Queue tạo đúng như khi sale bấm gửi trong CRM (CommunicationArea.vue gọi communication.email.make)."""
		with mock.patch.object(fe.frappe, "enqueue"):
			make(
				doctype="CRM Lead",
				name=self.lead.name,
				recipients=CUSTOMER,
				subject="Báo giá",
				content="Gửi anh báo giá",
				send_email=1,
				sender=MAIL,
			)
		(name,) = frappe.get_all(
			"Email Queue",
			filters={"reference_doctype": "CRM Lead", "reference_name": self.lead.name},
			pluck="name",
		)
		return name

	def test_gui_tu_crm_day_job_gui_ngay_sau_commit(self):
		with mock.patch.object(fe.frappe, "enqueue") as enq:
			make(
				doctype="CRM Lead",
				name=self.lead.name,
				recipients=CUSTOMER,
				subject="Báo giá",
				content="Gửi anh báo giá",
				send_email=1,
				sender=MAIL,
			)
		(name,) = frappe.get_all(
			"Email Queue",
			filters={"reference_doctype": "CRM Lead", "reference_name": self.lead.name},
			pluck="name",
		)
		enq.assert_called_once_with(
			fe.SEND_METHOD, queue="short", enqueue_after_commit=True, email_queue=name
		)

	def test_bo_qua_thu_khong_thuoc_crm_thu_hen_gio_hoac_da_gui(self):
		later = add_days(now_datetime(), 1)
		with mock.patch.object(fe.frappe, "enqueue") as enq:
			fe.on_email_queue_insert(frappe._dict(name="q1", reference_doctype="ToDo", status="Not Sent"))
			fe.on_email_queue_insert(
				frappe._dict(name="q2", reference_doctype="CRM Lead", status="Not Sent", send_after=later)
			)
			fe.on_email_queue_insert(frappe._dict(name="q3", reference_doctype="CRM Deal", status="Sent"))
		enq.assert_not_called()

	def test_loi_day_job_khong_lam_hong_viec_gui(self):
		with (
			mock.patch.object(fe.frappe, "enqueue", side_effect=RuntimeError("redis chết")),
			mock.patch.object(fe.frappe, "log_error") as le,
		):
			fe.on_email_queue_insert(frappe._dict(name="q1", reference_doctype="CRM Lead", status="Not Sent"))
		le.assert_called_once()

	def test_job_gui_mot_lan_bang_guest_flush_sau_khong_gui_lai(self):
		name = self.queue_for_lead()
		frappe.db.commit()
		frappe.flags.sent_mail = None
		frappe.set_user("Guest")  # ADR 0006: job nền chạy được bằng Guest
		fe.send(name)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Email Queue", name, "status"), "Sent")
		self.assertIsNotNone(frappe.flags.sent_mail)

		frappe.flags.sent_mail = None
		frappe.get_doc("Email Queue", name, for_update=True).send()  # như flush của Frappe chạy sau đó
		self.assertIsNone(frappe.flags.sent_mail)
		fe.send(name)  # job chạy lại (vd thử lại) cũng không gửi lần hai
		self.assertIsNone(frappe.flags.sent_mail)

	def test_job_loi_ghi_log_giu_thu_cho_flush(self):
		name = self.queue_for_lead()
		frappe.db.commit()
		with (
			mock.patch(
				"frappe.email.doctype.email_queue.email_queue.EmailQueue.send",
				side_effect=RuntimeError("smtp"),
			),
			mock.patch("frappe.model.document.Document.log_error") as le,
		):
			fe.send(name)  # không ném lỗi ra worker
		le.assert_called_once()
		self.assertEqual(frappe.db.get_value("Email Queue", name, "status"), "Not Sent")

	def test_job_bo_qua_ban_ghi_khong_con(self):
		fe.send("khong-co-hang-doi-nay")
