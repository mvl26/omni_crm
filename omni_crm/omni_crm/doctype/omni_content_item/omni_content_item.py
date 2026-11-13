"""Bài đăng của chiến dịch (V3-10). Trạng thái chỉ đổi qua `omni_crm.marketing.approval` (cờ status_via_rules)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from omni_crm.marketing import rules


def ensure_campaign_utm(campaign: str) -> str:
	"""Chiến dịch chưa có utm_campaign thì sinh slug không dấu + mmyy (thêm -2, -3... nếu trùng); trả mã."""
	doc = frappe.get_doc("Omni Campaign", campaign)
	if doc.utm_campaign:
		return doc.utm_campaign
	base = rules.campaign_utm(doc.campaign_name, getdate(doc.start_date or nowdate()))
	code, n = base, 1
	while frappe.db.exists("Omni Campaign", {"utm_campaign": code}):
		n += 1
		code = f"{base}-{n}"
	frappe.db.set_value("Omni Campaign", campaign, "utm_campaign", code)
	return code


class OmniContentItem(Document):
	def before_insert(self) -> None:
		self.status = rules.I_DRAFT
		self.approved_by = None
		self.approved_at = None
		existing = frappe.get_all(
			"Omni Content Item", filters={"campaign": self.campaign}, pluck="utm_content"
		)
		self.utm_content = rules.next_content_code(existing)
		ensure_campaign_utm(self.campaign)

	def validate(self) -> None:
		if self.landing_page:
			try:
				self.landing_page = rules.landing_path(self.landing_page)
			except ValueError as e:
				frappe.throw(_(str(e)))
		before = self.get_doc_before_save()
		if before is None:
			return
		if before.status != self.status and not self.flags.status_via_rules:
			frappe.throw(_("Đổi trạng thái bài bằng các nút Gửi duyệt / Duyệt và lên lịch / Hủy lịch"))
		if before.campaign != self.campaign:
			frappe.throw(_("Không đổi chiến dịch của bài đã tạo"))
		if before.status not in (rules.I_DRAFT, rules.I_REVIEW) and not self.flags.status_via_rules:
			frappe.throw(_("Bài đã duyệt: hủy lịch rồi tạo bài mới nếu cần sửa"))
