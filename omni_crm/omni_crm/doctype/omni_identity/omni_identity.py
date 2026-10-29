import frappe
from frappe.model.document import Document


class OmniIdentity(Document):
	pass


def on_doctype_update() -> None:
	"""Ràng buộc duy nhất ở mức DB cho identity_key."""
	frappe.db.add_unique("Omni Identity", ["identity_key"], "unique_identity_key")
