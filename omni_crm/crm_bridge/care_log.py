"""Nhật ký phiên chăm sóc `Omni Care Log` (docs/13 mục 3.2, V2-04).

Một dòng = một phiên chăm sóc: chuỗi trao đổi liên tục với khách trên MỘT kênh, kết thúc khi im lặng quá
`care_session_gap_hours` (mặc định 4 giờ). Mỗi cuộc gọi là một phiên riêng.

- `touch_session`: worker/gửi tin gọi sau mỗi lượt xử lý để tạo hoặc kéo dài phiên. Không commit. Không gọi cho
  follow/unfollow, tin `System` (câu giữ chân, thư tự động), tin sandbox. Công tắc `care_log_enabled` tắt thì không làm gì.
- `save_outcome`: sale ghi kết quả; kiểm quy tắc bắt buộc, tạo/cập nhật đúng một CRM Task, ghi `omni_first_response_at`.
- Phiên TỰ TẠO từ tin khách KHÔNG tính là phản hồi (nếu tính, mọi Lead đều "phản hồi" ngay khi khách nhắn). Chỉ nhật ký
  do sale GHI (`save_outcome` / `create_manual_log`) mới gọi `first_response.mark` (docs/15 mục 7 bước 4).
- Nhãn "Chưa ghi kết quả": phiên `handled_kind = Sale`, chưa có `outcome`, im lặng từ `UNRECORDED_HOURS` giờ trở lên.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import first_response
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import task as task_bridge

LOG = "Omni Care Log"
LEAD = "CRM Lead"
TASK = "CRM Task"
SUMMARY_LEN = 200
UNRECORDED_HOURS = 2  # docs/13 mục 3.2 quy tắc 2
CHANNELS = ("Điện thoại", "Zalo OA", "Email", "Gặp trực tiếp", "Khác")
CHANNEL_LABELS = {"Telephony": "Điện thoại", "Zalo OA": "Zalo OA", "Email": "Email"}
OUTCOMES = (
	"Đã tư vấn",
	"Đã gửi tài liệu",
	"Đã gửi báo giá",
	"Hẹn gọi lại",
	"Chờ khách phản hồi",
	"Đã chốt đơn",
	"Không có nhu cầu",
	"Không liên lạc được",
	"Đã xử lý khiếu nại",
)
NO_NEXT_OUTCOMES = ("Đã chốt đơn", "Không có nhu cầu")  # không bắt buộc bước tiếp theo
NOT_CONTACTED_OUTCOME = "Không liên lạc được"  # chưa gặp được khách: không đẩy giai đoạn "Đã liên hệ"
INBOUND_EVENTS = ("message", "user_info")  # tin khách; oa_echo là sale trả lời ngoài hệ thống
KIND_RANK = {"Hệ thống": 0, "Khách": 1, "Bot": 2, "Sale": 3}
FIRST_STAGE = "Tiềm năng"
CONTACTED_STAGE = "Đã liên hệ"
OPEN_TASK_STATUSES = ("Backlog", "Todo", "In Progress")


def channel_label(channel_type: str | None) -> str:
	"""Nhãn kênh của Omni Care Log từ `channel_type` của Omni (Telephony -> Điện thoại)."""
	return CHANNEL_LABELS.get(channel_type or "", "Khác")


def _gap() -> timedelta:
	return timedelta(hours=cint(flags.care_setting("care_session_gap_hours")))


def _summary(text: str | None) -> str:
	return " ".join((text or "").split())[:SUMMARY_LEN]


def touch_session(
	lead: str | None,
	channel: str,
	at: datetime | str | None,
	kind: str,
	user: str | None = None,
	call: str | None = None,
	text: str | None = None,
) -> str | None:
	"""Tạo phiên chăm sóc mới hoặc kéo dài phiên cùng kênh còn "nóng"; trả tên phiên (None nếu tắt/không có Lead). Không commit.

	`kind` là ai vừa hành động: "Khách" | "Sale" | "Bot" | "Hệ thống". `call` (tên Omni Call) luôn là phiên riêng.
	`text` là nội dung tin của khách, dùng làm `summary` nếu phiên chưa có tóm tắt.
	"""
	if not lead or not flags.care_log_enabled():
		return None
	lead = lead_bridge.follow_merged(lead) or lead
	if not frappe.db.exists(LEAD, lead):
		return None
	channel = channel if channel in CHANNELS else channel_label(channel)
	kind = kind if kind in KIND_RANK else "Hệ thống"
	at = get_datetime(at) if at else now_datetime()
	# Khóa dòng Lead trước khi tìm/tạo: worker khóa theo định danh, hai kênh của cùng Lead có thể chạy song song.
	frappe.db.get_value(LEAD, lead, "name", for_update=True)
	if call:
		existing = frappe.db.get_value(LOG, {"call": call}, "name")
		if existing:
			return _extend(existing, at, kind, user, text)
		return _create(lead, channel, at, kind, user, text, call=call)
	row = frappe.db.sql(
		f"""select name from `tab{LOG}`
		where lead = %s and channel = %s and ifnull(`call`, '') = '' and last_activity_at >= %s
		order by last_activity_at desc limit 1""",
		(lead, channel, at - _gap()),
	)
	if row:
		return _extend(row[0][0], at, kind, user, text)
	return _create(lead, channel, at, kind, user, text)


def _create(
	lead: str,
	channel: str,
	at: datetime,
	kind: str,
	user: str | None,
	text: str | None,
	call: str | None = None,
	auto: bool = True,
) -> str:
	doc = frappe.get_doc(
		{
			"doctype": LOG,
			"lead": lead,
			"channel": channel,
			"started_at": at,
			"last_activity_at": at,
			"handled_kind": kind,
			"handled_by": user if kind == "Sale" else None,
			"summary": _summary(text) if kind == "Khách" else None,
			"auto_created": 1 if auto else 0,
			"call": call,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return str(doc.name)


def _extend(name: str, at: datetime, kind: str, user: str | None, text: str | None) -> str:
	row = frappe.db.get_value(
		LOG,
		name,
		["started_at", "last_activity_at", "handled_kind", "handled_by", "summary"],
		as_dict=True,
	)
	values: dict[str, Any] = {}
	if not row.last_activity_at or at > get_datetime(row.last_activity_at):
		values["last_activity_at"] = at
	if not row.started_at or at < get_datetime(row.started_at):
		values["started_at"] = at
	if KIND_RANK[kind] > KIND_RANK.get(row.handled_kind or "Hệ thống", 0):
		values["handled_kind"] = kind  # Khách -> Bot -> Sale: người can thiệp cao nhất chịu trách nhiệm
	if kind == "Sale" and user and not row.handled_by:
		values["handled_by"] = user
	if kind == "Khách" and text and not row.summary:
		values["summary"] = _summary(text)
	if values:
		frappe.db.set_value(LOG, name, values)
	return name


# --- điểm nối: gọi từ worker, gửi tin, cuộc gọi (lỗi chỉ ghi log, không làm hỏng việc chính) -----------------


def _safe(fn: Any, *args: Any, **kwargs: Any) -> None:
	try:
		fn(*args, **kwargs)
	except Exception:
		frappe.log_error(title="Omni: lỗi ghi nhật ký chăm sóc", message=frappe.get_traceback()[-800:])


def on_inbound_batch(lead: str | None, msgs: list[Any]) -> None:
	"""Sau mỗi lô tin vào đã Done: mỗi tin khách (message/user_info) và tin sale trả lời ngoài hệ thống (oa_echo) chạm phiên.

	Bỏ qua follow/unfollow, tin Ignored, sự kiện cuộc gọi (phiên riêng, xem `on_call_recorded`). Không commit.
	"""
	if not lead or not flags.care_log_enabled():
		return
	for m in sorted(msgs, key=lambda x: get_datetime(x.received_at) if x.received_at else datetime.min):
		if m.get("status") == "Ignored" or m.channel_type == "Telephony":
			continue
		if m.event_type in INBOUND_EVENTS:
			_safe(
				touch_session, lead, channel_label(m.channel_type), m.received_at, "Khách", None, None, m.text
			)
		elif m.event_type == "oa_echo":
			_safe(touch_session, lead, channel_label(m.channel_type), m.received_at, "Sale")


def on_outbound_sent(row: Any, sent_at: Any, sandbox: bool) -> None:
	"""Sau khi tin ra chuyển `Sent`: tin Sale/Bot thật (không sandbox, không System) chạm phiên. Không commit."""
	if sandbox or not row.get("lead") or row.get("source") not in ("Sale", "Bot"):
		return
	channel = row.get("channel")
	channel_type = frappe.db.get_value("Omni Channel", channel, "channel_type") if channel else None
	kind = "Sale" if row.get("source") == "Sale" else "Bot"
	_safe(touch_session, row.get("lead"), channel_label(channel_type), sent_at, kind, row.get("sent_by"))


def on_call_recorded(call: Any, lead: str | None) -> None:
	"""Cuộc gọi kết thúc ghi vào Omni Call: luôn là phiên riêng (Điện thoại). Không commit."""
	if not lead:
		return
	answered_out = call.direction == "Outbound" or call.status == "Answered"
	kind = "Sale" if answered_out and call.agent else "Khách"
	at = call.answered_at or call.started_at or call.ended_at
	_safe(touch_session, lead, "Điện thoại", at, kind, call.agent, call.name)


def delete_for_lead(doc: Any, method: str | None = None) -> None:
	"""Hook `CRM Lead.on_trash`: nhật ký chăm sóc là dữ liệu của Lead (docs/13 mục 3.2) nên đi theo khi Lead bị xóa."""
	frappe.db.delete(LOG, {"lead": doc.name})


# --- ghi kết quả -----------------------------------------------------------------------------------------------


def _parse_due(value: Any) -> datetime | None:
	if not value:
		return None
	try:
		return get_datetime(value)
	except Exception:
		frappe.throw(_("Hạn không đúng định dạng ngày giờ"))


def validate_outcome(outcome: str | None, next_action: str | None, next_due: Any) -> datetime | None:
	"""Kiểm quy tắc docs/13 mục 3.2: kết quả hợp lệ; bước tiếp theo + hạn bắt buộc trừ "Đã chốt đơn"/"Không có nhu cầu"."""
	if outcome not in OUTCOMES:
		frappe.throw(_("Chọn một kết quả chăm sóc hợp lệ"))
	due = _parse_due(next_due)
	action = (next_action or "").strip()
	if outcome not in NO_NEXT_OUTCOMES and (not action or not due):
		frappe.throw(_('Kết quả "{0}" cần nhập bước tiếp theo và hạn').format(outcome))
	if due and not action:
		frappe.throw(_("Có hạn thì cần nhập bước tiếp theo"))
	return due


def _task_target(lead: str) -> tuple[str, str]:
	deal = lead_bridge.get_deal_for_lead(lead)
	return ("CRM Deal", deal) if deal else (LEAD, lead)


def _sync_task(log: Any, lead: str, action: str, due: datetime, user: str) -> str:
	"""Tạo CRM Task giao người phụ trách Lead; log đã có việc còn mở thì cập nhật (không tạo việc thứ hai)."""
	title = action[:140]
	if log.crm_task and frappe.db.get_value(TASK, log.crm_task, "status") in OPEN_TASK_STATUSES:
		task = frappe.get_doc(TASK, log.crm_task)
		task.title = title
		task.due_date = due
		task.flags.ignore_permissions = True
		task.save()
		return str(task.name)
	ref_dt, ref_name = _task_target(lead)
	owner = task_bridge._owner_for(lead, ref_name if ref_dt == "CRM Deal" else None) or user
	task = frappe.get_doc(
		{
			"doctype": TASK,
			"title": title,
			"status": "Todo",
			"priority": "Medium",
			"assigned_to": owner,
			"due_date": due,
			"reference_doctype": ref_dt,
			"reference_docname": ref_name,
		}
	)
	task.flags.ignore_permissions = True
	task.insert()
	return str(task.name)


def _advance_stage(lead: str) -> bool:
	"""Lead đang ở "Tiềm năng" thì chuyển "Đã liên hệ" (docs/14 mục 4); chỉ tiến, không lùi."""
	if frappe.db.get_value(LEAD, lead, "status") != FIRST_STAGE:
		return False
	if not frappe.db.exists("CRM Lead Status", CONTACTED_STAGE):
		return False
	doc = frappe.get_doc(LEAD, lead)
	doc.status = CONTACTED_STAGE
	doc.flags.ignore_permissions = True
	with flags.omni_bot():
		doc.save()
	return True


def clamp_contact_time(lead: str, at: Any) -> datetime | None:
	"""Kẹp thời điểm tiếp xúc `at` vào [thời điểm tạo Lead, bây giờ]; None/rỗng giữ None (dùng "bây giờ")."""
	if not at:
		return None
	lead = lead_bridge.follow_merged(lead) or lead
	when = get_datetime(at)
	now = now_datetime()
	created = frappe.db.get_value("CRM Lead", lead, "creation")
	if created and when < get_datetime(created):
		return get_datetime(created)
	return min(when, now)


def save_outcome(
	log: str,
	outcome: str,
	next_action: str | None = None,
	next_due: Any = None,
	create_task: bool = True,
	summary: str | None = None,
	user: str | None = None,
	at: Any = None,
) -> dict[str, Any]:
	"""Ghi kết quả của một phiên. Không kiểm quyền (API kiểm), không commit.

	Trả {"log", "task", "suggest_deal", "stage_changed"}. `at` là thời điểm tiếp xúc (mặc định bây giờ), dùng cho
	`omni_first_response_at`. Nhật ký do sale ghi là một phản hồi (docs/15 mục 7 bước 4).
	"""
	user = user or frappe.session.user
	due = validate_outcome(outcome, next_action, next_due)
	doc = frappe.get_doc(LOG, log)
	lead = lead_bridge.follow_merged(doc.lead) or doc.lead
	action = (next_action or "").strip()
	now = now_datetime()
	doc.outcome = outcome
	doc.next_action = action or None
	doc.next_due = due
	doc.outcome_by = user
	doc.outcome_at = now
	if summary is not None:
		doc.summary = summary.strip()[:2000]
	task_name = doc.crm_task
	if cint(create_task) and action and due:
		task_name = _sync_task(doc, lead, action, due, user)
		doc.crm_task = task_name
	doc.flags.ignore_permissions = True
	doc.save()
	first_response.mark(lead, get_datetime(at) if at else now)
	changed = False
	if outcome != NOT_CONTACTED_OUTCOME:
		changed = _advance_stage(lead)
	return {
		"log": doc.name,
		"task": task_name,
		"suggest_deal": outcome == "Đã chốt đơn" and not lead_bridge.get_deal_for_lead(lead),
		"stage_changed": changed,
	}


def create_manual_log(lead: str, channel: str, at: Any, summary: str | None, user: str | None = None) -> str:
	"""Tạo phiên do sale ghi tay (không `auto_created`); chưa có kết quả. Không kiểm quyền, không commit."""
	if channel not in CHANNELS:
		frappe.throw(_("Chọn kênh hợp lệ"))
	lead = lead_bridge.follow_merged(lead) or lead
	when = get_datetime(at) if at else now_datetime()
	user = user or frappe.session.user
	name = _create(lead, channel, when, "Sale", user, None, auto=False)
	if summary:
		frappe.db.set_value(LOG, name, "summary", summary.strip()[:2000])
	return name


# --- "Chưa ghi kết quả" ---------------------------------------------------------------------------------------


def unrecorded_cutoff() -> datetime:
	"""Phiên im lặng từ thời điểm này trở về trước và chưa có kết quả thì bị gắn nhãn "Chưa ghi kết quả"."""
	return now_datetime() - timedelta(hours=UNRECORDED_HOURS)


def unrecorded_by_lead(leads: list[str]) -> dict[str, str]:
	"""{lead: phiên mới nhất chưa ghi kết quả} cho các Lead trong danh sách (một truy vấn)."""
	if not leads:
		return {}
	rows = frappe.db.sql(
		f"""select lead, name from `tab{LOG}`
		where lead in %s and handled_kind = 'Sale' and ifnull(outcome, '') = '' and last_activity_at <= %s
		order by last_activity_at asc""",
		(tuple(leads), unrecorded_cutoff()),
	)
	return {lead: name for lead, name in rows}  # phiên mới nhất ghi đè phiên cũ


def end_of_day_digest() -> dict[str, int]:
	"""17:45 giờ làm việc: một thông báo tổng hợp cho mỗi sale có phiên "Chưa ghi kết quả" (không báo từng phiên).

	Chạy ngày nghỉ/lễ thì bỏ qua. Công tắc `care_log_enabled` tắt thì không làm gì. Trả {user: số phiên}.
	"""
	from omni_crm.utils.business_hours import is_working_day

	if not flags.care_log_enabled() or not is_working_day(now_datetime().date()):
		return {}
	rows = frappe.db.sql(
		f"""select handled_by, count(*) from `tab{LOG}`
		where handled_kind = 'Sale' and ifnull(handled_by, '') != '' and ifnull(outcome, '') = ''
			and last_activity_at <= %s and last_activity_at >= %s
		group by handled_by""",
		(unrecorded_cutoff(), now_datetime() - timedelta(days=7)),
	)
	sent: dict[str, int] = {}
	for user, count in rows:
		if not frappe.db.get_value("User", user, "enabled"):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": _("Còn {0} phiên chăm sóc chưa ghi kết quả").format(count),
					"email_content": _(
						"Mở Chi tiết khách để ghi kết quả và bước tiếp theo cho các phiên này."
					),
				}
			).insert(ignore_permissions=True)
			sent[user] = count
		except Exception:
			frappe.log_error(title="Omni: không báo được phiên chưa ghi kết quả", message=f"user={user}")
	return sent  # tác vụ định kỳ: scheduler commit sau khi hàm kết thúc
