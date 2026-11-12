"""Mẫu tin nhắn chat cho sale (ADR 0026). Logic dựng mẫu ở `omni_crm.crm_bridge.chat_templates`."""

from frappe.model.document import Document
from frappe.utils import cint

from omni_crm.crm_bridge import chat_templates


class OmniChatTemplate(Document):
	def validate(self) -> None:
		self.template_name = " ".join((self.template_name or "").split())
		self.usage_note = " ".join((self.usage_note or "").split())
		self.content = (self.content or "").replace("\r\n", "\n").strip()
		self.sort_order = cint(self.sort_order)
		chat_templates.check(self.content)
