"""V3-07: gắn Loại tin cho tin Zalo đã gửi trong chu kỳ quỹ hiện tại trước khi có bộ chọn loại tin.

Không có bước này thì job đối chiếu hằng ngày sẽ coi các tin cũ là "đi vòng" và bộ đếm quỹ bỏ sót tin ngoài 48 giờ.
Phân loại như tin nhân viên gõ ngoài hệ thống: trong 48 giờ kể từ tin khách gần nhất trước nó = CS 48h, ngoài = CS ngoài 48h.
"""

import frappe

from omni_crm.channels.zalo import quota
from omni_crm.channels.zalo.worker_hooks import _echo_route


def execute():
	start, end = quota.cycle_bounds()
	rows = frappe.db.sql(
		"""
		select o.name, o.identity, o.sent_at from `tabOmni Outbound Message` o
		join `tabOmni Channel` c on c.name = o.channel
		where c.channel_type = 'Zalo OA' and o.status = 'Sent' and ifnull(o.message_kind, '') = ''
			and o.sent_at >= %(start)s and o.sent_at < %(end)s
		""",
		{"start": start, "end": end},
		as_dict=True,
	)
	for r in rows:
		frappe.db.set_value(
			"Omni Outbound Message",
			r.name,
			_echo_route(frappe._dict(received_at=r.sent_at), r.identity),
			update_modified=False,
		)
