import json

import frappe
from frappe import _
from frappe.model.document import Document

JSON_FIELDS = ("default_lead_source_map", "business_hours")


def parse_backoff_minutes(value: str | None) -> list[int]:
	"""Tách chuỗi "1,5" thành danh sách số nguyên dương; ném ValueError nếu sai."""
	items = [x.strip() for x in (value or "").split(",") if x.strip()]
	result = []
	for item in items:
		if not item.isdigit() or int(item) <= 0:
			raise ValueError(item)
		result.append(int(item))
	return result


class OmniSettings(Document):
	def validate(self) -> None:
		self.validate_backoff()
		self.validate_json_fields()
		self.validate_positive_numbers()

	def validate_backoff(self) -> None:
		try:
			parse_backoff_minutes(self.retry_backoff_minutes)
		except ValueError:
			frappe.throw(_("Chờ giữa các lần thử phải là danh sách số nguyên dương, ví dụ 1,5"))

	def validate_json_fields(self) -> None:
		for fieldname in JSON_FIELDS:
			value = self.get(fieldname)
			if not value:
				continue
			try:
				json.loads(value) if isinstance(value, str) else None
			except ValueError:
				frappe.throw(_("{0} không phải JSON hợp lệ").format(_(self.meta.get_label(fieldname))))

	def validate_positive_numbers(self) -> None:
		for fieldname in ("max_attempts", "stuck_after_minutes", "raw_payload_retention_days"):
			if (self.get(fieldname) or 0) < 1:
				frappe.throw(_("{0} phải lớn hơn 0").format(_(self.meta.get_label(fieldname))))
		if (self.debounce_seconds or 0) < 0 or (self.recording_retention_days or 0) < 0:
			frappe.throw(_("Giá trị không được âm"))
