"""Gộp hai Lead (docs/04 mục 5): chuyển tường minh mọi tham chiếu, không xóa Lead phụ. Xem ADR 0002."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html

from omni_crm.crm_bridge.lead import normalized_fields

LEAD = "CRM Lead"
SAVEPOINT = "omni_merge_leads"
LOST_REASON = "Other"  # CRM Lost Reason có sẵn (apps/crm/crm/install.py::add_default_lost_reasons)
JUNK_STATUS = "Junk"  # CRM Lead Status loại Lost có sẵn (docs/12 mục C, G1)

# DocType của app này có trường Link tới CRM Lead: (DocType, tên trường). DocType chưa tồn tại thì bỏ qua
# (Omni Call, Omni Bot Draft thuộc Đợt 2).
OMNI_LEAD_LINKS: list[tuple[str, str]] = [
	("Omni Identity", "lead"),
	("Omni Inbound Message", "lead"),
	("Omni Outbound Message", "lead"),
	("Omni Call", "lead"),
	("Omni Bot Draft", "lead"),
]

# Bản ghi của Frappe/CRM tham chiếu Lead qua cặp (reference_doctype, tên trường tên bản ghi).
# Nguồn: apps/frappe/frappe/core/doctype/communication, desk/doctype/... comment, và
# apps/crm/crm/fcrm/doctype/{crm_call_log,crm_task,fcrm_note}/*.json (xác nhận ở docs/12 mục C, G1).
REFERENCE_LINKS: list[tuple[str, str, str]] = [
	("Communication", "reference_doctype", "reference_name"),
	("Comment", "reference_doctype", "reference_name"),
	("CRM Call Log", "reference_doctype", "reference_docname"),
	("CRM Task", "reference_doctype", "reference_docname"),
	("FCRM Note", "reference_doctype", "reference_docname"),
]

# Bảng con Dynamic Link: (DocType bảng con, parenttype, trường link_doctype, trường link_name).
CHILD_LINKS: list[tuple[str, str, str, str]] = [
	("Communication Link", "Communication", "link_doctype", "link_name"),  # timeline_links
	("Dynamic Link", "CRM Call Log", "link_doctype", "link_name"),  # CRM Call Log.links
]

# Trường của Lead: trống ở chính thì lấy từ phụ; đã có thì giữ và ghi vào danh sách khác biệt.
# Không gồm tên (first_name...), lead_owner (gán sale có hệ quả chia sẻ/phân việc), status.
FILL_FIELDS = [
	"email",
	"mobile_no",
	"phone",
	"organization",
	"job_title",
	"website",
	"territory",
	"industry",
	"no_of_employees",
	"annual_revenue",
	"source",
	"omni_first_channel",
]


def _table_ok(doctype: str, field: str | None = None) -> bool:
	if not frappe.db.exists("DocType", doctype):
		return False
	return field is None or frappe.db.has_column(doctype, field)


def _check_permission(lead: str, user: str) -> None:
	if not frappe.has_permission(LEAD, "write", lead, user=user):
		frappe.throw(_("Bạn không có quyền sửa Lead {0}").format(lead), frappe.PermissionError)


def _move_references(primary: str, secondary: str) -> dict[str, int]:
	"""Đổi mọi tham chiếu từ secondary sang primary; trả số bản ghi đã chuyển theo DocType."""
	moved: dict[str, int] = {}
	for dt, field in OMNI_LEAD_LINKS:
		if _table_ok(dt, field):
			moved[dt] = _repoint(dt, {field: secondary}, {field: primary})
	for dt, ref_dt, ref_name in REFERENCE_LINKS:
		if _table_ok(dt, ref_name):
			moved[dt] = _repoint(dt, {ref_dt: LEAD, ref_name: secondary}, {ref_name: primary})
	for dt, parenttype, link_dt, link_name in CHILD_LINKS:
		if _table_ok(dt, link_name):
			moved[f"{parenttype}.{dt}"] = _repoint(
				dt, {"parenttype": parenttype, link_dt: LEAD, link_name: secondary}, {link_name: primary}
			)
	return {k: v for k, v in moved.items() if v}


def _repoint(doctype: str, where: dict, values: dict) -> int:
	count = frappe.db.count(doctype, where)
	if count:
		# update trực tiếp (không save): không chạy hook, không đổi `modified` của lịch sử
		frappe.db.set_value(doctype, where, values, update_modified=False)
	return count


def _fill_blanks(primary: str, secondary: str) -> tuple[list[str], list[tuple[str, str, str]]]:
	"""Điền trường trống của primary từ secondary; trả (trường đã điền, [(trường, giá trị chính, giá trị phụ)])."""
	p = frappe.db.get_value(LEAD, primary, FILL_FIELDS, as_dict=True)
	s = frappe.db.get_value(LEAD, secondary, FILL_FIELDS, as_dict=True)
	filled: list[str] = []
	diffs: list[tuple[str, str, str]] = []
	values: dict[str, Any] = {}
	for f in FILL_FIELDS:
		pv, sv = p.get(f), s.get(f)
		if not sv:
			continue
		if not pv:
			values[f] = sv
			filled.append(f)
		elif str(pv) != str(sv):
			diffs.append((f, str(pv), str(sv)))
	if values:
		merged = {**p, **values}
		values["omni_phone_e164"], values["omni_email_norm"] = normalized_fields(
			merged.get("mobile_no"), merged.get("phone"), merged.get("email")
		)
		# db.set_value: không chạy validate của CRM (tránh has_gravatar qua mạng), tự tính trường chuẩn hóa.
		frappe.db.set_value(LEAD, primary, values)
	return filled, diffs


def _close_related_candidates(primary: str, secondary: str, user: str, candidate: str | None) -> None:
	"""Candidate đang mở của secondary: cặp (primary, secondary) -> Merged; cặp khác trỏ lại sang primary."""
	rows = frappe.get_all(
		"Omni Duplicate Candidate",
		filters={"status": "Open"},
		or_filters=[["lead_a", "=", secondary], ["lead_b", "=", secondary]],
		fields=["name", "lead_a", "lead_b"],
	)
	for r in rows:
		other = r.lead_b if r.lead_a == secondary else r.lead_a
		doc = frappe.get_doc("Omni Duplicate Candidate", r.name)
		if other == primary:
			doc.status = "Merged"
		else:
			a, b = sorted((primary, other))
			if frappe.db.exists("Omni Duplicate Candidate", {"lead_a": a, "lead_b": b}):
				doc.status = "Dismissed"  # cặp (primary, other) đã có: đóng cặp thừa
			else:
				doc.lead_a, doc.lead_b = a, b  # validate() tự sắp thứ tự
				doc.save(ignore_permissions=True)
				continue
		doc.decided_by = user
		doc.save(ignore_permissions=True)


def merge_leads(primary: str, secondary: str, user: str, candidate: str | None = None) -> dict:
	"""Gộp Lead `secondary` vào `primary` trong một transaction (savepoint); chạy lại lần hai không lỗi."""
	if primary == secondary:
		frappe.throw(_("Không thể gộp một Lead vào chính nó"))
	for lead in (primary, secondary):
		if not frappe.db.exists(LEAD, lead):
			frappe.throw(_("Lead {0} không tồn tại").format(lead))
	_check_permission(primary, user)
	_check_permission(secondary, user)

	p_merged = frappe.db.get_value(LEAD, primary, "omni_merged_into")
	s_merged = frappe.db.get_value(LEAD, secondary, "omni_merged_into")
	if p_merged:
		frappe.throw(_("Lead chính {0} đã bị gộp vào {1}").format(primary, p_merged))
	if s_merged:
		if s_merged == primary:  # đã gộp trước đó: không làm lại, chỉ bảo đảm Candidate được đóng
			_close_related_candidates(primary, secondary, user, candidate)
			return {"already_merged": True, "moved": {}, "filled": [], "differences": []}
		frappe.throw(_("Lead {0} đã bị gộp vào {1}").format(secondary, s_merged))

	frappe.db.savepoint(SAVEPOINT)
	try:
		moved = _move_references(primary, secondary)
		filled, diffs = _fill_blanks(primary, secondary)

		last = frappe.db.get_value(LEAD, primary, "omni_last_inbound_at")
		last_s = frappe.db.get_value(LEAD, secondary, "omni_last_inbound_at")
		if last_s and (not last or last_s > last):
			frappe.db.set_value(LEAD, primary, "omni_last_inbound_at", last_s, update_modified=False)

		values: dict[str, Any] = {"omni_merged_into": primary}
		# Junk thuộc nhóm Lost: CRM đòi lost_reason (và lost_notes nếu "Other") mỗi lần lưu Lead, nên phải
		# điền để Lead phụ vẫn lưu được; thiếu trạng thái/lý do "Other" thì giữ nguyên trạng thái.
		if frappe.db.exists("CRM Lead Status", JUNK_STATUS) and frappe.db.exists(
			"CRM Lost Reason", LOST_REASON
		):
			values.update(
				status=JUNK_STATUS, lost_reason=LOST_REASON, lost_notes=_("Đã gộp vào {0}").format(primary)
			)
		frappe.db.set_value(LEAD, secondary, values)

		lines = [_("Đã gộp Lead {0} vào Lead này.").format(escape_html(secondary))]
		if filled:
			lines.append(_("Trường được điền từ Lead phụ: {0}.").format(", ".join(filled)))
		for f, pv, sv in diffs:
			lines.append(
				_("Khác biệt {0}: giữ «{1}», Lead phụ có «{2}».").format(f, escape_html(pv), escape_html(sv))
			)
		frappe.get_doc(LEAD, primary).add_comment("Comment", "<br>".join(lines), comment_email=user)
		frappe.get_doc(LEAD, secondary).add_comment(
			"Comment", _("Đã gộp vào {0}").format(escape_html(primary)), comment_email=user
		)

		_close_related_candidates(primary, secondary, user, candidate)
		if candidate and frappe.db.exists("Omni Duplicate Candidate", candidate):
			if frappe.db.get_value("Omni Duplicate Candidate", candidate, "status") != "Merged":
				frappe.db.set_value(
					"Omni Duplicate Candidate", candidate, {"status": "Merged", "decided_by": user}
				)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		raise
	return {"already_merged": False, "moved": moved, "filled": filled, "differences": diffs}
