"""Khớp định danh với Lead (tạo, khớp, nghi trùng) theo docs/04 mục 3.

Nguyên tắc: gộp khi chắc, hỏi người khi không chắc. Tín hiệu mạnh mới được dùng để gắn Lead;
tín hiệu trung bình chỉ gắn khi duy nhất và tên không mâu thuẫn; tên giống nhau chỉ tạo nghi trùng.
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
SCORE_MEDIUM_AMBIGUOUS = 50


@dataclass
class Signals:
	"""Tín hiệu định danh của một lô tin: mạnh (xác thực bởi kênh) và trung bình (trích từ văn bản)."""

	strong_phones: list[str] = field(default_factory=list)
	strong_emails: list[str] = field(default_factory=list)
	medium_phones: list[str] = field(default_factory=list)
	medium_emails: list[str] = field(default_factory=list)
	display_name: str | None = None
	last_inbound_at: datetime | None = None

	def lock_values(self) -> list[str]:
		"""Mọi SĐT/email liên quan, đã sắp xếp (thứ tự cố định để không deadlock)."""
		return sorted(set(self.strong_phones + self.strong_emails + self.medium_phones + self.medium_emails))


@dataclass
class Resolution:
	"""Kết quả khớp: identity, lead, và thông tin để D1-06 ghi dòng thời gian."""

	identity: str
	lead: str | None
	created_lead: bool = False
	matched_by: str = "new"  # identity | strong | medium | new
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
		name = (profile.get("display_name") or profile.get("name") or "").strip()
		if name:
			sig.display_name = name
		found = extract_contacts(m.text)
		for p in found["phones"]:
			_add(medium_phones, p)
		for e in found["emails"]:
			_add(medium_emails, e)
		if m.received_at and (not sig.last_inbound_at or m.received_at > sig.last_inbound_at):
			sig.last_inbound_at = m.received_at
	sig.medium_phones = [p for p in medium_phones if p not in sig.strong_phones][:MAX_MEDIUM_SIGNALS]
	sig.medium_emails = [e for e in medium_emails if e not in sig.strong_emails][:MAX_MEDIUM_SIGNALS]
	return sig


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

	# Bước 2: đã gắn Lead (đi theo Lead đã gộp)
	lead = lead_bridge.follow_merged(identity.lead) if identity.lead else None
	if lead:
		res.matched_by = "identity"
		others = [x for x in find_leads(sig.strong_phones, sig.strong_emails, identity.name) if x != lead]
		_flag_shared(
			lead,
			others,
			_("Cùng SĐT/email do kênh cung cấp, nhưng thuộc Lead khác"),
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
		if strong:
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
			if len(medium) == 1 and not _name_conflict(
				identity_name, frappe.db.get_value(LEAD, medium[0], "lead_name")
			):
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
				if len(medium) == 1:
					reason, score = (
						_("SĐT/email trong nội dung tin trùng một Lead nhưng tên khác hẳn"),
						SCORE_MEDIUM_NAME_CONFLICT,
					)
				else:
					reason, score = (
						_("SĐT/email trong nội dung tin trùng nhiều Lead"),
						SCORE_MEDIUM_AMBIGUOUS,
					)
				_flag_shared(lead, medium, reason, score, res.candidates)

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
