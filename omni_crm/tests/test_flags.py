import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm import flags


class TestFlags(FrappeTestCase):
	def _channel(self, name: str, **kw):
		return frappe.get_doc(
			{
				"doctype": "Omni Channel",
				"channel_name": name,
				"channel_type": "Zalo OA",
				"zalo_app_id": "a",
				**kw,
			}
		).insert()

	def test_channel_enabled_reflects_save_immediately(self):
		channel = self._channel("Flag channel", enabled=1, outbound_enabled=1)
		self.assertTrue(flags.channel_enabled(channel.name))
		self.assertTrue(flags.outbound_enabled(channel.name))
		channel.enabled = 0
		channel.save()
		self.assertFalse(flags.channel_enabled(channel.name))
		self.assertTrue(flags.outbound_enabled(channel.name))
		channel.outbound_enabled = 0
		channel.save()
		self.assertFalse(flags.outbound_enabled(channel.name))
		channel.enabled = 1
		channel.save()
		self.assertTrue(flags.channel_enabled(channel.name))

	def test_unknown_channel_is_disabled(self):
		self.assertFalse(flags.channel_enabled("khong-ton-tai"))
		self.assertFalse(flags.outbound_enabled("khong-ton-tai"))
		self.assertFalse(flags.channel_enabled(""))

	def test_sandbox_default_on_and_follows_settings(self):
		self.assertTrue(flags.sandbox())
		settings = frappe.get_doc("Omni Settings")
		settings.sandbox_mode = 0
		settings.save()
		self.assertFalse(flags.sandbox())
		settings.sandbox_mode = 1
		settings.save()
		self.assertTrue(flags.sandbox())
