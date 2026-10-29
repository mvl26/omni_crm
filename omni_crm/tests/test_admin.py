from unittest import mock

import frappe

from omni_crm.api import admin
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase

SALES = "plt_sales@example.com"
ADMIN = "plt_admin@example.com"


def _user(email: str, role: str) -> None:
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


class TestAdminReprocess(PipelineTestCase):
	def setUp(self):
		super().setUp()
		_user(SALES, "Sales User")
		_user(ADMIN, "Omni Admin")
		self.addCleanup(self._drop_users)
		self.addCleanup(frappe.set_user, "Administrator")

	def _drop_users(self):
		frappe.set_user("Administrator")
		for u in (SALES, ADMIN):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_sales_user_denied(self):
		name = self.add_msg("a1")
		frappe.db.set_value("Omni Inbound Message", name, "status", "Manual")
		frappe.db.commit()
		frappe.set_user(SALES)
		with self.assertRaises(frappe.PermissionError):
			admin.reprocess([name])
		frappe.set_user("Administrator")
		self.assertEqual(self.status_of(name), ["Manual"])

	def test_admin_resets_manual_and_retry_only(self):
		manual, retry, done = self.add_msg("m"), self.add_msg("r"), self.add_msg("d")
		frappe.db.set_value("Omni Inbound Message", manual, {"status": "Manual", "attempts": 3})
		frappe.db.set_value("Omni Inbound Message", retry, {"status": "Retry", "attempts": 1})
		frappe.db.set_value("Omni Inbound Message", done, "status", "Done")
		frappe.db.commit()
		frappe.set_user(ADMIN)
		with mock.patch("omni_crm.api.admin.enqueue_identity") as enq:
			res = admin.reprocess(frappe.as_json([manual, retry, done]))
		frappe.set_user("Administrator")
		self.assertEqual(res, {"reset": 2, "skipped": 1})
		self.assertEqual(self.status_of(manual, retry, done), ["New", "New", "Done"])
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", manual, "attempts"), 0)
		enq.assert_called_once_with(f"Zalo OA:{PREFIX}U1")

	def test_too_many_names_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			admin.reprocess([f"x{i}" for i in range(admin.MAX_REPROCESS + 1)])
