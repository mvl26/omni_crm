"""API trang Chi tiết khách `omni-customer/<lead>` (docs/13 mục 3, 3b; docs/14 mục 8).

Mọi hàm kiểm quyền theo Lead (`frappe.has_permission("CRM Lead", ...)`), không chỉ theo DocType. Khách là nhà thầu
(Lead có `organization` khớp CRM Organization) thì trang là "Chi tiết nhà thầu". Dữ liệu cá nhân hiển thị tối thiểu:
Contact từ chối nhận liên hệ hoặc đã bị xóa dữ liệu không có SĐT/email và không có nút gọi/nhắn.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.api import inbox
from omni_crm.bot import scoring
from omni_crm.crm_bridge import care_log, timeline
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.dashboard import priority
from omni_crm.prospect import contacts as contacts_mod
from omni_crm.prospect import fit, fit_view
from omni_crm.prospect.organization import ORGANIZATION

LEAD = "CRM Lead"
LOG = care_log.LOG
PAGE_SIZE = 20
ACTIVITY_PAGE = 30
ACTIVITY_SOURCE_LIMIT = 150
REFUSED = contacts_mod.REFUSED
ERASED_PREFIX = contacts_mod.ERASED_NAME_PREFIX
FILTERS = {
	"all": None,
	"message": {"message", "bot"},
	"call": {"call"},
	"care": {"care"},
	"system": {"system"},
}
EDITABLE = {
	"first_name",
	"job_title",
	"mobile_no",
	"phone",
	"email",
	"organization",
	"omni_need",
	"omni_products",
	"omni_quantity",
	"omni_timeframe",
	"omni_urgency",
}
NEED_FIELDS = ("omni_need", "omni_products", "omni_quantity", "omni_timeframe", "omni_urgency")


def _readable(lead: str) -> str:
	"""Kiểm quyền ĐỌC Lead, trả tên Lead đích (theo Lead đã gộp)."""
	inbox.check_lead_access(lead)
	target = lead_bridge.follow_merged(lead) or lead
	if (
		target != lead
	):  # Lead đã gộp: người dùng phải xem được cả Lead đích, nếu không sẽ đọc được dữ liệu của sale khác
		inbox.check_lead_access(target)
	return target


def _writable(lead: str) -> str:
	return inbox.check_lead_write(lead)


def _org_of(lead: str) -> str | None:
	org = frappe.db.get_value(LEAD, lead, "organization")
	return org if org and frappe.db.exists(ORGANIZATION, org) else None


def _names(users: list[str | None]) -> dict[str, str]:
	users = [u for u in set(users) if u]
	if not users:
		return {}
	return {
		r.name: r.full_name or r.name
		for r in frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	}


# --- đầu trang, thanh giai đoạn, thao tác ------------------------------------------------------------------------


def _person_flags(lead: str) -> dict[str, bool]:
	"""Người của Lead có từ chối nhận liên hệ / đã bị xóa dữ liệu không (theo Contact khớp SĐT/email hoặc Deal)."""
	names = contacts_mod.contacts_of_lead(lead)
	rows = (
		frappe.get_all("Contact", filters={"name": ["in", names]}, fields=["name", "omni_consent"])
		if names
		else []
	)
	refused = any(r.omni_consent == REFUSED for r in rows)
	erased = any(r.name.startswith(ERASED_PREFIX) for r in rows)
	if (
		not erased
	):  # Contact đã xóa dữ liệu không còn SĐT/email để khớp: nhận ra qua khóa băm trên SĐT/email của Lead
		lead_row = frappe.db.get_value(LEAD, lead, ["mobile_no", "phone", "email"], as_dict=True) or {}
		erased = contacts_mod.is_erased_person(
			phone=lead_row.get("mobile_no") or lead_row.get("phone"), email=lead_row.get("email")
		)
	return {"refused": refused or erased, "erased": erased}


def _stages(lead: str) -> dict[str, Any]:
	statuses = frappe.get_all(
		"CRM Lead Status", fields=["name", "type", "color", "position"], order_by="position asc"
	)
	status = frappe.db.get_value(LEAD, lead, "status")
	deal = lead_bridge.get_deal_for_lead(lead)
	deal_stages: list[dict] = []
	deal_status = None
	if deal:
		deal_status = frappe.db.get_value("CRM Deal", deal, "status")
		deal_stages = frappe.get_all(
			"CRM Deal Status", fields=["name", "type", "color", "position"], order_by="position asc"
		)
	return {
		"lead_stages": [s for s in statuses if s.type != "Lost" or s.name == status],
		"deal_stages": [s for s in deal_stages if s.type != "Lost" or s.name == deal_status],
		"lead_status": status,
		"deal": deal,
		"deal_status": deal_status,
		"lost_stage": "Không phù hợp",
	}


@frappe.whitelist()
def get_header(lead: str) -> dict[str, Any]:
	"""Đầu trang: tiêu đề (nhà thầu: tên công ty + MST), nhãn, giai đoạn, thao tác nhanh."""
	lead = _readable(lead)
	row = frappe.db.get_value(
		LEAD,
		lead,
		[
			"name",
			"lead_name",
			"job_title",
			"organization",
			"status",
			"lead_owner",
			"mobile_no",
			"phone",
			"email",
			"converted",
			"omni_score",
			"omni_first_channel",
		],
		as_dict=True,
	)
	org = _org_of(lead)
	title, subtitle, tax_id = (
		row.lead_name or lead,
		" · ".join(x for x in (row.job_title, row.organization) if x),
		None,
	)
	org_info = None
	if org:
		org_info = frappe.db.get_value(
			ORGANIZATION,
			org,
			["name", "organization_name", "omni_tax_id", "omni_fit_tier", "omni_fit_score", "omni_region"],
			as_dict=True,
		)
		tax_id = org_info.omni_tax_id
		title = org_info.organization_name or org
		subtitle = row.lead_name or ""
	person = _person_flags(lead)
	has_phone = bool(row.mobile_no or row.phone)
	can_write = bool(frappe.has_permission(LEAD, "write", doc=lead))
	identities = frappe.get_all(
		"Omni Identity",
		filters={"lead": lead},
		fields=["channel_type", "is_follower"],
		order_by="creation asc",
	)
	score_on = scoring.scoring_enabled()
	return {
		"lead": lead,
		"title": title,
		"tax_id": tax_id,
		"subtitle": subtitle,
		"is_prospect": bool(org),
		"organization": org_info,
		"channels": [r.channel_type for r in identities],
		"hot": bool(score_on and scoring.is_hot(row.omni_score, scoring.fit_tier_of(org))),
		"score": cint(row.omni_score) if score_on else None,
		"owner": row.lead_owner,
		"stages": _stages(lead),
		"actions": {
			# Gọi/nhắn bị ẩn khi người đó từ chối nhận liên hệ hoặc đã bị xóa dữ liệu (docs/14 mục 6)
			"can_call": bool(has_phone and not person["refused"] and not person["erased"]),
			"can_message": bool(
				any(i.channel_type == "Zalo OA" for i in identities)
				and not person["refused"]
				and not person["erased"]
			),
			"can_email": bool(row.email and not person["refused"] and not person["erased"]),
			"person_refused": person["refused"],
			"person_erased": person["erased"],
			"phone": row.mobile_no or row.phone
			if (has_phone and not person["refused"] and not person["erased"])
			else None,
			"can_write": can_write,
			"can_convert": bool(can_write and not row.converted),
			"converted": bool(cint(row.converted)),
		},
		"features": priority.features(),
		"care_log_enabled": flags.care_log_enabled(),
	}


# --- cột trái: thông tin, kênh, người liên hệ ------------------------------------------------------------------


def _source_of(field: str, manual: list[str], value: Any) -> str | None:
	if not value:
		return None
	if field in manual:
		return "Sale"
	return "Bot" if field.startswith("omni_") else "Kênh"


@frappe.whitelist()
def get_profile(lead: str) -> dict[str, Any]:
	"""Thông tin khách ba nhóm (Liên hệ, Nhu cầu, Quản lý), mỗi trường có nhãn nguồn Kênh/Bot/Sale."""
	lead = _readable(lead)
	row = frappe.db.get_value(LEAD, lead, "*", as_dict=True)
	manual = lead_bridge._parse_fields(row.get("omni_manual_fields"))
	identities = frappe.get_all(
		"Omni Identity",
		filters={"lead": lead},
		fields=["channel_type", "display_name", "phone_e164", "email_norm", "is_follower", "last_inbound_at"],
		order_by="creation asc",
	)
	person = _person_flags(lead)
	hidden = person["refused"] or person["erased"]
	org_exists = bool(_org_of(lead))

	def item(field: str, label: str, value: Any, editable: bool = True) -> dict:
		return {
			"field": field,
			"label": label,
			"value": value or "",
			"source": _source_of(field, manual, value),
			"editable": editable and field in EDITABLE and not (field == "organization" and org_exists),
		}

	contact = [
		item("first_name", _("Họ tên"), row.lead_name),
		item("job_title", _("Chức vụ / phòng"), row.job_title),
		item("organization", _("Đơn vị"), row.organization),
		item("mobile_no", _("Điện thoại"), None if hidden else (row.mobile_no or row.phone)),
		item("email", _("Email"), None if hidden else row.email),
	]
	zalo = [i for i in identities if i.channel_type == "Zalo OA"]
	contact.append(
		{
			"field": "zalo",
			"label": _("Zalo"),
			"value": (_("Đang quan tâm OA") if any(i.is_follower for i in zalo) else _("Chưa quan tâm OA"))
			if zalo
			else "",
			"source": "Kênh" if zalo else None,
			"editable": False,
		}
	)
	need_labels = {
		"omni_need": _("Nhu cầu"),
		"omni_products": _("Sản phẩm quan tâm"),
		"omni_quantity": _("Số lượng"),
		"omni_timeframe": _("Thời điểm mua"),
		"omni_urgency": _("Mức gấp"),
	}
	need = [item(f, need_labels[f], row.get(f)) for f in NEED_FIELDS]
	need.append(
		item("omni_intent", _("Ý định gần nhất"), row.get("omni_intent"))
	)  # V21-06: nhãn ý định B2B, chỉ đọc
	show_need = flags.bot_analyze_enabled() or any(n["value"] for n in need)
	owners = _names([row.lead_owner])
	manage = [
		{
			"field": "omni_first_channel",
			"label": _("Nguồn / kênh đầu"),
			"value": row.omni_first_channel or "",
		},
		{"field": "lead_owner", "label": _("Người phụ trách"), "value": owners.get(row.lead_owner, "")},
		{"field": "status", "label": _("Trạng thái"), "value": row.status or ""},
		{
			"field": "omni_last_inbound_at",
			"label": _("Liên hệ gần nhất"),
			"value": str(row.omni_last_inbound_at or ""),
		},
	]
	groups = [
		{"key": "contact", "label": _("Liên hệ"), "items": contact},
		{"key": "manage", "label": _("Quản lý"), "items": manage},
	]
	if show_need:
		groups.insert(1, {"key": "need", "label": _("Nhu cầu"), "items": need})
	return {
		"groups": groups,
		"channels": [
			{
				"channel_type": i.channel_type,
				"name": i.display_name or "",
				"phone": None if hidden else i.phone_e164,
				"email": None if hidden else i.email_norm,
				"last_inbound_at": str(i.last_inbound_at or ""),
				"is_follower": bool(i.is_follower) if i.channel_type == "Zalo OA" else None,
			}
			for i in identities
		],
	}


@frappe.whitelist(methods=["POST"])
def update_field(lead: str, field: str, value: str | None = None) -> dict[str, Any]:
	"""Sửa tại chỗ một trường hồ sơ: ghi CRM Lead và đánh dấu sửa tay (bot không ghi đè)."""
	lead = _writable(lead)
	if field not in EDITABLE:
		frappe.throw(_("Trường này không sửa tại chỗ được"))
	if field == "organization" and _org_of(lead):
		frappe.throw(_("Đơn vị của nhà thầu đổi tại hồ sơ nhà thầu, không đổi tại Lead"))
	if field == "omni_urgency" and value not in (None, "", "Thấp", "Trung bình", "Cao"):
		frappe.throw(_("Mức gấp không hợp lệ"))
	doc = frappe.get_doc(LEAD, lead)
	doc.set(field, (value or "").strip() or None)
	if (
		field == "first_name"
	):  # "Họ tên" hiển thị là lead_name = ghép first + middle + last: ghi cả tên vào first_name
		doc.middle_name = doc.last_name = None
	doc.save()  # qua quyền của người dùng; hook track_manual_edits ghi omni_manual_fields
	return {
		"lead": lead,
		"field": field,
		"manual": field in lead_bridge._parse_fields(doc.get("omni_manual_fields")),
	}


@frappe.whitelist()
def get_contacts(lead: str) -> dict[str, Any]:
	"""Người liên hệ của nhà thầu: vai trò, đồng ý nhận liên hệ; từ chối/đã xóa thì không SĐT/email và ẩn nút gọi/nhắn."""
	lead = _readable(lead)
	org = _org_of(lead)
	if not org:
		return {"rows": [], "organization": None}
	rows = frappe.get_all(
		"Contact",
		filters={"company_name": org},
		fields=[
			"name",
			"full_name",
			"designation",
			"email_id",
			"mobile_no",
			"omni_contact_role",
			"omni_has_zalo",
			"omni_consent",
		],
		order_by="creation asc",
		limit_page_length=200,
	)
	out = []
	for r in rows:
		erased = r.name.startswith(ERASED_PREFIX)
		refused = r.omni_consent == REFUSED or erased
		out.append(
			{
				"name": r.name,
				"full_name": _("Đã xóa dữ liệu") if erased else r.full_name,
				"designation": "" if erased else r.designation,
				"role": r.omni_contact_role or "",
				"consent": r.omni_consent or "Chưa hỏi",
				"refused": refused,
				"erased": erased,
				"phone": None if refused else r.mobile_no,
				"email": None if refused else r.email_id,
				"can_call": bool(not refused and r.mobile_no),
				"can_message": bool(not refused and r.mobile_no and r.omni_has_zalo == "Có"),
			}
		)
	return {"rows": out, "organization": org}


# --- cột phải: Mức phù hợp, điểm, gói đề xuất, việc, nghi trùng ---------------------------------------------------


@frappe.whitelist()
def get_fit(lead: str) -> dict[str, Any]:
	"""Khối "Mức phù hợp" cạnh "Điểm quan tâm" và "Gói đề xuất"; {} nếu Lead chưa gắn nhà thầu."""
	lead = _readable(lead)
	org = _org_of(lead)
	row = frappe.db.get_value(LEAD, lead, ["omni_score", "omni_score_breakdown"], as_dict=True)
	engagement = None
	if scoring.scoring_enabled():
		view = scoring.score_view(row) if row.omni_score_breakdown else None
		engagement = {
			"score": cint(row.omni_score),
			"threshold": scoring.hot_threshold(),
			"reasons": (view or {}).get("reasons", []),
		}
	if not org:
		return {"fit": None, "package": None, "engagement": engagement}
	package = frappe.db.get_value(ORGANIZATION, org, "omni_interested_package")
	return {
		"fit": fit_view.explain(org) if fit.fit_enabled() else None,
		"package": package or "Chưa rõ",
		"engagement": engagement,
	}


@frappe.whitelist()
def get_side(lead: str) -> dict[str, Any]:
	"""Cột phải còn lại: việc cần làm (CRM Task của Lead/Deal), nghi trùng đang mở, số liệu quản lý."""
	lead = _readable(lead)
	deal = lead_bridge.get_deal_for_lead(lead)
	refs = [lead] + ([deal] if deal else [])
	tasks = frappe.get_all(
		"CRM Task",
		filters={"reference_docname": ["in", refs]},
		fields=["name", "title", "status", "due_date", "assigned_to", "priority"],
		order_by="status in ('Done', 'Canceled') asc, due_date asc",
		limit_page_length=30,
	)
	names = _names([t.assigned_to for t in tasks])
	# get_list (áp permission_query_conditions): chỉ cặp mà người dùng đọc được CẢ hai Lead, vì `reason` có thể lộ SĐT/tên Lead kia
	candidates = frappe.get_list(
		"Omni Duplicate Candidate",
		or_filters=[["lead_a", "=", lead], ["lead_b", "=", lead]],
		filters={"status": "Open"},
		fields=["name", "lead_a", "lead_b", "reason", "score"],
	)
	for c in candidates:
		c["other"] = c.lead_b if c.lead_a == lead else c.lead_a
		c["other_name"] = frappe.db.get_value(LEAD, c.other, "lead_name") or c.other
	since = add_to_date(now_datetime(), days=-30)
	contacts_30d = frappe.db.sql(
		"""select count(distinct date(received_at)) from `tabOmni Inbound Message`
		where lead = %s and event_type in ('message', 'user_info') and received_at >= %s""",
		(lead, since),
	)[0][0]
	return {
		"tasks": [
			{**t, "assigned_name": names.get(t.assigned_to, ""), "done": t.status in ("Done", "Canceled")}
			for t in tasks
		],
		"duplicates": candidates,
		"contacts_30d": cint(contacts_30d),
		"can_write": bool(frappe.has_permission(LEAD, "write", doc=lead)),
	}


@frappe.whitelist(methods=["POST"])
def set_task_done(task: str, done: int | str = 1) -> str:
	"""Tích hoàn thành (hoặc mở lại) một việc của Lead; cần quyền ghi Lead mà việc tham chiếu tới."""
	row = frappe.db.get_value("CRM Task", task, ["reference_doctype", "reference_docname"], as_dict=True)
	if not row:
		frappe.throw(_("Không tìm thấy việc"), frappe.DoesNotExistError)
	lead = row.reference_docname
	if row.reference_doctype == "CRM Deal":
		lead = frappe.db.get_value("CRM Deal", lead, "lead") or lead
	_writable(lead)
	doc = frappe.get_doc("CRM Task", task)
	doc.status = "Done" if cint(done) else "Todo"
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.status


@frappe.whitelist(methods=["POST"])
def add_task(lead: str, title: str, due: str | None = None) -> str:
	"""Thêm việc cho Lead (giao người phụ trách Lead; không gửi gì cho khách)."""
	lead = _writable(lead)
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Nhập tên việc"))
	deal = lead_bridge.get_deal_for_lead(lead)
	doc = frappe.get_doc(
		{
			"doctype": "CRM Task",
			"title": title[:140],
			"status": "Todo",
			"priority": "Medium",
			"assigned_to": frappe.db.get_value(LEAD, lead, "lead_owner") or frappe.session.user,
			"due_date": get_datetime(due) if due else None,
			"reference_doctype": "CRM Deal" if deal else LEAD,
			"reference_docname": deal or lead,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


# --- tab Gói thầu đã trúng ---------------------------------------------------------------------------------------


@frappe.whitelist()
def get_awards(lead: str, page: int = 1) -> dict[str, Any]:
	"""Gói thầu đã trúng của ĐÚNG nhà thầu của Lead, ngày mới nhất trước, có cờ còn hiệu lực."""
	lead = _readable(lead)
	org = _org_of(lead)
	if not org:
		return {"rows": [], "total": 0, "page": 1}
	page = max(cint(page), 1)
	today = frappe.utils.getdate()
	total = frappe.db.count("Omni Bid Award", {"organization": org})
	rows = frappe.get_all(
		"Omni Bid Award",
		filters={"organization": org},
		fields=[
			"name",
			"hospital",
			"notice_no",
			"package_name",
			"category",
			"award_date",
			"end_date",
			"award_value",
			"item_count",
			"role",
		],
		order_by="award_date desc, name asc",
		start=(page - 1) * PAGE_SIZE,
		page_length=PAGE_SIZE,
	)
	for r in rows:
		r["active"] = bool(r.end_date and frappe.utils.getdate(r.end_date) >= today)
	return {"rows": rows, "total": total, "page": page, "page_size": PAGE_SIZE}


# --- Nhật ký chăm sóc -------------------------------------------------------------------------------------------


def _log_row(r: Any, names: dict[str, str]) -> dict[str, Any]:
	return {
		"name": r.name,
		"channel": r.channel,
		"started_at": str(r.started_at or ""),
		"last_activity_at": str(r.last_activity_at or ""),
		"handled_by": r.handled_by,
		"handled_name": names.get(r.handled_by, ""),
		"handled_kind": r.handled_kind,
		"summary": r.summary or "",
		"outcome": r.outcome or "",
		"next_action": r.next_action or "",
		"next_due": str(r.next_due or ""),
		"crm_task": r.crm_task,
		"auto_created": bool(r.auto_created),
		"call": r.call,
		"unrecorded": bool(
			r.handled_kind == "Sale"
			and not r.outcome
			and r.last_activity_at
			and get_datetime(r.last_activity_at) <= care_log.unrecorded_cutoff()
		),
	}


@frappe.whitelist()
def get_care_logs(lead: str, page: int = 1) -> dict[str, Any]:
	"""Bảng Nhật ký chăm sóc của Lead, mới nhất trước, 20 dòng một trang, kèm phiên đang mở để điền sẵn form."""
	lead = _readable(lead)
	page = max(cint(page), 1)
	total = frappe.db.count(LOG, {"lead": lead})
	rows = frappe.get_all(
		LOG,
		filters={"lead": lead},
		fields=[
			"name",
			"channel",
			"started_at",
			"last_activity_at",
			"handled_by",
			"handled_kind",
			"summary",
			"outcome",
			"next_action",
			"next_due",
			"crm_task",
			"auto_created",
			"call",
		],
		order_by="last_activity_at desc, name desc",
		start=(page - 1) * PAGE_SIZE,
		page_length=PAGE_SIZE,
	)
	names = _names([r.handled_by for r in rows])
	open_session = frappe.db.get_value(
		LOG,
		{
			"lead": lead,
			"outcome": ["is", "not set"],
			"last_activity_at": [">=", now_datetime() - care_log._gap()],
		},
		["name", "channel", "started_at", "summary"],
		as_dict=True,
		order_by="last_activity_at desc",
	)
	return {
		"rows": [_log_row(r, names) for r in rows],
		"total": total,
		"page": page,
		"page_size": PAGE_SIZE,
		"open_session": {**open_session, "started_at": str(open_session.started_at)}
		if open_session
		else None,
		"outcomes": list(care_log.OUTCOMES),
		"no_next_outcomes": list(care_log.NO_NEXT_OUTCOMES),
		"channels": list(care_log.CHANNELS),
		"can_write": bool(frappe.has_permission(LEAD, "write", doc=lead)),
	}


@frappe.whitelist(methods=["POST"])
def save_care_log(
	lead: str,
	outcome: str,
	channel: str | None = None,
	at: str | None = None,
	summary: str | None = None,
	next_action: str | None = None,
	next_due: str | None = None,
	create_task: int | str = 1,
	log: str | None = None,
) -> dict[str, Any]:
	"""Form "Ghi nhật ký chăm sóc": cập nhật phiên `log` (điền sẵn phiên đang mở) hoặc tạo phiên ghi tay; trả kết quả.

	Kiểm quy tắc bắt buộc trước khi tạo gì (kết quả sai thì không để lại phiên rỗng). Cần quyền ghi Lead.
	Không gửi gì cho khách.
	"""
	lead = _writable(lead)
	if not flags.dashboard_enabled():
		frappe.throw(_("Dashboard CSKH đang tắt"))
	care_log.validate_outcome(outcome, next_action, next_due)
	at = care_log.clamp_contact_time(lead, at)  # V2-05: không để `at` làm đẹp "Phản hồi lần đầu"
	if log:
		log_lead = frappe.db.get_value(LOG, log, "lead")
		if not log_lead or (lead_bridge.follow_merged(log_lead) or log_lead) != lead:
			frappe.throw(_("Phiên không thuộc Lead này"), frappe.PermissionError)
	else:
		log = care_log.create_manual_log(lead, channel or "Khác", at, None)
	result = care_log.save_outcome(
		log,
		outcome,
		next_action=next_action,
		next_due=next_due,
		create_task=cint(create_task),
		summary=summary,
		at=at,
	)
	timeline.notify(lead, None)
	return result


# --- tab Hoạt động -----------------------------------------------------------------------------------------------


def _event(
	kind: str, at: Any, title: str, detail: str, source: str, ref: dict, direction: str | None = None
) -> dict:
	return {
		"kind": kind,
		"at": str(at),
		"title": title,
		"detail": detail,
		"source": source,
		"ref": ref,
		"direction": direction,
	}


def _clip(text: str | None, n: int = 160) -> str:
	return " ".join((text or "").split())[:n]


def _message_events(lead: str) -> list[dict]:
	out: list[dict] = []
	for m in frappe.get_all(
		"Omni Inbound Message",
		filters={
			"lead": lead,
			"event_type": ["in", ["message", "oa_echo"]],
			"status": ["!=", "Ignored"],
		},
		fields=["name", "channel_type", "event_type", "text", "received_at"],
		order_by="received_at desc",
		limit_page_length=ACTIVITY_SOURCE_LIMIT,
	):
		echo = m.event_type == "oa_echo"
		out.append(
			_event(
				"message",
				m.received_at,
				_("Nhân viên trả lời ngoài hệ thống") if echo else _("Khách nhắn"),
				_clip(m.text),
				m.channel_type,
				{"type": "inbox", "lead": lead},
				"out" if echo else "in",
			)
		)
	rows = frappe.get_all(
		"Omni Outbound Message",
		filters={"lead": lead, "status": ["in", ["Sent", "Failed", "Blocked", "Queued"]]},
		fields=[
			"name",
			"channel",
			"text",
			"source",
			"sent_by",
			"status",
			"sent_at",
			"creation",
			"is_sandbox",
		],
		order_by="creation desc",
		limit_page_length=ACTIVITY_SOURCE_LIMIT,
	)
	names = _names([o.sent_by for o in rows])
	types = {r.name: r.channel_type for r in frappe.get_all("Omni Channel", fields=["name", "channel_type"])}
	for o in rows:
		who = {"Sale": names.get(o.sent_by) or _("Nhân viên"), "Bot": _("Bot"), "System": _("Hệ thống")}.get(
			o.source, o.source
		)
		state = "" if o.status == "Sent" else f" ({o.status})"
		out.append(
			_event(
				"message",
				o.sent_at or o.creation,
				_("{0} gửi").format(who) + state + (" [sandbox]" if cint(o.is_sandbox) else ""),
				_clip(o.text),
				types.get(o.channel) or "",
				{"type": "inbox", "lead": lead},
				"out",
			)
		)
	return out


def _call_events(lead: str) -> list[dict]:
	rows = frappe.get_all(
		"Omni Call",
		filters={"lead": lead, "status": ["in", list(inbox.CALL_FINAL_STATUSES)]},
		fields=["name", "direction", "status", "duration_seconds", "agent", "started_at", "creation"],
		order_by="creation desc",
		limit_page_length=ACTIVITY_SOURCE_LIMIT,
	)
	names = _names([r.agent for r in rows])
	out = []
	for c in rows:
		item = inbox._call_item(c)
		out.append(
			_event(
				"call",
				c.started_at or c.creation,
				item["text"],
				names.get(c.agent, ""),
				_("Điện thoại"),
				{"type": "call", "call": c.name},
				item["direction"],
			)
		)
	return out


def _care_events(lead: str) -> list[dict]:
	rows = frappe.get_all(
		LOG,
		filters={"lead": lead},
		fields=["name", "channel", "last_activity_at", "outcome", "summary", "handled_by", "auto_created"],
		order_by="last_activity_at desc",
		limit_page_length=ACTIVITY_SOURCE_LIMIT,
	)
	return [
		_event(
			"care",
			r.last_activity_at,
			_("Nhật ký chăm sóc") + (f": {r.outcome}" if r.outcome else ""),
			_clip(r.summary),
			_("Hệ thống") if r.auto_created else _("Sale"),
			{"type": "care", "log": r.name},
		)
		for r in rows
	]


def _bot_events(lead: str) -> list[dict]:
	if flags.bot_reply_mode() == "Off":
		return []
	rows = frappe.get_all(
		"Omni Bot Draft",
		filters={"lead": lead, "mode": "Draft"},
		fields=["name", "action", "decision", "question_group", "creation"],
		order_by="creation desc",
		limit_page_length=ACTIVITY_SOURCE_LIMIT,
	)
	return [
		_event(
			"bot",
			r.creation,
			_("Bot chuyển người") if r.action == "Handoff" else _("Nháp của bot"),
			f"{r.question_group or ''} · {r.decision}".strip(" ·"),
			_("Bot"),
			{"type": "inbox", "lead": lead},
		)
		for r in rows
	]


def _system_events(lead: str) -> list[dict]:
	out: list[dict] = []
	created = frappe.db.get_value(LEAD, lead, ["creation", "omni_first_channel"], as_dict=True)
	out.append(
		_event(
			"system",
			created.creation,
			_("Tạo Lead"),
			created.omni_first_channel or "",
			_("Hệ thống"),
			{"type": "none"},
		)
	)
	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": LEAD, "docname": lead},
		fields=["name", "creation", "data", "owner"],
		order_by="creation desc",
		limit_page_length=ACTIVITY_SOURCE_LIMIT,
	)
	labels = {
		"lead_owner": _("Đổi người phụ trách"),
		"status": _("Đổi trạng thái"),
		"organization": _("Đổi đơn vị"),
	}
	for v in versions:
		try:
			changed = (json.loads(v.data or "{}") or {}).get("changed") or []
		except ValueError:
			continue
		for field, old, new in (c[:3] for c in changed if len(c) >= 3):
			if field in labels:
				out.append(
					_event(
						"system",
						v.creation,
						labels[field],
						f"{_clip(str(old or ''), 60)} → {_clip(str(new or ''), 60)}",
						v.owner or _("Hệ thống"),
						{"type": "none"},
					)
				)
	for c in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": LEAD,
			"reference_name": lead,
			"comment_type": "Comment",
			"content": ["like", "Omni:%"],
		},
		fields=["creation", "content"],
		order_by="creation desc",
		limit_page_length=50,
	):
		out.append(
			_event(
				"system",
				c.creation,
				_("Gộp kênh / Lead"),
				frappe.utils.strip_html_tags(c.content or "")[:160],
				_("Hệ thống"),
				{"type": "none"},
			)
		)
	return out


@frappe.whitelist()
def get_activity(lead: str, kind: str = "all", page: int = 1) -> dict[str, Any]:
	"""Dòng hoạt động của Lead, mới nhất trước: tin vào/ra, cuộc gọi, nhật ký, nháp bot, sự kiện hệ thống; lọc theo `kind`."""
	lead = _readable(lead)
	if kind not in FILTERS:
		frappe.throw(_("Bộ lọc không hợp lệ"))
	wanted = FILTERS[kind]
	events: list[dict] = []
	sources = {
		"message": _message_events,
		"call": _call_events,
		"care": _care_events,
		"bot": _bot_events,
		"system": _system_events,
	}
	for key, fn in sources.items():
		if wanted is None or key in wanted:
			events.extend(fn(lead))
	events.sort(key=lambda e: e["at"], reverse=True)
	page = max(cint(page), 1)
	start = (page - 1) * ACTIVITY_PAGE
	return {
		"rows": events[start : start + ACTIVITY_PAGE],
		"total": len(events),
		"page": page,
		"page_size": ACTIVITY_PAGE,
	}


@frappe.whitelist()
def get_mini_profile(lead: str) -> dict[str, Any]:
	"""Hồ sơ rút gọn cho Hộp thư: đơn vị + hạng, bước tiếp theo gần nhất, nhãn "Chưa ghi kết quả"."""
	lead = _readable(lead)
	row = frappe.db.get_value(
		LEAD,
		lead,
		[
			"lead_name",
			"job_title",
			"organization",
			"omni_first_channel",
			"lead_owner",
			"status",
			"mobile_no",
			"email",
		],
		as_dict=True,
	)
	org = _org_of(lead)
	tier = frappe.db.get_value(ORGANIZATION, org, "omni_fit_tier") if org and fit.fit_enabled() else None
	step = frappe.db.get_value(
		LOG,
		{"lead": lead, "next_due": ["is", "set"], "outcome": ["is", "set"]},
		["next_action", "next_due", "crm_task"],
		as_dict=True,
		order_by="next_due desc",
	)
	open_step = None
	if step:
		done = step.crm_task and frappe.db.get_value("CRM Task", step.crm_task, "status") in (
			"Done",
			"Canceled",
		)
		if not done:
			open_step = {"action": step.next_action, "due": str(step.next_due)}
	person = _person_flags(lead)
	hidden = person["refused"] or person["erased"]
	return {
		"lead": lead,
		"organization": row.organization or "",
		"fit_tier": tier,
		"job_title": row.job_title or "",
		"first_channel": row.omni_first_channel or "",
		"owner": row.lead_owner,
		"status": row.status,
		"phone": None if hidden else row.mobile_no,
		"email": None if hidden else row.email,
		"next_step": open_step,
		"unrecorded": lead in care_log.unrecorded_by_lead([lead]),
		"person_refused": person["refused"],
		"care_log_enabled": flags.care_log_enabled(),
	}
