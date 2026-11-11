"""Thư Sale gửi từ CRM (trên Lead/Deal) đi ngay, không chờ lượt quét hàng đợi email của Frappe.

Frappe xếp thư vào Email Queue rồi `frappe.email.queue.flush` mới gửi; flush chạy theo sự kiện "all" của scheduler
(mặc định 4 phút một lần, `scheduler_interval`), nên thư trả lời khách trễ trung bình khoảng 2 phút. Ở đây Email Queue
mới tham chiếu CRM Lead/CRM Deal được đẩy một job gửi ngay sau commit.

Không gửi hai lần khi chạy cùng flush: cả hai khóa dòng Email Queue (`for_update`), và `EmailQueue.send` chỉ gửi khi
trạng thái còn "Not Sent"/"Partially Sent" (đặt "Sending" và commit trước khi gửi). Job lỗi thì thư vẫn nằm trong hàng
đợi với số lần thử như Frappe tự ghi, flush gửi lại như cũ. Thư hẹn giờ (`send_after` trong tương lai) để Frappe lo.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

DOCTYPES = ("CRM Lead", "CRM Deal")
QUEUE = "short"
SEND_METHOD = "omni_crm.crm_bridge.fast_email.send"


def on_email_queue_insert(doc: Any, method: str | None = None) -> None:
	"""after_insert của Email Queue. Không bao giờ ném lỗi: không đẩy được thì thư vẫn đi theo lượt flush."""
	try:
		if doc.reference_doctype not in DOCTYPES or doc.status != "Not Sent":
			return
		if doc.send_after and get_datetime(doc.send_after) > now_datetime():
			return
		frappe.enqueue(
			SEND_METHOD,
			queue=QUEUE,
			enqueue_after_commit=True,
			email_queue=doc.name,
		)
	except Exception:
		frappe.log_error(title="Omni: không đẩy được job gửi thư ngay")


def send(email_queue: str) -> None:
	"""Job nền: gửi một Email Queue nếu nó vẫn đang chờ. Lỗi được ghi log giống flush của Frappe."""
	if not frappe.db.exists("Email Queue", email_queue):
		return
	try:
		frappe.get_doc("Email Queue", email_queue, for_update=True).send()
	except Exception:
		frappe.db.rollback()
		frappe.get_doc("Email Queue", email_queue).log_error(
			title="Omni: gửi thư ngay thất bại, chờ flush gửi lại"
		)
