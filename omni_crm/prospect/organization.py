"""Hook của CRM Organization (docs/14 mục 1.1, 2.1): chuẩn hóa và bảo đảm duy nhất mã số thuế."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm.prospect.normalize import InvalidTaxId, normalize_tax_id

ORGANIZATION = "CRM Organization"


def organization_by_tax_id(tax_id: str | None, exclude: str | None = None) -> str | None:
	"""Tên CRM Organization có đúng mã số thuế đã chuẩn hóa này (None nếu không có)."""
	if not tax_id:
		return None
	filters: dict[str, Any] = {"omni_tax_id": tax_id}
	if exclude:
		filters["name"] = ["!=", exclude]
	return frappe.db.get_value(ORGANIZATION, filters, "name")


def validate(doc: Any, method: str | None = None) -> None:
	"""Hook `CRM Organization.validate`: chuẩn hóa `omni_tax_id` (rỗng -> NULL) và chặn trùng.

	Không đặt bắt buộc: CRM tự tạo Organization khi chuyển Lead -> Deal mà không có mã số thuế. Ràng buộc duy nhất
	thật nằm ở chỉ mục UNIQUE của MariaDB (NULL không đụng nhau); kiểm tra ở đây chỉ để báo lỗi dễ hiểu.
	"""
	try:
		tax_id = normalize_tax_id(doc.get("omni_tax_id"))
	except InvalidTaxId:
		frappe.throw(
			_("Mã số thuế «{0}» không hợp lệ: phải gồm 10 số, hoặc 10 số-3 số (chi nhánh).").format(
				doc.get("omni_tax_id")
			),
			title=_("Mã số thuế sai định dạng"),
		)
	doc.omni_tax_id = tax_id
	other = organization_by_tax_id(tax_id, exclude=doc.name if not doc.is_new() else None)
	if other:
		frappe.throw(
			_("Mã số thuế {0} đã thuộc nhà thầu «{1}». Mở nhà thầu đó thay vì tạo mới.").format(
				tax_id, other
			),
			frappe.DuplicateEntryError,
			title=_("Trùng mã số thuế"),
		)
