"""Gộp hai Lead (docs/04 mục 5): chuyển tường minh mọi tham chiếu, không xóa Lead phụ. Xem ADR 0002."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html

from omni_crm.crm_bridge.lead import normalized_fields
from omni_crm.flags import omni_bot

LEAD = "CRM Lead"
ORGANIZATION = "CRM Organization"
SAVEPOINT = "omni_merge_leads"
ORG_SAVEPOINT = "omni_merge_orgs"
LOST_REASON = "Other"  # CRM Lost Reason có sẵn (apps/crm/crm/install.py::add_default_lost_reasons)
JUNK_STATUS = "Junk"  # CRM Lead Status loại Lost có sẵn (docs/12 mục C, G1)

# DocType của app này có trường Link tới CRM Lead: (DocType, tên trường). DocType chưa tồn tại thì bỏ qua
# (Omni Call, Omni Bot Draft thuộc Đợt 2).
OMNI_LEAD_LINKS: list[tuple[str, str]] = [
	("Omni Identity", "lead"),
	("Omni Inbound Message", "lead"),
	("Omni Outbound Message", "lead"),
	("Omni Call", "lead"),
	("Omni Bot Run", "lead"),  # D2-04
	("Omni Bot Draft", "lead"),
	(
		"Omni Care Log",
		"lead",
	),  # V2-04 (docs/13): chưa có DocType thì _table_ok bỏ qua; có thì được chuyển theo
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
	("ToDo", "reference_type", "reference_name"),  # phân công sale (frappe/desk/doctype/todo)
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
	"""Gộp Lead `secondary` vào `primary` trong một transaction (savepoint); chạy lại lần hai không lỗi.

	Ghi Lead trong lúc gộp là việc của hệ thống: không tính vào `omni_manual_fields` (D1-06).
	"""
	with omni_bot():
		return _merge_leads(primary, secondary, user, candidate)


def _merge_leads(primary: str, secondary: str, user: str, candidate: str | None = None) -> dict:
	if primary == secondary:
		frappe.throw(_("Không thể gộp một Lead vào chính nó"))
	for lead in (primary, secondary):
		if not frappe.db.exists(LEAD, lead):
			frappe.throw(_("Lead {0} không tồn tại").format(lead))
	_check_permission(primary, user)
	_check_permission(secondary, user)

	frappe.db.savepoint(SAVEPOINT)
	try:
		# Khóa hàng cả hai Lead theo thứ tự tên để hai lượt gộp ngược chiều đồng thời không cùng qua
		# bước kiểm tra rồi tạo vòng omni_merged_into; kiểm tra trên giá trị mới nhất sau khi giữ khóa.
		locked = {
			n: frappe.db.get_value(LEAD, n, "omni_merged_into", for_update=True)
			for n in sorted((primary, secondary))
		}
		p_merged, s_merged = locked[primary], locked[secondary]
		if p_merged:
			frappe.throw(_("Lead chính {0} đã bị gộp vào {1}").format(primary, p_merged))
		if s_merged:
			if s_merged == primary:  # đã gộp trước đó: không làm lại, chỉ bảo đảm Candidate được đóng
				_close_related_candidates(primary, secondary, user, candidate)
				return {"already_merged": True, "moved": {}, "filled": [], "differences": []}
			frappe.throw(_("Lead {0} đã bị gộp vào {1}").format(secondary, s_merged))
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


# --- Gộp Organization trùng (docs/14, D1-13 mục 6; hiếm: do nhập tay) ----------------------------------------------

# Trường của CRM Organization: trống ở chính thì lấy từ phụ; đã có thì giữ và ghi vào danh sách khác biệt.
ORG_FILL_FIELDS = [
	"website",
	"industry",
	"territory",
	"no_of_employees",
	"annual_revenue",
	"omni_tax_id",
	"omni_short_name",
	"omni_address",
	"omni_province",
	"omni_region",
	"omni_company_phone",
	"omni_company_email",
	"omni_revenue_bn",
	"omni_revenue_year",
	"omni_accounting_software",
	"omni_est_item_count",
	"omni_data_source",
	"omni_collected_on",
	"omni_interested_package",
]
SCORE_MERGED_ORG_OPEN_LEADS = 90


def _check_org_permission(org: str, user: str) -> None:
	if not frappe.has_permission(ORGANIZATION, "write", org, user=user):
		frappe.throw(_("Bạn không có quyền sửa nhà thầu {0}").format(org), frappe.PermissionError)


def merge_organizations(primary: str, secondary: str, user: str) -> dict:
	"""Gộp CRM Organization `secondary` vào `primary` (cùng một nhà thầu nhập hai lần) trong một savepoint.

	Chuyển Contact (`company_name`), Omni Bid Award, CRM Lead (`organization`, Data) và CRM Deal (`organization`, Link)
	sang `primary`; điền trường trống (kể cả mã số thuế) từ phụ; xóa Organization phụ khi đã hết tham chiếu. Nếu cả hai
	còn Lead đang mở thì tạo nghi trùng cho người quyết (không tự gộp Lead). Chạy lại sau khi đã gộp: không lỗi.
	"""
	with omni_bot():
		return _merge_organizations(primary, secondary, user)


def _merge_organizations(primary: str, secondary: str, user: str) -> dict:
	if primary == secondary:
		frappe.throw(_("Không thể gộp một nhà thầu vào chính nó"))
	if not frappe.db.exists(ORGANIZATION, primary):
		frappe.throw(_("Nhà thầu {0} không tồn tại").format(primary))
	if not frappe.db.exists(ORGANIZATION, secondary):
		return {"already_merged": True, "moved": {}, "filled": [], "differences": [], "candidates": []}
	_check_org_permission(primary, user)
	_check_org_permission(secondary, user)

	frappe.db.savepoint(ORG_SAVEPOINT)
	try:
		p = frappe.db.get_value(ORGANIZATION, primary, ORG_FILL_FIELDS, as_dict=True, for_update=True)
		s = frappe.db.get_value(ORGANIZATION, secondary, ORG_FILL_FIELDS, as_dict=True, for_update=True)
		if p.omni_tax_id and s.omni_tax_id and p.omni_tax_id != s.omni_tax_id:
			frappe.throw(
				_("Hai nhà thầu có mã số thuế khác nhau ({0} và {1}): không phải trùng, không gộp").format(
					p.omni_tax_id, s.omni_tax_id
				)
			)
		filled: list[str] = []
		diffs: list[tuple[str, str, str]] = []
		fill: dict[str, Any] = {}
		for f in ORG_FILL_FIELDS:
			pv, sv = p.get(f), s.get(f)
			if sv in (None, "", 0):
				continue
			if pv in (None, "", 0):
				fill[f] = sv
				filled.append(f)
			elif str(pv) != str(sv):
				diffs.append((f, str(pv), str(sv)))
		if (
			"omni_tax_id" in fill
		):  # chỉ mục UNIQUE: giải phóng mã số thuế ở bản phụ trước khi gán cho bản chính
			frappe.db.set_value(ORGANIZATION, secondary, "omni_tax_id", None, update_modified=False)

		moved = {
			"Contact": _repoint("Contact", {"company_name": secondary}, {"company_name": primary}),
			"Omni Bid Award": _repoint(
				"Omni Bid Award", {"organization": secondary}, {"organization": primary}
			),
			"CRM Lead": _repoint(LEAD, {"organization": secondary}, {"organization": primary}),
			"CRM Deal": _repoint("CRM Deal", {"organization": secondary}, {"organization": primary}),
		}
		moved = {k: v for k, v in moved.items() if v}
		if fill:
			frappe.db.set_value(ORGANIZATION, primary, fill, update_modified=False)

		from omni_crm.identity import duplicates
		from omni_crm.identity.resolver import organization_leads

		leads = organization_leads(primary)
		candidates: list[str] = []
		for other in leads[1:]:
			cand = duplicates.create_candidate(
				leads[0],
				other,
				_("Hai nhà thầu trùng đã được gộp ({0} vào {1}), nhưng còn nhiều Lead đang mở").format(
					secondary, primary
				),
				SCORE_MERGED_ORG_OPEN_LEADS,
			)
			if cand:
				candidates.append(cand)

		lines = [_("Đã gộp nhà thầu {0} vào nhà thầu này.").format(escape_html(secondary))]
		if filled:
			lines.append(_("Trường được điền từ nhà thầu phụ: {0}.").format(", ".join(filled)))
		for f, pv, sv in diffs:
			lines.append(
				_("Khác biệt {0}: giữ «{1}», nhà thầu phụ có «{2}».").format(
					f, escape_html(pv), escape_html(sv)
				)
			)
		frappe.get_doc(ORGANIZATION, primary).add_comment("Comment", "<br>".join(lines), comment_email=user)
		frappe.delete_doc(ORGANIZATION, secondary, force=True, ignore_permissions=True)
		from omni_crm.prospect import fit

		fit.apply_fit(primary)  # gói thầu và mã hàng ước tính đã dồn về một nhà thầu
	except Exception:
		frappe.db.rollback(save_point=ORG_SAVEPOINT)
		raise
	return {
		"already_merged": False,
		"moved": moved,
		"filled": filled,
		"differences": diffs,
		"candidates": candidates,
	}
