"""Thư sale gửi từ CRM: nối luồng thư của khách (In-Reply-To) và gửi từ đúng hộp thư dù ô FROM của CRM sai.

Tạo thư đúng như giao diện CRM (communication.email.make); dữ liệu có thể bị commit nên dọn thủ công (tiền tố TDOE).
"""

import email
from email import policy
from unittest import mock

import frappe
from frappe.core.doctype.communication.email import make
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.crm_bridge import fast_email
from omni_crm.crm_bridge import outgoing_email as oe

ACCOUNT = "TDOE hop thu"
MAIL = "tdoe.sale@example.org"
CUSTOMER = "tdoe.khach@example.org"
OTHER = "tdoe.nguoikhac@example.org"


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	leads = frappe.get_all("CRM Lead", filters={"first_name": ["like", "TDOE%"]}, pluck="name") or [""]
	deals = frappe.get_all("CRM Deal", filters={"lead": ["in", leads]}, pluck="name") or [""]
	refs = leads + deals
	queues = frappe.get_all("Email Queue", filters={"reference_name": ["in", refs]}, pluck="name") or [""]
	frappe.db.delete("Email Queue Recipient", {"parent": ["in", queues]})
	frappe.db.delete("Email Queue", {"name": ["in", queues]})
	frappe.db.delete("Communication", {"reference_name": ["in", refs]})
	for d in deals:
		if d:
			frappe.delete_doc("CRM Deal", d, force=True, ignore_permissions=True, delete_permanently=True)
	for lead in leads:
		if lead:
			frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Email Account", {"name": ACCOUNT})
	frappe.db.commit()


class TestOutgoingEmail(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		p = mock.patch.object(fast_email.frappe, "enqueue")  # không đẩy job gửi thật
		p.start()
		self.addCleanup(p.stop)
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
			{"doctype": "CRM Lead", "first_name": "TDOE Khách", "email": CUSTOMER}
		).insert(ignore_permissions=True)

	def received(self, sender, mid, minutes_ago, ref=None):
		"""Thư khách gửi đến, như intake nạp (Communication Received của Email Account, Message-ID không có <>)."""
		ref = ref or ("CRM Lead", self.lead.name)
		return (
			frappe.get_doc(
				{
					"doctype": "Communication",
					"communication_type": "Communication",
					"communication_medium": "Email",
					"sent_or_received": "Received",
					"email_account": ACCOUNT,
					"sender": sender,
					"recipients": MAIL,
					"subject": "Báo giá",
					"content": "Cho xin báo giá",
					"message_id": mid,
					"communication_date": add_to_date(now_datetime(), minutes=-minutes_ago),
					"reference_doctype": ref[0],
					"reference_name": ref[1],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def send(self, sender, doctype="CRM Lead", name=None, recipients=CUSTOMER):
		"""Như CRM: make(...) với ô FROM do giao diện điền."""
		make(
			doctype=doctype,
			name=name or self.lead.name,
			recipients=recipients,
			subject="Re: Báo giá",
			content="Em gửi anh báo giá",
			send_email=1,
			sender=sender,
			sender_full_name="Sale Thử",
		)
		comm = frappe.get_last_doc(
			"Communication", filters={"reference_name": name or self.lead.name, "sent_or_received": "Sent"}
		)
		raw = frappe.db.get_value("Email Queue", {"communication": comm.name}, "message")
		return comm, email.message_from_string(raw, policy=policy.default)

	def test_tra_loi_noi_luong_va_sua_from_la_dia_chi_khach(self):
		parent = self.received(f"Lê Khách <{CUSTOMER}>", "tdoe-1@mail.example", 30)
		comm, msg = self.send(sender=CUSTOMER)  # bấm Trả lời: CRM điền FROM = địa chỉ khách
		self.assertEqual(comm.in_reply_to, parent)
		self.assertEqual((comm.sender, comm.email_account), (MAIL, ACCOUNT))
		self.assertEqual(msg["In-Reply-To"], "<tdoe-1@mail.example>")
		self.assertIn(MAIL, msg["From"])
		self.assertNotIn(CUSTOMER, msg["From"])

	def test_uu_tien_thu_cua_nguoi_dang_duoc_tra_loi(self):
		mine = self.received(CUSTOMER, "tdoe-cu@mail.example", 60)
		self.received(OTHER, "tdoe-moi@mail.example", 5)  # mới hơn nhưng của người khác
		comm, _ = self.send(sender=MAIL)
		self.assertEqual(comm.in_reply_to, mine)

	def test_bo_qua_ma_thay_the_khong_phai_message_id(self):
		self.received(CUSTOMER, "id:18f0abc", 5)  # intake dùng "id:..." khi thư không có Message-ID
		comm, msg = self.send(sender=MAIL)
		self.assertFalse(comm.in_reply_to)
		self.assertIsNone(msg["In-Reply-To"])

	def test_thu_moi_khong_co_thu_den_thi_giu_nguyen(self):
		comm, msg = self.send(sender=MAIL)
		self.assertFalse(comm.in_reply_to)
		self.assertEqual(comm.sender, MAIL)
		self.assertIsNone(msg["In-Reply-To"])

	def test_from_la_email_dang_nhap_dung_hop_thu_lien_ket_voi_nguoi_dung(self):
		user = frappe.get_doc("User", "Administrator")
		user.append("user_emails", {"email_account": ACCOUNT})
		user.save(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "User Email", {"parent": "Administrator", "email_account": ACCOUNT})
		comm, msg = self.send(sender="admin@example.com")  # chưa chọn FROM: CRM dùng email đăng nhập
		self.assertEqual((comm.sender, comm.email_account), (MAIL, ACCOUNT))
		self.assertIn(MAIL, msg["From"])

	def test_deal_noi_vao_thu_khach_gui_luc_con_la_lead(self):
		parent = self.received(CUSTOMER, "tdoe-lead@mail.example", 10)
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": self.lead.name}).insert(ignore_permissions=True)
		comm, msg = self.send(sender=MAIL, doctype="CRM Deal", name=deal.name)
		self.assertEqual(comm.in_reply_to, parent)
		self.assertEqual(msg["In-Reply-To"], "<tdoe-lead@mail.example>")

	def test_khong_dong_toi_thu_den_va_loi_khong_chan_gui(self):
		name = self.received(CUSTOMER, "tdoe-2@mail.example", 1)
		self.assertFalse(frappe.db.get_value("Communication", name, "in_reply_to"))
		with (
			mock.patch.object(oe, "last_received", side_effect=RuntimeError("x")),
			mock.patch.object(oe.frappe, "log_error") as le,
		):
			comm, _ = self.send(sender=MAIL)
		le.assert_called_once()
		self.assertEqual(comm.sender, MAIL)
