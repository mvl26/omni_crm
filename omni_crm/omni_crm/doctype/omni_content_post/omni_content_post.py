"""Bài đăng của chiến dịch (ADR 0029): mã bài, link có UTM, trạng thái chỉ đổi qua nút, khóa nội dung khi đã đăng."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from omni_crm.marketing import attribution, media
from omni_crm.marketing.constants import (
	AUTO,
	CAMPAIGN,
	DRAFT,
	LOCKED_FIELDS,
	LOCKED_STATUSES,
	MARK_PUBLISHED_FROM,
	PUBLISHED,
)
from omni_crm.marketing.utm import build_utm_link, is_valid_post_code, normalize_post_code


class OmniContentPost(Document):
	def before_naming(self) -> None:
		self.post_code = normalize_post_code(self.post_code)
		self._check_post_code()

	def validate(self) -> None:
		self.post_code = normalize_post_code(self.post_code)
		self._check_post_code()
		self.title = (self.title or "").strip()
		self.landing_url = (self.landing_url or "").strip() or None
		self.post_url = (self.post_url or "").strip() or None
		before = self.get_doc_before_save()
		if not self.status:
			self.status = DRAFT
		if before and before.status != self.status and not self.flags.status_by_api:
			frappe.throw(_("Đổi trạng thái bài bằng các nút trên form, không sửa trực tiếp"))
		if before and before.status in LOCKED_STATUSES and not self.flags.status_by_api:
			changed = [f for f in LOCKED_FIELDS if str(before.get(f) or "") != str(self.get(f) or "")]
			if changed:
				frappe.throw(
					_("Bài {0} không sửa được: {1}").format(
						_(before.status), ", ".join(_(self.meta.get_label(f)) for f in changed)
					)
				)
		if self.image and not media.is_stored_file_url(self.image):
			frappe.throw(_("Ảnh phải tải lên CRM, không dùng link ảnh bên ngoài"))
		self.utm_link = self.compute_utm_link() or None
		if self.post_url and self.status in MARK_PUBLISHED_FROM:
			self.status = PUBLISHED
			self.published_at = self.published_at or now_datetime()

	def _check_post_code(self) -> None:
		if not is_valid_post_code(self.post_code):
			frappe.throw(
				_(
					"Mã bài chỉ gồm chữ thường không dấu, số và gạch ngang, tối đa 40 ký tự (ví dụ spd-b01): {0}"
				).format(self.post_code or "")
			)

	def compute_utm_link(self) -> str:
		"""Link có UTM của bài: link đích (hoặc trang đích của chiến dịch) + utm_campaign của chiến dịch + mã bài."""
		row = (
			frappe.db.get_value(CAMPAIGN, self.campaign, ["landing_url", "utm_campaign"])
			if self.campaign
			else None
		)
		landing, utm_campaign = row or (None, None)
		return build_utm_link(self.landing_url or landing, self.platform, utm_campaign, self.post_code)

	def after_insert(self) -> None:
		# Bài nhập bù sau khi Lead đã vào (đăng tay trước khi có CRM): gán bài cho các Lead có utm_content trùng mã.
		if attribution.relink_post(self.name):
			attribution.refresh_counts(self.name)

	def onload(self) -> None:
		self.set_onload("counts", attribution.counts(self.name))
		if self.publish_mode == AUTO:
			from omni_crm.marketing.content_posts import auto_publish_state

			self.set_onload("auto_publish_off", auto_publish_state())
