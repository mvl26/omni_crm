"""Trang Lead của bot kịch bản v3 (kịch bản mục 3.5, 4.4, 5.1): mốc "Đã gửi tài liệu / báo giá", kết quả demo,
"không cho bot nhắn lại", và phần "Hành trình bot" trong bảng bên của Lead (CRM Fields Layout).

Hai đường vào cùng một logic: API `omni_crm.api.script.*` (nút bấm) và `on_lead_fields_changed` (nhân viên sửa trường
ngay trong bảng bên). Lời gọi sang phần K5/K6 (`scheduler`, `flows.postdemo`) là import trễ, chưa gộp thì bỏ qua.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.script import state, switch

LEAD = "CRM Lead"
LAYOUT = "CRM Fields Layout"
SECTION_NAME = "omni_journey_section"
SECTION_LABEL = "Hành trình bot"
PANEL_FIELDS = (
	"omni_services",
	"omni_journey_status",
	"omni_journey_need",
	"omni_docs_sent_at",
	"omni_quote_sent_at",
	"omni_demo_at",
	"omni_demo_mode",
	"omni_demo_attendees",
	"omni_demo_result",
	"omni_demo_note",
	"omni_no_bot_followup",
	"omni_do_not_contact",
)
DEMO_RESULTS = ("Đã demo", "Khách vắng", "Hoãn")
JOURNEY_DOCS = "Đã gửi tài liệu"
JOURNEY_QUOTE = "Đã gửi báo giá"
JOURNEY_DEMO_DONE = "Đã demo"
NOTE_MAX = 500


def _call(module: str, func: str, *args: Any) -> bool:
	"""Gọi hàm của phần khác (import trễ); chưa gộp hoặc lỗi thì ghi log và trả False."""
	try:
		mod = frappe.get_module(f"omni_crm.script.{module}")
	except ImportError:
		return False
	fn = getattr(mod, func, None)
	if not fn:
		return False
	try:
		fn(*args)
		return True
	except NotImplementedError:
		return False
	except Exception:
		frappe.log_error(
			title="Omni: lỗi gọi luồng kịch bản từ trang Lead", message=f"{module}.{func} {args[:1]}"
		)
		return False


def _set(lead: str, values: dict) -> None:
	with flags.omni_bot():
		frappe.db.set_value(LEAD, lead, values)


def after_docs_sent(lead: str) -> None:
	"""Việc sau mốc "Đã gửi tài liệu": hành trình + đặt lịch nhắn lại Bước 4 (`scheduler.on_docs_sent`)."""
	with flags.omni_bot():
		state.set_journey(lead, JOURNEY_DOCS)
	_call("scheduler", "on_docs_sent", lead)


def after_quote_sent(lead: str) -> None:
	"""Việc sau mốc "Đã gửi báo giá": hành trình + đặt lịch nhắn lại (`scheduler.on_quote_sent`)."""
	with flags.omni_bot():
		state.set_journey(lead, JOURNEY_QUOTE)
	_call("scheduler", "on_quote_sent", lead)


def after_demo_result(lead: str, result: str) -> None:
	"""Việc sau khi có kết quả demo: "Đã demo" đổi hành trình; báo luồng sau demo (`flows.postdemo.on_demo_result`)."""
	if result == JOURNEY_DEMO_DONE:
		with flags.omni_bot():
			state.set_journey(lead, JOURNEY_DEMO_DONE)
	_call("flows.postdemo", "on_demo_result", lead, result)


def after_no_bot_followup(lead: str, value: bool) -> None:
	"""Bật "không cho bot nhắn lại": hủy các tin hẹn giờ đang chờ của Lead."""
	if value:
		_call("scheduler", "cancel_for", lead)


def mark_docs_sent(lead: str) -> dict:
	"""Ghi mốc "Đã gửi tài liệu" (giờ hiện tại) cho Lead và chạy việc kèm theo; trả giá trị đã ghi."""
	at = now_datetime()
	_set(lead, {"omni_docs_sent_at": at})
	after_docs_sent(lead)
	return {"omni_docs_sent_at": at, "omni_journey_status": JOURNEY_DOCS}


def mark_quote_sent(lead: str) -> dict:
	"""Ghi mốc "Đã gửi báo giá" (giờ hiện tại) cho Lead và chạy việc kèm theo; trả giá trị đã ghi."""
	at = now_datetime()
	_set(lead, {"omni_quote_sent_at": at})
	after_quote_sent(lead)
	return {"omni_quote_sent_at": at, "omni_journey_status": JOURNEY_QUOTE}


def set_demo_result(lead: str, result: str, note: str | None = None) -> dict:
	"""Ghi kết quả demo (Đã demo | Khách vắng | Hoãn) và ghi chú; chạy việc kèm theo."""
	result = (result or "").strip()
	if result not in DEMO_RESULTS:
		frappe.throw(_("Kết quả demo không hợp lệ"), frappe.ValidationError)
	values: dict = {"omni_demo_result": result}
	if note is not None:
		values["omni_demo_note"] = str(note).strip()[:NOTE_MAX]
	_set(lead, values)
	after_demo_result(lead, result)
	return values


def set_no_bot_followup(lead: str, value: Any) -> dict:
	"""Bật/tắt "không cho bot nhắn lại" trên Lead; bật thì hủy tin hẹn giờ."""
	on = bool(cint(value)) if not isinstance(value, str) else value.strip().lower() in ("1", "true", "yes")
	_set(lead, {"omni_no_bot_followup": int(on)})
	after_no_bot_followup(lead, on)
	return {"omni_no_bot_followup": int(on)}


WATCHED = (
	"omni_docs_sent_at",
	"omni_quote_sent_at",
	"omni_demo_result",
	"omni_no_bot_followup",
	"omni_do_not_contact",
)


def _script_lead(lead: str) -> bool:
	"""Lead có định danh đang chạy bot kịch bản (bật toàn bộ hoặc trong danh sách thử)."""
	identities = frappe.get_all("Omni Identity", filters={"lead": lead}, pluck="name")
	return any(switch.enabled_for(i) for i in identities)


def on_lead_fields_changed(doc: Any, method: str | None = None) -> None:
	"""Hook `CRM Lead.on_update`: nhân viên sửa trường hành trình trong bảng bên thì chạy cùng logic với nút bấm.

	Rẻ và an toàn: không trường nào trong `WATCHED` đổi thì thoát ngay; bỏ qua khi tạo mới, khi hệ thống ghi (cờ
	in_omni_bot, job nền, migrate/patch) và với Lead không chạy bot kịch bản; không bao giờ ném lỗi ra ngoài.
	"""
	try:
		if doc.flags.in_insert or flags.is_system_write():
			return
		changed = [f for f in WATCHED if doc.has_value_changed(f)]
		if not changed or not _script_lead(doc.name):
			return
		if "omni_docs_sent_at" in changed and doc.get("omni_docs_sent_at"):
			after_docs_sent(doc.name)
		if "omni_quote_sent_at" in changed and doc.get("omni_quote_sent_at"):
			after_quote_sent(doc.name)
		if "omni_demo_result" in changed and doc.get("omni_demo_result") in DEMO_RESULTS:
			after_demo_result(doc.name, doc.get("omni_demo_result"))
		if "omni_no_bot_followup" in changed:
			after_no_bot_followup(doc.name, bool(cint(doc.get("omni_no_bot_followup"))))
		if "omni_do_not_contact" in changed and cint(doc.get("omni_do_not_contact")):
			_call("scheduler", "cancel_for", doc.name)
	except Exception:
		frappe.log_error(
			title="Omni: lỗi xử lý trường hành trình bot trên Lead", message=f"lead={doc.get('name')}"
		)


def add_section(sections: list) -> bool:
	"""Hàm thuần: thêm phần "Hành trình bot" vào cuối layout bảng bên; đã có thì không đổi. True nếu có thêm."""
	if any(isinstance(s, dict) and s.get("name") == SECTION_NAME for s in sections):
		return False
	used = {
		f if isinstance(f, str) else (f or {}).get("fieldname")
		for s in sections
		if isinstance(s, dict)
		for c in s.get("columns") or []
		for f in (c or {}).get("fields") or []
	}
	fields = [f for f in PANEL_FIELDS if f not in used]
	if not fields:
		return False
	sections.append(
		{
			"label": SECTION_LABEL,
			"name": SECTION_NAME,
			"opened": True,
			"columns": [{"name": "column_omni_journey", "fields": fields}],
		}
	)
	return True


def ensure_layout() -> bool:
	"""Thêm phần "Hành trình bot" vào bảng bên của Lead (CRM Fields Layout "CRM Lead-Side Panel"). KHÔNG tự chạy:
	người phụ trách chạy tay sau khi duyệt (`bench --site <site> execute omni_crm.script.lead_ui.ensure_layout`).

	Chỉ sửa bản ghi layout (dữ liệu), không sửa code app `crm`. Site chưa có layout bảng bên thì không làm gì (CRM
	tự tạo layout mặc định khi cài). Trả True nếu đã thêm. Không commit (bench execute tự commit).
	"""
	name = frappe.db.get_value(LAYOUT, {"dt": LEAD, "type": "Side Panel"})
	if not name:
		return False
	try:
		layout = json.loads(frappe.db.get_value(LAYOUT, name, "layout") or "[]")
	except ValueError:
		return False
	if not isinstance(layout, list):
		return False
	meta = frappe.get_meta(LEAD)
	if not all(meta.has_field(f) for f in PANEL_FIELDS):
		return False  # chưa migrate Custom Field: không thêm trường lạ vào layout
	if not add_section(layout):
		return False
	frappe.db.set_value(LAYOUT, name, "layout", json.dumps(layout, ensure_ascii=False))
	frappe.clear_cache(doctype=LEAD)
	return True
