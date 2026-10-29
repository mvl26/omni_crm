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
		if self.channel_type == "Telephony":
			self.validate_telephony()
		if (self.zalo_cs_free_hours or 0) < 0 or (self.zalo_cs_max_days or 0) < 0:
			frappe.throw(_("Khung gửi tin Zalo không được âm"))

	def validate_telephony(self) -> None:
		"""Nhà cung cấp Fake chỉ chọn được ở môi trường dev/test; token webhook (nếu đặt) phải đủ dài."""
		from omni_crm.channels.telephony.registry import DEV_ONLY, dev_allowed

		if self.telephony_provider in DEV_ONLY and not dev_allowed():
			frappe.throw(
				_("Nhà cung cấp tổng đài giả chỉ dùng được khi bật developer_mode"), title=_("Cấu hình sai")
			)
		if self.enabled and not self.telephony_provider:
			frappe.throw(_("Kênh tổng đài đang bật phải chọn nhà cung cấp"), title=_("Thiếu cấu hình"))
		token = self.get_password("webhook_token", raise_exception=False) if self.name else None
		token = self.webhook_token if self.webhook_token and not set(self.webhook_token) <= {"*"} else token
		if token and len(token) < 32:
			frappe.throw(_("Token webhook phải dài ít nhất 32 ký tự"), title=_("Cấu hình sai"))
