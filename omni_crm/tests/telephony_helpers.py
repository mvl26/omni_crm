"""Hỗ trợ test kênh tổng đài (D2-01): dữ liệu commit thật nên phải dọn thủ công."""

import json
import time
from types import SimpleNamespace
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import webhooks
from omni_crm.channels.telephony.fake import SIGN_HEADER, SIGN_VALUE
from omni_crm.pipeline.worker import process_identity

PREFIX = "TD201"
TOKEN = "tok-td201-" + "x" * 30  # >= 32 ký tự
CUSTOMER = "0903201001"
CUSTOMER_E164 = "+84903201001"
HOTLINE = "02873001234"
AGENT = "A100"
INBOUND = "Omni Inbound Message"
NUMBERS = ("+84903201001", "+84903201002", "+84903201003")


def cleanup() -> None:
	"""Xóa mọi dữ liệu test D2-01 và commit."""
	frappe.db.rollback()
	frappe.set_user("Administrator")
	keys = [f"Telephony:{n}" for n in NUMBERS]
	idents = frappe.get_all("Omni Identity", filters={"identity_key": ["in", keys]}, pluck="name")
	leads = {
		x
		for x in frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead")
		if x
	}
	leads.update(frappe.get_all("CRM Lead", filters={"omni_phone_e164": ["in", list(NUMBERS)]}, pluck="name"))
	lead_list = list(leads) or [""]
	deals = frappe.get_all("CRM Deal", filters={"lead": ["in", lead_list]}, pluck="name")
	refs = lead_list + deals
	logs = frappe.get_all("CRM Call Log", filters={"id": ["like", "%td201-%"]}, pluck="name")
	frappe.db.delete("Dynamic Link", {"parenttype": "CRM Call Log", "parent": ["in", logs or [""]]})
	frappe.db.delete("CRM Call Log", {"name": ["in", logs or [""]]})
	frappe.db.delete("Omni Call", {"call_id": ["like", "td201-%"]})
	for dt in ("Comment", "Communication", "ToDo"):
		frappe.db.delete(dt, {"reference_name": ["in", refs]})
	frappe.db.delete("Omni Inbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_a": ["in", lead_list]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_b": ["in", lead_list]})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	frappe.db.delete("CRM Deal", {"name": ["in", deals or [""]]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.commit()


def make_channel(name: str = f"{PREFIX} tong dai", **kw):
	"""Kênh tổng đài Fake có token đủ dài và một nhân viên ánh xạ."""
	values = {
		"doctype": "Omni Channel",
		"channel_name": name,
		"channel_type": "Telephony",
		"telephony_provider": "Fake",
		"telephony_hotline": HOTLINE,
		"webhook_token": TOKEN,
		"enabled": 1,
		"agent_map": [{"agent_ref": AGENT, "user": "Administrator", "extension": "101"}],
	}
	values.update(kw)
	return frappe.get_doc(values).insert()


def call(call_id: str, status: str = "Ended", **over) -> dict:
	"""Payload cuộc gọi đến mẫu của FakeProvider (đã nhấc máy 125 giây nếu status = Ended)."""
	data = {
		"call_id": f"td201-{call_id}",
		"direction": "Inbound",
		"from": CUSTOMER,
		"to": HOTLINE,
		"status": status,
		"started_at": "2026-10-30 09:00:00",
		"answered_at": "2026-10-30 09:00:05",
		"ended_at": "2026-10-30 09:02:10",
		"duration": 125,
		"agent_ref": AGENT,
	}
	data.update(over)
	return data


def fake_request(payload, args: dict | None = None, headers: dict | None = None, method: str = "POST"):
	"""Yêu cầu giả: `args` là query string thật (Frappe bỏ query khi thân là JSON)."""
	body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
	return SimpleNamespace(
		method=method,
		content_length=len(body),
		get_data=lambda: body,
		headers={SIGN_HEADER: SIGN_VALUE} if headers is None else headers,
		args=args or {},
		host="omni.test",
	)


class TelephonyTestCase(FrappeTestCase):
	"""Kênh Fake bật sẵn; tiện ích gửi webhook và chạy worker (mock mọi enqueue)."""

	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		self.channel = make_channel()
		frappe.db.commit()
		frappe.cache().delete_keys("omni:")
		self._old_req = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", self._old_req)

	def args(self, **over) -> dict:
		return {"ch": self.channel.name, "t": TOKEN, **over}

	def post(self, payload, args: dict | None = None, headers: dict | None = None) -> int:
		"""Gọi telephony_event như webhook thật (token ở query string, form_dict rỗng); trả mã HTTP."""
		frappe.local.request = fake_request(payload, self.args() if args is None else args, headers)
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = frappe._dict()
		with mock.patch("frappe.enqueue") as self.enqueue:
			webhooks.telephony_event()
			frappe.db.after_commit.run()
		frappe.db.commit()
		return frappe.local.response.get("http_status_code", 200)

	def run_worker(self, number: str = CUSTOMER_E164) -> None:
		with mock.patch("frappe.enqueue"):
			process_identity(f"Telephony:{number}", debounce_seconds=0)

	def inbound(self) -> list:
		return frappe.get_all(
			INBOUND,
			filters={"channel": self.channel.name},
			fields=["name", "status", "event_type", "dedupe_key", "lead", "external_message_id"],
			order_by="creation",
		)

	def lookup(self, args: dict | None = None, method: str = "GET"):
		"""Gọi telephony_lookup; trả (mã HTTP, nội dung JSON thô, thời gian giây)."""
		q = {"from": CUSTOMER, "to": HOTLINE}
		frappe.local.request = fake_request(b"", {**self.args(), **q, **(args or {})}, method=method)
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = frappe._dict()
		started = time.monotonic()
		webhooks.telephony_lookup()
		elapsed = time.monotonic() - started
		resp = frappe.local.response
		if resp.get("filename") == "lookup.json":
			body = json.loads(resp["filecontent"])
		else:
			body = {k: v for k, v in resp.items() if k != "http_status_code"}
		return frappe.local.response.get("http_status_code", 200), body, elapsed
