"""Ẩn tin Messenger cũ khỏi tab Hoạt động/Email (ADR 0023), cùng cách với `hide_zalo_activity` (ADR 0017). CHẠY TAY,
mặc định chỉ xem trước.

    bench --site <site> execute omni_crm.ops.hide_messenger_activity.run                                  # xem trước
    bench --site <site> execute omni_crm.ops.hide_messenger_activity.run --kwargs "{'dry_run': 0}"        # áp dụng
    bench --site <site> execute omni_crm.ops.hide_messenger_activity.run --kwargs "{'dry_run': 0, 'reverse': 1}"   # đổi lại

Chỉ đổi `Communication.communication_type` "Communication" -> "Chat" (reverse: ngược lại) của Communication tin Meta
Messenger do Omni ghi (có `omni_inbound_message` / `omni_outbound_message` trỏ tới tin của kênh Meta Messenger). Không đụng
kênh khác. `db.set_value(update_modified=False)`: không chạy lại hook CRM, không đổi `modified`. Chạy lại không đổi thêm gì.
Không in tên hay nội dung khách: chỉ số lượng và mã Communication mẫu.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

BATCH = 500
SAMPLE = 5
MESSENGER = "Meta Messenger"


def _rows(src: str) -> list[str]:
	"""Mã Communication tin Messenger đang có loại `src`."""
	inbound = frappe.db.sql(
		"""select c.name from `tabCommunication` c
		join `tabOmni Inbound Message` i on i.name = c.omni_inbound_message
		where c.communication_type = %s and i.channel_type = %s and ifnull(c.omni_inbound_message, '') != ''""",
		(src, MESSENGER),
	)
	outbound = frappe.db.sql(
		"""select c.name from `tabCommunication` c
		join `tabOmni Outbound Message` o on o.name = c.omni_outbound_message
		join `tabOmni Channel` ch on ch.name = o.channel
		where c.communication_type = %s and ch.channel_type = %s and ifnull(c.omni_outbound_message, '') != ''""",
		(src, MESSENGER),
	)
	return sorted({r[0] for r in (*inbound, *outbound)})


def run(dry_run: int | str = 1, reverse: int | str = 0) -> dict:
	"""Đổi loại Communication tin Messenger cũ; trả {"dry_run", "reverse", "to_type", "matched", "changed", "sample"}."""
	dry, rev = bool(cint(dry_run)), bool(cint(reverse))
	src, dst = ("Chat", "Communication") if rev else ("Communication", "Chat")
	names = _rows(src)
	changed = 0
	if not dry:
		for i in range(0, len(names), BATCH):
			for name in names[i : i + BATCH]:
				frappe.db.set_value("Communication", name, "communication_type", dst, update_modified=False)
				changed += 1
			frappe.db.commit()
	result = {
		"dry_run": dry,
		"reverse": rev,
		"to_type": dst,
		"matched": len(names),
		"changed": changed,
		"sample": names[:SAMPLE],
	}
	print(result)
	return result
