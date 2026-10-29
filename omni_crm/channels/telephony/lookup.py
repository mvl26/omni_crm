"""Tra cứu khách khi chuông reo (docs/07 mục 2.1): CHỈ ĐỌC, không ghi, không enqueue, nhắm < 300 ms.

Hai lớp giới hạn thời gian: (1) `max_statement_time` của MariaDB cho mọi truy vấn trong lượt tra cứu, nên một
truy vấn chậm bị DB hủy thay vì treo; (2) mốc thời gian kiểm giữa các bước, quá hạn thì trả khách mới.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import frappe
from frappe.utils import get_url

from omni_crm.channels.telephony.base import LookupResult
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity.normalize import normalize_phone

BUDGET_SECONDS = 0.3
STATEMENT_SECONDS = 0.25


class LookupTimeout(Exception):
	"""Quá thời hạn tra cứu."""


@contextmanager
def statement_limit(seconds: float = STATEMENT_SECONDS) -> Iterator[None]:
	"""Đặt max_statement_time cho phiên DB trong khối; MariaDB cũ/không hỗ trợ thì bỏ qua."""
	try:
		frappe.db.sql("set session max_statement_time = %s", (seconds,))
		active = True
	except Exception:
		active = False
	try:
		yield
	finally:
		if active:
			try:
				frappe.db.sql("set session max_statement_time = 0")
			except Exception:
				pass


def _check(deadline: float) -> None:
	if time.monotonic() > deadline:
		raise LookupTimeout


def _lead_of(phone: str, deadline: float) -> str | None:
	from omni_crm.identity import resolver

	identity_lead = frappe.db.get_value("Omni Identity", {"identity_key": f"Telephony:{phone}"}, "lead")
	lead = lead_bridge.follow_merged(identity_lead) if identity_lead else None
	if lead:
		return lead
	_check(deadline)
	found = resolver.find_leads([phone], [])
	return found[0] if found else None


def find_customer(number: str | None, deadline: float | None = None) -> LookupResult:
	"""LookupResult cho một số điện thoại; số sai định dạng hoặc chưa có Lead thì là khách mới."""
	deadline = deadline if deadline is not None else time.monotonic() + BUDGET_SECONDS
	phone = normalize_phone(number)
	if not phone:
		return LookupResult()
	lead = _lead_of(phone, deadline)
	if not lead:
		return LookupResult()
	_check(deadline)
	row = frappe.db.get_value(
		"CRM Lead", lead, ["name", "lead_name", "first_name", "lead_owner", "converted"], as_dict=True
	)
	if not row:
		return LookupResult()
	deal = lead_bridge.get_deal_for_lead(lead) if row.converted else None
	owner_name = frappe.db.get_value("User", row.lead_owner, "full_name") if row.lead_owner else None
	path = f"/crm/deals/{deal}" if deal else f"/crm/leads/{lead}"
	try:
		url = get_url(path)
	except Exception:
		url = path  # không dựng được địa chỉ tuyệt đối: dùng đường dẫn tương đối
	return LookupResult(
		is_new=False,
		customer_name=row.lead_name or row.first_name,
		lead=lead,
		owner=row.lead_owner,
		owner_name=owner_name or row.lead_owner,
		lead_url=url,
	)
