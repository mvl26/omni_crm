"""Nhóm câu hỏi của khách; nhóm `always_handoff` luôn chuyển người.

Cờ `auto_send_enabled` (bot tự gửi, D3-05) KHÔNG sửa tay được: chỉ `bot/auto_send.py` (qua `enabling()`) mới được bật.
Tắt thì ai có quyền ghi cũng tắt được (hướng an toàn). Mọi đường ghi (biểu mẫu, REST, nhập dữ liệu) đều qua `validate`.
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.model.document import Document

ENABLE_FLAG = "in_omni_auto_send_enable"


@contextmanager
def enabling():
	"""Cho phép lần lưu hiện tại bật `auto_send_enabled` (chỉ `bot.auto_send` gọi, sau khi đã kiểm mọi điều kiện)."""
	previous = frappe.flags.get(ENABLE_FLAG)
	frappe.flags[ENABLE_FLAG] = True
	try:
		yield
	finally:
		frappe.flags[ENABLE_FLAG] = previous


class OmniQuestionGroup(Document):
	def validate(self):
		"""Nhóm luôn chuyển người không bao giờ tự gửi; bật tự gửi chỉ qua `enabling()`; tắt thì xóa dấu vết bật."""
		if self.always_handoff and self.auto_send_enabled:
			if self.is_new() or not self.has_value_changed("always_handoff"):
				frappe.throw(_("Nhóm luôn chuyển người không thể bật tự động gửi"), frappe.ValidationError)
			self.auto_send_enabled = 0  # vừa chuyển nhóm thành luôn chuyển người: tự tắt
		was = 0 if self.is_new() else frappe.db.get_value(self.doctype, self.name, "auto_send_enabled")
		if self.auto_send_enabled and not was and not frappe.flags.get(ENABLE_FLAG):
			frappe.throw(
				_('Chỉ bật được tự động gửi bằng nút "Bật tự động" của Omni Admin (cần đạt điều kiện)'),
				frappe.ValidationError,
			)
		if not self.auto_send_enabled:
			self.auto_send_enabled_at = None
			self.auto_send_enabled_by = None
