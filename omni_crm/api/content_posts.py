"""API mỏng cho các nút trên form bài đăng và Calendar view (ADR 0029).

Mọi nút đổi trạng thái đòi quyền sửa đúng bài đó (Omni Admin, Sales Manager). Lịch chỉ trả bài người xem được đọc.
Không có hàm nào gọi Meta: việc đăng do job nền `marketing/publisher.py` làm.
"""

from __future__ import annotations

import frappe

from omni_crm.marketing import content_posts
from omni_crm.marketing.constants import POST


def _can_write(name: str) -> None:
	frappe.has_permission(POST, "write", doc=name, throw=True)


@frappe.whitelist(methods=["POST"])
def schedule(name: str) -> str:
	"""Nút "Lên lịch"."""
	_can_write(name)
	return content_posts.schedule(name)


@frappe.whitelist(methods=["POST"])
def unschedule(name: str) -> str:
	"""Nút "Huỷ lịch"."""
	_can_write(name)
	return content_posts.unschedule(name)


@frappe.whitelist(methods=["POST"])
def cancel(name: str) -> str:
	"""Nút "Huỷ bài"."""
	_can_write(name)
	return content_posts.cancel(name)


@frappe.whitelist(methods=["POST"])
def reopen(name: str) -> str:
	"""Nút "Mở lại" cho bài Đã huỷ."""
	_can_write(name)
	return content_posts.reopen(name)


@frappe.whitelist(methods=["POST"])
def resolve_uncertain(name: str, published: int | str, post_url: str | None = None) -> str:
	"""Bài Cần kiểm: "Bài đã lên Page" (kèm link) hoặc "Bài chưa lên Page"."""
	_can_write(name)
	return content_posts.resolve_uncertain(name, bool(frappe.utils.cint(published)), post_url)


@frappe.whitelist()
def calendar_events(
	doctype: str | None = None, start: str = "", end: str = "", filters=None, **_kwargs
) -> list[dict]:
	"""Sự kiện cho Calendar view của Omni Content Post (theo quyền đọc)."""
	return content_posts.calendar_events(start, end, filters)
