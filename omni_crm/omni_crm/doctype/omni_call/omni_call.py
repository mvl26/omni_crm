import frappe
from frappe import _
from frappe.model.document import Document


class OmniCall(Document):
	def validate(self) -> None:
		if self.duration_seconds is not None and self.duration_seconds < 0:
			frappe.throw(_("Thời lượng cuộc gọi không được âm"))
