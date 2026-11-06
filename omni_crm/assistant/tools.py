"""Bảy tool của trợ lý nội bộ (V3-05) và lớp chặn chung: quyền người dùng, tập Lead hợp lệ, trần ghi, ghi vết.

Danh sách tool và danh sách trường được sửa là hằng số trong code (quy tắc 2, 3). Mô hình chỉ chọn tool và tham số; mọi
kiểm tra nằm ở đây, chạy dưới phiên của người đang chat (quy tắc 1):
- Đọc: `frappe.get_list` / `has_permission(..., "read")`, nên bot không thấy gì hơn người dùng.
- Ghi: Lead phải nằm trong `allowed_leads` của phiên (quy tắc 6, ngoài tập = Denied + Error Log) và người dùng phải có
  quyền SỬA Lead. Ghi nhật ký chăm sóc dùng đúng các hàm của nút "Ghi nhật ký chăm sóc" (`api/customer.save_care_log`),
  cùng điều kiện quyền (anh Việt chốt 2026-11-06). CRM Task và CRM Lead ghi bằng quyền của chính người dùng.
- Bản ghi kiểm toán (Omni Assistant Session/Action) chỉ hệ thống ghi, qua `_sys_insert`/`_sys_update` (ADR 0018).
Không có tool xóa, gửi tin, sửa cấu hình hay chạy truy vấn tự do.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime, validate_email_address

from omni_crm.assistant import context
from omni_crm.crm_bridge import care_log, timeline
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity.normalize import normalize_email, normalize_phone

LEAD = "CRM Lead"
TASK = "CRM Task"
CALL_LOG = "CRM Call Log"
NOTE = "FCRM Note"
CALL_TYPES = ("Incoming", "Outgoing")
CALL_STATUSES = ("Completed", "No Answer", "Busy", "Failed", "Canceled")
LOG = "Omni Care Log"
SESSION = "Omni Assistant Session"
ACTION = "Omni Assistant Action"
FIND_LIMIT = 5
SEARCH_LIMIT = 20
CARE_LOG_LIMIT = 20
SCAN_LIMIT = 20000  # số Lead tối đa quét khi tìm theo tên không dấu (lọc trong Python)
TEXT_LIMIT = 2000
# Brief ghi `email_id`; CRM Lead v1.73.0 dùng `email` (đã đọc crm_lead.json). Theo code, ghi lệch ở báo cáo.
LEAD_UPDATE_FIELDS = ("status", "lead_owner", "source", "mobile_no", "email", "organization")
LEAD_READ_FIELDS = (
	"name",
	"lead_name",
	"first_name",
	"last_name",
	"organization",
	"status",
	"lead_owner",
	"source",
	"mobile_no",
	"phone",
	"email",
	"job_title",
	"territory",
	"industry",
	"omni_bot_summary",
	"omni_intent",
	"creation",
	"modified",
)
TASK_STATUSES = ("Backlog", "Todo", "In Progress", "Done", "Canceled")
DONE = "Success"


class ToolError(Exception):
	"""Tham số sai hoặc thiếu: trả thông điệp cho mô hình (status Error), không có gì được ghi."""


class Denied(Exception):
	"""Không đủ quyền hoặc Lead nằm ngoài phiên (status Denied)."""


class LimitReached(Exception):
	"""Chạm trần ghi của một lượt (status Limit): dừng, không chạy tiếp."""


@dataclass
class Ctx:
	"""Trạng thái của một lượt chat: phiên, người ra lệnh, câu lệnh gốc, tập Lead hợp lệ, số lần ghi."""

	session: str
	user: str
	command: str
	allowed: set[str]
	max_writes: int
	writes: int = 0
	actions: list[dict] = field(default_factory=list)


# --- ghi kiểm toán (chỉ hệ thống) ---------------------------------------------------------------------------------


def _sys_insert(data: dict) -> str:
	"""Ghi một bản ghi kiểm toán của trợ lý (Session/Action): chỉ hai doctype này, người dùng không tự ghi được (ADR 0018)."""
	if data.get("doctype") not in (SESSION, ACTION):
		raise ValueError("chỉ dùng cho bản ghi kiểm toán của trợ lý")
	doc = frappe.get_doc(data)
	doc.insert(ignore_permissions=True)
	return str(doc.name)


def _sys_update(doctype: str, name: str, values: dict) -> None:
	"""Cập nhật bản ghi kiểm toán của trợ lý (Session/Action), không qua quyền người dùng."""
	if doctype not in (SESSION, ACTION):
		raise ValueError("chỉ dùng cho bản ghi kiểm toán của trợ lý")
	frappe.db.set_value(doctype, name, values, update_modified=True)


def _dump(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, default=str)


def record_action(
	ctx: Ctx,
	tool: str,
	args: dict,
	status: str,
	result: Any,
	target: tuple[str, str] | None = None,
	requires_approval: bool = False,
) -> str:
	"""Ghi một Omni Assistant Action (ai ra lệnh = chủ phiên, câu lệnh gốc, tool, tham số, kết quả) và commit."""
	name = _sys_insert(
		{
			"doctype": ACTION,
			"session": ctx.session,
			"tool_name": tool,
			"status": status,
			"command": ctx.command[:TEXT_LIMIT],
			"arguments": _dump(args),
			"result": _dump(result),
			"requires_approval": 1 if requires_approval else 0,
			"target_doctype": target[0] if target else None,
			"target_name": target[1] if target else None,
		}
	)
	frappe.db.commit()
	return name


def add_comment(doctype: str, name: str, user: str, verb: str = "Tạo") -> None:
	"""Comment ngắn trên bản ghi bot tạo/sửa: "<verb> bởi trợ lý AI theo lệnh của <user>"."""
	full = frappe.utils.get_fullname(user) or user
	frappe.get_doc(doctype, name).add_comment(
		"Comment", _("{0} bởi trợ lý AI theo lệnh của {1}").format(_(verb), full)
	)


# --- tiện ích ---------------------------------------------------------------------------------------------------


def fold(text: str | None) -> str:
	"""Bỏ dấu tiếng Việt (kể cả đ/Đ), chữ thường, gộp khoảng trắng: "Bùi Hoàng Việt" -> "bui hoang viet"."""
	text = unicodedata.normalize("NFKD", str(text or "")).replace("đ", "d").replace("Đ", "D")
	text = "".join(c for c in text if not unicodedata.combining(c))
	return " ".join(text.lower().split())


def digits(text: str | None) -> str:
	"""Chỉ giữ chữ số."""
	return re.sub(r"\D", "", str(text or ""))


def _datetime(value: Any, label: str) -> Any:
	if value in (None, ""):
		return None
	try:
		return get_datetime(value)
	except Exception:
		raise ToolError(_("{0} không đúng định dạng YYYY-MM-DD HH:MM:SS").format(label)) from None


def _primary(lead: Any) -> str:
	name = str(lead or "").strip()
	if not name or not frappe.db.exists(LEAD, name):
		raise ToolError(_("Không tìm thấy Lead {0}").format(name or "(trống)"))
	return lead_bridge.follow_merged(name) or name


def _readable(ctx: Ctx, lead: Any) -> str:
	name = _primary(lead)
	if not frappe.has_permission(LEAD, "read", doc=name, user=ctx.user):
		raise Denied(_("Bạn không có quyền xem Lead {0}").format(name))
	return name


def _writable(ctx: Ctx, lead: Any) -> str:
	"""Lead ghi được: có trong tập Lead của phiên (quy tắc 6) và người dùng có quyền sửa (quy tắc 1)."""
	raw = str(lead or "").strip()
	name = lead_bridge.follow_merged(raw) or raw if raw and frappe.db.exists(LEAD, raw) else raw
	if raw not in ctx.allowed and name not in ctx.allowed:
		frappe.log_error(
			title="Omni: trợ lý AI định ghi vào Lead ngoài phiên",
			message=_dump({"session": ctx.session, "user": ctx.user, "lead": raw[:140]}),
		)
		frappe.db.commit()
		raise Denied(
			_(
				"Lead {0} chưa xuất hiện trong phiên này (chưa tìm bằng find_lead/search_leads, không phải Lead đang mở)"
			).format(raw[:140] or "(trống)")
		)
	name = _primary(raw)
	if not frappe.has_permission(LEAD, "write", doc=name, user=ctx.user):
		raise Denied(_("Bạn không có quyền sửa Lead {0}").format(name))
	return name


def _count_write(ctx: Ctx) -> None:
	if ctx.writes >= ctx.max_writes:
		raise LimitReached(_("Đã chạm trần {0} thao tác ghi trong một lượt").format(ctx.max_writes))
	ctx.writes += 1


def _lead_summary(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"lead_name": row.get("lead_name") or "",
		"organization": row.get("organization") or "",
		"mobile_no": context.mask_value(row.get("mobile_no")),
		"status": row.get("status") or "",
		"lead_owner": row.get("lead_owner") or "",
		"modified": str(row.get("modified") or ""),
	}


# --- đọc --------------------------------------------------------------------------------------------------------


def find_lead(ctx: Ctx, query: str) -> dict:
	"""Tìm Lead theo tên (không dấu, không phân biệt hoa thường), SĐT (9 số cuối) hoặc email; tối đa 5 ứng viên."""
	q = str(query or "").strip()
	if len(q) < 2:
		raise ToolError(_("Cần ít nhất 2 ký tự để tìm"))
	rows = frappe.get_list(
		LEAD,
		filters={"omni_merged_into": ["is", "not set"]},
		fields=[
			"name",
			"lead_name",
			"organization",
			"mobile_no",
			"phone",
			"email",
			"omni_phone_e164",
			"omni_email_norm",
			"status",
			"lead_owner",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=SCAN_LIMIT,
	)
	q_digits = digits(q)
	if "@" in q:
		email = normalize_email(q) or q.lower()
		hits = [r for r in rows if email in {(r.email or "").lower(), (r.omni_email_norm or "").lower()}]
	elif len(q_digits) >= 9 and len(q_digits) >= len(re.sub(r"[\s.+()\-]", "", q)) - 1:
		tail = q_digits[-9:]
		hits = [
			r
			for r in rows
			if any(digits(r.get(f)).endswith(tail) for f in ("mobile_no", "phone", "omni_phone_e164"))
		]
	else:
		needle = fold(q)
		hits = [r for r in rows if needle in fold(r.lead_name)]
		hits.sort(key=lambda r: fold(r.lead_name) != needle)  # khớp nguyên tên lên đầu
	top = hits[:FIND_LIMIT]
	ctx.allowed.update(r.name for r in top)
	return {"total": len(hits), "leads": [_lead_summary(r) for r in top]}


def get_lead(ctx: Ctx, lead: str) -> dict:
	"""Chi tiết nghiệp vụ một Lead (chỉ trường permlevel 0, không có trường bí mật)."""
	name = _readable(ctx, lead)
	meta = frappe.get_meta(LEAD)
	fields = [
		f for f in LEAD_READ_FIELDS if f == "name" or (meta.get_field(f) and not meta.get_field(f).permlevel)
	]
	row = frappe.db.get_value(LEAD, name, fields, as_dict=True) or {}
	for f in ("mobile_no", "phone", "email"):
		if f in row:
			row[f] = context.mask_value(row[f])
	return {k: ("" if v is None else str(v) if not isinstance(v, int | float) else v) for k, v in row.items()}


def list_care_logs(ctx: Ctx, lead: str, limit: int = 10) -> dict:
	"""Lịch sử chăm sóc của Lead, mới nhất trước."""
	name = _readable(ctx, lead)
	rows = frappe.get_list(
		LOG,
		filters={"lead": name},
		fields=[
			"name",
			"channel",
			"started_at",
			"handled_by",
			"handled_kind",
			"summary",
			"outcome",
			"next_action",
			"next_due",
			"crm_task",
		],
		order_by="started_at desc",
		limit_page_length=max(1, min(cint(limit) or 10, CARE_LOG_LIMIT)),
	)
	return {"lead": name, "care_logs": [{k: "" if v is None else str(v) for k, v in r.items()} for r in rows]}


def search_leads(
	ctx: Ctx,
	status: str | None = None,
	lead_owner: str | None = None,
	source: str | None = None,
	created_after: str | None = None,
	created_before: str | None = None,
	limit: int = 20,
) -> dict:
	"""Lọc Lead theo đúng các tham số cho phép; trả tổng số khớp và tối đa 20 dòng."""
	filters: list[list] = [[LEAD, "omni_merged_into", "is", "not set"]]
	for fieldname, value in (("status", status), ("lead_owner", lead_owner), ("source", source)):
		if value:
			filters.append([LEAD, fieldname, "=", str(value)])
	after = _datetime(created_after, "created_after")
	before = _datetime(created_before, "created_before")
	if after:
		filters.append([LEAD, "creation", ">=", after])
	if before:
		filters.append([LEAD, "creation", "<=", before])
	total = frappe.get_list(LEAD, filters=filters, fields=["count(name) as n"])
	rows = frappe.get_list(
		LEAD,
		filters=filters,
		fields=[
			"name",
			"lead_name",
			"organization",
			"mobile_no",
			"status",
			"lead_owner",
			"source",
			"creation",
			"modified",
		],
		order_by="creation desc",
		limit_page_length=max(1, min(cint(limit) or SEARCH_LIMIT, SEARCH_LIMIT)),
	)
	ctx.allowed.update(r.name for r in rows)
	leads = []
	for r in rows:
		item = _lead_summary(r)
		item.update({"source": r.source or "", "creation": str(r.creation)})
		leads.append(item)
	return {"total": cint(total[0].n) if total else 0, "leads": leads}


# --- ghi --------------------------------------------------------------------------------------------------------


def create_care_log(
	ctx: Ctx,
	lead: str,
	channel: str,
	started_at: str,
	outcome: str,
	summary: str | None = None,
	next_action: str | None = None,
	next_due: str | None = None,
) -> dict:
	"""Ghi một phiên chăm sóc có kết quả như nút "Ghi nhật ký chăm sóc"; có bước tiếp theo + hạn thì tạo CRM Task."""
	name = _writable(ctx, lead)
	if channel not in care_log.CHANNELS:
		raise ToolError(_("Kênh phải là một trong: {0}").format(", ".join(care_log.CHANNELS)))
	if outcome not in care_log.OUTCOMES:
		raise ToolError(_("Kết quả phải là một trong: {0}").format(", ".join(care_log.OUTCOMES)))
	if not started_at:
		raise ToolError(_("Thiếu thời điểm trao đổi (started_at)"))
	_datetime(started_at, "started_at")
	_datetime(next_due, "next_due")
	try:
		care_log.validate_outcome(outcome, next_action, next_due)
	except frappe.ValidationError as e:
		raise ToolError(str(e)) from None
	_count_write(ctx)
	at = care_log.clamp_contact_time(name, started_at)
	log = care_log.create_manual_log(name, channel, at, None, user=ctx.user)
	res = care_log.save_outcome(
		log,
		outcome,
		next_action=next_action,
		next_due=next_due,
		create_task=1,
		summary=(summary or "").strip()[:TEXT_LIMIT] or None,
		user=ctx.user,
		at=at,
	)
	add_comment(LOG, log, ctx.user)
	if res.get("task"):
		add_comment(TASK, res["task"], ctx.user)
	timeline.notify(name, None)
	frappe.db.commit()  # một thao tác ghi = một đơn vị việc
	return {
		"care_log": log,
		"crm_task": res.get("task"),
		"lead": name,
		"started_at": str(at),
		"channel": channel,
		"outcome": outcome,
		"next_action": (next_action or "").strip(),
	}


def upsert_task(
	ctx: Ctx,
	lead: str,
	title: str,
	due_date: str | None = None,
	assigned_to: str | None = None,
	status: str | None = None,
	task: str | None = None,
) -> dict:
	"""Tạo (hoặc sửa, khi có `task`) một CRM Task của Lead bằng quyền của người dùng."""
	name = _writable(ctx, lead)
	title = str(title or "").strip()
	if not title:
		raise ToolError(_("Thiếu tiêu đề việc"))
	due = _datetime(due_date, "due_date")
	if status and status not in TASK_STATUSES:
		raise ToolError(_("Trạng thái việc phải là một trong: {0}").format(", ".join(TASK_STATUSES)))
	if assigned_to and not (assigned_to != "Guest" and frappe.db.get_value("User", assigned_to, "enabled")):
		raise ToolError(_("assigned_to phải là email của một người dùng đang hoạt động"))
	deal = lead_bridge.get_deal_for_lead(name)
	refs = {(LEAD, name)} | ({("CRM Deal", deal)} if deal else set())
	if task:
		if not frappe.db.exists(TASK, task):
			raise ToolError(_("Không tìm thấy việc {0}").format(task))
		ref = frappe.db.get_value(TASK, task, ["reference_doctype", "reference_docname"])
		if tuple(ref or ()) not in refs:
			raise Denied(_("Việc {0} không thuộc Lead {1}").format(task, name))
		if not frappe.has_permission(TASK, "write", doc=task, user=ctx.user):
			raise Denied(_("Bạn không có quyền sửa việc {0}").format(task))
		_count_write(ctx)
		doc = frappe.get_doc(TASK, task)
		doc.title = title[:140]
		if due:
			doc.due_date = due
		if assigned_to:
			doc.assigned_to = assigned_to
		if status:
			doc.status = status
		doc.save()
		verb = "Sửa"
	else:
		if not frappe.has_permission(TASK, "create", user=ctx.user):
			raise Denied(_("Bạn không có quyền tạo việc"))
		_count_write(ctx)
		ref_dt, ref_name = ("CRM Deal", deal) if deal else (LEAD, name)
		doc = frappe.get_doc(
			{
				"doctype": TASK,
				"title": title[:140],
				"status": status or "Todo",
				"priority": "Medium",
				"assigned_to": assigned_to or frappe.db.get_value(LEAD, name, "lead_owner") or ctx.user,
				"due_date": due,
				"reference_doctype": ref_dt,
				"reference_docname": ref_name,
			}
		)
		doc.insert()
		verb = "Tạo"
	add_comment(TASK, doc.name, ctx.user, verb)
	frappe.db.commit()
	return {"crm_task": doc.name, "lead": name, "status": doc.status, "due_date": str(doc.due_date or "")}


def create_call_log(
	ctx: Ctx,
	lead: str,
	type: str,
	start_time: str,
	status: str = "Completed",
	duration_minutes: int | None = None,
	note: str | None = None,
) -> dict:
	"""Ghi một cuộc gọi tay vào tab Cuộc gọi của Lead (CRM Call Log, như nút tạo cuộc gọi của CRM); có ghi chú thì kèm FCRM Note."""
	name = _writable(ctx, lead)
	if type not in CALL_TYPES:
		raise ToolError(_("type phải là Incoming (khách gọi đến) hoặc Outgoing (gọi cho khách)"))
	if status not in CALL_STATUSES:
		raise ToolError(_("status phải là một trong: {0}").format(", ".join(CALL_STATUSES)))
	if not start_time:
		raise ToolError(_("Thiếu thời điểm cuộc gọi (start_time)"))
	at = care_log.clamp_contact_time(name, _datetime(start_time, "start_time"))
	minutes = cint(duration_minutes)
	if minutes < 0 or minutes > 600:
		raise ToolError(_("duration_minutes phải trong khoảng 0-600"))
	if not frappe.has_permission(CALL_LOG, "create", user=ctx.user):
		raise Denied(_("Bạn không có quyền tạo cuộc gọi"))
	note_text = (note or "").strip()[:TEXT_LIMIT]
	if note_text and not frappe.has_permission(NOTE, "create", user=ctx.user):
		raise Denied(_("Bạn không có quyền tạo ghi chú"))
	_count_write(ctx)
	deal = lead_bridge.get_deal_for_lead(name)
	ref_dt, ref_name = ("CRM Deal", deal) if deal else (LEAD, name)
	row = frappe.db.get_value(LEAD, name, ["mobile_no", "phone"], as_dict=True) or {}
	customer = row.get("mobile_no") or row.get("phone") or "unknown"
	staff = frappe.db.get_value("User", ctx.user, "mobile_no") or ctx.user
	incoming = type == "Incoming"
	note_name = None
	if note_text:
		note_doc = frappe.get_doc(
			{
				"doctype": NOTE,
				"title": _("Cuộc gọi {0}").format(frappe.utils.format_datetime(at, "dd/MM/yyyy HH:mm")),
				"content": f"<p>{frappe.utils.escape_html(note_text)}</p>",
				"reference_doctype": ref_dt,
				"reference_docname": ref_name,
			}
		)
		note_doc.insert()
		note_name = note_doc.name
	doc = frappe.get_doc(
		{
			"doctype": CALL_LOG,
			"type": type,
			"status": status,
			"from": customer if incoming else staff,
			"to": staff if incoming else customer,
			"receiver": ctx.user if incoming else None,
			"caller": None if incoming else ctx.user,
			"start_time": at,
			"duration": minutes * 60 if minutes else None,
			"telephony_medium": "Manual",
			"note": note_name,
			"reference_doctype": ref_dt,
			"reference_docname": ref_name,
		}
	)
	doc.insert()
	add_comment(CALL_LOG, doc.name, ctx.user)
	frappe.db.commit()
	return {"call_log": doc.name, "note": note_name, "lead": name, "start_time": str(at)}


def _validate_lead_values(fields: Any) -> dict:
	"""Kiểm khóa (đúng 6 trường cho phép) và giá trị; trả dict đã chuẩn hóa. Sai thì ToolError, không ghi gì."""
	if not isinstance(fields, dict) or not fields:
		raise ToolError(_("fields phải là một đối tượng khác rỗng"))
	bad = [k for k in fields if k not in LEAD_UPDATE_FIELDS]
	if bad:
		raise ToolError(
			_("Không được sửa trường {0}. Chỉ được: {1}").format(
				", ".join(map(str, bad)), ", ".join(LEAD_UPDATE_FIELDS)
			)
		)
	out: dict[str, Any] = {}
	for k, v in fields.items():
		value = "" if v is None else str(v).strip()
		if k == "status":
			options = frappe.get_all("CRM Lead Status", pluck="name")
			if value not in options:
				raise ToolError(_("Trạng thái phải là một trong: {0}").format(", ".join(options)))
		elif k == "source":
			if value and not frappe.db.exists("CRM Lead Source", value):
				options = frappe.get_all("CRM Lead Source", pluck="name")
				raise ToolError(_("Nguồn phải là một trong: {0}").format(", ".join(options)))
		elif k == "lead_owner":
			if not (value and value != "Guest" and frappe.db.get_value("User", value, "enabled")):
				raise ToolError(_("lead_owner phải là email của một người dùng đang hoạt động"))
		elif k == "mobile_no":
			if value and not normalize_phone(value):
				raise ToolError(_("Số điện thoại không hợp lệ"))
		elif k == "email":
			if value and not validate_email_address(value):
				raise ToolError(_("Email không hợp lệ"))
		elif k == "organization":
			value = value[:140]
		out[k] = value
	return out


def update_lead_fields(ctx: Ctx, lead: str, fields: dict) -> dict:
	"""Trình đề xuất sửa Lead (CHƯA ghi): tạo Omni Assistant Action chờ duyệt; chỉ `api.approve` mới áp dụng."""
	name = _writable(ctx, lead)
	values = _validate_lead_values(fields)
	_count_write(ctx)
	old = frappe.db.get_value(LEAD, name, list(values), as_dict=True) or {}
	changes = [
		{"field": k, "old": "" if old.get(k) is None else str(old.get(k)), "new": v}
		for k, v in values.items()
		if str(old.get(k) or "") != v
	]
	if not changes:
		raise ToolError(_("Các giá trị đề xuất trùng với giá trị hiện tại, không có gì để sửa"))
	return {"__pending__": True, "lead": name, "changes": changes}


def field_label(fieldname: str) -> str:
	"""Nhãn hiển thị của một trường CRM Lead."""
	return _(frappe.get_meta(LEAD).get_label(fieldname))


def apply_lead_update(user: str, lead: str, values: dict) -> list[dict]:
	"""Áp đề xuất đã duyệt: kiểm lại quyền sửa và giá trị, đọc lại giá trị cũ, lưu bằng quyền người dùng."""
	if not frappe.has_permission(LEAD, "write", doc=lead, user=user):
		raise frappe.PermissionError(_("Bạn không có quyền sửa Lead {0}").format(lead))
	clean = _validate_lead_values(values)
	doc = frappe.get_doc(LEAD, lead)
	changes = []
	for k, v in clean.items():
		before = "" if doc.get(k) is None else str(doc.get(k))
		if before != v:
			changes.append({"field": k, "old": before, "new": v})
			doc.set(k, v or None)
	if changes:
		doc.save()
		add_comment(LEAD, lead, user, "Cập nhật")
	return changes


# --- danh mục tool (hằng số trong code, quy tắc 2) ------------------------------------------------------------


def _schema(props: dict, required: list[str]) -> dict:
	return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


STR = {"type": "string"}
DATETIME = {"type": "string", "description": "YYYY-MM-DD HH:MM:SS, giờ Việt Nam"}
LEAD_ID = {"type": "string", "description": "Mã Lead (vd CRM-LEAD-2026-00003) lấy từ find_lead/search_leads"}


@dataclass(frozen=True)
class Tool:
	"""Một tool: hàm Python, mô tả + schema gửi mô hình, có ghi dữ liệu không, có bọc kết quả như dữ liệu khách không."""

	fn: Callable[..., dict]
	description: str
	schema: dict
	writes: bool = False
	wrap_result: bool = False


TOOLS: dict[str, Tool] = {
	"find_lead": Tool(
		find_lead,
		"Tìm Lead theo tên (không cần dấu), số điện thoại hoặc email. Trả tối đa 5 ứng viên và tổng số khớp.",
		_schema({"query": STR}, ["query"]),
		wrap_result=True,
	),
	"get_lead": Tool(
		get_lead,
		"Xem chi tiết một Lead.",
		_schema({"lead": LEAD_ID}, ["lead"]),
		wrap_result=True,
	),
	"list_care_logs": Tool(
		list_care_logs,
		"Lịch sử chăm sóc của một Lead, mới nhất trước.",
		_schema(
			{"lead": LEAD_ID, "limit": {"type": "integer", "minimum": 1, "maximum": CARE_LOG_LIMIT}}, ["lead"]
		),
		wrap_result=True,
	),
	"search_leads": Tool(
		search_leads,
		"Lọc danh sách Lead theo trạng thái, người phụ trách, nguồn, khoảng ngày tạo. Trả tổng số khớp và tối đa 20 dòng.",
		_schema(
			{
				"status": STR,
				"lead_owner": {"type": "string", "description": "email người phụ trách"},
				"source": {"type": "string", "description": "vd Zalo OA, Email, Điện thoại, Quảng cáo Meta"},
				"created_after": DATETIME,
				"created_before": DATETIME,
				"limit": {"type": "integer", "minimum": 1, "maximum": SEARCH_LIMIT},
			},
			[],
		),
		wrap_result=True,
	),
	"create_care_log": Tool(
		create_care_log,
		"Ghi nhật ký chăm sóc (cuộc gọi, Zalo, email, gặp trực tiếp) có kết quả cho một Lead. Kết quả khác 'Đã chốt đơn'"
		" và 'Không có nhu cầu' bắt buộc có next_action và next_due; khi có, hệ thống tạo luôn CRM Task.",
		_schema(
			{
				"lead": LEAD_ID,
				"channel": {"type": "string", "enum": list(care_log.CHANNELS)},
				"started_at": DATETIME,
				"outcome": {"type": "string", "enum": list(care_log.OUTCOMES)},
				"summary": STR,
				"next_action": STR,
				"next_due": DATETIME,
			},
			["lead", "channel", "started_at", "outcome"],
		),
		writes=True,
	),
	"create_call_log": Tool(
		create_call_log,
		"Ghi một cuộc gọi vào tab Cuộc gọi (Call Log) của Lead trong Frappe CRM. type: Incoming = khách gọi đến, "
		"Outgoing = nhân viên gọi cho khách. note là nội dung trao đổi. Dùng khi nhân viên muốn có bản ghi cuộc gọi; "
		"muốn ghi cả kết quả chăm sóc thì gọi thêm create_care_log (mỗi tool tính một thao tác ghi).",
		_schema(
			{
				"lead": LEAD_ID,
				"type": {"type": "string", "enum": list(CALL_TYPES)},
				"start_time": DATETIME,
				"status": {"type": "string", "enum": list(CALL_STATUSES)},
				"duration_minutes": {"type": "integer", "minimum": 0, "maximum": 600},
				"note": STR,
			},
			["lead", "type", "start_time"],
		),
		writes=True,
	),
	"upsert_task": Tool(
		upsert_task,
		"Tạo việc (CRM Task) cho Lead; truyền task để sửa việc đã có.",
		_schema(
			{
				"lead": LEAD_ID,
				"title": STR,
				"due_date": DATETIME,
				"assigned_to": {"type": "string", "description": "email người dùng đang hoạt động"},
				"status": {"type": "string", "enum": list(TASK_STATUSES)},
				"task": {"type": "string", "description": "mã CRM Task cần sửa"},
			},
			["lead", "title"],
		),
		writes=True,
	),
	"update_lead_fields": Tool(
		update_lead_fields,
		"Đề xuất sửa trường của Lead. KHÔNG ghi ngay: nhân viên phải bấm Duyệt trên giao diện. Chỉ các trường: "
		+ ", ".join(LEAD_UPDATE_FIELDS)
		+ ".",
		_schema(
			{
				"lead": LEAD_ID,
				"fields": {
					"type": "object",
					"properties": {k: STR for k in LEAD_UPDATE_FIELDS},
					"additionalProperties": False,
					"minProperties": 1,
				},
			},
			["lead", "fields"],
		),
		writes=True,
	),
}


def tool_specs() -> list[dict]:
	"""Danh sách tool gửi cho mô hình (8 tool, không có tool xóa)."""
	return [{"name": n, "description": t.description, "input_schema": t.schema} for n, t in TOOLS.items()]


def _target(name: str, result: dict) -> tuple[str, str] | None:
	if name == "create_care_log":
		return (LOG, result["care_log"])
	if name == "upsert_task":
		return (TASK, result["crm_task"])
	if name == "create_call_log":
		return (CALL_LOG, result["call_log"])
	if name == "update_lead_fields":
		return (LEAD, result["lead"])
	return None


def run_tool(ctx: Ctx, name: str, args: Any) -> tuple[str, bool, bool]:
	"""Chạy một tool theo yêu cầu của mô hình; trả (nội dung tool_result, là lỗi?, phải dừng lượt?). Luôn ghi Action."""
	args = args if isinstance(args, dict) else {}
	tool = TOOLS.get(name)
	if tool is None:
		record_action(ctx, str(name)[:140], args, "Error", {"error": "tool không tồn tại"})
		return _("Tool {0} không tồn tại").format(name), True, False
	props = tool.schema["properties"]
	unknown = [k for k in args if k not in props]
	if unknown:
		record_action(ctx, name, args, "Error", {"error": f"tham số lạ: {unknown}"})
		return _("Tham số không hợp lệ: {0}").format(", ".join(unknown)), True, False
	try:
		result = tool.fn(ctx, **args)
	except LimitReached as e:
		frappe.db.rollback()
		record_action(ctx, name, args, "Limit", {"error": str(e)})
		ctx.actions.append({"tool": name, "status": "Limit", "args": args})
		return str(e) + _(". Không thực hiện thao tác này."), True, True
	except (Denied, frappe.PermissionError) as e:
		frappe.db.rollback()
		msg = str(e) or _("Không đủ quyền")
		record_action(ctx, name, args, "Denied", {"error": msg})
		ctx.actions.append({"tool": name, "status": "Denied", "error": msg})
		return _("Bị từ chối: {0}").format(msg), True, False
	except (ToolError, frappe.ValidationError, TypeError) as e:
		frappe.db.rollback()
		msg = frappe.utils.strip_html(str(e))[:500]
		record_action(ctx, name, args, "Error", {"error": msg})
		return _("Lỗi: {0}").format(msg), True, False
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Omni: tool của trợ lý AI lỗi", message=f"{name}: {type(e).__name__}")
		record_action(ctx, name, args, "Error", {"error": type(e).__name__})
		return _("Lỗi hệ thống khi chạy {0}").format(name), True, False
	if result.get("__pending__"):
		changes = [dict(c, label=field_label(c["field"])) for c in result["changes"]]
		action = record_action(
			ctx,
			name,
			args,
			"Pending",
			{"lead": result["lead"], "changes": changes},
			target=(LEAD, result["lead"]),
			requires_approval=True,
		)
		ctx.actions.append(
			{"tool": name, "status": "Pending", "action": action, "lead": result["lead"], "changes": changes}
		)
		return (
			_(
				"Đã trình duyệt, CHƯA áp dụng. Nhân viên cần bấm Duyệt trên giao diện thì mới ghi (mã {0})."
			).format(action),
			False,
			False,
		)
	target = _target(name, result) if tool.writes else None
	action = record_action(ctx, name, args, DONE, result, target=target)
	if tool.writes:
		ctx.actions.append({"tool": name, "status": DONE, "action": action, "result": result})
	content = context.wrap_json(result) if tool.wrap_result else _dump(result)
	return content, False, False
