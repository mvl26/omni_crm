"""Client giả "Oracle" để kiểm hạ tầng bộ chấm OFFLINE: trả đúng kỳ vọng của ca đang chạy.

KẾT QUẢ VỚI CLIENT NÀY KHÔNG ĐO BOT. Nó chỉ chứng minh đường ống (dựng ngữ cảnh -> chốt chặn -> giám khảo -> tính
chỉ số -> ghi Omni Eval Run -> báo cáo) chạy thông. Không gọi mạng.
"""

from __future__ import annotations

from typing import Any

from omni_crm.bot.ai_client import AIResponse
from omni_crm.bot.fake_client import FakeAIClient
from omni_crm.identity.normalize import normalize_phone

EVIDENCE_MAX = 200


class OracleClient(FakeAIClient):
	"""FakeAIClient biết ca đang chạy (`prepare(case, items)`) và trả đáp án theo `expected_*` của ca."""

	case: dict | None = None
	items: list[dict] | None = None

	def prepare(self, case: dict, items: list[dict]) -> None:
		"""Run gọi trước mỗi ca."""
		self.case, self.items = case, items

	def _analyze(self) -> dict:
		case = self.case or {}
		expected = case.get("expected_fields") or {}
		turns = [t["text"] for t in case.get("conversation", []) if t.get("role") == "customer"]
		quote = (turns[-1] if turns else "")[:EVIDENCE_MAX]
		out: dict[str, Any] = {
			"intent": expected.get("intent", "Khác"),
			"wants_human": bool(expected.get("wants_human", False)),
			"summary": "Tóm tắt thử nghiệm.",
			"phones": [],
			"emails": [],
			"products": [],
			"evidence": {},
		}
		for key in ("customer_name", "organization", "quantity", "timeframe", "need", "urgency"):
			if expected.get(key):
				out[key] = expected[key]
				out["evidence"][key] = quote
		for key in ("phones", "emails", "products"):
			if expected.get(key):
				out[key] = (
					[normalize_phone(x) or x for x in expected[key]] if key == "phones" else expected[key]
				)
				out["evidence"][key] = quote
		return out

	def _reply(self) -> dict:
		case = self.case or {}
		group = case.get("expected_group") or "Khác"
		if case.get("expected_action") == "Reply":
			ref = case.get("reference_answer") or ""
			ids = [i["id"] for i in self.items or [] if i["answer"] == ref]
			return {
				"action": "reply",
				"question_group": group,
				"reply_text": ref,
				"kb_item_ids": ids,
				"confidence": 0.9,
			}
		return {
			"action": "handoff",
			"question_group": group,
			"kb_item_ids": [],
			"confidence": 0.9,
			"handoff_reason": "Cần nhân viên xử lý.",
		}

	def create(
		self, *, kind: str, model: str, system: str, messages: list[dict], tool: dict, **kw
	) -> AIResponse:
		"""Cùng chữ ký với AnthropicClient.create; ghi lại lời gọi như FakeAIClient."""
		base = super().create(kind=kind, model=model, system=system, messages=messages, tool=tool, **kw)
		if kind == "Analyze":
			result = self._analyze()
		elif kind == "Reply":
			result = self._reply()
		else:
			result = {
				**{
					k: True
					for k in ("r1_correct", "r2_complete", "r3_within_authority", "r4_style", "r5_safe")
				},
				"pass": True,
				"critical_failure": False,
				"reason": "Oracle",
			}
		base.tool_input = result
		return base
