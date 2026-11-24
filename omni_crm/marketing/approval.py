"""Chuyển trạng thái bài đăng (V3-10, Prompt 1 mục C). Mọi đổi trạng thái bài/nền tảng do người dùng đi qua đây.

- Gửi duyệt: Nháp -> Chờ duyệt (cần ít nhất 1 nền tảng).
- Duyệt: Chờ duyệt -> Đã lên lịch, chỉ Marketing Manager, điểm >= ngưỡng, có >= 1 nền tảng, giờ đăng chưa quá cửa sổ
  đăng muộn. Mọi nền tảng -> Đã lên lịch, sinh idempotency_key và tracking_url. Thiết lập tắt "Bắt buộc duyệt" thì
  Marketing User cũng duyệt được và duyệt thẳng từ Nháp (vẫn cần điểm và nền tảng).
- Hủy: nền tảng chưa đăng -> Đã hủy; bài theo trạng thái tổng hợp.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from omni_crm.marketing import roles, rules
from omni_crm.marketing import settings as mk_settings

ITEM = "Omni Content Item"
TARGET = "Omni Content Target"
CAMPAIGN = "Omni Campaign"
PLACEHOLDER = "[cần bổ sung]"
NEED_IMAGE = {"fb_photo": 1, "fb_multi_photo": 2, "zalo_article": 1, "zalo_video": 1, "zalo_pdf_link": 1}
# Định dạng cần tệp đăng kèm (ADR 0035): ô của bài phải có tệp trước khi duyệt
NEED_FILE = {
	"fb_video": ("video", "video"),
	"fb_pdf_link": ("document", "tài liệu PDF"),
	"zalo_video": ("video", "video"),
	"zalo_pdf_link": ("document", "tài liệu PDF"),
}


def _check_media(doc, item: str) -> None:
	"""Định dạng cần ảnh (ảnh FB, nhiều ảnh FB, bài viết Zalo cần ảnh bìa) phải có đủ ảnh trước khi lên lịch."""
	images = len([x for x in [doc.media] + [m.image for m in (doc.get("media_items") or [])] if x])
	for t in frappe.get_all(
		TARGET,
		filters={"content_item": item, "status": ["!=", rules.T_CANCELLED]},
		fields=["platform", "post_format"],
	):
		need = NEED_IMAGE.get(t.post_format, 0)
		if images < need:
			frappe.throw(
				_("Định dạng {0} trên {1} cần ít nhất {2} ảnh: thêm ảnh trước khi duyệt").format(
					t.post_format, t.platform, need
				)
			)
		field, label = NEED_FILE.get(t.post_format, (None, None))
		if field and not doc.get(field):
			frappe.throw(
				_("Định dạng {0} trên {1} cần {2} đăng kèm: thêm tệp trước khi duyệt").format(
					t.post_format, t.platform, _(label)
				)
			)


def _targets(item: str, exclude_cancelled: bool = True) -> list:
	filters = {"content_item": item}
	if exclude_cancelled:
		filters["status"] = ["!=", rules.T_CANCELLED]
	return frappe.get_all(
		TARGET,
		filters=filters,
		fields=["name", "platform", "status", "scheduled_at"],
		order_by="scheduled_at",
	)


def _set_item_status(doc, status: str, **values) -> None:
	doc.status = status
	for k, v in values.items():
		doc.set(k, v)
	doc.flags.status_via_rules = True
	doc.save()


def _set_target(name: str, **values) -> None:
	doc = frappe.get_doc(TARGET, name)
	for k, v in values.items():
		doc.set(k, v)
	doc.flags.status_via_rules = True
	doc.save()


def submit(item: str) -> str:
	"""Nháp -> Chờ duyệt."""
	doc = frappe.get_doc(ITEM, item)
	doc.check_permission("write")
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền gửi duyệt bài"), frappe.PermissionError)
	if doc.status != rules.I_DRAFT:
		frappe.throw(_("Chỉ gửi duyệt được bài đang ở trạng thái Nháp"))
	if not _targets(item):
		frappe.throw(_("Bài chưa chọn nền tảng đăng nào"))
	_set_item_status(doc, rules.I_REVIEW)
	return doc.status


def approve(item: str) -> str:
	"""Duyệt và lên lịch một bài; trả trạng thái mới. Ném lỗi tiếng Việt nếu không đủ điều kiện."""
	doc = frappe.get_doc(ITEM, item)
	doc.check_permission("write")
	need_review = mk_settings.require_approval()
	if need_review:
		if not roles.is_manager():
			frappe.throw(_("Chỉ Marketing Manager được duyệt bài"), frappe.PermissionError)
		if doc.status != rules.I_REVIEW:
			frappe.throw(_("Chỉ duyệt được bài đang Chờ duyệt"))
	else:
		if not roles.can_edit():
			frappe.throw(_("Bạn không có quyền lên lịch bài"), frappe.PermissionError)
		if doc.status not in (rules.I_DRAFT, rules.I_REVIEW):
			frappe.throw(_("Chỉ lên lịch được bài Nháp hoặc Chờ duyệt"))
	threshold = mk_settings.quality_threshold()
	score = doc.quality_score or 0
	if score < threshold:
		frappe.throw(_("Điểm chất lượng {0} dưới ngưỡng {1}: chưa lên lịch được").format(score, threshold))
	if PLACEHOLDER in f"{doc.title or ''} {doc.caption or ''}":
		frappe.throw(_("Bài còn chỗ [cần bổ sung]: điền đủ thông tin trước khi duyệt"))
	targets = _targets(item)
	if not targets:
		frappe.throw(_("Bài chưa chọn nền tảng đăng nào"))
	_check_media(doc, item)
	earliest = add_to_date(now_datetime(), minutes=-mk_settings.late_window_minutes())
	for t in targets:
		if get_datetime(t.scheduled_at) < earliest:
			frappe.throw(
				_("Giờ đăng {0} trên {1} đã quá cửa sổ đăng muộn: đổi giờ trước khi duyệt").format(
					t.scheduled_at, t.platform
				)
			)
	campaign = frappe.get_doc(CAMPAIGN, doc.campaign)
	if not campaign.utm_campaign:
		frappe.throw(_("Chiến dịch chưa có mã utm_campaign"))
	base = mk_settings.get("landing_base_url")
	landing = doc.landing_page or campaign.get("landing_page") or mk_settings.get("default_landing_page")
	for t in targets:
		_set_target(
			t.name,
			status=rules.T_SCHEDULED,
			idempotency_key=f"omni-mk-{t.name}",
			tracking_url=rules.tracking_url(
				base, landing, t.platform, campaign.utm_campaign, doc.utm_content
			),
			error_type="",
			last_error="",
		)
	_set_item_status(doc, rules.I_SCHEDULED, approved_by=frappe.session.user, approved_at=now_datetime())
	return doc.status


def cancel(item: str) -> str:
	"""Hủy bài: nền tảng chưa đăng -> Đã hủy; trả trạng thái bài sau khi tổng hợp."""
	doc = frappe.get_doc(ITEM, item)
	doc.check_permission("write")
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền hủy bài"), frappe.PermissionError)
	if doc.status == rules.I_CANCELLED:
		return doc.status
	all_targets = _targets(item, exclude_cancelled=False)
	if any(t.status == rules.T_PROCESSING for t in all_targets):
		frappe.throw(_("Có nền tảng đang đăng dở: chờ xong rồi hủy"))
	for t in all_targets:
		if t.status not in (rules.T_SUCCESS, rules.T_FAILED, rules.T_CANCELLED):
			_set_target(t.name, status=rules.T_CANCELLED)
	statuses = [
		t.status if t.status in (rules.T_SUCCESS, rules.T_FAILED) else rules.T_CANCELLED for t in all_targets
	]
	_set_item_status(doc, rules.aggregate_status(statuses) or rules.I_CANCELLED)
	return doc.status


def refresh_item_status(item: str) -> str | None:
	"""Tổng hợp trạng thái bài từ các nền tảng (bộ đăng bài gọi sau mỗi lần đăng); trả trạng thái mới hoặc None."""
	doc = frappe.get_doc(ITEM, item)
	if doc.status in (rules.I_DRAFT, rules.I_REVIEW):
		return None
	status = rules.aggregate_status(t.status for t in _targets(item, exclude_cancelled=False))
	if status and status != doc.status:
		doc.flags.ignore_permissions = True
		_set_item_status(doc, status)
	return status
