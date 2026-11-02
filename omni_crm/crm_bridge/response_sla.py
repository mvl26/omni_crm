"""Hạn phản hồi lead quảng cáo (docs/15 mục 7 bước 4): đồng hồ giờ làm việc, nhắc người được giao, báo trưởng nhóm.

"Lead vào" là `received_at` của tin `form_lead` đầu tiên (không phải lúc worker xong - gap-report 15.07/ADR 0001). Hạn = thời
điểm vào + `response_sla_minutes` phút LÀM VIỆC (08:00-17:30 T2-T6, 08:00-12:00 T7). Quá hạn: nhắc người được giao một lần;
quá 2 lần ngưỡng: báo trưởng nhóm một lần. Chỉ nhắc Lead mới (3 ngày), không phải Lead thử nghiệm, chưa có `omni_first_response_at`.
Lead nhập CSV cũ không nhắc vì Lead tạo quá 3 ngày. Không gửi gì cho khách.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import form_lead
from omni_crm.utils.business_hours import add_business_minutes

LEAD = "CRM Lead"
WINDOW_DAYS = 3
SUBJECT_L1 = "Quá hạn phản hồi"
SUBJECT_L2 = "Quá 2 lần hạn phản hồi"


def sla_minutes() -> int:
	"""Ngưỡng phản hồi (phút làm việc) từ Omni Settings, mặc định 15."""
	return int(flags.care_setting("response_sla_minutes"))


def deadline(entered_at: datetime, factor: int = 1) -> datetime:
	"""Hạn phản hồi cho Lead vào lúc `entered_at`: cộng `factor x ngưỡng` phút làm việc."""
	return add_business_minutes(get_datetime(entered_at), sla_minutes() * factor)


def pending_leads(now: datetime | None = None) -> list[dict]:
	"""Lead quảng cáo (chưa thử nghiệm, chưa gộp, còn mở, chưa phản hồi) kèm lúc vào; mới nhất trong WINDOW_DAYS ngày."""
	now = now or now_datetime()
	return frappe.db.sql(
		"""select l.name as lead, l.lead_name, min(m.received_at) as entered_at
		from `tabCRM Lead` l
		join `tabOmni Inbound Message` m on m.lead = l.name and m.event_type = 'form_lead' and m.status != 'Ignored'
		left join `tabCRM Lead Status` s on s.name = l.status
		where l.omni_first_response_at is null and ifnull(l.omni_is_test, 0) = 0 and l.omni_merged_into is null
			and ifnull(l.omni_ad_platform, '') in ('Meta', 'Google', 'Website') and l.creation >= %(since)s
			and ifnull(s.type, 'Open') in ('Open', 'Ongoing', 'On Hold')
		group by l.name""",
		{"since": now - timedelta(days=WINDOW_DAYS)},
		as_dict=True,
	)


def team_leads() -> list[str]:
	"""Người dùng đang bật có vai trò trưởng nhóm (`team_lead_role`), không tính Administrator/Guest."""
	role = flags.care_setting("team_lead_role")
	users = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
	return sorted(
		u
		for u in set(users)
		if u not in ("Administrator", "Guest") and frappe.db.get_value("User", u, "enabled")
	)


def _already_notified(lead: str, user: str, subject_prefix: str) -> bool:
	return bool(
		frappe.db.exists(
			"Notification Log",
			{
				"document_type": LEAD,
				"document_name": lead,
				"for_user": user,
				"subject": ["like", f"{subject_prefix}%"],
			},
		)
	)


def run(now: datetime | None = None) -> dict[str, int]:
	"""Tác vụ mỗi 5 phút: nhắc quá hạn (người được giao), báo trưởng nhóm khi quá 2 lần. Trả {"overdue", "reminded", "escalated"}."""
	now = now or now_datetime()
	counts = {"overdue": 0, "reminded": 0, "escalated": 0}
	if not flags.dashboard_enabled():
		return counts
	leaders = team_leads()
	for row in pending_leads(now):
		entered = get_datetime(row.entered_at)
		if now <= deadline(entered):
			continue
		counts["overdue"] += 1
		owners = form_lead.recipients(row.lead)
		name = row.lead_name or row.lead
		for user in owners or leaders:  # chưa ai phụ trách thì báo thẳng trưởng nhóm
			if not _already_notified(row.lead, user, SUBJECT_L1):
				counts["reminded"] += form_lead.notify_users(
					row.lead,
					[user],
					f"{_(SUBJECT_L1)}: {name}",
					_("Lead quảng cáo chưa được phản hồi trong {0} phút làm việc.").format(sla_minutes()),
				)
		if now > deadline(entered, 2):
			for user in leaders:
				if not _already_notified(row.lead, user, SUBJECT_L2):
					counts["escalated"] += form_lead.notify_users(
						row.lead,
						[user],
						f"{_(SUBJECT_L2)}: {name}",
						_("Lead quảng cáo chưa được phản hồi sau {0} phút làm việc.").format(
							sla_minutes() * 2
						),
					)
	frappe.db.commit()
	return counts
