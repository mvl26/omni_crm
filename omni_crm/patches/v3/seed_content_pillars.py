"""ADR 0029: nạp 4 trụ cột nội dung của nguyên mẫu Marketing Hub nếu chưa có trụ cột nào (Chi đổi tên hoặc tắt được)."""

import frappe

PILLARS = (
	("Giới thiệu dịch vụ", "Dịch vụ SPD, cách Miyano vận hành kho vật tư cho cơ sở y tế."),
	("Kiến thức quản lý vật tư", "Mẹo, quy trình, số liệu về quản lý vật tư y tế, hóa chất."),
	("Câu chuyện khách hàng", "Kết quả thực tế tại bệnh viện, phòng khám đang dùng dịch vụ."),
	("Tin tức Miyano", "Sự kiện, hợp tác, tuyển dụng của Miyano."),
)


def execute():
	if not frappe.db.table_exists("Omni Content Pillar") or frappe.db.count("Omni Content Pillar"):
		return
	for name, description in PILLARS:
		frappe.get_doc(
			{"doctype": "Omni Content Pillar", "pillar_name": name, "description": description, "enabled": 1}
		).insert(ignore_permissions=True)
