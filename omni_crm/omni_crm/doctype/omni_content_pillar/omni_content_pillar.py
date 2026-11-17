"""Trụ cột nội dung của bài đăng (ADR 0029)."""

from frappe.model.document import Document


class OmniContentPillar(Document):
	def validate(self) -> None:
		self.pillar_name = (self.pillar_name or "").strip()
