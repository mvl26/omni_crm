"""Hỗ trợ test lead quảng cáo (V2-05): webhook, worker và dọn dữ liệu. Dữ liệu được commit thật nên phải dọn thủ công.

Mọi payload là MẪU theo tài liệu Meta/Google (đối chiếu 2026-11-02), KHÔNG phải phản hồi từ nền tảng thật.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.pipeline.worker import process_identity

PREFIX = "TD505"
FIXTURES = Path(__file__).parent / "fixtures"
APP_SECRET = "td505-meta-app-secret-khong-that"
VERIFY_TOKEN = "td505-verify-token-khong-that"
PAGE_TOKEN = "td505-page-token-khong-that-" + "x" * 20
GOOGLE_KEY = "td505-google-key-khong-that"
LEAD_SOURCES = ("Quảng cáo Meta", "Quảng cáo Google", "Website")
SALE = "td505_sale@example.com"
SALE2 = "td505_sale2@example.com"


def load(path: str) -> dict:
	return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def body(data: dict) -> bytes:
	return json.dumps(data, ensure_ascii=False).encode()


def cleanup() -> None:
	"""Xóa mọi dữ liệu test V2-05 và commit."""
	frappe.db.rollback()
	frappe.set_user("Administrator")
	frappe.flags.pop("omni_web_submission", None)
	channels = frappe.get_all("Omni Channel", filters={"channel_name": ["like", f"{PREFIX}%"]}, pluck="name")
	idents = frappe.get_all("Omni Identity", filters={"identity_key": ["like", f"%:{PREFIX}%"]}, pluck="name")
	leads = {
		x
		for x in frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead")
		if x
	}
	leads.update(
		frappe.get_all("Omni Inbound Message", filters={"channel": ["in", channels or [""]]}, pluck="lead")
	)
	leads.update(frappe.get_all("CRM Lead", filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name"))
	leads.update(
		frappe.get_all("CRM Lead", filters={"facebook_lead_id": ["like", f"{PREFIX}%"]}, pluck="name")
	)
	leads.update(frappe.get_all("CRM Lead", filters={"organization": ["like", f"%{PREFIX}%"]}, pluck="name"))
	leads.discard(None)
	lead_list = list(leads) or [""]
	deals = frappe.get_all("CRM Deal", filters={"lead": ["in", lead_list]}, pluck="name")
	refs = lead_list + deals
	for dt in ("Comment", "Communication", "ToDo", "Notification Log"):
		field = "document_name" if dt == "Notification Log" else "reference_name"
		frappe.db.delete(dt, {field: ["in", refs]})
	for table in ("CRM Notification",):
		if frappe.db.exists("DocType", table):
			frappe.db.delete(table, {"reference_name": ["in", refs]})
	frappe.db.delete("Omni Care Log", {"lead": ["in", lead_list]})
	frappe.db.delete("Omni Inbound Message", {"channel": ["in", channels or [""]]})
	frappe.db.delete("Omni Outbound Message", {"channel": ["in", channels or [""]]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_a": ["in", lead_list]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_b": ["in", lead_list]})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	frappe.db.delete("CRM Deal", {"name": ["in", deals or [""]]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	for contact in frappe.get_all("Contact", filters={"first_name": ["like", f"{PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("Contact", contact, force=True, ignore_permissions=True, delete_permanently=True)
	for org in frappe.get_all(
		"CRM Organization",
		or_filters=[["organization_name", "like", f"%{PREFIX}%"], ["omni_tax_id", "like", "05050505%"]],
		pluck="name",
	):
		frappe.delete_doc(
			"CRM Organization", org, force=True, ignore_permissions=True, delete_permanently=True
		)
	frappe.db.delete("Omni Lead Form", {"form_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Campaign", {"campaign_name": ["like", f"%{PREFIX}%"]})
	frappe.db.delete("Omni Campaign", {"external_campaign_id": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Lead Form"})
	frappe.db.delete("ToDo", {"allocated_to": ["in", [SALE, SALE2]]})
	for u in (SALE, SALE2):
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.commit()


def make_users() -> None:
	with mock.patch("frappe.enqueue"):
		for email in (SALE, SALE2):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": f"{PREFIX} Sale",
						"send_welcome_email": 0,
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)
	frappe.db.commit()


def make_channel(channel_type: str, name: str, **kw):
	base = {
		"Meta Lead Ads": {
			"meta_page_id": f"{PREFIX}PAGE",
			"meta_app_secret": APP_SECRET,
			"meta_verify_token": VERIFY_TOKEN,
			"meta_page_access_token": PAGE_TOKEN,
		},
		"Google Lead Form": {},
		"Web Form": {},
	}[channel_type]
	doc = frappe.get_doc(
		{
			"doctype": "Omni Channel",
			"channel_name": name,
			"channel_type": channel_type,
			"enabled": 1,
			**base,
			**kw,
		}
	).insert()
	frappe.db.commit()
	return doc


def make_form(platform: str, external_id: str, name: str, field_map=None, **kw):
	doc = frappe.get_doc(
		{
			"doctype": "Omni Lead Form",
			"platform": platform,
			"external_form_id": external_id,
			"form_name": f"{PREFIX} {name}",
			"enabled": 1,
			"field_map": [{"source_key": k, "target": v} for k, v in (field_map or {}).items()],
			**kw,
		}
	).insert()
	frappe.db.commit()
	return doc


class FakeResponse:
	def __init__(self, payload, status=200):
		self.payload, self.status_code = payload, status

	def json(self):
		return self.payload


def graph_router(lead=None, ad=None, disclaimer=None, calls=None, fail=None):
	"""requests.get giả cho Graph API theo đường dẫn; `fail` = (trạng thái, payload) cho mọi lời gọi."""
	lead = lead if lead is not None else load("meta_leads/graph_lead.json")
	ad = ad if ad is not None else load("meta_leads/graph_ad.json")
	disclaimer = disclaimer if disclaimer is not None else load("meta_leads/graph_disclaimer.json")

	def fake_get(url, params=None, headers=None, timeout=None, **kw):
		if calls is not None:
			calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
		if fail:
			return FakeResponse(fail[1], fail[0])
		path = url.split("graph.facebook.com/", 1)[1].split("/", 1)[1]
		if path.endswith("/leads"):
			return FakeResponse({"data": lead if isinstance(lead, list) else [lead]})
		if path.startswith(lead["id"] if isinstance(lead, dict) else "~~"):
			if params and params.get("fields") == "custom_disclaimer_responses":
				return FakeResponse(disclaimer)
			return FakeResponse(lead)
		if path.startswith(ad["id"]):
			return FakeResponse(ad)
		return FakeResponse({"error": {"code": 100, "message": "x"}}, 400)

	return fake_get


def call_webhook(fn, raw: bytes, headers=None, method="POST", args=None, ip="10.9.9.9"):
	"""Gọi hàm webhook với request giả; trả (kết quả, mã HTTP). enqueue được giả để không đẩy vào Redis."""
	args = args or {}
	frappe.local.request = SimpleNamespace(
		method=method, content_length=len(raw), get_data=lambda: raw, headers=headers or {}, args=args
	)
	frappe.local.request_ip = ip
	frappe.local.form_dict = frappe._dict(args)
	frappe.local.response = frappe._dict()
	with mock.patch("frappe.enqueue"):
		result = fn()
		frappe.db.after_commit.run()
	return result, frappe.local.response.get("http_status_code", 200)


def work(identity_key: str) -> str:
	"""Chạy worker đồng bộ cho một định danh (không chờ debounce)."""
	with mock.patch("frappe.enqueue"):
		return process_identity(identity_key, debounce_seconds=0)


def inbound(channel: str) -> list:
	return frappe.get_all(
		"Omni Inbound Message",
		filters={"channel": channel},
		fields=[
			"name",
			"status",
			"lead",
			"identity_key",
			"event_type",
			"last_error",
			"text",
			"external_message_id",
		],
	)


class AdsCase(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		self.addCleanup(frappe.clear_document_cache, "Omni Settings", "Omni Settings")
		frappe.cache().delete_keys("omni:")
