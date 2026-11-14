"""API mỏng cho nút "Connect Facebook Page" trên form Omni Channel loại Meta Messenger (ADR 0028).

Logic và kiểm quyền nằm ở `channels/meta_messenger/connect.py`; file này chỉ chuyển hướng trình duyệt và đổi
lỗi Graph thành thông điệp cho người dùng. Mọi hàm đòi vai trò Omni Admin.
"""

from __future__ import annotations

import frappe
from frappe import _

from omni_crm.channels.meta_messenger import connect
from omni_crm.channels.meta_messenger.client import MessengerError


def _redirect(location: str) -> None:
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = location


@frappe.whitelist()
def start(channel: str) -> None:
	"""Nút "Connect Facebook Page": chuyển trình duyệt sang hộp thoại Facebook Login for Business."""
	_redirect(connect.start_url(channel))


@frappe.whitelist()
def callback(code: str | None = None, state: str | None = None, error: str | None = None, **_kwargs) -> None:
	"""Meta chuyển về sau hộp thoại: có code thì đổi lấy token rồi quay về form để chọn Page."""
	if error or not code:
		channel = connect.cancel(state)
		if not channel:
			frappe.throw(_("The Facebook login is invalid or has expired."), frappe.PermissionError)
		_redirect(connect.form_url(channel, {"fb_connect_error": (error or "no_code")[:40]}))
		return
	try:
		channel, session = connect.complete(code, state or "")
	except MessengerError as e:
		frappe.throw(e.message_vi)
	_redirect(connect.form_url(channel, {"fb_connect": session}))


@frappe.whitelist(methods=["POST"])
def pages(session: str) -> dict:
	"""Danh sách Page vừa được cấp quyền (mã, tên)."""
	try:
		return connect.list_pages(session)
	except MessengerError as e:
		frappe.throw(e.message_vi)


@frappe.whitelist(methods=["POST"])
def subscribe(session: str, page_id: str) -> dict:
	"""Đăng ký webhook cho Page đã chọn; trả các trường Page đang đăng ký sau khi đọc lại."""
	try:
		return connect.subscribe(session, page_id)
	except MessengerError as e:
		frappe.throw(e.message_vi)
