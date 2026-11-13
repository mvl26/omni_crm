"""Thiết lập Marketing Hub (Single, V3-10). Đọc qua `omni_crm.marketing.settings` (có mặc định khi chưa lưu)."""

import frappe
from frappe import _
from frappe.model.document import Document

from omni_crm.marketing import roles, rules


class OmniMarketingSettings(Document):
	def validate(self) -> None:
		if self.quality_threshold is not None and not 0 <= int(self.quality_threshold) <= 100:
			frappe.throw(_("Ngưỡng điểm chất lượng phải từ 0 đến 100"))
		if self.default_landing_page:
			try:
				self.default_landing_page = rules.landing_path(self.default_landing_page)
			except ValueError as e:
				frappe.throw(_(str(e)))
		self.landing_base_url = (self.landing_base_url or "").strip().rstrip("/")
		if self.landing_base_url and not self.landing_base_url.startswith("https://"):
			frappe.throw(_("Địa chỉ gốc trang đích phải bắt đầu bằng https://"))
		if self.default_approver and not roles.is_manager(self.default_approver):
			frappe.throw(_("Người duyệt mặc định phải có vai trò Marketing Manager"))
