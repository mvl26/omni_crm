import json

import frappe
from frappe import _
from frappe.model.document import Document

JSON_FIELDS = ("default_lead_source_map", "business_hours", "bot_active_hours")
NOTIFY_CHANNELS = ("system", "email")


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
		self.validate_score_rules()
		self.validate_handoff()
		self.validate_prompt_pins()

	def validate_prompt_pins(self) -> None:
		"""Phiên bản câu lệnh ghim phải có dạng v<N> và có tệp tương ứng (V2-01)."""
		from omni_crm.bot.prompts.loader import load_prompt

		for name, fieldname in (("analyze", "analyze_prompt_version"), ("reply", "reply_prompt_version")):
			value = (self.get(fieldname) or "").strip()
			if not value:
				continue
			try:
				load_prompt(name, value)
			except FileNotFoundError:
				frappe.throw(_("Không có tệp câu lệnh {0} phiên bản {1}").format(name, value))

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

	def validate_score_rules(self) -> None:
		"""Ngưỡng Lead nóng > 0; mỗi tín hiệu chỉ một dòng trong bảng điểm."""
		if self.get("hot_lead_threshold") is not None and (self.hot_lead_threshold or 0) < 1:
			frappe.throw(_("Ngưỡng Lead nóng phải lớn hơn 0"))
		seen: set[str] = set()
		for row in self.get("score_rules") or []:
			if row.signal in seen:
				frappe.throw(_("Dòng {0}: tín hiệu {1} bị trùng trong bảng điểm").format(row.idx, row.signal))
			seen.add(row.signal)

	def validate_handoff(self) -> None:
		"""Số giờ im không âm; kênh báo chỉ gồm system, email."""
		if (self.get("handoff_pause_hours") or 0) < 0:
			frappe.throw(_("{0} không được âm").format(_(self.meta.get_label("handoff_pause_hours"))))
		raw = [
			x.strip().lower()
			for x in (self.get("handoff_notify_channels") or "").replace("\n", ",").split(",")
		]
		bad = [x for x in raw if x and x not in NOTIFY_CHANNELS]
		if bad:
			frappe.throw(_("Kênh báo không hợp lệ: {0}. Chỉ dùng system hoặc email").format(", ".join(bad)))
