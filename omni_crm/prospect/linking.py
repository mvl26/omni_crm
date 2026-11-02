"""Chuyển đổi Lead đang có sang mô hình nhà thầu (V2-02, mục B): liên kết Lead với CRM Organization, tạo Contact.

Quy tắc:
- Lead có tên tổ chức (`organization`) hoặc email tên miền công ty -> tìm/tạo CRM Organization (không có mã số thuế
  thì để trống, `omni_verified = 0`), liên kết Lead; tạo Contact từ thông tin người của Lead nếu chưa có.
- KHÔNG gộp Lead, KHÔNG đổi người phụ trách, KHÔNG đổi trạng thái của Lead/Deal.
- Trường hợp mơ hồ (nhiều Organization trùng tên chuẩn hóa / cùng tên miền) KHÔNG tự xử lý: đưa vào danh sách cho người.
- Chạy nhiều lần không tạo trùng. Mặc định `dry_run=True`: chỉ tính và in, không ghi.

Chạy thật chỉ sau khi người phụ trách duyệt dry run (xem README-v2 / báo cáo V2-02):
`bench --site <site> execute omni_crm.patches.v2.link_existing_leads_to_organizations.execute --kwargs "{'dry_run': False}"`
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm.flags import omni_bot
from omni_crm.identity.normalize import normalize_email, normalize_phone
from omni_crm.prospect import contacts as prospect_contacts
from omni_crm.prospect import stages
from omni_crm.prospect.normalize import company_name_key, corporate_domain, website_domain

ORGANIZATION = "CRM Organization"
LEAD = "CRM Lead"
CONTACT = "Contact"
DATA_SOURCE = "Chuyển đổi từ Lead hiện có"
INFO_SOURCE = "Chuyển đổi từ Lead {0}"


def _org_indexes() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
	"""(khóa tên chuẩn hóa -> Organization, tên miền -> Organization) của mọi Organization đang có."""
	by_name: dict[str, list[str]] = {}
	by_domain: dict[str, list[str]] = {}
	for o in frappe.get_all(
		ORGANIZATION,
		fields=["name", "organization_name", "website", "omni_company_email"],
		limit_page_length=0,
	):
		by_name.setdefault(company_name_key(o.organization_name or o.name), []).append(o.name)
		domains = {website_domain(o.website), corporate_domain(o.omni_company_email)} - {None}
		for d in domains:
			by_domain.setdefault(d, []).append(o.name)
	return by_name, by_domain


def _existing_contact(lead: Any) -> str | None:
	"""Contact đã có cho người của Lead: khớp email/SĐT thô (cách `contact_exists` của CRM) hoặc khóa chuẩn hóa."""
	emails = [e for e in {lead.email, normalize_email(lead.email)} if e]
	raw_phones = [p for p in {lead.mobile_no, lead.phone} if p]
	norm_phones = [p for p in (normalize_phone(x) for x in raw_phones) if p]
	if emails:
		found = frappe.db.get_value("Contact Email", {"email_id": ["in", emails]}, "parent")
		if found:
			return found
	if raw_phones:
		found = frappe.db.get_value("Contact Phone", {"phone": ["in", raw_phones]}, "parent")
		if found:
			return found
	found_rows = prospect_contacts.contacts_matching(norm_phones, [e for e in emails if "@" in e])
	return found_rows[0].name if found_rows else None


def _create_organization(name: str, *, website: str | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": ORGANIZATION,
			"organization_name": name,
			"website": website,
			"omni_verified": 0,
			"omni_data_source": DATA_SOURCE,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def _create_contact(lead: Any, org: str) -> str:
	first = lead.first_name or lead.lead_name or lead.name
	doc = frappe.get_doc(
		{
			"doctype": CONTACT,
			"first_name": first,
			"middle_name": lead.middle_name,
			"last_name": lead.last_name,
			"designation": lead.job_title,
			"company_name": org,
			"omni_info_source": INFO_SOURCE.format(lead.name),
		}
	)
	if lead.email:
		doc.append("email_ids", {"email_id": lead.email, "is_primary": 1})
	if lead.mobile_no:
		doc.append("phone_nos", {"phone": lead.mobile_no, "is_primary_mobile_no": 1})
	if lead.phone and lead.phone != lead.mobile_no:
		doc.append("phone_nos", {"phone": lead.phone, "is_primary_phone": 0 if lead.mobile_no else 1})
	doc.flags.ignore_permissions = True
	previous = frappe.flags.in_import
	frappe.flags.in_import = True  # Contact.validate gọi has_gravatar (mạng) trừ khi đang nhập: bỏ qua
	try:
		doc.insert()
	finally:
		frappe.flags.in_import = previous
	return doc.name


def run(dry_run: bool = True, create_from_domain: bool = True) -> dict[str, Any]:
	"""Liên kết Lead với Organization/Contact; `dry_run` (mặc định) chỉ tính. Trả báo cáo (xem `format_report`)."""
	by_name, by_domain = _org_indexes()
	planned: dict[
		str, str
	] = {}  # khóa tên/tên miền -> tên Organization sẽ tạo (dùng chung cho các Lead cùng khóa)
	report: dict[str, Any] = {
		"dry_run": dry_run,
		"leads_total": 0,
		"leads_to_link": 0,
		"leads_relink_spelling": 0,
		"already_linked": 0,
		"skipped_no_org_info": 0,
		"organizations_to_create": [],
		"contacts_to_create": 0,
		"contacts_existing": 0,
		"ambiguous": [],
	}
	leads = frappe.get_all(
		LEAD,
		filters={"omni_merged_into": ["is", "not set"]},
		order_by="creation asc",
		limit_page_length=0,
		pluck="name",
	)
	for name in leads:
		report["leads_total"] += 1
		lead = frappe.get_doc(LEAD, name)
		org_name = (lead.organization or "").strip()
		domain = None if org_name else corporate_domain(lead.email)
		if not org_name and not domain:
			report["skipped_no_org_info"] += 1
			continue
		key = company_name_key(org_name) if org_name else f"domain:{domain}"
		candidates = by_name.get(key, []) if org_name else by_domain.get(domain, [])
		if len(candidates) > 1:
			report["ambiguous"].append(
				{
					"lead": name,
					"nguon": org_name or domain,
					"ly_do": _("Nhiều Organization trùng tên chuẩn hóa")
					if org_name
					else _("Nhiều Organization cùng tên miền"),
					"ung_vien": candidates,
				}
			)
			continue
		if not candidates and not org_name and not create_from_domain:
			report["skipped_no_org_info"] += 1
			continue
		target = candidates[0] if candidates else planned.get(key)
		create_needed = target is None
		if create_needed:
			target = org_name or domain
			planned[key] = target
			report["organizations_to_create"].append(target)
		spelling = bool(org_name) and org_name != target and not create_needed
		if org_name == target and not create_needed and _contact_ok(lead):
			report["already_linked"] += 1
			continue
		report["leads_to_link"] += 1
		report["leads_relink_spelling"] += 1 if spelling else 0
		has_contact = _existing_contact(lead) if (lead.email or lead.mobile_no or lead.phone) else None
		need_contact = not has_contact and bool(lead.email or lead.mobile_no or lead.phone)
		report["contacts_existing"] += 1 if has_contact else 0
		report["contacts_to_create"] += 1 if need_contact else 0
		if dry_run:
			continue
		with omni_bot():
			if create_needed and not frappe.db.exists(ORGANIZATION, target):
				_create_organization(target, website=domain)
				by_name.setdefault(company_name_key(target), []).append(target)
			frappe.db.set_value(LEAD, name, "organization", target, update_modified=False)
			if need_contact:
				_create_contact(lead, target)
		frappe.db.commit()  # job nền tự quản lý transaction: một Lead là một đơn vị việc
	report["organizations_to_create"] = sorted(set(report["organizations_to_create"]))
	report["status_mapping"] = stages.mapping_table()
	return report


def _contact_ok(lead: Any) -> bool:
	"""Lead đã liên kết xong phần người liên hệ (không có thông tin liên hệ, hoặc Contact đã tồn tại)."""
	if not (lead.email or lead.mobile_no or lead.phone):
		return True
	return bool(_existing_contact(lead))


def format_report(report: dict[str, Any]) -> str:
	"""Báo cáo dạng chữ để in ra terminal / dán vào báo cáo duyệt."""
	lines = [
		f"{'DRY RUN (không ghi gì)' if report['dry_run'] else 'CHẠY THẬT'} — chuyển Lead sang mô hình nhà thầu",
		f"Lead xét (chưa bị gộp): {report['leads_total']}",
		f"  sẽ liên kết: {report['leads_to_link']} (trong đó đổi cách viết tên cho khớp Organization có sẵn: {report['leads_relink_spelling']})",
		f"  đã liên kết sẵn: {report['already_linked']}",
		f"  bỏ qua (không có tên tổ chức, không có email tên miền công ty): {report['skipped_no_org_info']}",
		f"Organization sẽ tạo: {len(report['organizations_to_create'])}",
		f"Contact sẽ tạo: {report['contacts_to_create']} (đã có Contact: {report['contacts_existing']})",
		f"Trường hợp mơ hồ (KHÔNG tự xử lý, cần người): {len(report['ambiguous'])}",
	]
	for a in report["ambiguous"]:
		lines.append(f"  - {a['lead']}: {a['nguon']} -> {a['ly_do']}: {', '.join(a['ung_vien'])}")
	lines.append("Bảng ánh xạ trạng thái ĐỀ XUẤT (KHÔNG áp dụng, chờ anh duyệt):")
	for r in report["status_mapping"]:
		lines.append(
			f"  {r['loai']:<5} {r['hien_tai']:<24} -> {r['de_xuat']:<28} [{r['so_luong']}] {r['ghi_chu']}"
		)
	return "\n".join(lines)
