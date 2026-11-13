"""Chiến dịch marketing (docs/15 mục 6): quy kết first/last touch và báo cáo theo chiến dịch."""

import frappe
from frappe import _
from frappe.model.document import Document

from omni_crm.marketing.rules import T_LIVE, landing_path

PLATFORMS = ("Meta", "Google", "Website", "Liên hệ chủ động", "Đa kênh")


def campaign_key(platform: str | None, external_id: str | None) -> str | None:
	"""Khóa duy nhất "nền tảng:mã"; None (không phải chuỗi rỗng) khi chưa có mã để không vấp chỉ mục UNIQUE."""
	external_id = (external_id or "").strip()
	return f"{platform}:{external_id}" if platform and external_id else None


class OmniCampaign(Document):
	def validate(self) -> None:
		self.external_campaign_id = (self.external_campaign_id or "").strip() or None
		self.utm_campaign = (
			self.utm_campaign or ""
		).strip() or None  # giữ nguyên chữ người nhập (khớp UTM quảng cáo cũ)
		self._guard_utm_change()
		if self.landing_page:
			try:
				self.landing_page = landing_path(self.landing_page)
			except ValueError as e:
				frappe.throw(_(str(e)))
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

	def _guard_utm_change(self) -> None:
		"""Mã utm_campaign sửa được tới khi có bài đã duyệt (link có mã đã/đang được đăng thì không đổi nữa)."""
		if self.is_new():
			return
		old = frappe.db.get_value("Omni Campaign", self.name, "utm_campaign")
		if (
			old
			and old != self.utm_campaign
			and frappe.db.exists("Omni Content Target", {"campaign": self.name, "status": ["in", T_LIVE]})
		):
			frappe.throw(_("Chiến dịch đã có bài được duyệt: không đổi mã utm_campaign nữa"))
