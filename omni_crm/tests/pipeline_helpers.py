"""Hỗ trợ test đường xử lý tin: dữ liệu được commit thật nên phải dọn thủ công."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.ingest.gateway import save_inbound
from omni_crm.tests.fake_adapter import make_channel, make_event

PREFIX = "PLT"


def cleanup_pipeline_data() -> None:
	"""Xóa dữ liệu test (tin, định danh, kênh) có tiền tố PLT và commit."""
	frappe.db.rollback()
	frappe.db.delete("Omni Inbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Identity", {"identity_key": ["like", f"%:{PREFIX}%"]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.commit()


class PipelineTestCase(FrappeTestCase):
	"""Tạo kênh bật và tiện ích thêm tin; mỗi test tự commit và dọn."""

	def setUp(self):
		cleanup_pipeline_data()
		self.addCleanup(cleanup_pipeline_data)
		self.channel = make_channel(f"{PREFIX} kenh", enabled=1)
		frappe.db.commit()
		frappe.cache().delete_keys("omni:")

	def add_msg(self, ext_id: str, sender: str = f"{PREFIX}U1", commit: bool = True) -> str:
		"""Lưu một tin New (không enqueue thật) và commit."""
		with mock.patch("frappe.enqueue"):
			name = save_inbound(make_event(self.channel.name, ext_id, sender=sender))
			frappe.db.after_commit.run()
		if commit:
			frappe.db.commit()
		return name

	def status_of(self, *names: str) -> list[str]:
		return [frappe.db.get_value("Omni Inbound Message", n, "status") for n in names]
