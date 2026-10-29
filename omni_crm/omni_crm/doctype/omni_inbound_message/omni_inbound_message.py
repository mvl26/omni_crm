import frappe
from frappe.model.document import Document


class OmniInboundMessage(Document):
	pass


def on_doctype_update() -> None:
	"""Ràng buộc duy nhất cho dedupe_key và chỉ mục ghép phục vụ worker và sweeper."""
	frappe.db.add_unique("Omni Inbound Message", ["dedupe_key"], "unique_dedupe_key")
	frappe.db.add_index("Omni Inbound Message", ["identity_key", "status"], "identity_key_status_index")
	frappe.db.add_index("Omni Inbound Message", ["status", "next_attempt_at"], "status_next_attempt_at_index")
