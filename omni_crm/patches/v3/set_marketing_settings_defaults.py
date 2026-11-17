"""ADR 0029, 0030: ghi giá trị mặc định cho các trường mới của Omni Settings.

Frappe không áp `default` cho trường mới thêm vào một Single đã có dữ liệu: ô đọc ra None. Với `s3_verify_tls`
(mặc định 1) điều đó nghĩa là TẮT kiểm chứng chỉ TLS. Patch chỉ ghi trường CHƯA từng được lưu (không có dòng trong
tabSingles), không đè giá trị người dùng đã đặt.
"""

import frappe

DEFAULTS = {
	"content_late_window_minutes": 30,
	"content_reach_metric": "post_impressions_unique",
	"content_clicks_metric": "post_clicks_by_type",
	"s3_prefix": "omni-content-post/",
	"s3_region": "us-east-1",
	"s3_verify_tls": 1,
}


def execute():
	for field, value in DEFAULTS.items():
		saved = frappe.db.sql(
			"select 1 from `tabSingles` where doctype='Omni Settings' and field=%s", (field,)
		)
		if not saved:
			frappe.db.set_single_value("Omni Settings", field, value)
	frappe.clear_document_cache("Omni Settings", "Omni Settings")
