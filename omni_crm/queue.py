"""Định tuyến hàng đợi nền: MỘT chỗ duy nhất quyết định việc nào vào hàng đợi nào (V21-01, F1).

Lead quảng cáo (Meta Lead Ads, Google Lead Form, Web Form) đi `omni_leads` để không xếp sau chat; mọi việc
còn lại (email, Zalo, tổng đài, bot, tin ra, nhập danh sách) đi `omni`. Thiếu khai báo `omni_leads` trong
`common_site_config.json` (khóa `workers`) thì rơi về `omni` và ghi cảnh báo tối đa một lần mỗi giờ; không lỗi.
Không ghi cứng tên hàng đợi ở nơi khác: gọi `queue_for()` / `queue_for_identity()`.
"""

from __future__ import annotations

import frappe
from frappe.utils.background_jobs import get_queues_timeout

DEFAULT_QUEUE = "omni"
LEADS_QUEUE = "omni_leads"
LEAD_CHANNEL_TYPES = frozenset({"Meta Lead Ads", "Google Lead Form", "Web Form"})
WARN_KEY = "omni:queue:leads_missing_warned"
WARN_TTL = 3600


def leads_queue_declared() -> bool:
	"""`omni_leads` có trong khóa `workers` của cấu hình (common_site_config / site_config) không."""
	try:
		return LEADS_QUEUE in get_queues_timeout()
	except Exception:
		return False


def _warn_once_per_hour() -> None:
	"""Ghi cảnh báo thiếu khai báo `omni_leads` tối đa một lần mỗi giờ; lỗi Redis không làm hỏng việc xếp hàng."""
	try:
		first = frappe.cache().set(frappe.cache().make_key(WARN_KEY), 1, nx=True, ex=WARN_TTL)
	except Exception:
		return
	if first:
		frappe.logger("omni").warning(
			"omni: chưa khai báo hàng đợi omni_leads trong common_site_config.json (khóa workers); "
			"lead quảng cáo tạm đi hàng đợi omni"
		)


def queue_for(channel_type: str | None = None) -> str:
	"""Tên hàng đợi cho việc của một loại kênh; lead quảng cáo -> omni_leads (nếu đã khai báo), còn lại -> omni."""
	if channel_type in LEAD_CHANNEL_TYPES:
		if leads_queue_declared():
			return LEADS_QUEUE
		_warn_once_per_hour()
	return DEFAULT_QUEUE


def channel_type_of(identity_key: str | None) -> str | None:
	"""Loại kênh là phần trước dấu ':' đầu tiên của identity_key ("{channel_type}:{người gửi}")."""
	if not identity_key or ":" not in identity_key:
		return None
	return identity_key.split(":", 1)[0]


def queue_for_identity(identity_key: str | None) -> str:
	"""Hàng đợi cho job `process_identity` của một định danh (tác vụ quét, reprocess, job tiếp nối dùng chung)."""
	return queue_for(channel_type_of(identity_key))
