import frappe
from frappe import _
from frappe.model.document import Document


class OmniChannel(Document):
	def validate(self) -> None:
		if self.channel_type == "Zalo OA" and not (self.zalo_app_id or "").strip():
			frappe.throw(_("Kênh Zalo OA phải có App ID"), title=_("Thiếu cấu hình"))
		if self.channel_type == "Email" and self.enabled and not self.email_account:
			frappe.throw(_("Kênh Email đang bật phải chọn tài khoản email"), title=_("Thiếu cấu hình"))
		if self.channel_type == "Email" and self.enabled:
			from omni_crm.channels.email.config import crm_auto_lead_issues

			for issue in crm_auto_lead_issues(self.email_account):
				frappe.msgprint(issue, title=_("CRM sẽ tạo Lead trùng với Omni CRM"), indicator="orange")
		if (self.zalo_cs_free_hours or 0) < 0 or (self.zalo_cs_max_days or 0) < 0:
			frappe.throw(_("Khung gửi tin Zalo không được âm"))
