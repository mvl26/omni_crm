"""Sửa tên người gửi của tin Zalo cũ trong dòng thời gian (V3-01, mục A). CHẠY TAY, mặc định chỉ xem trước.

    bench --site <site> execute omni_crm.ops.backfill_sender.run                       # xem trước (dry-run)
    bench --site <site> execute omni_crm.ops.backfill_sender.run --kwargs "{'dry_run': 0}"   # áp dụng

Chỉ sửa `sender_full_name` của Communication tin Zalo OA do khách gửi (có `omni_inbound_message`), theo cùng quy tắc với tin
mới (`timeline.sender_label`): tên định danh, rồi tên Lead/Deal, rồi mã rút gọn. Không đổi `modified`, không gọi hook, chạy lại
không đổi thêm gì (chỉ ghi khi giá trị khác). Không in tên hay mã khách: chỉ số lượng và mã Communication.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from omni_crm.crm_bridge import timeline

BATCH = 200
SAMPLE = 5


def _rows() -> list[dict]:
	return frappe.db.sql(
		"""
		select c.name, c.sender_full_name, c.reference_doctype, c.reference_name,
			i.channel_type, i.external_sender_id, d.display_name
		from `tabCommunication` c
		join `tabOmni Inbound Message` i on i.name = c.omni_inbound_message
		left join `tabOmni Identity` d on d.name = i.identity
		where c.communication_medium = 'Chat' and c.sent_or_received = 'Received'
			and i.channel_type = 'Zalo OA' and i.event_type = 'message'
		order by c.creation asc, c.name asc
		""",
		as_dict=True,
	)


def run(dry_run: int | str = 1) -> dict:
	"""Sửa (hoặc chỉ đếm khi dry_run) tên người gửi tin Zalo cũ; trả {"dry_run", "scanned", "changed", "sample"}."""
	dry = bool(cint(dry_run))
	rows = _rows()
	names: dict[tuple[str, str], str | None] = {}
	changed, sample, pending = 0, [], 0
	for row in rows:
		key = (row.reference_doctype, row.reference_name)
		if key not in names:
			names[key] = (
				timeline.target_display_name(key)
				if key[0] in (timeline.LEAD, timeline.DEAL) and frappe.db.exists(*key)
				else None
			)
		new = timeline.sender_label(row.display_name, names[key], row.channel_type, row.external_sender_id)
		if new == (row.sender_full_name or ""):
			continue
		changed += 1
		if len(sample) < SAMPLE:
			sample.append(row.name)
		if dry:
			continue
		frappe.db.set_value("Communication", row.name, "sender_full_name", new, update_modified=False)
		pending += 1
		if pending >= BATCH:
			frappe.db.commit()
			pending = 0
	if not dry:
		frappe.db.commit()
	result = {"dry_run": dry, "scanned": len(rows), "changed": changed, "sample": sample}
	print(result)
	return result
