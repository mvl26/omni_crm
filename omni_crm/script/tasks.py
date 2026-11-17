"""Giao việc cho nhân viên từ bot kịch bản v3 (kịch bản mục 1.4, 3.5, C.2): người nhận theo dịch vụ, CRM Task, tóm tắt.

Người nhận: bảng `Omni Settings.script_recipients` (Omni Service Recipient) → người phụ trách Lead → không có ai thì việc
không gán + cảnh báo (`monitoring.alerts`) + Error Log. Không có người nhận mặc định cố định (người dùng đã chốt 17/11).

Tên trường CRM Task đã xác nhận trong mã nguồn đang cài (`apps/crm/crm/fcrm/doctype/crm_task`): title (bắt buộc),
priority (Low/Medium/High), assigned_to (Link User), status (Backlog/Todo/In Progress/Done/Canceled), due_date
(Datetime), description (Text Editor), reference_doctype/reference_docname. `after_insert` của CRM Task tự giao ToDo.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.script import timeutil, validate

TASK = "CRM Task"
LEAD = "CRM Lead"
IDENTITY = "Omni Identity"
INBOUND = "Omni Inbound Message"
CHANNEL = "Omni Channel"
SETTINGS = "Omni Settings"
RECIPIENT_ROW = "Omni Service Recipient"
RECIPIENT_FIELD = "script_recipients"
SERVICES = ("VATTU", "SUPPLYCORE", "SPD", "KHAC", "DEMO_SUPPLYCORE", "DEMO_SPD", "TUAN_THU", "QUAN_LY")
SERVICE_ALIASES = {"PHANMEM": "SUPPLYCORE"}  # hậu tố mã ô kịch bản ↔ Select của giao ước
OTHER = "KHAC"
OPEN_STATUSES = ("Backlog", "Todo", "In Progress")
PRIORITIES = ("Low", "Medium", "High")
TITLE_MAX = 140
LAST_MESSAGE_MAX = 300
ALERT_KIND = "việc kịch bản không có người nhận"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<![\w@])\+?\d(?:[\s.\-()]{0,2}\d){7,13}(?!\w)")


def service_label(service: str | None) -> str:
	"""Nhãn tiếng Việt của mã dịch vụ (cho nhân viên đọc trong việc và thông báo)."""
	labels = {
		"VATTU": _("Cung ứng vật tư y tế"),
		"SUPPLYCORE": _("Phần mềm SupplyCore"),
		"SPD": _("Hệ thống SPD"),
		"KHAC": _("Khác"),
		"DEMO_SUPPLYCORE": _("Demo SupplyCore"),
		"DEMO_SPD": _("Demo SPD"),
		"TUAN_THU": _("Tuân thủ dữ liệu"),
		"QUAN_LY": _("Quản lý"),
	}
	return labels.get(normalize_service(service), _("Chưa chọn dịch vụ"))


def normalize_service(service: str | None) -> str:
	"""Mã dịch vụ chuẩn của bảng người nhận (`PHANMEM` → `SUPPLYCORE`); không hợp lệ trả chuỗi rỗng."""
	code = (service or "").strip().upper()
	code = SERVICE_ALIASES.get(code, code)
	return code if code in SERVICES else ""


def lead_services(lead: str | None) -> list[str]:
	"""Các mã dịch vụ khách quan tâm trên Lead (`omni_services`), theo thứ tự ghi nhận."""
	raw = frappe.db.get_value(LEAD, lead, "omni_services") if lead else None
	return [s for s in (normalize_service(x) for x in (raw or "").split(",")) if s]


def _enabled(user: str | None) -> str | None:
	return user if user and cint(frappe.db.get_value("User", user, "enabled")) else None


def pick_recipient(rows: list[dict], service: str, role: str = "primary") -> str | None:
	"""Hàm thuần: người nhận của `service` trong các dòng bảng (dòng đầu khớp); `primary` vắng thì lấy người thay."""
	for row in rows:
		if (row.get("service") or "").strip().upper() != service:
			continue
		primary, backup = row.get("primary_user"), row.get("backup_user")
		return (backup or None) if role == "backup" else (primary or backup or None)
	return None


def _recipient_rows() -> list[dict]:
	"""Các dòng bảng người nhận còn dùng được (người dùng bị khóa coi như trống), đọc thẳng DB."""
	rows = frappe.get_all(
		RECIPIENT_ROW,
		filters={"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": RECIPIENT_FIELD},
		fields=["service", "primary_user", "backup_user"],
		order_by="idx asc",
	)
	return [
		{
			"service": r.service,
			"primary_user": _enabled(r.primary_user),
			"backup_user": _enabled(r.backup_user),
		}
		for r in rows
	]


def recipient(service: str | None, role: str = "primary") -> str | None:
	"""Người nhận của dịch vụ theo bảng `script_recipients` (`role`: primary | backup); bảng trống hoặc không có dòng → None."""
	code = normalize_service(service)
	if not code:
		return None
	try:
		return pick_recipient(_recipient_rows(), code, role)
	except Exception:
		frappe.log_error(title="Omni: không đọc được bảng người nhận kịch bản", message=f"service={code}")
		return None


def lead_assignee(lead: str | None) -> str | None:
	"""Người phụ trách Lead còn hoạt động: `lead_owner`, rồi người được giao (`_assign`); không có → None."""
	if not lead:
		return None
	row = frappe.db.get_value(LEAD, lead, ["lead_owner", "_assign"], as_dict=True) or {}
	users = [row.get("lead_owner")]
	try:
		users += json.loads(row.get("_assign") or "[]")
	except ValueError:
		pass
	for user in users:
		if _enabled(user):
			return user
	return None


def assignee_for(lead: str | None, service: str | None) -> str | None:
	"""Người nhận việc: bảng người nhận của dịch vụ → người phụ trách Lead → None."""
	return recipient(service) or lead_assignee(lead)


@contextmanager
def as_admin() -> Iterator[None]:
	"""Job do webhook đẩy chạy bằng Guest; giao việc (assign_to) cần người dùng thật (ADR 0006)."""
	previous = frappe.session.user
	if previous != "Administrator":
		frappe.set_user("Administrator")
	try:
		yield
	finally:
		if previous != "Administrator":
			frappe.set_user(previous)


def _html(text: str) -> str:
	return "".join(f"<p>{escape_html(line)}</p>" for line in (text or "").splitlines() if line.strip())


def open_task(lead: str, title: str) -> str | None:
	"""Việc còn mở cùng tiêu đề trên Lead (để không tạo trùng khi khách bấm lại)."""
	name = frappe.db.get_value(
		TASK,
		{
			"reference_doctype": LEAD,
			"reference_docname": lead,
			"title": title,
			"status": ["in", list(OPEN_STATUSES)],
		},
		"name",
	)
	return str(name) if name else None


def create_task(
	lead: str | None,
	service: str | None,
	title: str,
	description: str,
	due: datetime | None = None,
	priority: str = "Medium",
) -> str | None:
	"""Tạo CRM Task gắn Lead, giao theo bảng người nhận → người phụ trách Lead → không gán (cảnh báo). Không commit.

	Hạn mặc định theo 3.5 (`timeutil.task_due`). Lead đã có việc mở cùng tiêu đề thì trả việc đó. Lỗi → None (đã ghi log).
	"""
	if not lead or not frappe.db.exists(LEAD, lead):
		return None
	lead = lead_bridge.follow_merged(lead) or lead
	title = " ".join((title or "").split())[:TITLE_MAX] or _("Việc từ bot kịch bản")
	existing = open_task(lead, title)
	if existing:
		return existing
	code = normalize_service(service)
	user = assignee_for(lead, code)
	try:
		with flags.omni_bot(), as_admin():
			task = frappe.get_doc(
				{
					"doctype": TASK,
					"title": title,
					"status": "Todo",
					"priority": priority if priority in PRIORITIES else "Medium",
					"assigned_to": user,
					"due_date": due or timeutil.task_due(now_datetime()),
					"description": _html(description),
					"reference_doctype": LEAD,
					"reference_docname": lead,
				}
			)
			task.flags.ignore_permissions = True
			task.insert()
	except Exception:
		frappe.log_error(title="Omni: không tạo được việc kịch bản", message=f"lead={lead} service={code}")
		return None
	if not user:
		_warn_unassigned(lead, code, str(task.name))
	return str(task.name)


def _warn_unassigned(lead: str, service: str, task: str) -> None:
	"""Việc không có người nhận: ghi Error Log và báo Omni Admin qua cảnh báo giám sát (chống lặp 30 phút)."""
	detail = f"lead={lead} service={service or '-'} task={task}"
	frappe.log_error(title="Omni: việc kịch bản không có người nhận", message=detail)
	try:
		from omni_crm.monitoring import alerts

		alerts.deliver_now(
			alerts.Alert(
				kind=ALERT_KIND,
				scope=service or "-",
				subject=_("Việc từ bot kịch bản chưa có người nhận"),
				detail=_(
					"Bảng người nhận (Omni Settings) chưa có dòng cho dịch vụ {0} và Lead {1} chưa có người phụ trách. Việc {2} đang để trống người nhận."
				).format(service or "-", lead, task),
				email=False,
			)
		)
	except Exception:
		frappe.log_error(title="Omni: không gửi được cảnh báo việc không người nhận", message=detail)


def append_note(task: str | None, text: str) -> None:
	"""Thêm một dòng vào mô tả của việc (vd khách chọn gọi điện, SĐT gọi lại). Không commit; lỗi chỉ ghi log."""
	if not task or not frappe.db.exists(TASK, task):
		return
	try:
		current = frappe.db.get_value(TASK, task, "description") or ""
		frappe.db.set_value(TASK, task, "description", current + _html(text))
	except Exception:
		frappe.log_error(title="Omni: không cập nhật được việc kịch bản", message=f"task={task}")


# --- Tóm tắt 5 dòng (C.2) ---------------------------------------------------------------------------------------


def mask_pii(text: str | None) -> str:
	"""Che email và số điện thoại trong lời khách (giữ đầu/cuối) trước khi đưa vào tóm tắt, thông báo."""
	out = _EMAIL_RE.sub(lambda m: validate.mask_email(m.group(0)), text or "")
	return _PHONE_RE.sub(lambda m: validate.mask_phone(m.group(0)), out)


def last_customer_message(identity: str | None) -> str:
	"""Tin chữ gần nhất của khách (đã che email/SĐT, cắt ngắn); không có → chuỗi rỗng. Nội dung khách là dữ liệu."""
	if not identity:
		return ""
	rows = frappe.get_all(
		INBOUND,
		filters={"identity": identity, "event_type": "message", "text": ["is", "set"]},
		fields=["text"],
		order_by="received_at desc, creation desc",
		limit_page_length=5,
	)
	for r in rows:
		text = " ".join((r.text or "").split())
		if text and not text.startswith("#"):  # bỏ mã nút khách bấm
			return mask_pii(text)[:LAST_MESSAGE_MAX]
	return ""


def chat_deadline(identity: str | None) -> dict | None:
	"""Hạn nhắn miễn phí của định danh: {channel_type, deadline, open}; Zalo = `last_inbound_at` + `zalo_cs_free_hours`."""
	if not identity:
		return None
	row = frappe.db.get_value(IDENTITY, identity, ["channel_type", "last_inbound_at"], as_dict=True)
	if not row:
		return None
	deadline = None
	if row.channel_type == "Zalo OA" and row.last_inbound_at:
		channel = frappe.db.get_value(
			INBOUND, {"identity": identity}, "channel", order_by="received_at desc"
		) or frappe.db.get_value(CHANNEL, {"channel_type": "Zalo OA", "enabled": 1}, "name")
		hours = cint(frappe.db.get_value(CHANNEL, channel, "zalo_cs_free_hours")) if channel else 0
		if hours:
			deadline = get_datetime(row.last_inbound_at) + timedelta(hours=hours)
	return {
		"identity": identity,
		"channel_type": row.channel_type,
		"deadline": deadline,
		"open": bool(deadline and deadline > now_datetime()),
	}


def summary(lead: str | None, identity: str | None) -> str:
	"""Tóm tắt 5 dòng cho nhân viên (C.2): khách là ai, dịch vụ, nhu cầu, tin cuối của khách (đã che), kênh + hạn chat."""
	row = (
		frappe.db.get_value(
			LEAD,
			lead,
			["lead_name", "organization", "omni_journey_need", "omni_journey_status"],
			as_dict=True,
		)
		if lead
		else None
	) or {}
	who = " ".join(str(row.get("lead_name") or "").split()) or _("Chưa rõ tên")
	if row.get("organization"):
		who += f" ({row.get('organization')})"
	services = ", ".join(service_label(s) for s in lead_services(lead)) or _("Chưa chọn dịch vụ")
	need = " · ".join(x for x in (row.get("omni_journey_need"), row.get("omni_journey_status")) if x)
	window = chat_deadline(identity) or {}
	channel = window.get("channel_type") or _("Chưa rõ kênh")
	if window.get("deadline"):
		channel += " · " + _("hạn nhắn miễn phí đến {0}").format(
			window["deadline"].strftime("%H:%M %d/%m/%Y")
		)
	return "\n".join(
		(
			_("Khách: {0}").format(who),
			_("Dịch vụ quan tâm: {0}").format(services),
			_("Nhu cầu: {0}").format(need or _("Chưa ghi nhận")),
			_("Tin cuối của khách: {0}").format(last_customer_message(identity) or _("(không có tin chữ)")),
			_("Kênh: {0}").format(channel),
		)
	)
