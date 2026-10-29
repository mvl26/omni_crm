"""Hỗ trợ test kênh Zalo (D1-08): dữ liệu commit thật nên phải dọn thủ công."""

import hashlib
import json
import time
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.channels.zalo.adapter import ZaloAdapter, parse_event
from omni_crm.identity.normalize import normalize_sender
from omni_crm.ingest.gateway import save_inbound
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests.fake_adapter import make_channel

PREFIX = "TD108"
APP_ID = "app-td108"
OA_SECRET = "oa-secret-td108-khong-that"
APP_SECRET = "app-secret-td108-khong-that"
FIXTURES = Path(__file__).parent / "fixtures" / "zalo"
USER = f"{PREFIX}U1"
OA_ID = f"{PREFIX}OA"


def load(name: str, user: str = USER, **over) -> dict:
	"""Payload mẫu, thay user id mẫu bằng `user`; `over` ghi đè khóa cấp ngoài cùng."""
	text = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
	text = text.replace("3333333333333333333", user).replace("2222222222222222222", OA_ID)
	data = json.loads(text)
	data.update(over)
	return data


def body_of(data: dict) -> bytes:
	return json.dumps(data, ensure_ascii=False).encode()


def sign(raw: bytes, timestamp: str, app_id: str = APP_ID, secret: str = OA_SECRET) -> dict:
	mac = hashlib.sha256(app_id.encode() + raw + timestamp.encode() + secret.encode()).hexdigest()
	return {"X-ZEvent-Signature": f"mac={mac}"}


def now_ms() -> str:
	return str(int(time.time() * 1000))


def cleanup() -> None:
	"""Xóa mọi dữ liệu test D1-08 và commit."""
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
	for dt in ("Comment", "Communication", "ToDo"):
		frappe.db.delete(dt, {"reference_name": ["in", refs]})
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
	frappe.db.commit()


def response(payload: dict, status: int = 200) -> mock.Mock:
	"""Phản hồi `requests` giả."""
	r = mock.Mock()
	r.status_code = status
	r.json.return_value = payload
	return r


OK_SEND = {"error": 0, "message": "Success", "data": {"message_id": "zl-sent-1", "user_id": USER}}
TOKEN_ERR = {"error": -216, "message": "Access token is invalid"}


class ZaloTestCase(FrappeTestCase):
	"""Dọn trước/sau mỗi test; kênh mẫu có token; tiện ích đưa sự kiện qua worker thật."""

	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		self.channel = make_channel(
			f"{PREFIX} kenh",
			enabled=1,
			outbound_enabled=1,
			zalo_app_id=APP_ID,
			zalo_app_secret=APP_SECRET,
			zalo_oa_secret_key=OA_SECRET,
			zalo_access_token="access-OLD-td108",
			zalo_refresh_token="refresh-OLD-td108",
			zalo_token_expires_at=frappe.utils.add_to_date(None, hours=20),
		)
		frappe.db.commit()
		frappe.cache().delete_keys("omni:")
		self.adapter = ZaloAdapter()
		# site test bật sandbox mặc định; test kênh cần HTTP (mock) nên mặc định tắt, test sandbox tự bật lại
		patcher = mock.patch("omni_crm.flags.sandbox", return_value=False)
		patcher.start()
		self.addCleanup(patcher.stop)

	def deliver(self, data: dict, profile: dict | None = None, process: bool = True) -> str:
		"""Parse, lưu, rồi chạy worker. `profile` != None: API hồ sơ Zalo trả giá trị này; None: không có token gọi được."""
		channel_doc = frappe.get_doc("Omni Channel", self.channel.name)
		event = parse_event(channel_doc, data)
		with mock.patch("frappe.enqueue"):
			name = save_inbound(event)
			frappe.db.after_commit.run()
		frappe.db.commit()
		if process:
			self.run_worker(event.external_sender_id, profile)
		return name

	def run_worker(self, user: str, profile: dict | None = None) -> None:
		reply = (
			response({"error": 0, "data": profile or {}})
			if profile is not None
			else response({"error": -201, "message": "loi"})
		)
		with (
			mock.patch("frappe.enqueue"),
			mock.patch("omni_crm.channels.zalo.client.requests.request", return_value=reply) as self.http,
		):
			process_identity(f"Zalo OA:{normalize_sender('Zalo OA', user)}", debounce_seconds=0)

	def identity(self, user: str = USER):
		name = frappe.db.get_value("Omni Identity", {"identity_key": f"Zalo OA:{user}"})
		return frappe.get_doc("Omni Identity", name) if name else None

	def lead_of(self, msg: str) -> str:
		return frappe.db.get_value("Omni Inbound Message", msg, "lead")
