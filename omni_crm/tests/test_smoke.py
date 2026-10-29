import frappe
from frappe.tests.utils import FrappeTestCase


class TestSmoke(FrappeTestCase):
	def test_apps_installed(self):
		apps = frappe.get_installed_apps()
		self.assertIn("omni_crm", apps)
		self.assertIn("crm", apps)

	def test_module_exists(self):
		self.assertTrue(frappe.db.exists("Module Def", "Omni CRM"))

	def test_roles_exist(self):
		for role in ("Omni Admin", "Omni Knowledge Editor"):
			self.assertTrue(frappe.db.exists("Role", role), role)
			self.assertEqual(frappe.db.get_value("Role", role, "desk_access"), 1)
