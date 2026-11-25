"""Nối Lead với bài đăng của Marketing Hub (V3-10, Prompt 5).

Landing page gửi kèm utm_campaign + utm_content (omni_utm.js lưu ở sessionStorage); `form_lead` tìm Omni Campaign theo
utm_campaign, ở đây tìm Omni Content Item theo (chiến dịch, utm_content). Mã không khớp: Lead vẫn tạo, giữ nguyên UTM.
Nguồn đầu tiên không bị ghi đè; nguồn sau chỉ ghi vào nhật ký hoạt động.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import frappe
from frappe import _

from omni_crm.marketing import rules

ITEM = "Omni Content Item"
SOURCE_LABEL = {v: k for k, v in rules.UTM_SOURCE.items()}  # facebook -> Facebook, zalo -> Zalo OA


def content_item_for(campaign: str | None, utm_content: str | None) -> dict[str, Any] | None:
	"""Bài đăng của chiến dịch có mã utm_content (bai-NN); None nếu thiếu mã hoặc không khớp."""
	code = (utm_content or "").strip().lower()
	if not campaign or not code:
		return None
	return frappe.db.get_value(
		ITEM, {"campaign": campaign, "utm_content": code}, ["name", "title", "funnel_stage"], as_dict=True
	)


def lead_values(campaign: str | None, utm: dict) -> dict[str, Any]:
	"""Trường Lead cần điền khi đây là nguồn đầu tiên: bài đăng nguồn và giai đoạn phễu."""
	item = content_item_for(campaign, utm.get("utm_content"))
	if not item:
		return {}
	values: dict[str, Any] = {"omni_content_item": item.name}
	if item.funnel_stage:
		values["omni_funnel_stage"] = item.funnel_stage
	return values


def _where(form: dict) -> str:
	platform = form.get("platform")
	if platform == "Website":
		path = urlsplit(form.get("landing_page") or "").path
		return _("form landing page {0}").format(path or form.get("form_id") or "")
	if platform == "Meta":
		return _("form Lead Ads {0}").format(form.get("form_name") or form.get("form_id") or "")
	return _("biểu mẫu {0}").format(form.get("form_name") or form.get("form_id") or "")


def activity_line(form: dict, campaign: str | None, created: bool) -> str | None:
	"""Dòng nhật ký hoạt động: "Lead tạo từ form landing page /spd/ - Chiến dịch … - bài …"; None nếu không có chiến dịch."""
	if not campaign:
		return None
	utm = form.get("utm") or {}
	item = content_item_for(campaign, utm.get("utm_content"))
	head = _("Lead tạo từ {0}") if created else _("Khách điền thêm {0}")
	parts = [head.format(_where(form)), _("Chiến dịch {0}").format(campaign)]
	if item:
		source = SOURCE_LABEL.get((utm.get("utm_source") or "").lower(), utm.get("utm_source") or "")
		parts.append(_("bài {0}").format(item.title) + (f" ({source})" if source else ""))
	elif utm.get("utm_content"):
		parts.append(_("mã bài {0} không khớp bài nào").format(utm.get("utm_content")))
	return " - ".join(parts)
