"""Lệnh chạy tay "cập nhật tên Zalo" cho định danh còn thiếu tên (V3-01). Mặc định chỉ xem trước.

    bench --site <site> execute omni_crm.channels.zalo.profile_refresh.run                                  # xem trước
    bench --site <site> execute omni_crm.channels.zalo.profile_refresh.run --kwargs "{'dry_run': 0}"        # áp dụng

Chạy sau khi OA đủ gói và đã TẮT sandbox: sandbox thì lệnh không gọi API Zalo (chỉ báo lý do). Bỏ qua thời gian chờ sau lỗi
(`PROFILE_COOLDOWN`). Chỉ điền `Omni Identity.display_name` đang trống; không đổi tên Lead (sale tự sửa Lead nếu cần). Không in
tên hay mã khách: chỉ số lượng và mã định danh nội bộ.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from omni_crm import flags
from omni_crm.channels.zalo import errors, worker_hooks
from omni_crm.pipeline.retry import redact

IDENTITY = "Omni Identity"
INBOUND = "Omni Inbound Message"
DEFAULT_LIMIT = 50


class _Msg:
	"""Tối thiểu của tin để dùng lại `fetch_profile`."""

	def __init__(self, identity_key: str, external_id: str, channel: str):
		self.identity_key, self.external_sender_id, self.channel = identity_key, external_id, channel


STOP_KINDS = (errors.TRANSIENT, errors.TOKEN)  # quá hạn mức / token hỏng: dừng cả lượt, không dội tiếp


def _refused(lead: str | None, cache: dict[str, bool]) -> bool:
	"""Lead của định danh đã từ chối nhận liên hệ / đã bị xóa dữ liệu (cùng hàm kiểm với Hộp thư)."""
	if not lead:
		return False
	if lead not in cache:
		from omni_crm.api import customer as customer_api

		cache[lead] = bool(customer_api._person_flags(lead)["refused"])
	return cache[lead]


def _missing(limit: int) -> list[dict]:
	"""Định danh Zalo thiếu tên, bỏ qua khách đã từ chối / đã bị xóa dữ liệu; tối đa `limit`."""
	out: list[dict] = []
	cache: dict[str, bool] = {}
	rows = frappe.get_all(
		IDENTITY,
		filters=[["channel_type", "=", "Zalo OA"], ["display_name", "is", "not set"]],
		fields=["name", "identity_key", "external_id", "lead"],
		order_by="last_inbound_at desc, creation desc",
		limit_page_length=limit * 4 + 20,  # dư để bù số bị loại
	)
	for row in rows:
		if _refused(row.lead, cache):
			continue
		out.append(row)
		if len(out) >= limit:
			break
	return out


def run(dry_run: int | str = 1, limit: int | str = DEFAULT_LIMIT) -> dict:
	"""Cập nhật (hoặc chỉ đếm khi dry_run) tên Zalo cho định danh thiếu tên; trả {"dry_run","candidates","updated","failed","sandbox"}."""
	dry = bool(cint(dry_run))
	rows = _missing(max(cint(limit) or DEFAULT_LIMIT, 1))
	result = {
		"dry_run": dry,
		"candidates": len(rows),
		"updated": 0,
		"failed": 0,
		"sandbox": bool(flags.sandbox()),
	}
	if dry or result["sandbox"] or not rows:
		print(result)
		return result
	for row in rows:
		channel = frappe.db.get_value(
			INBOUND, {"identity": row.name}, "channel", order_by="received_at desc"
		) or frappe.db.get_value("Omni Channel", {"channel_type": "Zalo OA", "enabled": 1}, "name")
		if not channel:
			result["failed"] += 1
			continue
		channel_doc = frappe.get_doc("Omni Channel", channel)
		msg = _Msg(row.identity_key, row.external_id, channel)
		sink: list = []
		try:
			profile = worker_hooks.fetch_profile(msg, channel_doc, ignore_cooldown=True, error_sink=sink)
		except Exception as e:  # fetch_profile không ném; phòng hờ để lô không dừng giữa chừng
			frappe.logger("omni").warning("omni zalo: cập nhật tên lỗi: %s", redact(str(e))[:200])
			profile = {}
		name = (profile.get("display_name") or "").strip()
		if name:
			frappe.db.set_value(IDENTITY, row.name, "display_name", name, update_modified=False)
			result["updated"] += 1
		else:
			result["failed"] += 1
		frappe.db.commit()
		if sink and getattr(sink[0], "kind", None) in STOP_KINDS:
			result["stopped"] = sink[0].kind  # dừng lượt: quá hạn mức hoặc token hỏng
			break  # mỗi khách một đơn vị việc; API ngoài đã gọi xong, không giữ khóa
	print(result)
	return result
