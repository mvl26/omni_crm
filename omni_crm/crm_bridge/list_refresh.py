"""Danh sách Lead của giao diện CRM (/crm/leads) tự tải lại khi có Lead mới, không phải F5.

Dùng lại bộ nghe `refetch_resource` có sẵn của giao diện Frappe CRM (crm/frontend/src/socket.js): nhận khóa
bộ nhớ đệm của một danh sách rồi tự gọi lại API có kiểm quyền. Tin phát cho cả site nên CHỈ chứa khóa đệm,
không chứa tên hay dữ liệu Lead nào.

Khóa đệm của danh sách là [doctype, route.query.view, route.params.viewType]
(crm/frontend/src/components/ViewControls.vue), vd ["CRM Lead", null, "list"]; trang /crm/leads chưa chọn
kiểu xem có viewType rỗng (null), chế độ xem tự tạo có query.view = tên CRM View Settings.

Gộp: tối đa một lần phát mỗi THROTTLE_SECONDS. Lead tạo trong lúc bị gộp (tạo dồn dập, vd nạp bù) được
cron mỗi phút `flush_pending` phát bù, nên trễ thêm tối đa khoảng một phút.
"""

from typing import Any

import frappe

LEAD = "CRM Lead"
EVENT = "refetch_resource"
VIEW_TYPES = (None, "list", "kanban", "group_by")
THROTTLE_SECONDS = 2
THROTTLE_KEY = "omni:lead_list_refresh:recent"
PENDING_KEY = "omni:lead_list_refresh:pending"
PENDING_TTL = 300


def cache_keys() -> list[list]:
	"""Mọi khóa đệm danh sách Lead mà giao diện CRM có thể đang mở."""
	keys: list[list] = [[LEAD, None, t] for t in VIEW_TYPES]
	for v in frappe.get_all("CRM View Settings", filters={"dt": LEAD}, fields=["name", "type"]):
		keys.append([LEAD, v.name, v.type or "list"])
	return keys


def publish(after_commit: bool = True) -> None:
	for key in cache_keys():
		frappe.publish_realtime(EVENT, {"cache_key": key}, after_commit=after_commit)


def on_lead_insert(doc: Any, method: str | None = None) -> None:
	"""after_insert của CRM Lead. Không bao giờ ném lỗi: báo giao diện hỏng không được làm hỏng việc tạo Lead."""
	try:
		cache = frappe.cache()
		if cache.set(cache.make_key(THROTTLE_KEY), 1, ex=THROTTLE_SECONDS, nx=True):
			publish(after_commit=True)  # phát sau commit để danh sách tải lại thấy được Lead này
		else:
			cache.set(cache.make_key(PENDING_KEY), 1, ex=PENDING_TTL)
	except Exception:
		frappe.log_error(title="Omni: báo tải lại danh sách Lead thất bại")


def flush_pending() -> None:
	"""Cron mỗi phút: phát bù cho Lead tạo trong lúc bị gộp."""
	cache = frappe.cache()
	if cache.delete(cache.make_key(PENDING_KEY)):
		publish(after_commit=False)
