"""Chuyển hội thoại cho người (docs/08 mục 6): tạm dừng bot, báo người phụ trách, câu giữ chân.

Một lần chuyển người = một thông báo cho mỗi người phụ trách (Notification Log + realtime). Email chỉ khi
`handoff_notify_channels` có "email" VÀ thông báo hệ thống chưa được mở sau 10 phút (job hẹn giờ). Nội dung
thông báo/email KHÔNG chứa văn bản của khách hay lý do do AI viết (có thể trích lời khách): chỉ tên Lead, kênh và
một nhãn lý do cố định. Lý do đầy đủ chỉ nằm trong nháp và dòng thời gian Lead (cần quyền đọc Lead).

`holding_message` chỉ dựng câu; việc gửi (nhóm đang tự động) do `auto_send.py` (D3-05) làm. Module này không tạo tin ra.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, get_datetime, get_url, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.utils import business_hours

SETTINGS = "Omni Settings"
IDENTITY = "Omni Identity"
DRAFT = "Omni Bot Draft"
CHANNEL = "Omni Channel"
LEAD = "CRM Lead"
DEFAULT_PAUSE_HOURS = 24
EMAIL_AFTER_MINUTES = 10  # docs/08 mục 6: email nếu thông báo hệ thống chưa được mở sau ngần này phút
EMAIL_BATCH = 200
DEFAULT_CHANNELS = ("system",)  # mặc định tạm cho Q8, chờ chủ dự án chốt
REALTIME_EVENT = "omni_handoff"
SUBJECT_MAX = 140
NAME_MAX = 60
SLOT_PLACEHOLDER = "{gio_lam_viec_ke_tiep}"
NO_RECIPIENT = "NO_RECIPIENT"  # dấu trong handoff_logs: chưa có ai để báo, job sẽ thử lại
FLAG_WANTS_HUMAN = "handoff_topic:khach_muon_gap_nguoi"
FLAG_ALWAYS_GROUP = "handoff_topic:nhom_luon_chuyen_nguoi"


def _default_in_hours() -> str:
	return _("Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị sớm nhất có thể.")


def _default_after_hours() -> str:
	return _("Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị vào {gio_lam_viec_ke_tiep}.")


def is_bot_paused(identity: str | None) -> bool:
	"""Khách (Omni Identity) đang bị tạm dừng bot, tức `bot_paused_until` còn ở tương lai."""
	until = frappe.db.get_value(IDENTITY, identity, "bot_paused_until") if identity else None
	return bool(until and get_datetime(until) > now_datetime())


# --- Cấu hình -------------------------------------------------------------------------------------------------


def _setting(fieldname: str) -> Any:
	try:
		return frappe.get_cached_doc(SETTINGS).get(fieldname)
	except Exception:
		return None


def pause_hours() -> int:
	"""Số giờ bot im sau khi chuyển người (mặc định 24 khi chưa cấu hình; 0 = không im)."""
	value = _setting("handoff_pause_hours")
	return DEFAULT_PAUSE_HOURS if value is None else max(0, cint(value))


def notify_channels() -> tuple[str, ...]:
	"""Kênh báo sale (`system`, `email`); trống hoặc không hợp lệ thì chỉ `system`."""
	raw = str(_setting("handoff_notify_channels") or "").replace("\n", ",")
	found = [x for x in dict.fromkeys(p.strip().lower() for p in raw.split(",")) if x in ("system", "email")]
	return tuple(found) or DEFAULT_CHANNELS


def bot_active_now(at: datetime | None = None) -> bool:
	"""Bot được phép hoạt động lúc `at`: theo `bot_active_hours`, để trống thì theo giờ làm việc (Q6, mặc định tạm)."""
	at = at or now_datetime()
	raw = _setting("bot_active_hours")
	if not (raw or "").strip():
		return business_hours.is_business_time(at)
	return business_hours.is_business_time(at, business_hours.parse_hours(raw))


def holding_message(now: datetime | None = None) -> str:
	"""Câu giữ chân cho khách khi chuyển người: mẫu trong giờ hoặc ngoài giờ; chỉ dựng câu, không gửi.

	`{gio_lam_viec_ke_tiep}` được thay bằng mốc mở cửa kế tiếp (ngoài giờ). Mẫu mặc định không hứa giá hay
	thời gian cụ thể nào khác.
	"""
	now = now or now_datetime()
	if business_hours.is_business_time(now):
		template = (_setting("handoff_message_in_hours") or "").strip() or _default_in_hours()
		slot = _("trong giờ làm việc")
	else:
		template = (_setting("handoff_message_after_hours") or "").strip() or _default_after_hours()
		slot = business_hours.next_business_start(now).strftime("%H:%M ngày %d/%m/%Y")
	return template.replace(SLOT_PLACEHOLDER, slot)


# --- Người nhận, nhãn lý do ------------------------------------------------------------------------------------


def recipients(lead: str) -> list[str]:
	"""Người phụ trách Lead (chủ + người được giao) còn hoạt động; không có thì `callback_fallback_user`,
	rồi `assignment_fallback_users`."""
	row = frappe.db.get_value(LEAD, lead, ["lead_owner", "_assign"], as_dict=True) or {}
	users = [row.get("lead_owner")]
	try:
		users += json.loads(row.get("_assign") or "[]")
	except ValueError:
		pass
	active = _enabled([u for u in users if u])
	if active:
		return active
	fallback = _setting("callback_fallback_user")
	active = _enabled([fallback] if fallback else [])
	if active:
		return active
	raw = _setting("assignment_fallback_users") or ""
	return _enabled([u.strip() for u in raw.replace("\n", ",").split(",") if u.strip()])


def _enabled(users: list[str]) -> list[str]:
	users = list(dict.fromkeys(users))
	return (
		frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name") if users else []
	)


def reason_label(guard_flags: str | None, question_group: str | None = None) -> str:
	"""Nhãn lý do cố định để đưa vào thông báo/email. Không dùng lý do tự do do AI viết (có thể chứa lời khách)."""
	flags_ = guard_flags or ""
	if FLAG_WANTS_HUMAN in flags_:
		return _("Khách muốn gặp nhân viên")
	if FLAG_ALWAYS_GROUP in flags_:
		label = _("Nhóm câu hỏi cần nhân viên xử lý")
		return f"{label}: {question_group}" if question_group else label
	return _("Bot đề nghị chuyển người")


def _short_name(lead: str) -> str:
	name = frappe.db.get_value(LEAD, lead, "lead_name") or lead
	return " ".join(str(name).split())[:NAME_MAX]


def _inbox_link(lead: str) -> str:
	return f"/app/omni-inbox?lead={lead}"


# --- Tạm dừng / tiếp tục --------------------------------------------------------------------------------------


def _lead_identities(lead: str) -> list[str]:
	return frappe.get_all(IDENTITY, filters={"lead": lead}, pluck="name")


def pause_lead(lead: str, hours: int | None = None) -> datetime:
	"""Đặt `bot_paused_until` cho mọi định danh của Lead (không rút ngắn thời hạn đang có); trả mốc hết hạn."""
	until = add_to_date(now_datetime(), hours=pause_hours() if hours is None else hours)
	for name in _lead_identities(lead):
		current = frappe.db.get_value(IDENTITY, name, "bot_paused_until")
		if current and get_datetime(current) > until:
			continue
		frappe.db.set_value(IDENTITY, name, "bot_paused_until", until, update_modified=False)
	return until


SALE_PAUSE_HOURS = 24  # V3-07 brief mục C: sale nhắn khách -> bot phía mình im 24 giờ


def pause_identity_for_sale(identity: str) -> datetime:
	"""Sale vừa gửi tin cho khách (CRM hoặc màn hình Zalo OA): tạm dừng bot của định danh 24 giờ, không rút ngắn hạn đang có.

	Trong thời gian này hệ thống không tự gửi gì cho khách (thẻ xin số, nháp bot). Khách nhắn lại không xóa tạm dừng.
	"""
	until = add_to_date(now_datetime(), hours=SALE_PAUSE_HOURS)
	current = frappe.db.get_value(IDENTITY, identity, "bot_paused_until")
	if current and get_datetime(current) >= until:
		return get_datetime(current)
	frappe.db.set_value(IDENTITY, identity, "bot_paused_until", until, update_modified=False)
	return until


def resume_lead(lead: str) -> int:
	"""Xóa `bot_paused_until` của mọi định danh của Lead; trả số định danh đang bị tạm dừng đã mở lại."""
	done = 0
	for name in _lead_identities(lead):
		if frappe.db.get_value(IDENTITY, name, "bot_paused_until"):
			frappe.db.set_value(IDENTITY, name, "bot_paused_until", None, update_modified=False)
			done += 1
	return done


def paused_until(lead: str) -> datetime | None:
	"""Mốc tạm dừng bot muộn nhất còn hiệu lực của Lead, hoặc None."""
	now = now_datetime()
	values = [
		get_datetime(v)
		for v in frappe.get_all(IDENTITY, filters={"lead": lead}, pluck="bot_paused_until")
		if v and get_datetime(v) > now
	]
	return max(values) if values else None


def note_on_lead(lead: str, text: str, user: str | None = None) -> None:
	"""Ghi một dòng vào dòng thời gian Lead (Comment); `user` là người bấm (nếu có). Không commit."""
	who = None
	if user and user not in ("Administrator", "Guest"):
		who = frappe.db.get_value("User", user, "full_name") or user
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": LEAD,
			"reference_name": lead,
			"comment_by": who or "Omni CRM",
			"comment_email": user if who else None,
			"content": f"<p>{escape_html(text)}</p>",
		}
	).insert(ignore_permissions=True)


def manual_pause(lead: str, user: str) -> datetime:
	"""Sale tạm dừng bot với khách của Lead (cấu hình 0 giờ thì dùng 24 giờ); ghi ai bấm vào dòng thời gian."""
	until = pause_lead(lead, pause_hours() or DEFAULT_PAUSE_HOURS)
	note_on_lead(
		lead,
		_("{0} tạm dừng bot với khách này đến {1}").format(
			frappe.db.get_value("User", user, "full_name") or user, frappe.utils.format_datetime(until)
		),
		user,
	)
	return until


def manual_resume(lead: str, user: str) -> int:
	"""Sale cho bot tiếp tục với khách của Lead; ghi ai bấm vào dòng thời gian. Trả số định danh đã mở lại."""
	done = resume_lead(lead)
	if done:
		note_on_lead(
			lead,
			_("{0} cho bot tiếp tục với khách này").format(
				frappe.db.get_value("User", user, "full_name") or user
			),
			user,
		)
	return done


# --- Chuyển người ---------------------------------------------------------------------------------------------


def _notify_system(lead: str, users: list[str], label: str, channel_type: str | None) -> list[str]:
	"""Tạo Notification Log + realtime cho từng người; trả tên các Notification Log đã tạo."""
	subject = _("Bot chuyển người, mời xem: {0}").format(lead)[:SUBJECT_MAX]
	link = _inbox_link(lead)
	body = (
		f"{escape_html(label)} · {escape_html(channel_type or '')} · {escape_html(_short_name(lead))}. "
		f'<a href="{escape_html(link)}">{escape_html(_("Mở Hộp thư"))}</a>'
	)
	names = []
	for user in users:
		try:
			log = frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": body,
					"document_type": LEAD,
					"document_name": lead,
				}
			).insert(ignore_permissions=True)
			names.append(log.name)
			frappe.publish_realtime(REALTIME_EVENT, {"lead": lead}, user=user, after_commit=True)
		except Exception:
			frappe.log_error(title="Omni: không báo được chuyển người", message=f"lead={lead}")
	return names


def _queue_email(lead: str, users: list[str], label: str, channel_type: str | None) -> int:
	"""Xếp email nhắc vào hàng đợi thư của Frappe (gửi thật do scheduler ở ngoài giao dịch này).

	Sandbox: không gửi, chỉ ghi log. Nội dung: tên khách, kênh, nhãn lý do, liên kết; không có lời khách. Trả số thư xếp.
	"""
	if not users:
		return 0
	if flags.sandbox():
		frappe.logger("omni").info(
			"sandbox: bỏ qua email nhắc chuyển người lead=%s nguoi_nhan=%s", lead, len(users)
		)
		return 0
	subject = _("[Omni] Khách cần nhân viên: {0}").format(lead)[:SUBJECT_MAX]
	message = (
		f"<p>{escape_html(_('Bot đã chuyển hội thoại cho nhân viên và thông báo trong hệ thống chưa được mở.'))}</p>"
		f"<p>{escape_html(_('Khách'))}: {escape_html(_short_name(lead))}<br>"
		f"{escape_html(_('Kênh'))}: {escape_html(channel_type or '')}<br>"
		f"{escape_html(_('Lý do'))}: {escape_html(label)}</p>"
		f'<p><a href="{escape_html(get_url(_inbox_link(lead)))}">{escape_html(_("Mở Hộp thư"))}</a></p>'
	)
	sent = 0
	for user in users:
		email = frappe.db.get_value("User", user, "email")
		if not email:
			continue
		try:
			frappe.sendmail(recipients=[email], subject=subject, message=message, delayed=True)
			sent += 1
		except Exception:
			frappe.log_error(title="Omni: không xếp được email chuyển người", message=f"lead={lead}")
	return sent


def handoff(draft: str) -> dict[str, Any]:
	"""Thực hiện chuyển người cho nháp Handoff (Draft): im bot, báo người phụ trách, ghi dòng thời gian.

	Mỗi nháp chỉ chuyển một lần (gọi lại không báo trùng). Shadow không chuyển. Không gửi gì cho khách. Không commit.
	"""
	row = frappe.db.get_value(
		DRAFT,
		draft,
		[
			"lead",
			"channel",
			"mode",
			"action",
			"guard_flags",
			"question_group",
			"handoff_reason",
			"handoff_notified_at",
		],
		as_dict=True,
		for_update=True,
	)
	if not row or row.action != "Handoff" or row.mode != "Draft":
		return {"status": "skipped"}
	if row.handoff_notified_at:
		return {"status": "already"}
	lead = lead_bridge.follow_merged(row.lead) or row.lead
	retry = row.handoff_logs == NO_RECIPIENT
	until = (paused_until(lead) if retry else None) or pause_lead(lead)
	label = reason_label(row.guard_flags, row.question_group)
	channel_type = frappe.db.get_value(CHANNEL, row.channel, "channel_type") if row.channel else None
	users = recipients(lead)
	if not users:
		if not retry:  # ghi một lần; job thử lại không ghi lặp
			frappe.log_error(
				title="Omni: chuyển người nhưng không có người nhận", message=f"lead={lead} draft={draft}"
			)
			note_on_lead(
				lead,
				_(
					"Bot chuyển người nhưng không có người nhận thông báo (Lead chưa có người phụ trách và chưa cấu hình người dự phòng). Bot tạm dừng đến {0}"
				).format(frappe.utils.format_datetime(until)),
			)
			frappe.db.set_value(DRAFT, draft, "handoff_logs", NO_RECIPIENT, update_modified=False)
		return {"status": "no_recipient", "until": until}
	chans = notify_channels()
	logs = _notify_system(lead, users, label, channel_type) if "system" in chans else []
	emailed = 0
	if "email" in chans and "system" not in chans:
		emailed = _queue_email(lead, users, label, channel_type)
	note = _("Bot chuyển người: {0}").format(label)
	if row.handoff_reason:
		note += f". {_('Lý do bot ghi')}: {row.handoff_reason}"
	note_on_lead(lead, note + ". " + _("Bot tạm dừng đến {0}").format(frappe.utils.format_datetime(until)))
	frappe.db.set_value(
		DRAFT,
		draft,
		{
			"handoff_notified_at": now_datetime(),
			"handoff_logs": ",".join(logs),
			"handoff_email_done": 0 if "system" in chans and "email" in chans else 1,
		},
		update_modified=False,
	)
	timeline.notify(lead, [])
	return {"status": "done", "until": until, "notified": len(logs), "emailed": emailed}


def email_unread_handoffs() -> dict[str, int]:
	"""Tác vụ định kỳ: nháp chuyển người đã báo quá 10 phút mà thông báo hệ thống còn chưa mở -> xếp email nhắc.

	Chỉ khi `handoff_notify_channels` có email. Nháp đã được xử lý (không còn Pending) thì không nhắc. Mỗi nháp
	một lần. Không gọi API ngoài: email chỉ vào hàng đợi, sandbox chỉ ghi log. Commit sau từng nháp.
	"""
	retried = _retry_without_recipient()
	cutoff = add_to_date(now_datetime(), minutes=-EMAIL_AFTER_MINUTES)
	rows = frappe.get_all(
		DRAFT,
		filters={
			"mode": "Draft",
			"action": "Handoff",
			"handoff_email_done": 0,
			"handoff_notified_at": ["<=", cutoff],
		},
		fields=["name", "lead", "channel", "decision", "handoff_logs", "guard_flags", "question_group"],
		order_by="handoff_notified_at asc",
		limit_page_length=EMAIL_BATCH,
	)
	out = {"checked": 0, "emailed": 0, "skipped": 0, "retried": retried}
	email_on = "email" in notify_channels()
	for r in rows:
		out["checked"] += 1
		try:
			names = [n for n in (r.handoff_logs or "").split(",") if n]
			unread = []
			if email_on and r.decision == "Pending" and names:
				unread = frappe.get_all(
					"Notification Log", filters={"name": ["in", names], "read": 0}, pluck="for_user"
				)
			if unread:
				channel_type = frappe.db.get_value(CHANNEL, r.channel, "channel_type") if r.channel else None
				out["emailed"] += _queue_email(
					r.lead, _enabled(unread), reason_label(r.guard_flags, r.question_group), channel_type
				)
			else:
				out["skipped"] += 1
			frappe.db.set_value(DRAFT, r.name, "handoff_email_done", 1, update_modified=False)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi email nhắc chuyển người", message=f"draft={r.name}")
	return out


def _retry_without_recipient() -> int:
	"""Nháp Handoff còn Pending mà lúc chuyển người chưa có ai nhận: nay có người thì báo; trả số nháp đã báo."""
	names = frappe.get_all(
		DRAFT,
		filters={
			"mode": "Draft",
			"action": "Handoff",
			"decision": "Pending",
			"handoff_logs": NO_RECIPIENT,
			"handoff_notified_at": ["is", "not set"],
		},
		fields=["name", "lead"],
		limit_page_length=EMAIL_BATCH,
	)
	done = 0
	for r in names:
		try:
			if (
				recipients(lead_bridge.follow_merged(r.lead) or r.lead)
				and handoff(r.name)["status"] == "done"
			):
				done += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi thử lại báo chuyển người", message=f"draft={r.name}")
	return done
