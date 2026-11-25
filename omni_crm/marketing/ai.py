"""Lớp gọi AI của Marketing Hub (V3-10, ADR 0029) trên cổng chung `bot.ai_client.call_tool_result`.

- Ngân sách USD/tháng (`monthly_ai_budget`, 0 = tắt): tổng `cost_usd` các Bot Run loại Marketing trong tháng + ước
  lượng trần của lần gọi sắp tới không được vượt ngân sách.
- Mô hình dự phòng: mô hình chính trả Error / Invalid Output thì gọi lại một lần bằng `ai_fallback_model`.
- Không phải dữ liệu khách (brief, tài liệu nội bộ) nên `customer_data=False` (docs/12 "Đã xác nhận").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, nowdate

from omni_crm.bot import ai_client, pricing
from omni_crm.marketing import settings as mk_settings

KIND = "Marketing"
PROMPT_VERSION = "mk-v1"
FALLBACK_ON = ("Error", "Invalid Output")


@dataclass
class AIResult:
	"""Kết quả sau cả mô hình chính và dự phòng: `attempts` là danh sách (mô hình, trạng thái, Bot Run, USD)."""

	output: dict | None
	status: str
	error: str = ""
	model: str = ""
	run: str | None = None
	attempts: list[dict] = field(default_factory=list)

	@property
	def cost_usd(self) -> float:
		return round(sum(a["cost_usd"] for a in self.attempts), 6)


def spent_this_month() -> float:
	"""Tổng chi phí USD các lần gọi AI marketing từ đầu tháng."""
	row = frappe.db.sql(
		"select coalesce(sum(cost_usd), 0) from `tabOmni Bot Run` where kind = %s and creation >= %s",
		(KIND, get_first_day(nowdate())),
	)
	return flt(row[0][0]) if row else 0.0


def budget_state() -> dict[str, Any]:
	"""{budget, spent, remaining} theo USD cho giao diện."""
	budget = mk_settings.monthly_budget_usd()
	spent = spent_this_month()
	return {"budget": budget, "spent": round(spent, 4), "remaining": round(max(budget - spent, 0), 4)}


def budget_gate(model: str):
	"""Hàm chặn cho `call_tool_result`: trả lý do (tiếng Việt) nếu lần gọi ~estimate token làm vượt ngân sách tháng."""

	def gate(estimate_tokens: int) -> str | None:
		budget = mk_settings.monthly_budget_usd()
		if budget <= 0:
			return _("Chưa đặt ngân sách AI marketing (USD/tháng) trong Thiết lập")
		spent = spent_this_month()
		need = pricing.estimate_usd(model, estimate_tokens)
		if spent + need > budget:
			return _("Ngân sách AI tháng này: đã dùng {0} / {1} USD, lần gọi cần tới {2} USD").format(
				round(spent, 2), round(budget, 2), round(need, 2)
			)
		return None

	return gate


def call(
	purpose: str,
	system: str,
	messages: list[dict],
	tool: dict,
	*,
	cache_blocks: list[str] | None = None,
	max_tokens: int = 4000,
	check: Any = None,
	repairs: int = 2,
	timeout: float = 120,
	batch_id: str | None = None,
) -> AIResult:
	"""Gọi AI marketing: mô hình chính, lỗi thì mô hình dự phòng; mỗi lần gọi một Omni Bot Run có chi phí."""
	main, fallback = mk_settings.ai_models()
	models = [main] + ([fallback] if fallback and fallback != main else [])
	attempts: list[dict] = []
	res = None
	for model in models:
		res = ai_client.call_tool_result(
			KIND,
			model,
			system,
			messages,
			tool,
			batch_id=batch_id or purpose,
			cache_blocks=cache_blocks,
			prompt_version=f"{PROMPT_VERSION}-{purpose}",
			max_tokens=max_tokens,
			customer_data=False,
			budget_gate=budget_gate(model),
			schema_retries=repairs,
			repair=True,
			check=check,
			timeout=timeout,
		)
		attempts.append({"model": model, "status": res.status, "run": res.run, "cost_usd": res.cost_usd})
		if res.status not in FALLBACK_ON:
			break
	assert res is not None
	return AIResult(res.output, res.status, res.error, attempts[-1]["model"], res.run, attempts)


def user_message(result: AIResult) -> str:
	"""Câu báo lỗi cho người dùng theo trạng thái (không lộ chi tiết kỹ thuật/bí mật)."""
	if result.status == "Success":
		return ""
	if result.status == "Skipped Budget":
		return result.error or _("Đã hết ngân sách AI tháng này")
	if result.status == "Skipped Switch":
		return result.error or _("AI marketing đang tắt")
	if result.status == "Invalid Output":
		return _("AI trả kết quả chưa đúng định dạng sau nhiều lần sửa, hãy thử lại hoặc mô tả rõ hơn")
	return _("AI đang lỗi (đã thử cả mô hình dự phòng), hãy thử lại sau")
