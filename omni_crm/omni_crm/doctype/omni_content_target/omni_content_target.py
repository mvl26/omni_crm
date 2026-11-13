"""Một nền tảng đăng của bài (V3-10). DocType riêng để khóa / truy vấn theo dòng khi đăng theo lịch (Prompt 2)."""

import frappe
from frappe import _
from frappe.model.document import Document

from omni_crm.marketing import rules

EDITABLE_ITEM_STATUS = (rules.I_DRAFT, rules.I_REVIEW)


def channel_matches(platform: str, channel_type: str | None) -> bool:
	"""Kênh Omni Channel đúng nền tảng: Facebook -> kênh Meta (Lead Ads / Messenger, cùng Page); Zalo OA -> Zalo OA."""
	channel_type = channel_type or ""
	if platform == rules.FACEBOOK:
		return channel_type.startswith("Meta")
	if platform == rules.ZALO:
		return channel_type == "Zalo OA"
	return False


class OmniContentTarget(Document):
	def validate(self) -> None:
		if not rules.check_format(self.platform, self.post_format):
			frappe.throw(_("Định dạng {0} không dùng được cho {1}").format(self.post_format, self.platform))
		if self.platform != rules.TIKTOK:
			if not self.channel_account:
				frappe.throw(_("Chọn kênh {0} để đăng").format(self.platform))
			ctype = frappe.db.get_value("Omni Channel", self.channel_account, "channel_type")
			if not channel_matches(self.platform, ctype):
				frappe.throw(_("Kênh {0} không phải kênh {1}").format(self.channel_account, self.platform))
		before = self.get_doc_before_save()
		if self.flags.status_via_rules:
			return
		item_status = frappe.db.get_value("Omni Content Item", self.content_item, "status")
		if item_status not in EDITABLE_ITEM_STATUS:
			frappe.throw(_("Bài đã duyệt hoặc đã hủy: không thêm/sửa nền tảng đăng"))
		if before is None:
			self.status = rules.T_REVIEW
		elif before.status != self.status:
			frappe.throw(_("Trạng thái nền tảng đăng do hệ thống cập nhật"))

	def on_trash(self) -> None:
		if self.status in (rules.T_PROCESSING, rules.T_SUCCESS):
			frappe.throw(_("Không xóa nền tảng đã/đang đăng"))


def on_doctype_update() -> None:
	"""Chỉ mục cho bộ đăng bài mỗi phút (Prompt 2): lấy dòng theo (status, scheduled_at) và (status, next_retry_at)."""
	frappe.db.add_index("Omni Content Target", ["status", "scheduled_at"])
	frappe.db.add_index("Omni Content Target", ["status", "next_retry_at"])
