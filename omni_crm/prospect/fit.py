# ruff: noqa: RUF001
"""Điểm phù hợp (fit) của nhà thầu — tính bằng code, công thức giống mẫu Excel (docs/14 mục 3.1).

Không dùng AI. Số học theo kiểu Excel: làm tròn `Decimal` ROUND_HALF_UP (không dùng `round()` của Python), cộng
tháng bằng `relativedelta` (giống EDATE: ngày 31 cộng 1 tháng về ngày cuối tháng). `active_awards` chỉ lọc theo loại
hàng, KHÔNG lọc theo kỳ xét (giống cột R của sheet GoiThau).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import frappe
from dateutil.relativedelta import relativedelta
from frappe.utils import cint, flt, getdate, now_datetime

from omni_crm.flags import omni_bot

ORGANIZATION = "CRM Organization"
AWARD = "Omni Bid Award"
SETTINGS = "Omni Settings"
BILLION = Decimal(1_000_000_000)

# Mặc định giống sheet ThamSo của mẫu Excel; dùng khi Omni Settings chưa có giá trị (site migrate từ bản cũ).
FIT_DEFAULTS: dict[str, Any] = {
	"fit_period_months": 24,
	"fit_min_hospitals": 5,
	"fit_min_items": 200,
	"fit_max_hospitals": 10,
	"fit_max_items": 500,
	"fit_max_value_bn": 20,
	"fit_w_hospitals": 40,
	"fit_w_items": 30,
	"fit_w_value": 20,
	"fit_w_active": 10,
	"fit_tier_a": 70,
	"fit_categories": "VTYT tiêu hao\nHóa chất – sinh phẩm",
}
RESULT_FIELDS = {
	"awards_in_period": "omni_awards_in_period",
	"hospital_count": "omni_hospital_count",
	"max_items": "omni_max_items",
	"items_for_scoring": "omni_items_for_scoring",
	"award_value_bn": "omni_award_value_bn",
	"active_awards": "omni_active_awards",
	"meets_criteria": "omni_meets_criteria",
	"fit_score": "omni_fit_score",
	"fit_tier": "omni_fit_tier",
}


def _stored(keys: list[str]) -> dict[str, Any]:
	"""Giá trị đã LƯU của các trường Omni Settings (trường chưa từng lưu — site migrate từ bản cũ — không có mặt).

	`get_single_value` trả 0/"" cho trường chưa lưu nên không phân biệt được "chưa đặt" với 0; đọc `tabSingles`.
	"""
	rows = frappe.db.sql(
		"select field, value from `tabSingles` where doctype = %s and field in %s",
		(SETTINGS, tuple(keys)),
	)
	return dict(rows)


def fit_enabled() -> bool:
	"""Công tắc `fit_scoring_enabled`; chưa từng lưu (site cũ) coi là bật, đọc lỗi coi là tắt (an toàn)."""
	try:
		stored = _stored(["fit_scoring_enabled"])
	except Exception:
		return False
	value = stored.get("fit_scoring_enabled")
	return True if value is None else bool(cint(value))


def load_params() -> dict[str, Any]:
	"""Tham số chấm điểm từ Omni Settings; trường chưa lưu hoặc rỗng rơi về mặc định (FIT_DEFAULTS)."""
	stored = _stored(list(FIT_DEFAULTS))
	params: dict[str, Any] = {}
	for key, default in FIT_DEFAULTS.items():
		value = stored.get(key)
		params[key] = default if value in (None, "") else value
	params["categories"] = {x.strip() for x in str(params.pop("fit_categories")).splitlines() if x.strip()}
	return params


def excel_round(value: Decimal) -> int:
	"""Làm tròn nửa lên như hàm ROUND của Excel (số dương)."""
	return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _ratio(value: Decimal, maximum: Any) -> Decimal:
	maximum = Decimal(str(maximum))
	if maximum <= 0:
		return Decimal(1)
	return min(Decimal(1), value / maximum)


def fit_from_awards(
	awards: Iterable[Any], est_item_count: int, params: dict[str, Any], today: date | None = None
) -> dict[str, Any]:
	"""Tính điểm phù hợp từ danh sách gói thầu (dict/đối tượng có category, award_date, hospital_key, ...)."""
	today = getdate(today) if today else getdate()
	start = today - relativedelta(months=cint(params["fit_period_months"]))
	categories = params["categories"]
	hospitals: set[str] = set()
	count = max_items = active = 0
	value = Decimal(0)
	for a in awards:
		if a.get("category") not in categories:
			continue
		end = getdate(a.get("end_date")) if a.get("end_date") else None
		if end and end >= today:
			active += 1  # không lọc theo kỳ (giống mẫu Excel)
		if not a.get("award_date") or getdate(a.get("award_date")) < start:
			continue
		count += 1
		hospitals.add(a.get("hospital_key") or "")
		max_items = max(max_items, cint(a.get("item_count")))
		value += Decimal(str(flt(a.get("award_value"))))
	items = max(cint(est_item_count), max_items)
	value_bn = value / BILLION
	meets = len(hospitals) >= cint(params["fit_min_hospitals"]) and items >= cint(params["fit_min_items"])
	raw = (
		Decimal(cint(params["fit_w_hospitals"]))
		* _ratio(Decimal(len(hospitals)), params["fit_max_hospitals"])
		+ Decimal(cint(params["fit_w_items"])) * _ratio(Decimal(items), params["fit_max_items"])
		+ Decimal(cint(params["fit_w_value"])) * _ratio(value_bn, params["fit_max_value_bn"])
		+ Decimal(cint(params["fit_w_active"])) * (1 if active > 0 else 0)
	)
	score = excel_round(raw)
	tier = ("A" if score >= cint(params["fit_tier_a"]) else "B") if meets else "C"
	return {
		"awards_in_period": count,
		"hospital_count": len(hospitals),
		"max_items": max_items,
		"items_for_scoring": items,
		"award_value_bn": float(value_bn),
		"active_awards": active,
		"meets_criteria": 1 if meets else 0,
		"fit_score": score,
		"fit_tier": tier,
	}


def compute_fit(organization: str, today: date | None = None) -> dict[str, Any]:
	"""Điểm phù hợp của một CRM Organization (không ghi DB); dùng `apply_fit` để ghi."""
	est = frappe.db.get_value(ORGANIZATION, organization, "omni_est_item_count") or 0
	awards = frappe.get_all(
		AWARD,
		filters={"organization": organization},
		fields=["category", "award_date", "end_date", "hospital_key", "item_count", "award_value"],
		limit_page_length=0,
	)
	return fit_from_awards(awards, est, load_params(), today)


def apply_fit(organization: str, today: date | None = None) -> dict[str, Any] | None:
	"""Tính và ghi các trường chỉ đọc của Organization; công tắc tắt thì không làm gì (trả None).

	Ghi bằng `db.set_value` (không qua `save`) nên không kích hoạt lại hook của Organization.
	"""
	if not fit_enabled() or not frappe.db.exists(ORGANIZATION, organization):
		return None
	result = compute_fit(organization, today)
	values = {RESULT_FIELDS[k]: v for k, v in result.items()}
	values["omni_fit_computed_at"] = now_datetime()
	with omni_bot():
		frappe.db.set_value(ORGANIZATION, organization, values, update_modified=False)
	return result


def on_award_change(doc: Any, method: str | None = None) -> None:
	"""Hook Omni Bid Award (thêm/sửa/xóa): tính lại điểm của nhà thầu (và nhà thầu cũ nếu đổi). Nhập hàng loạt đặt
	`frappe.flags.omni_defer_fit = True` rồi tự gọi `apply_fit` một lần cho mỗi nhà thầu."""
	if frappe.flags.get("omni_defer_fit"):
		return
	orgs = {doc.organization}
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	if before and before.organization != doc.organization:
		orgs.add(before.organization)
	for org in orgs:
		if org:
			apply_fit(org)


def on_organization_update(doc: Any, method: str | None = None) -> None:
	"""Hook CRM Organization.on_update: số mã hàng ước tính đổi (hoặc Organization mới) thì tính lại điểm."""
	if frappe.flags.get("omni_defer_fit"):
		return
	if doc.flags.get("in_insert") or doc.has_value_changed("omni_est_item_count"):
		apply_fit(doc.name)


def recompute_all_fit(batch: int = 200) -> dict[str, int]:
	"""Tác vụ đêm: tính lại điểm mọi nhà thầu có gói thầu hoặc đã có điểm (ngày tính đổi nên điểm có thể đổi)."""
	if not fit_enabled():
		return {"organizations": 0, "skipped": 1}
	names = set(frappe.get_all(AWARD, pluck="organization", distinct=True, limit_page_length=0))
	names.update(
		frappe.get_all(
			ORGANIZATION, filters={"omni_fit_computed_at": ["is", "set"]}, pluck="name", limit_page_length=0
		)
	)
	done = 0
	for name in sorted(n for n in names if n):
		apply_fit(name)
		done += 1
		if done % batch == 0:
			frappe.db.commit()  # job nền tự commit sau mỗi lô
	frappe.db.commit()
	return {"organizations": done, "skipped": 0}
