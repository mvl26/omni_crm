"""Chiến dịch "Liên hệ chủ động – <tháng>" cho Lead từ danh sách mua sắm công (docs/15 mục 8)."""

from __future__ import annotations

from datetime import date, datetime

import frappe

CAMPAIGN = "Omni Campaign"
PROACTIVE_PLATFORM = "Liên hệ chủ động"
LEAD_PLATFORM = "Danh sách mua sắm công"  # giá trị omni_ad_platform của Lead
EN_DASH = "–"


def proactive_campaign_name(when: date | datetime) -> str:
	"""Tên chiến dịch theo tháng, ví dụ "Liên hệ chủ động – 11/2026"."""
	return f"{PROACTIVE_PLATFORM} {EN_DASH} {when.month:02d}/{when.year}"


def ensure_proactive_campaign(when: date | datetime, create: bool = True) -> str | None:
	"""Tên Omni Campaign của tháng `when`; tạo nếu chưa có (create=True). Trả None khi không tạo và chưa có."""
	name = proactive_campaign_name(when)
	if frappe.db.exists(CAMPAIGN, name):
		return name
	if not create:
		return None
	doc = frappe.get_doc(
		{
			"doctype": CAMPAIGN,
			"campaign_name": name,
			"platform": PROACTIVE_PLATFORM,
			"status": "Đang chạy",
			"start_date": date(when.year, when.month, 1),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name
