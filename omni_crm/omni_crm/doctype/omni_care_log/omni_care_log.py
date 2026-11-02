import frappe
from frappe.model.document import Document


class OmniCareLog(Document):
	"""Một phiên chăm sóc khách (docs/13 mục 3.2). Ghi qua `crm_bridge/care_log.py` và `api/customer.py`."""


def on_doctype_update() -> None:
	"""Chỉ mục (lead, last_activity_at): tìm phiên đang mở và liệt kê nhật ký của một Lead."""
	frappe.db.add_index("Omni Care Log", ["lead", "last_activity_at"], "lead_last_activity_index")
	frappe.db.add_index("Omni Care Log", ["handled_by", "outcome"], "handled_by_outcome_index")
