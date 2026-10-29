"""FakeAIClient dùng cho test: trả kết quả theo kịch bản, mô phỏng lỗi, đếm token giả. Không gọi mạng."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from omni_crm.bot.ai_client import AIError, AIResponse, TransientAIError

FAULTS = ("429", "500", "529", "timeout", "bad_schema", "fatal")
CHARS_PER_TOKEN = 4


@dataclass
class Rule:
	"""Một luật kịch bản: khớp theo `kind` (None = mọi loại) và `keyword` (None = mọi hội thoại)."""

	result: dict
	kind: str | None = None
	keyword: str | None = None


def _tokens(chars: int) -> int:
	return max(1, -(-chars // CHARS_PER_TOKEN))


def minimal_valid(schema: dict) -> Any:
	"""Giá trị tối thiểu hợp lệ theo một JSON Schema đơn giản (đủ cho schema của tool bot)."""
	if "enum" in schema:
		return schema["enum"][0]
	t = schema.get("type", "object")
	if isinstance(t, list):
		t = next((x for x in t if x != "null"), t[0])
	if t == "object":
		props = schema.get("properties", {})
		return {k: minimal_valid(props.get(k, {"type": "string"})) for k in schema.get("required", [])}
	return {"string": "x", "array": [], "boolean": False, "integer": 0, "number": 0, "null": None}.get(t, "x")


@dataclass
class FakeAIClient:
	"""Client giả: hàng đợi lỗi `faults` được dùng trước, rồi tới luật `rules`, cuối cùng là kết quả tối thiểu hợp lệ."""

	rules: list[Rule] = field(default_factory=list)
	faults: list[str] = field(default_factory=list)
	calls: list[dict] = field(default_factory=list)
	is_real: bool = False
	_seen_prefixes: set[str] = field(default_factory=set)

	def add_rule(self, result: dict, kind: str | None = None, keyword: str | None = None) -> None:
		"""Thêm luật: hội thoại chứa `keyword` (không phân biệt hoa thường) thì trả `result`."""
		self.rules.append(Rule(result, kind, keyword))

	def create(
		self,
		*,
		kind: str,
		model: str,
		system: str,
		messages: list[dict],
		tool: dict,
		cache_blocks: list[str] | None = None,
		max_tokens: int = 1024,
		timeout: float = 60,
	) -> AIResponse:
		"""Mô phỏng một lần gọi Messages API (cùng chữ ký với AnthropicClient.create)."""
		self.calls.append(
			{
				"kind": kind,
				"model": model,
				"system": system,
				"messages": messages,
				"tool": tool,
				"cache_blocks": cache_blocks,
				"timeout": timeout,
			}
		)
		convo = " ".join(str(m.get("content")) for m in messages).lower()
		prefix = system + "".join(cache_blocks or [])
		prefix_hash = hashlib.sha256(prefix.encode()).hexdigest()
		cached = prefix_hash in self._seen_prefixes
		self._seen_prefixes.add(prefix_hash)
		in_chars = len(convo) + (0 if cached else len(prefix))
		cache_read = _tokens(len(prefix)) if cached else 0
		fault = self.faults.pop(0) if self.faults else None
		if fault in ("429", "500", "529"):
			raise TransientAIError(f"HTTP {fault} (giả)", status=int(fault))
		if fault == "timeout":
			raise TransientAIError("hết thời gian (giả)", status=408)
		if fault == "fatal":
			raise AIError("HTTP 400 (giả)")
		if fault == "bad_schema":
			result: dict = {"__sai_schema__": True}
		else:
			result = next(
				(
					r.result
					for r in self.rules
					if r.kind in (None, kind) and (r.keyword is None or r.keyword.lower() in convo)
				),
				None,
			) or minimal_valid(tool.get("input_schema") or {})
		return AIResponse(
			tool_input=result,
			input_tokens=_tokens(in_chars),
			output_tokens=_tokens(len(json.dumps(result, ensure_ascii=False))),
			cache_read_tokens=cache_read,
		)
