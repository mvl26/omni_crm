"""V3-10 Prompt 5: khối "Marketing" ở cột phải trang Lead (cạnh "Bot AI") và bộ lọc nhanh "Chiến dịch nguồn".

Chỉ THÊM: không xóa / đổi khối hay bộ lọc có sẵn; chạy lại không thêm trùng.
"""

from __future__ import annotations

import json

import frappe

SECTION = "omni_marketing_section"
FIELDS = ["omni_campaign", "omni_content_item", "omni_utm_source", "omni_utm_medium", "omni_funnel_stage"]
AFTER = "omni_bot_section"
QUICK_FILTER = "omni_campaign"


def add_section(layout: list) -> bool:
	"""Thêm khối Marketing (sau khối Bot AI nếu có, không thì cuối); trả True nếu có thay đổi."""
	sections = layout
	if layout and isinstance(layout[0], dict) and "sections" in layout[0]:  # bố cục có tab
		sections = layout[0]["sections"]
	if any(s.get("name") == SECTION for s in sections):
		return False
	block = {
		"label": "Marketing",
		"name": SECTION,
		"opened": True,
		"columns": [{"name": "column_omni_mk", "fields": list(FIELDS)}],
	}
	idx = next((i + 1 for i, s in enumerate(sections) if s.get("name") == AFTER), len(sections))
	sections.insert(idx, block)
	return True


def add_quick_filter(filters: list) -> bool:
	if QUICK_FILTER in filters:
		return False
	filters.append(QUICK_FILTER)
	return True


def execute() -> None:
	name = frappe.db.get_value("CRM Fields Layout", {"dt": "CRM Lead", "type": "Side Panel"})
	if name:
		layout = json.loads(frappe.db.get_value("CRM Fields Layout", name, "layout") or "[]")
		if add_section(layout):
			frappe.db.set_value("CRM Fields Layout", name, "layout", json.dumps(layout, ensure_ascii=False))
	qf = frappe.db.get_value("CRM Global Settings", {"dt": "CRM Lead", "type": "Quick Filters"})
	if qf:
		filters = json.loads(frappe.db.get_value("CRM Global Settings", qf, "json") or "[]")
		if add_quick_filter(filters):
			frappe.db.set_value("CRM Global Settings", qf, "json", json.dumps(filters))
	else:  # CRM dùng in_standard_filter khi chưa cấu hình bộ lọc nhanh
		cf = frappe.db.get_value("Custom Field", {"dt": "CRM Lead", "fieldname": QUICK_FILTER})
		if cf:
			frappe.db.set_value("Custom Field", cf, "in_standard_filter", 1)
	frappe.clear_cache(doctype="CRM Lead")
