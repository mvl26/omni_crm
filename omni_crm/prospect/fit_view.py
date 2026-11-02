"""Giải thích điểm phù hợp cho giao diện "Mức phù hợp" (docs/14 mục 3.1, 8): tiêu chí đạt/chưa, điểm từng phần, hạng, công thức.

Dùng đúng `fit.compute_fit` và tham số thật của Omni Settings (`fit.load_params`) nên số hiển thị khớp số ghi trên
Organization; nếu tham số hoặc ngày đã đổi từ lần tính cuối thì `stale = True` (điểm lưu khác điểm tính lại).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.prospect import fit

ORGANIZATION = "CRM Organization"


def _points(weight: Any, value: Decimal, maximum: Any) -> float:
	return float(Decimal(cint(weight)) * fit._ratio(value, maximum))


def explain(organization: str) -> dict[str, Any]:
	"""Chi tiết cách tính điểm phù hợp của một nhà thầu (đọc, không ghi)."""
	params = fit.load_params()
	live = fit.compute_fit(organization)
	stored = frappe.db.get_value(
		ORGANIZATION, organization, ["omni_fit_score", "omni_fit_tier", "omni_fit_computed_at"], as_dict=True
	)
	hospitals = live["hospital_count"]
	items = live["items_for_scoring"]
	value_bn = live["award_value_bn"]
	criteria = [
		{
			"key": "hospitals",
			"label": _("Số bệnh viện đã trúng thầu (trong kỳ, đúng loại hàng)"),
			"value": hospitals,
			"required": cint(params["fit_min_hospitals"]),
			"met": hospitals >= cint(params["fit_min_hospitals"]),
		},
		{
			"key": "items",
			"label": _("Số mã hàng (lớn nhất giữa ước tính và gói thầu)"),
			"value": items,
			"required": cint(params["fit_min_items"]),
			"met": items >= cint(params["fit_min_items"]),
		},
	]
	components = [
		{
			"key": "hospitals",
			"label": _("Số bệnh viện"),
			"value": hospitals,
			"max": params["fit_max_hospitals"],
			"weight": cint(params["fit_w_hospitals"]),
			"points": _points(params["fit_w_hospitals"], Decimal(hospitals), params["fit_max_hospitals"]),
		},
		{
			"key": "items",
			"label": _("Số mã hàng"),
			"value": items,
			"max": params["fit_max_items"],
			"weight": cint(params["fit_w_items"]),
			"points": _points(params["fit_w_items"], Decimal(items), params["fit_max_items"]),
		},
		{
			"key": "value",
			"label": _("Giá trị trúng thầu (tỷ đồng)"),
			"value": round(value_bn, 2),
			"max": params["fit_max_value_bn"],
			"weight": cint(params["fit_w_value"]),
			"points": _points(params["fit_w_value"], Decimal(str(value_bn)), params["fit_max_value_bn"]),
		},
		{
			"key": "active",
			"label": _("Còn hợp đồng đang hiệu lực"),
			"value": live["active_awards"],
			"max": 1,
			"weight": cint(params["fit_w_active"]),
			"points": float(cint(params["fit_w_active"])) if live["active_awards"] > 0 else 0.0,
		},
	]
	formula = _(
		"Điểm = {wh}·min(1, BV/{mh}) + {wi}·min(1, MH/{mi}) + {wv}·min(1, GT/{mv}) + {wa}·[còn hiệu lực]. "
		"Hạng A: đạt cả hai tiêu chí và điểm từ {tier}; hạng B: đạt tiêu chí; hạng C: chưa đạt. "
		"Chỉ tính gói thuộc: {cats}, trong {months} tháng gần nhất."
	).format(
		wh=cint(params["fit_w_hospitals"]),
		mh=params["fit_max_hospitals"],
		wi=cint(params["fit_w_items"]),
		mi=params["fit_max_items"],
		wv=cint(params["fit_w_value"]),
		mv=params["fit_max_value_bn"],
		wa=cint(params["fit_w_active"]),
		tier=cint(params["fit_tier_a"]),
		cats=", ".join(sorted(params["categories"])),
		months=cint(params["fit_period_months"]),
	)
	return {
		"organization": organization,
		"score": live["fit_score"],
		"tier": live["fit_tier"],
		"meets_criteria": bool(live["meets_criteria"]),
		"criteria": criteria,
		"components": components,
		"formula": formula,
		"params": {k: v for k, v in params.items() if k != "categories"},
		"categories": sorted(params["categories"]),
		"stored_score": stored.omni_fit_score if stored else None,
		"stored_tier": stored.omni_fit_tier if stored else None,
		"computed_at": str(stored.omni_fit_computed_at) if stored and stored.omni_fit_computed_at else None,
		"stale": bool(
			stored
			and (stored.omni_fit_score != live["fit_score"] or stored.omni_fit_tier != live["fit_tier"])
		),
		"enabled": fit.fit_enabled(),
	}
