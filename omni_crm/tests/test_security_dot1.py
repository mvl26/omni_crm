"""D1-11: kiểm tra bảo mật nhanh. Quyền ở tầng hàm (trong tiến trình); tầng HTTP (Guest bị 403 ở hàm không
allow_guest) được kiểm bằng script qua gunicorn thật, kết quả ghi ở docs/nghiem-thu/dot-1.md.
"""

from types import SimpleNamespace
from unittest import mock

import frappe

from omni_crm.api import admin, inbox, webhooks
from omni_crm.identity import duplicates
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase

SALES = "plt_sec_sales@example.com"
INBOUND = "Omni Inbound Message"


def _make_user(email: str, role: str) -> None:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "PLT",
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()


class TestAccess(PipelineTestCase):
	def setUp(self):
		super().setUp()
		_make_user(SALES, "Sales User")
		self.addCleanup(self._drop)
		self.addCleanup(frappe.set_user, "Administrator")

	def _drop(self):
		frappe.set_user("Administrator")
		frappe.delete_doc("User", SALES, force=True, ignore_permissions=True)
		frappe.db.commit()

	ADMIN_CALLS = (
		(admin.reprocess, (["x"],)),
		(admin.monitor_snapshot, ()),
		(admin.list_manual, ()),
		(admin.sweep_now, ()),
		(admin.refresh_zalo_token, ("kenh",)),
		(admin.erase_customer_data, ("CRM-LEAD-x", "ly do xoa du lieu")),
	)

	def test_admin_api_denied_for_guest_and_sales_user(self):
		name = self.add_msg("sec1")
		frappe.db.set_value(INBOUND, name, "status", "Manual")
		frappe.db.commit()
		for user in ("Guest", SALES):
			frappe.set_user(user)
			for fn, args in self.ADMIN_CALLS:
				with self.subTest(user=user, fn=fn.__name__), self.assertRaises(frappe.PermissionError):
					fn(*args)
			with self.subTest(user=user, fn="zalo_oauth_start"), self.assertRaises(frappe.PermissionError):
				with mock.patch("omni_crm.api.webhooks._redirect"):
					webhooks.zalo_oauth_start(self.channel.name)
			with self.subTest(user=user, fn="zalo_oauth_callback"), self.assertRaises(frappe.PermissionError):
				webhooks.zalo_oauth_callback("code", "state")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(INBOUND, name, "status"), "Manual", "không được đổi dữ liệu")

	def test_inbox_and_duplicate_api_denied_for_guest(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": f"{PREFIX} bao mat"}).insert(
			ignore_permissions=True
		)
		frappe.db.commit()
		frappe.set_user("Guest")
		for fn, args in (
			(inbox.get_thread, (lead.name,)),
			(inbox.send_reply, (lead.name, "Zalo OA", "x")),
			(inbox.request_phone, (lead.name,)),
			(inbox.list_conversations, ()),
		):
			with self.subTest(fn=fn.__name__), self.assertRaises(frappe.PermissionError):
				fn(*args)
		frappe.set_user("Administrator")
		frappe.delete_doc("CRM Lead", lead.name, force=True, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	def test_sales_user_sees_no_threads_of_unowned_lead(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": f"{PREFIX} cua nguoi khac"}).insert(
			ignore_permissions=True
		)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.set_user("Administrator"),
				frappe.delete_doc(
					"CRM Lead", lead.name, force=True, ignore_permissions=True, delete_permanently=True
				),
				frappe.db.commit(),
			)
		)
		frappe.set_user(SALES)
		for fn, args in (
			(inbox.get_thread, (lead.name,)),
			(inbox.send_reply, (lead.name, "Zalo OA", "x")),
		):
			with self.subTest(fn=fn.__name__), self.assertRaises(frappe.PermissionError):
				fn(*args)


class TestWebhookExposure(PipelineTestCase):
	"""Webhook là điểm duy nhất cho Guest: không chữ ký thì không có bản ghi nào (I-10 kiểm sâu hơn)."""

	def test_only_webhook_allows_guest(self):
		allow = []
		for mod in (admin, inbox, webhooks, duplicates):
			for attr in dir(mod):
				fn = getattr(mod, attr)
				if callable(fn) and fn in frappe.guest_methods:
					allow.append(f"{mod.__name__}.{attr}")
		self.assertEqual(allow, ["omni_crm.api.webhooks.zalo"])

	def test_unsigned_body_creates_nothing(self):
		raw = b'{"event_name":"user_send_text","timestamp":"1"}'
		frappe.local.request = SimpleNamespace(
			method="POST", content_length=len(raw), get_data=lambda: raw, headers={}
		)
		frappe.local.form_dict = frappe._dict(ch=self.channel.name)
		frappe.local.response = frappe._dict()
		before = frappe.db.count(INBOUND)
		webhooks.zalo()
		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(frappe.db.count(INBOUND), before)
