import frappe
from frappe.model.document import Document


class OmniOutboundMessage(Document):
	pass


def on_doctype_update() -> None:
	"""Chỉ mục (lead, creation) cho Hộp thư Omni."""
	frappe.db.add_index("Omni Outbound Message", ["lead", "creation"], "lead_creation_index")
