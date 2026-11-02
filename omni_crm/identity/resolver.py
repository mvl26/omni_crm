"""Khớp định danh với Lead (tạo, khớp, nghi trùng) theo docs/04 mục 3.

Nguyên tắc: gộp khi chắc, hỏi người khi không chắc. Tín hiệu mạnh mới được dùng để gắn Lead;
tín hiệu trung bình chỉ gắn khi duy nhất và tên HAI BÊN đều đủ thông tin và giống nhau (R1, ADR 0008); tên giống
nhau chỉ tạo nghi trùng.

V2-02 (docs/14 mục 2): trước khi tìm Lead theo SĐT/email, thử khớp NHÀ THẦU: mã số thuế (khách tự khai trong hồ sơ
kênh) -> Contact đã biết -> tên miền email công ty. Chỉ gắn Lead của Organization khi khớp ĐÚNG MỘT Organization
(ĐQ-4); mơ hồ thì không tự liên kết mà tạo Lead mới + nghi trùng với Lead của từng Organization ứng viên.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity import duplicates
from omni_crm.identity.extract import extract_contacts
from omni_crm.identity.normalize import (
	is_informative_name,
	is_system_email,
	name_similarity,
	normalize_email,
	normalize_phone,
)
from omni_crm.pipeline.locks import LockBusy, identity_lock
from omni_crm.pipeline.retry import TransientError
from omni_crm.prospect import contacts as prospect_contacts
from omni_crm.prospect.normalize import (
	InvalidTaxId,
	corporate_domain,
	normalize_tax_id,
	tax_id_base,
	website_domain,
)

IDENTITY = "Omni Identity"
LEAD = "CRM Lead"
NAME_CONFLICT_BELOW = 0.5  # docs/04 mục 3.1
NAME_DUPLICATE_FROM = 0.9  # docs/04 mục 3 bước 6
NAME_WINDOW_DAYS = 90
MAX_MEDIUM_SIGNALS = 5  # giới hạn số SĐT/email lấy từ nội dung một lô tin
SIGNAL_LOCK_TIMEOUT = 60
SIGNAL_LOCK_WAIT = 20

SCORE_STRONG_SHARED = 100
SCORE_MEDIUM_NAME_CONFLICT = 60
SCORE_MEDIUM_NAME_UNKNOWN = (
	55  # R1: một Lead trùng SĐT/email trong nội dung nhưng tên thiếu/một từ/chưa đủ giống
)
SCORE_MEDIUM_AMBIGUOUS = 50
SCORE_ORG_AMBIGUOUS = 50  # nhiều Organization cùng khớp một tín hiệu (docs/14 mục 2)
SCORE_ORG_OTHER_LEAD = 70  # định danh đã có Lead khác với Lead của Organization khớp
NAME_ATTACH_FROM = 0.5  # R1: ngưỡng độ giống tên để gắn bằng tín hiệu trung bình
ORGANIZATION = "CRM Organization"
OPEN_STATUS_TYPES = ("Open", "Ongoing", "On Hold")
PROSPECT_STATUS = "Tiềm năng"  # trạng thái Lead B2B đầu tiên (docs/14 mục 4); thiếu thì dùng mặc định của CRM


@dataclass
class Signals:
	"""Tín hiệu định danh của một lô tin: mạnh (xác thực bởi kênh) và trung bình (trích từ văn bản)."""

	strong_phones: list[str] = field(default_factory=list)
	strong_emails: list[str] = field(default_factory=list)
	medium_phones: list[str] = field(default_factory=list)
	medium_emails: list[str] = field(default_factory=list)
	tax_ids: list[str] = field(
		default_factory=list
	)  # MST khách tự khai trong hồ sơ kênh (không trích từ văn bản)
	display_name: str | None = None
	last_inbound_at: datetime | None = None

	def lock_values(self) -> list[str]:
		"""Mọi SĐT/email liên quan, đã sắp xếp (thứ tự cố định để không deadlock)."""
		return sorted(
			set(self.strong_phones + self.strong_emails + self.medium_phones + self.medium_emails)
			| {f"tax:{t}" for t in self.tax_ids}
		)


@dataclass
class Resolution:
	"""Kết quả khớp: identity, lead, và thông tin để D1-06 ghi dòng thời gian."""

	identity: str
	lead: str | None
	created_lead: bool = False
	matched_by: str = "new"  # identity | thread | org_tax | org_contact | org_domain | strong | medium | new
	deal: str | None = None  # CRM Deal nếu Lead đã chuyển (docs/04 mục 3.2)
	candidates: list[str] = field(default_factory=list)


def _add(items: list[str], value: str | None) -> None:
	if value and value not in items:
		items.append(value)


def _profile(msg: Any) -> dict:
	raw = getattr(msg, "sender_profile", None)
	if raw is None and getattr(msg, "name", None):
		raw = frappe.db.get_value("Omni Inbound Message", msg.name, "sender_profile")
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			raw = None
	return raw if isinstance(raw, dict) else {}


def collect_signals(msgs: list[Any]) -> Signals:
	"""Thu tín hiệu mạnh/trung bình từ lô tin (docs/04 mục 2); không chạm DB trừ khi thiếu sender_profile."""
	sig = Signals()
	medium_phones: list[str] = []
	medium_emails: list[str] = []
	for m in msgs:
		if getattr(m, "event_type", None) == "oa_echo":
			continue  # lời nhân viên trả lời ngoài hệ thống: không phải tín hiệu của khách (SĐT hotline...)
		profile = _profile(m)
		if m.channel_type == "Telephony":
			_add(sig.strong_phones, normalize_phone(m.external_sender_id))  # số gọi đến
		elif m.channel_type == "Email":
			email = normalize_email(m.external_sender_id)
			if email and not is_system_email(email):
				_add(sig.strong_emails, email)
		# Kênh cung cấp SĐT/email (vd form thông tin Zalo, event_type = user_info): mạnh.
		_add(sig.strong_phones, normalize_phone(profile.get("phone") or profile.get("mobile")))
		email = normalize_email(profile.get("email"))
		if email and not is_system_email(email):
			_add(sig.strong_emails, email)
		try:  # MST chỉ lấy từ hồ sơ có cấu trúc (form kênh), không bao giờ từ văn bản: 10 số dễ lẫn với SĐT
			tax = normalize_tax_id(profile.get("tax_id"))
		except InvalidTaxId:
			tax = None
		_add(sig.tax_ids, tax)
		name = (profile.get("display_name") or profile.get("name") or "").strip()
		if name:
			sig.display_name = name
		found = extract_contacts(m.text)
		# Chữ ký email (adapter email tách riêng, không để trong text): cũng là tín hiệu trung bình.
		found["phones"] = found["phones"] + [
			p for p in (normalize_phone(x) for x in profile.get("signature_phones") or []) if p
		]
		found["emails"] = found["emails"] + [
			e for e in (normalize_email(x) for x in profile.get("signature_emails") or []) if e
		]
		for p in found["phones"]:
			_add(medium_phones, p)
		for e in found["emails"]:
			_add(medium_emails, e)
		if m.received_at and (not sig.last_inbound_at or m.received_at > sig.last_inbound_at):
			sig.last_inbound_at = m.received_at
	sig.medium_phones = [p for p in medium_phones if p not in sig.strong_phones][:MAX_MEDIUM_SIGNALS]
	sig.medium_emails = [e for e in medium_emails if e not in sig.strong_emails][:MAX_MEDIUM_SIGNALS]
	return sig


MAX_THREAD_HOPS = 5


def thread_lead(msgs: list[Any]) -> str | None:
	"""Lead (chính) của chuỗi thư mà các thư Email trong lô trả lời; None nếu không có.

	Đi từ `Communication.in_reply_to` (tên Communication cha, do Frappe đặt khi nhận thư) lên các thư cha:
	cha trỏ Lead/Deal, hoặc cha là thư đã được gắn Lead trong Omni Inbound Message -> tín hiệu chắc chắn.
	"""
	for m in msgs:
		if m.channel_type != "Email":
			continue
		comm = getattr(m, "communication", None) or frappe.db.get_value(
			"Omni Inbound Message", m.name, "communication"
		)
		seen: set[str] = set()
		for _hop in range(MAX_THREAD_HOPS):
			row = (
				frappe.db.get_value(
					"Communication",
					comm,
					["in_reply_to", "reference_doctype", "reference_name"],
					as_dict=True,
				)
				if comm
				else None
			)
			if not row or not row.in_reply_to or row.in_reply_to in seen:
				break
			seen.add(row.in_reply_to)
			parent = frappe.db.get_value(
				"Communication",
				row.in_reply_to,
				["reference_doctype", "reference_name"],
				as_dict=True,
			)
			found = _lead_of_reference(parent.reference_doctype, parent.reference_name) if parent else None
			if not found:
				found = frappe.db.get_value(
					"Omni Inbound Message", {"communication": row.in_reply_to, "lead": ["is", "set"]}, "lead"
				)
				found = lead_bridge.follow_merged(found) if found else None
			if found:
				return found
			comm = row.in_reply_to
	return None


def _lead_of_reference(doctype: str | None, name: str | None) -> str | None:
	if doctype == "CRM Deal" and name:
		return lead_bridge.follow_merged(frappe.db.get_value("CRM Deal", name, "lead"))
	if doctype == LEAD and name:
		return lead_bridge.follow_merged(name)
	return None


def _get_or_create_identity(first: Any, sig: Signals) -> Any:
	"""Bước 1: lấy/tạo Omni Identity, cập nhật tên và SĐT/email mạnh còn trống."""
	name = frappe.db.get_value(IDENTITY, {"identity_key": first.identity_key}, "name")
	if name:
		doc = frappe.get_doc(IDENTITY, name)
	else:
		doc = frappe.get_doc(
			{
				"doctype": IDENTITY,
				"identity_key": first.identity_key,
				"channel_type": first.channel_type,
				"external_id": first.identity_key.split(":", 1)[-1],
			}
		)
	changed = not doc.name  # chưa có tên = bản ghi mới
	if sig.display_name and doc.display_name != sig.display_name:
		doc.display_name, changed = sig.display_name, True
	if sig.strong_phones and not doc.phone_e164:
		doc.phone_e164, changed = sig.strong_phones[0], True
	if sig.strong_emails and not doc.email_norm:
		doc.email_norm, changed = sig.strong_emails[0], True
	if changed:
		doc.flags.ignore_permissions = True
		if not doc.name:
			doc.insert()
		else:
			doc.save()
	return doc


def _activity_key(lead: str) -> tuple:
	row = frappe.db.get_value(LEAD, lead, ["omni_last_inbound_at", "modified"], as_dict=True)
	return (row.omni_last_inbound_at or row.modified, row.modified)


def find_leads(phones: list[str], emails: list[str], exclude_identity: str | None = None) -> list[str]:
	"""Các Lead chính (đã đi theo omni_merged_into) khớp SĐT/email, hoạt động gần nhất trước."""
	or_filters: list[list] = []
	if phones:
		or_filters.append(["omni_phone_e164", "in", phones])
	if emails:
		or_filters.append(["omni_email_norm", "in", emails])
	if not or_filters:
		return []
	found: set[str] = set()
	rows = frappe.get_all(
		LEAD,
		filters={"omni_merged_into": ["is", "not set"]},
		or_filters=or_filters,
		pluck="name",
		limit_page_length=100,
	)
	found.update(rows)
	id_or: list[list] = []
	if phones:
		id_or.append(["phone_e164", "in", phones])
	if emails:
		id_or.append(["email_norm", "in", emails])
	id_filters: dict = {"lead": ["is", "set"]}
	if exclude_identity:
		id_filters["name"] = ["!=", exclude_identity]
	for lead in frappe.get_all(
		IDENTITY, filters=id_filters, or_filters=id_or, pluck="lead", limit_page_length=100
	):
		primary = lead_bridge.follow_merged(lead)
		if primary:
			found.add(primary)
	return sorted(found, key=_activity_key, reverse=True)


def _name_conflict(name_a: str | None, name_b: str | None) -> bool:
	"""Tên mâu thuẫn: cả hai đủ thông tin (>= 2 từ riêng) và độ giống < 0.5 (docs/04 mục 3.1)."""
	return (
		is_informative_name(name_a)
		and is_informative_name(name_b)
		and name_similarity(name_a, name_b) < NAME_CONFLICT_BELOW
	)


def _names_compatible(name_a: str | None, name_b: str | None) -> bool:
	"""R1: cả hai tên đủ thông tin (>= 2 từ riêng) và độ giống >= 0,5 — điều kiện để gắn bằng tín hiệu trung bình."""
	return (
		is_informative_name(name_a)
		and is_informative_name(name_b)
		and name_similarity(name_a, name_b) >= NAME_ATTACH_FROM
	)


def _flag_name_duplicates(lead: str) -> list[str]:
	"""Bước 6: Lead khác có tên giống >= 0.9 trong 90 ngày -> nghi trùng (không gộp)."""
	name = frappe.db.get_value(LEAD, lead, "lead_name")
	if not is_informative_name(name):
		return []
	cutoff = add_days(now_datetime(), -NAME_WINDOW_DAYS)
	rows = frappe.get_all(
		LEAD,
		filters={"omni_merged_into": ["is", "not set"], "name": ["!=", lead]},
		or_filters=[["creation", ">=", cutoff], ["omni_last_inbound_at", ">=", cutoff]],
		fields=["name", "lead_name"],
		limit_page_length=2000,
	)
	created: list[str] = []
	for r in rows:
		score = name_similarity(name, r.lead_name)
		if score >= NAME_DUPLICATE_FROM and is_informative_name(r.lead_name):
			cand = duplicates.create_candidate(
				lead,
				r.name,
				_("Tên hiển thị giống nhau ({0}), chưa đủ căn cứ để gộp").format(name),
				int(round(score * 100)),
			)
			if cand:
				created.append(cand)
	return created


@dataclass
class OrgMatch:
	"""Kết quả khớp nhà thầu: `org` khi khớp đúng một; `ambiguous` là các Organization ứng viên khi khớp nhiều."""

	org: str | None = None
	via: str | None = None  # tax | contact | domain
	ambiguous: list[str] = field(default_factory=list)


def _existing_orgs(names: set[str]) -> list[str]:
	"""Giữ lại tên Organization còn tồn tại (Contact.company_name là Data tự do), sắp xếp để kết quả ổn định."""
	if not names:
		return []
	return sorted(frappe.get_all(ORGANIZATION, filters={"name": ["in", sorted(names)]}, pluck="name"))


def _orgs_by_tax_id(tax_ids: list[str]) -> list[str]:
	"""Organization có đúng mã số thuế; MST 10 số khớp thêm các chi nhánh `<10 số>-xxx` (nhiều hơn một -> mơ hồ)."""
	found: set[str] = set()
	for tax in tax_ids:
		rows = frappe.get_all(ORGANIZATION, filters={"omni_tax_id": tax}, pluck="name")
		if not rows and "-" not in tax:
			rows = frappe.get_all(ORGANIZATION, filters={"omni_tax_id": ["like", f"{tax}-%"]}, pluck="name")
		if not rows and "-" in tax:  # khách nêu MST chi nhánh, hệ thống chỉ có trụ sở
			rows = frappe.get_all(ORGANIZATION, filters={"omni_tax_id": tax_id_base(tax)}, pluck="name")
		found.update(rows)
	return sorted(found)


def _orgs_by_contacts(phones: list[str], emails: list[str]) -> list[str]:
	return _existing_orgs({c.company_name for c in prospect_contacts.contacts_matching(phones, emails)})


def _orgs_by_domain(emails: list[str]) -> list[str]:
	"""Organization có website hoặc email công ty cùng tên miền công ty với email (bỏ tên miền thư miễn phí)."""
	found: set[str] = set()
	for domain in {d for d in (corporate_domain(e) for e in emails) if d}:
		rows = frappe.get_all(
			ORGANIZATION,
			or_filters=[["website", "like", f"%{domain}%"], ["omni_company_email", "like", f"%@{domain}%"]],
			fields=["name", "website", "omni_company_email"],
			limit_page_length=200,
		)
		for r in rows:  # LIKE chỉ lọc thô; so khớp chính xác tên miền ở đây
			mail_domain = (r.omni_company_email or "").rsplit("@", 1)[-1].strip().lower()
			if website_domain(r.website) == domain or mail_domain == domain:
				found.add(r.name)
	return sorted(found)


def match_organization(sig: Signals) -> OrgMatch:
	"""Thứ tự docs/14 mục 2: MST -> Contact đã biết -> tên miền email công ty. Bước đầu tiên có ứng viên quyết định.

	Một Organization -> khớp; nhiều -> mơ hồ (KHÔNG tự liên kết, ĐQ-4). Contact chỉ tính theo SĐT/email MẠNH
	(do kênh xác thực); SĐT/email trong nội dung (trung bình) không dùng ở bước này.
	"""
	steps = (
		("tax", lambda: _orgs_by_tax_id(sig.tax_ids)),
		("contact", lambda: _orgs_by_contacts(sig.strong_phones, sig.strong_emails)),
		("domain", lambda: _orgs_by_domain(sig.strong_emails)),
	)
	for via, finder in steps:
		orgs = finder()
		if len(orgs) == 1:
			return OrgMatch(orgs[0], via)
		if len(orgs) > 1:
			return OrgMatch(None, via, orgs)
	return OrgMatch()


def _lead_status_types(leads: list[str]) -> dict[str, str]:
	if not leads:
		return {}
	rows = frappe.db.sql(
		"""select l.name, s.type from `tabCRM Lead` l left join `tabCRM Lead Status` s on s.name = l.status
		where l.name in %s""",
		(tuple(leads),),
	)
	return dict(rows)


def organization_leads(org: str) -> list[str]:
	"""Lead chính (chưa bị gộp) của Organization: đang mở trước, rồi đã chuyển Deal (Won); hoạt động gần nhất trước.
	Lead đã mất (Lost) không trả về."""
	names = frappe.get_all(
		LEAD,
		filters={"organization": org, "omni_merged_into": ["is", "not set"]},
		pluck="name",
		limit_page_length=200,
	)
	types = _lead_status_types(names)
	rank = {t: 0 for t in OPEN_STATUS_TYPES} | {"Won": 1}
	usable = [n for n in names if types.get(n) in rank]
	return sorted(usable, key=lambda n: (rank[types[n]], tuple(-x.timestamp() for x in _activity_key(n))))


def _lead_for_organization(org: str, identity: Any, sig: Signals, name: str | None) -> tuple[str, bool]:
	"""Lead đang mở của Organization (tạo Lead Tiềm năng mới nếu chưa có); trả (lead, đã_tạo)."""
	existing = organization_leads(org)
	if existing:
		return existing[0], False
	lead = lead_bridge.create_lead_for_identity(
		identity.name,
		{
			"phone": sig.strong_phones[0] if sig.strong_phones else None,
			"email": sig.strong_emails[0] if sig.strong_emails else None,
			"display_name": name,
			"last_inbound_at": sig.last_inbound_at,
		},
	)
	values: dict[str, Any] = {"organization": org}
	if frappe.db.exists("CRM Lead Status", PROSPECT_STATUS):
		values["status"] = PROSPECT_STATUS
	frappe.db.set_value(LEAD, lead, values, update_modified=False)
	return lead, True


def _flag_org_ambiguity(lead: str, orgs: list[str], via: str, out: list[str]) -> None:
	"""Mơ hồ: nghi trùng giữa Lead mới và Lead của từng Organization ứng viên + ghi chú liệt kê, cho người xử lý."""
	label = {"tax": _("mã số thuế"), "contact": _("người liên hệ"), "domain": _("tên miền email")}[via]
	for org in orgs:
		others = organization_leads(org)
		if others:
			_flag_shared(
				lead,
				[others[0]],
				_("Khớp nhiều nhà thầu theo {0} ({1}): chưa tự liên kết").format(label, ", ".join(orgs)),
				SCORE_ORG_AMBIGUOUS,
				out,
			)
	frappe.get_doc(LEAD, lead).add_comment(
		"Comment", _("Khớp nhiều nhà thầu theo {0}, chưa tự liên kết: {1}").format(label, ", ".join(orgs))
	)


def _flag_shared(lead: str, others: list[str], reason: str, score: int, out: list[str]) -> None:
	for other in others:
		cand = duplicates.create_candidate(lead, other, reason, score)
		if cand:
			out.append(cand)


def _resolve_locked(msgs: list[Any], sig: Signals) -> Resolution:
	first = msgs[0]
	identity = _get_or_create_identity(first, sig)
	res = Resolution(identity=identity.name, lead=None)
	identity_name = sig.display_name or identity.display_name
	thread = thread_lead(msgs)

	# Bước 2: đã gắn Lead (đi theo Lead đã gộp)
	lead = lead_bridge.follow_merged(identity.lead) if identity.lead else None
	if not lead and not thread and all(getattr(m, "event_type", None) == "oa_echo" for m in msgs):
		# 04.08 (ADR 0008): lô chỉ có tin nhân viên trả lời ngoài hệ thống (oa_echo) và khách chưa từng nhắn -> không có
		# tín hiệu nào của khách: ghi Identity, không tạo Lead rỗng tên. Tin được ghi khi khách nhắn (lô sau).
		frappe.db.commit()
		return res
	if lead:
		res.matched_by = "identity"
		org = match_organization(sig)
		if org.org and frappe.db.get_value(LEAD, lead, "organization") != org.org:
			# Định danh đã gắn Lead khác với Lead của nhà thầu khớp: KHÔNG gắn lại (đổi hành vi với khách), chỉ nghi trùng.
			_flag_shared(
				lead,
				organization_leads(org.org)[:1],
				_("Cùng nhà thầu «{0}» (khớp theo {1}) nhưng thuộc Lead khác").format(org.org, org.via),
				SCORE_ORG_OTHER_LEAD,
				res.candidates,
			)
		others = [x for x in find_leads(sig.strong_phones, sig.strong_emails, identity.name) if x != lead]
		_flag_shared(
			lead,
			others,
			_("Cùng SĐT/email do kênh cung cấp, nhưng thuộc Lead khác"),
			SCORE_STRONG_SHARED,
			res.candidates,
		)
		if thread and thread != lead:
			_flag_shared(
				lead,
				[thread],
				_("Thư trả lời trong chuỗi thư của Lead khác"),
				SCORE_STRONG_SHARED,
				res.candidates,
			)
		lead_bridge.fill_blank_contact(
			lead,
			sig.strong_phones[0] if sig.strong_phones else None,
			sig.strong_emails[0] if sig.strong_emails else None,
		)
	else:
		# Bước 3-5
		strong = find_leads(sig.strong_phones, sig.strong_emails, identity.name)
		org_match = OrgMatch() if thread else match_organization(sig)
		if thread:  # thư trả lời trong chuỗi đã gắn Lead: tín hiệu chắc chắn, đứng trước SĐT/email
			lead, res.matched_by = thread, "thread"
			_flag_shared(
				lead,
				[x for x in strong if x != lead],
				_("Cùng SĐT/email do kênh cung cấp, nhưng thuộc Lead khác"),
				SCORE_STRONG_SHARED,
				res.candidates,
			)
			lead_bridge.fill_blank_contact(
				lead,
				sig.strong_phones[0] if sig.strong_phones else None,
				sig.strong_emails[0] if sig.strong_emails else None,
			)
		elif org_match.org:
			# V2-02: nhà thầu khớp đúng một -> Lead đang mở của nhà thầu (tạo Lead Tiềm năng nếu chưa có)
			lead, res.created_lead = _lead_for_organization(org_match.org, identity, sig, identity_name)
			res.matched_by = f"org_{org_match.via}"
			_flag_shared(
				lead,
				[x for x in strong if x != lead],
				_("Cùng SĐT/email do kênh cung cấp, nhưng thuộc Lead khác"),
				SCORE_STRONG_SHARED,
				res.candidates,
			)
			lead_bridge.fill_blank_contact(
				lead,
				sig.strong_phones[0] if sig.strong_phones else None,
				sig.strong_emails[0] if sig.strong_emails else None,
			)
		elif strong:
			lead, res.matched_by = strong[0], "strong"  # gần nhất; Lead còn lại chỉ thành nghi trùng
			_flag_shared(
				lead,
				strong[1:],
				_("Nhiều Lead cùng SĐT/email do kênh cung cấp"),
				SCORE_STRONG_SHARED,
				res.candidates,
			)
			lead_bridge.fill_blank_contact(
				lead,
				sig.strong_phones[0] if sig.strong_phones else None,
				sig.strong_emails[0] if sig.strong_emails else None,
			)
		else:
			medium = find_leads(sig.medium_phones, sig.medium_emails, identity.name)
			medium_name = frappe.db.get_value(LEAD, medium[0], "lead_name") if len(medium) == 1 else None
			# R1 (ADR 0008): gắn bằng tín hiệu trung bình chỉ khi tên HAI BÊN đủ thông tin và giống nhau (>= 0,5)
			if len(medium) == 1 and _names_compatible(identity_name, medium_name):
				lead, res.matched_by = medium[0], "medium"
			else:
				lead = lead_bridge.create_lead_for_identity(
					identity.name,
					{
						"phone": sig.strong_phones[0] if sig.strong_phones else None,
						"email": sig.strong_emails[0] if sig.strong_emails else None,
						"display_name": identity_name,
						"last_inbound_at": sig.last_inbound_at,
					},
				)
				res.created_lead = True
				if len(medium) == 1 and _name_conflict(identity_name, medium_name):
					reason, score = (
						_("SĐT/email trong nội dung tin trùng một Lead nhưng tên khác hẳn"),
						SCORE_MEDIUM_NAME_CONFLICT,
					)
				elif len(medium) == 1:
					reason, score = (
						_(
							"SĐT/email trong nội dung tin trùng một Lead nhưng tên thiếu hoặc chưa đủ giống để gộp"
						),
						SCORE_MEDIUM_NAME_UNKNOWN,
					)
				else:
					reason, score = (
						_("SĐT/email trong nội dung tin trùng nhiều Lead"),
						SCORE_MEDIUM_AMBIGUOUS,
					)
				_flag_shared(lead, medium, reason, score, res.candidates)
				if org_match.ambiguous:
					_flag_org_ambiguity(lead, org_match.ambiguous, org_match.via or "", res.candidates)

	# Bước 6: tên giống
	res.candidates += _flag_name_duplicates(lead)

	# Bước 7: gắn identity, cập nhật hoạt động
	res.lead = lead
	if identity.lead != lead:
		frappe.db.set_value(IDENTITY, identity.name, "lead", lead)
	if sig.last_inbound_at:
		cur = frappe.db.get_value(LEAD, lead, "omni_last_inbound_at")
		if not cur or sig.last_inbound_at > cur:
			frappe.db.set_value(
				LEAD, lead, "omni_last_inbound_at", sig.last_inbound_at, update_modified=False
			)
	res.deal = lead_bridge.get_deal_for_lead(lead)
	frappe.db.commit()
	return res


def resolve_full(msgs: list[Any]) -> Resolution:
	"""Khớp lô tin của một khách với Lead (docs/04 mục 3), trả Resolution đầy đủ.

	Các SĐT/email liên quan được khóa (Redis) trong lúc tìm + tạo Lead + commit, để hai định danh khác
	nhau cùng SĐT chạy song song không tạo hai Lead không được nối.
	"""
	sig = collect_signals(msgs)
	with ExitStack() as stack:
		try:
			for value in sig.lock_values():
				stack.enter_context(
					identity_lock(f"sig:{value}", timeout=SIGNAL_LOCK_TIMEOUT, wait=SIGNAL_LOCK_WAIT)
				)
		except LockBusy:
			raise TransientError("Khóa tín hiệu định danh đang bận") from None
		frappe.db.commit()  # kết thúc transaction cũ -> snapshot mới, thấy Lead do lượt khác vừa commit
		return _resolve_locked(msgs, sig)


def resolve(msgs: list[Any]) -> tuple[str, str | None]:
	"""Trả (tên Omni Identity, tên Lead) cho lô tin của một khách."""
	res = resolve_full(msgs)
	return res.identity, res.lead
