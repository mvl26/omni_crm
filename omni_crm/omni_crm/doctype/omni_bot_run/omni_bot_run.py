from frappe.model.document import Document


class OmniBotRun(Document):
	"""Nhật ký mỗi lần gọi AI (kể cả lần bị bỏ qua); chỉ hệ thống ghi."""
