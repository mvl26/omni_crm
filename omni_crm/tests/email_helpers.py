"""Hỗ trợ test kênh email (D1-07): thư giả đi qua InboundMail thật của Frappe, dữ liệu được commit nên dọn thủ công."""

import json
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from unittest import mock

import frappe
from frappe.email.receive import InboundMail
from frappe.tests.utils import FrappeTestCase

from omni_crm.identity.normalize import normalize_sender
from omni_crm.pipeline.worker import process_identity

PREFIX = "TD107"
ACCOUNT = f"{PREFIX} hop thu"
ACCOUNT_EMAIL = "td107.sales@example.org"
CHANNEL = f"{PREFIX} kenh"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "email"


def load_fixtures() -> list[dict]:
	"""Mọi thư mẫu trong tests/fixtures/email, theo tên tệp."""
	out = []
	for path in sorted(FIXTURE_DIR.glob("*.json")):
		data = json.loads(path.read_text(encoding="utf-8"))
		data["file"] = path.name
		out.append(data)
	return out


def build_raw(
	sender: str,
	subject: str,
	body: str,
	*,
	sender_name: str = "",
	html: bool = False,
	message_id: str | None = None,
	in_reply_to: str | None = None,
	headers: dict | None = None,
	to: str = ACCOUNT_EMAIL,
) -> tuple[str, str]:
	"""Dựng thư RFC822 giả; trả (nội dung, Message-ID)."""
	msg = EmailMessage()
	msg["From"] = f"{sender_name} <{sender}>" if sender_name else sender
	msg["To"] = to
	msg["Subject"] = subject
	msg["Date"] = formatdate(localtime=False)
	mid = message_id or make_msgid(domain="example.com")
	msg["Message-ID"] = mid
	if in_reply_to:
		msg["In-Reply-To"] = in_reply_to
	for k, v in (headers or {}).items():
		msg[k] = v
	msg.set_content(body, subtype="html" if html else "plain")
	return msg.as_string(), mid


def cleanup() -> None:
	"""Xóa mọi dữ liệu test D1-07 và commit."""
	frappe.db.rollback()
	frappe.set_user("Administrator")
	idents = frappe.get_all("Omni Identity", filters={"identity_key": ["like", f"%:{PREFIX}%"]}, pluck="name")
	leads = {
		x
		for x in frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead")
		if x
	}
	leads.update(frappe.get_all("Omni Inbound Message", filters={"channel": CHANNEL}, pluck="lead"))
	leads.update(frappe.get_all("CRM Lead", filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name"))
	leads.update(frappe.get_all("CRM Lead", filters={"email": ["like", f"{PREFIX.lower()}%"]}, pluck="name"))
	leads.discard(None)
	lead_list = list(leads) or [""]
	comms = frappe.get_all("Communication", filters={"email_account": ACCOUNT}, pluck="name") or [""]
	for dt, f in (
		("Comment", "reference_name"),
		("Communication", "reference_name"),
		("ToDo", "reference_name"),
	):
		frappe.db.delete(dt, {f: ["in", lead_list]})
	frappe.db.delete("Omni Inbound Message", {"channel": CHANNEL})
	frappe.db.delete("Communication", {"name": ["in", comms]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_a": ["in", lead_list]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_b": ["in", lead_list]})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Omni Channel", {"channel_name": CHANNEL})
	if frappe.db.exists("Email Account", ACCOUNT):
		frappe.delete_doc("Email Account", ACCOUNT, force=True, ignore_permissions=True)
	frappe.db.delete("Error Log", {"method": ["like", "Omni: lỗi đưa email%"]})
	frappe.db.commit()


class EmailTestCase(FrappeTestCase):
	"""Tạo Email Account giả + Omni Channel Email; tiện ích đưa thư qua InboundMail rồi qua worker."""

	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		self.account = self.make_account()
		self.channel = self.make_channel()
		frappe.db.commit()
		frappe.cache().delete_keys("omni:")

	def make_account(self, **kw):
		doc = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": ACCOUNT,
				"email_id": ACCOUNT_EMAIL,
				"enable_incoming": 0,
				"enable_outgoing": 0,
				"default_incoming": 0,
				"default_outgoing": 0,
				"password": "khong-co-that",
				**kw,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		return doc

	def make_channel(self, **kw):
		return frappe.get_doc(
			{
				"doctype": "Omni Channel",
				"channel_name": CHANNEL,
				"channel_type": "Email",
				"email_account": ACCOUNT,
				"enabled": 1,
				**kw,
			}
		).insert()

	def receive(self, raw: str):
		"""Nhận một thư như luồng nhận thư của Frappe (InboundMail.process); trả Communication."""
		with mock.patch("frappe.enqueue"):
			comm = InboundMail(raw, frappe.get_doc("Email Account", ACCOUNT)).process()
			frappe.db.commit()
		return comm

	def run_worker(self, sender: str) -> None:
		"""Chạy worker cho định danh email của người gửi (như job nền)."""
		# CRM `on_comment_insert` gọi frappe.enqueue (Redis queue của bench dev đang hỏng): mock
		with mock.patch("frappe.enqueue"):
			process_identity(f"Email:{normalize_sender('Email', sender)}", debounce_seconds=0)

	def deliver(self, sender: str, subject: str, body: str = "Cho tôi xin báo giá xe nâng.", **kw):
		"""Nhận thư rồi xử lý bằng worker; trả (Communication, Omni Inbound Message)."""
		raw, _mid = build_raw(sender, subject, body, **kw)
		comm = self.receive(raw)
		self.run_worker(sender)
		msg = frappe.db.get_value("Omni Inbound Message", {"communication": comm.name}, "name")
		return comm, msg

	def lead_of(self, msg: str | None) -> str | None:
		return frappe.db.get_value("Omni Inbound Message", msg, "lead") if msg else None

	def lead_count(self, email_like: str = "td107") -> int:
		return frappe.db.count("CRM Lead", {"email": ["like", f"{email_like}%"]})
