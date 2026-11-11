"""Hằng số API Zalo OA. Giá trị dễ đổi nên gom một chỗ; site có thể ghi đè bằng `omni_zalo` trong site_config.

CHƯA XÁC NHẬN VỚI TÀI LIỆU/API THẬT (xem docs/12 mục B, G7-G9): đọc developers.zalo.me không được trong môi
trường dev (trang chỉ là SPA), nên các giá trị dưới đây theo docs/06 và hiểu biết sẵn có.
"""

from __future__ import annotations

import frappe

DEFAULTS: dict = {
	"api_base": "https://openapi.zalo.me",
	"oauth_base": "https://oauth.zaloapp.com",
	"send_path": "/v3.0/oa/message/cs",
	"profile_path": "/v3.0/oa/user/detail",
	"quota_path": "/v3.0/oa/quota/message",  # V3-07: tài liệu "Kiểm tra hạn mức tin nhắn OA" (POST)
	"upload_paths": {  # V3-09: tài liệu "Upload hình ảnh / Upload ảnh GIF / Upload file" (multipart, trường `file`)
		"image": "/v2.0/oa/upload/image",
		"gif": "/v2.0/oa/upload/gif",
		"file": "/v2.0/oa/upload/file",
	},
	"upload_timeout": 30,  # giây: tải tệp tới 5 MB lâu hơn gửi tin chữ
	"oauth_permission_path": "/v4/oa/permission",
	"oauth_token_path": "/v4/oa/access_token",
	"max_text_length": 2000,  # độ dài tối đa một tin văn bản tư vấn (chưa xác nhận)
	"timeout": 10,  # giây (CLAUDE.md mục 3)
	"signature_header": "X-ZEvent-Signature",
	"max_timestamp_skew_seconds": 600,  # 10 phút (docs/06 mục 3)
	"refresh_before_hours": 2,  # làm mới khi còn dưới 2 giờ (docs/06 mục 2)
	"refresh_lock_seconds": 60,
	"get_retries": 2,  # số lần thử lại thêm cho yêu cầu đọc (GET) khi lỗi mạng/5xx
}


def get(key: str):
	"""Giá trị cấu hình `key`; site_config `omni_zalo` ghi đè mặc định."""
	override = frappe.conf.get("omni_zalo") or {}
	return override.get(key, DEFAULTS[key])
