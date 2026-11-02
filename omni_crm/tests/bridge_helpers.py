"""Hỗ trợ test cầu nối CRM (D1-06): dữ liệu được commit thật nên phải dọn thủ công."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.identity.normalize import normalize_sender
from omni_crm.ingest.events import InboundEvent
from omni_crm.ingest.gateway import save_inbound
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests.fake_adapter import make_channel

PREFIX = "TD106"
USERS = [f"td106_sale{i}@example.com" for i in (1, 2, 3)]
SOURCE = "TD106 nguon"


def cleanup() -> None:
	"""Xóa mọi dữ liệu test D1-06 và commit."""
	frappe.db.rollback()
	frappe.set_user("Administrator")
	idents = frappe.get_all("Omni Identity", filters={"identity_key": ["like", f"%:{PREFIX}%"]}, pluck="name")
	leads = {
		x
		for x in frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead")
		if x
	}
	leads.update(frappe.get_all("CRM Lead", filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name"))
	leads.update(
		frappe.get_all("Omni Inbound Message", filters={"channel": ["like", f"{PREFIX}%"]}, pluck="lead")
	)
	leads.discard(None)
	lead_list = list(leads) or [""]
	deals = frappe.get_all("CRM Deal", filters={"lead": ["in", lead_list]}, pluck="name")
	refs = lead_list + deals
	for dt, f in (
		("Comment", "reference_name"),
		("Communication", "reference_name"),
		("ToDo", "reference_name"),
	):
		frappe.db.delete(dt, {f: ["in", refs]})
	frappe.db.delete("Communication", {"subject": ["like", f"%{PREFIX}%"]})
	frappe.db.delete("Omni Inbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Outbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_a": ["in", lead_list]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_b": ["in", lead_list]})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	frappe.db.delete("CRM Deal", {"name": ["in", deals or [""]]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", USERS]})
	# V2-08: chuông của CRM (CRM Notification) sinh khi giao Lead cho người dùng test; xóa Lead mà không xóa chuông để lại
	# hàng chục nghìn dòng mồ côi trên site test.
	frappe.db.delete("CRM Notification", {"to_user": ["in", USERS]})
	frappe.db.delete(
		"CRM Notification", {"reference_doctype": "CRM Lead", "reference_name": ["in", lead_list]}
	)
	if frappe.db.exists("Assignment Rule", "td106-rule"):
		frappe.delete_doc("Assignment Rule", "td106-rule", force=True, ignore_permissions=True)
	for u in USERS:
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True, delete_permanently=True)
	if frappe.db.exists("CRM Lead Source", SOURCE):
		frappe.delete_doc("CRM Lead Source", SOURCE, force=True, ignore_permissions=True)
	frappe.db.commit()


def make_users() -> None:
	"""Tạo ba tài khoản Sales User (không gửi thư)."""
	with mock.patch("frappe.enqueue"):
		for i, email in enumerate(USERS, 1):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": f"TD106 Sale{i}",
						"send_welcome_email": 0,
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)
	frappe.db.commit()


class BridgeTestCase(FrappeTestCase):
	"""Dọn trước/sau mỗi test; tiện ích đưa tin qua worker thật."""

	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		self.channel = make_channel(f"{PREFIX} kenh", enabled=1)
		frappe.db.commit()
		frappe.cache().delete_keys("omni:")

	def send(
		self,
		ext_id: str,
		sender: str = f"{PREFIX}U1",
		text: str = "xin chào",
		event_type: str = "message",
		attachments: list | None = None,
		user: str | None = None,
	) -> str:
		"""Lưu một tin Zalo rồi chạy worker xử lý (như job nền); trả tên Omni Inbound Message."""
		event = InboundEvent(
			channel=self.channel.name,
			channel_type="Zalo OA",
			event_type=event_type,
			external_message_id=ext_id,
			external_sender_id=sender,
			received_at=now_datetime(),
			text=text,
			attachments=attachments or [],
		)
		with mock.patch("frappe.enqueue"):
			name = save_inbound(event)
			frappe.db.after_commit.run()
		frappe.db.commit()
		try:
			if user:
				frappe.set_user(user)
			# CRM `on_comment_insert` gọi frappe.enqueue (Redis queue của bench dev đang hỏng): mock
			with mock.patch("frappe.enqueue"):
				process_identity(f"Zalo OA:{normalize_sender('Zalo OA', sender)}", debounce_seconds=0)
		finally:
			frappe.set_user("Administrator")
		return name

	def lead_of(self, msg: str) -> str:
		return frappe.db.get_value("Omni Inbound Message", msg, "lead")

	def communications(self, doctype: str, name: str) -> list[dict]:
		return frappe.get_all(
			"Communication",
			filters={"reference_doctype": doctype, "reference_name": name},
			fields=[
				"name",
				"subject",
				"content",
				"sent_or_received",
				"communication_medium",
				"omni_inbound_message",
			],
			order_by="communication_date asc, creation asc",
		)
