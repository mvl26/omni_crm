"""Trạng thái hội thoại kịch bản (trường `script_*` trên Omni Identity) và hành trình trên CRM Lead."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm.script import store

IDENTITY = "Omni Identity"
LEAD = "CRM Lead"
FIELDS = (
	"script_node",
	"script_step",
	"script_reask_count",
	"script_greeted_at",
	"script_last_bot_at",
	"script_state",
)
# Trạng thái kịch bản -> CRM Lead Status gốc (docs/bot_v3_plan.md mục 6; tên status đã kiểm trên omni.test 17/11)
JOURNEY_TO_STATUS = {
	"Mới": "New",
	"Đang tư vấn": "Contacted",
	"Chờ gửi tài liệu": "Contacted",
	"Chờ báo giá": "Contacted",
	"Đã gửi tài liệu": "Contacted",
	"Đã gửi báo giá": "Contacted",
	"Đã hẹn demo": "Qualified",
	"Đã demo": "Qualified",
	"Thương lượng": "Qualified",
	"Đồng ý mua – chờ ký hợp đồng": "Qualified",  # noqa: RUF001 (đúng option Select)
	"Chăm sóc lại": "Nurture",
	"Thua": "Unqualified",
	"Không liên hệ": "Unqualified",
}
LOST_REASON = "Other"  # CRM Lost Reason chuẩn của CRM
KEEP_STATUSES = {"Converted"}  # không hạ trạng thái Lead đã chuyển đổi
# Chỉ đẩy CRM Lead Status TIẾN theo bậc; status ngoài bảng (Junk, Lost, tự đặt) không bị bot đổi
STATUS_RANK = {"New": 0, "Contacted": 1, "Nurture": 2, "Qualified": 3}
LOST_JOURNEYS = frozenset({"Thua", "Không liên hệ"})  # chuyển rõ ràng sang trạng thái thua
JUNK_STATUS = "Junk"
JOURNEY_FIELD = "omni_journey_status"


def journey_options() -> set[str]:
	"""Các lựa chọn hợp lệ của trường Select `omni_journey_status` trên CRM Lead."""
	field = frappe.get_meta(LEAD).get_field(JOURNEY_FIELD)
	return {o.strip() for o in (field.options or "").split("\n") if o.strip()} if field else set()


def _is_lost(status: str | None) -> bool:
	return bool(status) and frappe.get_cached_value("CRM Lead Status", status, "type") == "Lost"


def _may_move(current: str | None, target: str, journey: str) -> bool:
	"""Có được đổi CRM Lead Status từ `current` sang `target` không (chỉ tiến, không đụng Converted/Lost/Junk)."""
	if current == target or current in KEEP_STATUSES or current == JUNK_STATUS or _is_lost(current):
		return False
	if journey in LOST_JOURNEYS:
		return True
	if not current:
		return True
	if current not in STATUS_RANK or target not in STATUS_RANK:
		return False
	return STATUS_RANK[target] > STATUS_RANK[current]


def get(identity: str) -> dict:
	"""Trạng thái kịch bản của định danh: các trường `script_*` (script_state đã đọc thành dict)."""
	row = frappe.db.get_value(IDENTITY, identity, list(FIELDS), as_dict=True) or {}
	out = {f: row.get(f) for f in FIELDS}
	out["script_reask_count"] = cint(out["script_reask_count"])
	out["script_state"] = store.as_dict(out["script_state"])
	return out


def set_node(identity: str, code: str | None, step: str | None = None) -> None:
	"""Ghi ô kịch bản đang đứng (và câu đang hỏi)."""
	frappe.db.set_value(
		IDENTITY,
		identity,
		{"script_node": store.normalize_code(code) or None, "script_step": step},
		update_modified=False,
	)


def update_state(identity: str, **values) -> dict:
	"""Gộp `values` vào `script_state` (JSON) và trả dict mới; giá trị None xóa khóa."""
	data = get(identity)["script_state"]
	for k, v in values.items():
		if v is None:
			data.pop(k, None)
		else:
			data[k] = v
	frappe.db.set_value(
		IDENTITY,
		identity,
		"script_state",
		json.dumps(data, ensure_ascii=False, default=str),
		update_modified=False,
	)
	return data


def mark_greeted(identity: str) -> None:
	"""Đánh dấu đã chào (khách cũ không chào lại)."""
	frappe.db.set_value(IDENTITY, identity, "script_greeted_at", now_datetime(), update_modified=False)


def bump_reask(identity: str) -> int:
	"""Tăng số lần hỏi lại liên tiếp; trả số mới."""
	count = cint(frappe.db.get_value(IDENTITY, identity, "script_reask_count")) + 1
	frappe.db.set_value(IDENTITY, identity, "script_reask_count", count, update_modified=False)
	return count


def reset_reask(identity: str) -> None:
	"""Đặt lại số lần hỏi lại về 0."""
	frappe.db.set_value(IDENTITY, identity, "script_reask_count", 0, update_modified=False)


def add_service(lead: str | None, svc: str | None) -> list[str]:
	"""Thêm mã dịch vụ vào `omni_services` của Lead (không trùng, giữ thứ tự); trả danh sách mới."""
	if not lead or not svc:
		return []
	raw = frappe.db.get_value(LEAD, lead, "omni_services") or ""
	items = [s.strip() for s in raw.split(",") if s.strip()]
	if svc.strip().upper() not in items:
		items.append(svc.strip().upper())
		frappe.db.set_value(LEAD, lead, "omni_services", ", ".join(items), update_modified=False)
	return items


def set_journey(lead: str | None, status: str) -> None:
	"""Ghi `omni_journey_status` và cập nhật CRM Lead Status gốc theo bảng quy đổi (mục 6 kế hoạch).

	Trạng thái hành trình không nằm trong lựa chọn của trường thì KHÔNG ghi gì (log một dòng). CRM Lead Status chỉ tiến
	theo bậc New < Contacted < Nurture < Qualified; không đổi Lead đã Converted, đang ở trạng thái thua hay Junk; riêng
	"Thua"/"Không liên hệ" đưa Lead đang mở về Unqualified. Status quy đổi không có trên site thì chỉ ghi trường hành
	trình và log một dòng. "Không liên hệ" bật thêm `omni_do_not_contact`.
	"""
	if not lead or not frappe.db.exists(LEAD, lead):
		return
	if status not in journey_options():
		frappe.logger("omni").info("omni script: trạng thái hành trình không hợp lệ, bỏ qua (lead=%s)", lead)
		return
	values: dict = {"omni_journey_status": status}
	if status == "Không liên hệ":
		values["omni_do_not_contact"] = 1
	frappe.db.set_value(LEAD, lead, values, update_modified=False)
	target = JOURNEY_TO_STATUS.get(status)
	current = frappe.db.get_value(LEAD, lead, "status")
	if not target or not _may_move(current, target, status):
		return
	if not frappe.db.exists("CRM Lead Status", target):
		frappe.logger("omni").info("omni script: CRM Lead Status %s không có trên site, bỏ qua", target)
		return
	doc = frappe.get_doc(LEAD, lead)
	doc.status = target
	if frappe.get_cached_value("CRM Lead Status", target, "type") == "Lost" and not doc.get("lost_reason"):
		# CRM bắt buộc lý do khi chuyển sang trạng thái Lost: dùng "Other" + ghi chú nội bộ
		doc.lost_reason = LOST_REASON if frappe.db.exists("CRM Lost Reason", LOST_REASON) else None
		doc.lost_notes = (
			doc.get("lost_notes") or doc.get("omni_lost_reason_text") or _("Bot kịch bản: {0}").format(status)
		)
		if not doc.lost_reason:
			frappe.logger("omni").info("omni script: thiếu CRM Lost Reason %s, bỏ qua status", LOST_REASON)
			return
	doc.save(ignore_permissions=True)
