import frappe
from frappe import _
from frappe.model.document import Document


class OmniDuplicateCandidate(Document):
	def validate(self) -> None:
		self.order_pair()
		if self.score is not None and not 0 <= self.score <= 100:
			frappe.throw(_("Độ giống phải từ 0 đến 100"))

	def order_pair(self) -> None:
		"""Sắp lead_a < lead_b theo tên để không có cặp đảo."""
		if self.lead_a == self.lead_b:
			frappe.throw(_("Hai Lead của cặp nghi trùng phải khác nhau"))
		if self.lead_a > self.lead_b:
			self.lead_a, self.lead_b = self.lead_b, self.lead_a


def on_doctype_update() -> None:
	"""Ràng buộc duy nhất ở mức DB cho cặp (lead_a, lead_b)."""
	frappe.db.add_unique("Omni Duplicate Candidate", ["lead_a", "lead_b"], "unique_lead_pair")
