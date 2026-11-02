"""Biểu mẫu quảng cáo/website và ánh xạ trường (docs/15 mục 5)."""

import frappe
from frappe import _
from frappe.model.document import Document

TARGETS = (
	"full_name",
	"phone",
	"email",
	"company_name",
	"tax_id",
	"job_title",
	"province",
	"hospital_count_text",
	"message",
	"consent_contact",
	"consent_marketing",
)
CUSTOM_PREFIX = "custom:"


def form_key(platform: str | None, external_id: str | None) -> str | None:
	"""Khóa duy nhất "nền tảng:mã biểu mẫu"."""
	external_id = (external_id or "").strip()
	return f"{platform}:{external_id}" if platform and external_id else None


def valid_target(target: str) -> bool:
	"""Đích ánh xạ hợp lệ: một trong TARGETS hoặc custom:<tên không rỗng>."""
	return target in TARGETS or (
		target.startswith(CUSTOM_PREFIX) and bool(target[len(CUSTOM_PREFIX) :].strip())
	)


class OmniLeadForm(Document):
	def validate(self) -> None:
		self.external_form_id = (self.external_form_id or "").strip()
		self.form_key = form_key(self.platform, self.external_form_id)
		if not self.form_key:
			frappe.throw(_("Biểu mẫu phải có nền tảng và mã biểu mẫu"))
		if frappe.db.exists("Omni Lead Form", {"form_key": self.form_key, "name": ["!=", self.name or ""]}):
			frappe.throw(_("Đã có biểu mẫu cùng nền tảng và mã này"))
		seen: set[str] = set()
		for row in self.field_map or []:
			row.source_key = (row.source_key or "").strip()
			row.target = (row.target or "").strip()
			if not valid_target(row.target):
				frappe.throw(_("Dòng {0}: đích «{1}» không hợp lệ").format(row.idx, row.target))
			if row.source_key in seen:
				frappe.throw(_("Dòng {0}: khóa nguồn «{1}» bị lặp").format(row.idx, row.source_key))
			seen.add(row.source_key)
		if self.assignment_rule and not frappe.db.exists("Assignment Rule", self.assignment_rule):
			frappe.throw(_("Không có Assignment Rule «{0}»").format(self.assignment_rule))
