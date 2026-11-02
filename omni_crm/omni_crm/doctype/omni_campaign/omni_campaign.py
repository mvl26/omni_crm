"""Chiến dịch marketing (docs/15 mục 6): quy kết first/last touch và báo cáo theo chiến dịch."""

import frappe
from frappe import _
from frappe.model.document import Document

PLATFORMS = ("Meta", "Google", "Website", "Liên hệ chủ động", "Đa kênh")


def campaign_key(platform: str | None, external_id: str | None) -> str | None:
	"""Khóa duy nhất "nền tảng:mã"; None (không phải chuỗi rỗng) khi chưa có mã để không vấp chỉ mục UNIQUE."""
	external_id = (external_id or "").strip()
	return f"{platform}:{external_id}" if platform and external_id else None


class OmniCampaign(Document):
	def validate(self) -> None:
		self.external_campaign_id = (self.external_campaign_id or "").strip() or None
		self.utm_campaign = (self.utm_campaign or "").strip() or None
		self.campaign_key = campaign_key(self.platform, self.external_campaign_id)
		if self.start_date and self.end_date and self.end_date < self.start_date:
			frappe.throw(_("Ngày kết thúc không được trước ngày bắt đầu"))
		for field in ("budget", "spend"):
			if (self.get(field) or 0) < 0:
				frappe.throw(_("{0} không được âm").format(_(self.meta.get_label(field))))
		if self.campaign_key and frappe.db.exists(
			"Omni Campaign", {"campaign_key": self.campaign_key, "name": ["!=", self.name or ""]}
		):
			frappe.throw(_("Đã có chiến dịch cùng nền tảng và mã chiến dịch này"))
