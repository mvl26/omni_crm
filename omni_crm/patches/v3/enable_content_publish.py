"""ADR 0029, yêu cầu ngày 18/11/2026: bật sẵn tự đăng bài khi triển khai và chọn kênh Page.

`content_publish_enabled` = 1. `content_publish_channel` còn trống thì chọn kênh Meta Messenger DUY NHẤT đang bật và có
Page ID (trên yhct là `messenger-mvl`); có 0 hoặc nhiều hơn 1 kênh như vậy thì để trống, Omni Admin chọn tay.
Chưa có quyền đăng bài (pages_manage_posts) thì bài Tự động đến giờ chuyển Lỗi kèm mã lỗi Meta, không đăng gì.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Omni Content Post"):
		return
	frappe.db.set_single_value("Omni Settings", "content_publish_enabled", 1)
	if not frappe.db.get_single_value("Omni Settings", "content_publish_channel"):
		channels = frappe.get_all(
			"Omni Channel",
			filters={"channel_type": "Meta Messenger", "enabled": 1, "meta_page_id": ["is", "set"]},
			pluck="name",
		)
		if len(channels) == 1:
			frappe.db.set_single_value("Omni Settings", "content_publish_channel", channels[0])
	frappe.clear_document_cache("Omni Settings", "Omni Settings")
