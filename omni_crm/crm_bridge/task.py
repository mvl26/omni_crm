"""Tạo, gộp và tự đóng CRM Task "Gọi lại khách" cho cuộc gọi nhỡ (docs/07 mục 2.2 bước 5).

Tên trường CRM Task đã xác nhận ở G1: title, status (Backlog/Todo/In Progress/Done/Canceled), priority, assigned_to,
due_date (Datetime), description, reference_doctype/reference_docname. Việc nào do ta tạo thì được nhận biết qua
`Omni Call.callback_task` (không sửa CRM Task, không thêm trường).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.utils.business_hours import add_business_minutes

TASK = "CRM Task"
CALL = "Omni Call"
LEAD = "CRM Lead"
OPEN_STATUSES = ("Backlog", "Todo", "In Progress")
DEFAULT_MINUTES = 30


def _call_time(call) -> object:
	return call.ended_at or call.started_at or now_datetime()


def _owner_for(lead: str, deal: str | None) -> str | None:
	"""Người phụ trách: chủ Deal/Lead đang hoạt động, không có thì `callback_fallback_user`."""
	candidates = []
	if deal:
		candidates.append(frappe.db.get_value("CRM Deal", deal, "deal_owner"))
	candidates.append(frappe.db.get_value(LEAD, lead, "lead_owner"))
	candidates.append(frappe.db.get_single_value("Omni Settings", "callback_fallback_user"))
	for user in candidates:
		if user and frappe.db.get_value("User", user, "enabled"):
			return user
	return None


def _open_task_for_lead(lead: str) -> str | None:
	"""Việc gọi lại còn mở của Lead (do ta tạo, nhận biết qua Omni Call.callback_task)."""
	rows = frappe.db.sql(
		"""select t.name from `tabCRM Task` t
		join `tabOmni Call` c on c.callback_task = t.name
		where c.lead = %s and t.status in ('Backlog', 'Todo', 'In Progress')
		order by t.creation desc limit 1""",
		lead,
	)
	return rows[0][0] if rows else None


def _description(count: int, last_at) -> str:
	text = _("Khách đã gọi nhỡ {0} lần, lần cuối {1}.").format(
		count, get_datetime(last_at).strftime("%d/%m/%Y %H:%M")
	)
	return f"<p>{escape_html(text)}</p>"


def _label(lead: str) -> str:
	row = frappe.db.get_value(LEAD, lead, ["lead_name", "omni_phone_e164"], as_dict=True) or {}
	return row.get("lead_name") or row.get("omni_phone_e164") or lead


def create_callback_task(call) -> str | None:
	"""Tạo việc gọi lại cho cuộc gọi nhỡ `call` (Omni Call hoặc tên); gộp vào việc đang mở của Lead. Không commit.

	Trả tên CRM Task, hoặc None nếu không phải gọi nhỡ đến / chưa có Lead (ẩn số: không biết gọi lại ai).
	Idempotent: cuộc gọi đã có `callback_task` thì trả lại việc đó, không đếm thêm.
	"""
	# Khóa dòng Lead TRƯỚC mọi lần đọc thường: hai cuộc nhỡ của cùng khách xử lý song song không tạo hai việc. Đọc khóa
	# không mở ảnh chụp (REPEATABLE READ), nên sau khi chờ xong các lần đọc bên dưới thấy việc job kia vừa commit; nếu
	# đọc thường trước, ảnh chụp cũ làm job thứ hai tạo việc trùng hoặc TimestampMismatchError khi gộp (D2-09).
	call_name = call if isinstance(call, str) else call.name
	first_lead = frappe.db.get_value(CALL, call_name, "lead", for_update=True)
	if first_lead:
		frappe.db.get_value(LEAD, first_lead, "name", for_update=True)
	doc = frappe.get_doc(CALL, call_name)
	if doc.direction != "Inbound" or doc.status != "Missed" or not doc.lead:
		return None
	if doc.callback_task and frappe.db.exists(TASK, doc.callback_task):
		return doc.callback_task
	lead = lead_bridge.follow_merged(doc.lead) or doc.lead
	if lead != doc.lead:
		frappe.db.get_value(LEAD, lead, "name", for_update=True)  # Lead đã gộp: khóa cả Lead còn sống
	deal = lead_bridge.get_deal_for_lead(lead)
	at = _call_time(doc)
	existing = _open_task_for_lead(lead)
	with flags.omni_bot():
		if existing:
			frappe.db.set_value(CALL, doc.name, "callback_task", existing, update_modified=False)
			count = cint(frappe.db.count(CALL, {"callback_task": existing}))
			task = frappe.get_doc(TASK, existing)
			task.description = _description(count, at)
			task.flags.ignore_permissions = True
			task.save()
			return str(existing)
		minutes = (
			cint(frappe.db.get_single_value("Omni Settings", "missed_call_callback_minutes"))
			or DEFAULT_MINUTES
		)
		task = frappe.get_doc(
			{
				"doctype": TASK,
				"title": _("Gọi lại khách {0}").format(_label(lead)),
				"status": "Todo",
				"priority": "High",
				"assigned_to": _owner_for(lead, deal),
				"due_date": add_business_minutes(get_datetime(at), minutes),
				"description": _description(1, at),
				"reference_doctype": "CRM Deal" if deal else LEAD,
				"reference_docname": deal or lead,
			}
		)
		task.flags.ignore_permissions = True
		task.insert()
		frappe.db.set_value(CALL, doc.name, "callback_task", task.name, update_modified=False)
	return str(task.name)


def close_callback_tasks(lead: str | None, answered_call) -> list[str]:
	"""Đóng việc gọi lại còn mở của Lead khi có cuộc gọi (đến hoặc đi) được nghe máy SAU lần gọi nhỡ cuối của việc.

	`answered_call` là Omni Call đã có trạng thái Answered. Trả danh sách việc đã đóng. Không commit.
	"""
	if not lead or answered_call.status != "Answered":
		return []
	when = answered_call.answered_at or answered_call.ended_at or answered_call.started_at
	if not when:
		return []
	closed: list[str] = []
	rows = frappe.db.sql(
		"""select t.name, max(coalesce(c.ended_at, c.started_at)) as last_missed from `tabCRM Task` t
		join `tabOmni Call` c on c.callback_task = t.name
		where t.status in ('Backlog', 'Todo', 'In Progress')
		and t.name in (select callback_task from `tabOmni Call` where lead = %s and callback_task is not null)
		group by t.name""",
		lead,
		as_dict=True,
	)
	for row in rows:
		if row.last_missed and get_datetime(when) <= get_datetime(row.last_missed):
			continue  # cuộc gọi xảy ra trước (hoặc cùng lúc) lần gọi nhỡ cuối: không phải khách được gọi lại
		with flags.omni_bot():
			task = frappe.get_doc(TASK, row.name)
			task.status = "Done"
			task.flags.ignore_permissions = True
			task.save()
		closed.append(row.name)
	return closed
