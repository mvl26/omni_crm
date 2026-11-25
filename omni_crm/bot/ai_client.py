"""Client gọi Claude API: công cụ ép buộc, kiểm schema, thử lại, hạn mức token/ngày và nhật ký Omni Bot Run.

Mọi lần gọi (kể cả bị bỏ qua) ghi một `Omni Bot Run` không chứa toàn văn hội thoại. Khóa API chỉ đọc bằng
`get_password()` ở lúc tạo client, không đi vào log, Bot Run hay lời nhắc. Tài liệu API: docs/12 mục C (G12).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import frappe
from frappe import _
from frappe.utils import cint, today
from jsonschema import Draft202012Validator

from omni_crm import flags
from omni_crm.bot import pricing
from omni_crm.monitoring.alerts import Alert, deliver_now
from omni_crm.pipeline.retry import redact

BOT_RUN = "Omni Bot Run"
DEFAULT_TIMEOUT_SECONDS = 60  # docs/08 mục 1 (cấu hình được: site_config omni_ai_timeout_seconds)
TRANSIENT_RETRIES = 2  # thử lại tối đa 2 lần khi lỗi tạm thời
SCHEMA_RETRIES = 1  # thử lại 1 lần khi sai schema
BACKOFF_SECONDS = (2, 6)  # chờ tăng dần trước lần thử lại thứ 1, 2
DEFAULT_MAX_TOKENS = 1024
ERROR_MAX_CHARS = 500
COUNTER_TTL_SECONDS = 2 * 24 * 3600
ALLOW_DATA_CONF = "omni_ai_allow_customer_data"  # site_config: chỉ bật sau khi chủ dự án chốt Q11
BREAKER_FAILURES = 5  # số lần Error liên tiếp thì ngắt mạch
BREAKER_OPEN_SECONDS = 300  # thời gian ngắt: các lần gọi trong lúc này bị bỏ qua, không tốn thử lại
# Theo tài liệu (G12, docs/12 mục C) các mô hình này trả HTTP 400 khi ép tool_choice any/tool: dùng "auto"
NO_FORCED_TOOL_PREFIXES = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-mythos-5-1")
SETTINGS_PROMPT_FIELD = {
	"Analyze": "analyze_prompt_version",
	"Reply": "reply_prompt_version",
	"Summarize": "summarize_prompt_version",
}
CHAT_RETRIES = 1  # trợ lý nội bộ (V3-05): chạy trong request nên chỉ thử lại 1 lần, chờ ngắn
CHAT_BACKOFF_SECONDS = 1

_client_override: AIClient | None = None


class AIError(Exception):
	"""Lỗi gọi AI không thể thử lại (400/401/403/404, thiếu khóa, …)."""


class TransientAIError(AIError):
	"""Lỗi tạm thời đáng thử lại: 429, 5xx, 529 quá tải, hết thời gian, mất kết nối."""

	def __init__(self, message: str, status: int | None = None):
		super().__init__(message)
		self.status = status


@dataclass
class AIResponse:
	"""Kết quả một lần gọi: đầu vào của tool (None nếu mô hình không gọi tool) và số token."""

	tool_input: dict | None
	input_tokens: int = 0
	output_tokens: int = 0
	cache_read_tokens: int = 0
	cache_creation_tokens: int = 0
	stop_reason: str = ""


@dataclass
class ChatResponse:
	"""Kết quả một lượt hội thoại nhiều tool (trợ lý nội bộ): khối nội dung dạng dict (text / tool_use) và số token."""

	content: list[dict]
	stop_reason: str = ""
	input_tokens: int = 0
	output_tokens: int = 0
	cache_read_tokens: int = 0


class AIClient(Protocol):
	"""Giao diện chung của client thật và FakeAIClient."""

	is_real: bool

	def create(
		self,
		*,
		kind: str,
		model: str,
		system: str,
		messages: list[dict],
		tool: dict,
		cache_blocks: list[str] | None,
		max_tokens: int,
		timeout: float,
	) -> AIResponse: ...


class AnthropicClient:
	"""Bọc SDK `anthropic`; SDK không tự thử lại (max_retries=0) để vòng thử lại ở call_tool kiểm soát."""

	is_real = True

	def __init__(self, api_key: str):
		self._api_key = api_key

	def create(
		self,
		*,
		kind: str,
		model: str,
		system: str,
		messages: list[dict],
		tool: dict,
		cache_blocks: list[str] | None,
		max_tokens: int,
		timeout: float,
	) -> AIResponse:
		"""Một lần gọi Messages API với tool_choice ép vào `tool` và cache_control cho phần cố định."""
		import anthropic

		client = anthropic.Anthropic(api_key=self._api_key, timeout=timeout, max_retries=0)
		system_blocks: list[dict] = [{"type": "text", "text": system}]
		for text in cache_blocks or []:
			system_blocks.append({"type": "text", "text": text})
		# Điểm cache ở cuối phần cố định (system + kho tri thức); tools được đưa vào tiền tố cache tự động
		system_blocks[-1]["cache_control"] = {"type": "ephemeral"}
		try:
			resp = client.messages.create(
				model=model,
				max_tokens=max_tokens,
				system=system_blocks,
				messages=messages,
				tools=[tool],
				tool_choice=tool_choice_for(model, tool),
			)
		except anthropic.APITimeoutError as e:
			raise TransientAIError(self._clean(e), status=408) from None
		except anthropic.APIConnectionError as e:
			raise TransientAIError(self._clean(e)) from None
		except anthropic.APIStatusError as e:
			status = int(e.status_code)
			if status in (408, 409, 429) or status >= 500:
				raise TransientAIError(self._clean(e), status=status) from None
			raise AIError(f"HTTP {status}: {self._clean(e)}") from None
		tool_input = next(
			(b.input for b in resp.content if b.type == "tool_use" and b.name == tool["name"]), None
		)
		usage = resp.usage
		return AIResponse(
			tool_input=tool_input if isinstance(tool_input, dict) else None,
			input_tokens=cint(usage.input_tokens),
			output_tokens=cint(usage.output_tokens),
			cache_read_tokens=cint(getattr(usage, "cache_read_input_tokens", 0)),
			cache_creation_tokens=cint(getattr(usage, "cache_creation_input_tokens", 0)),
			stop_reason=str(getattr(resp, "stop_reason", "") or ""),
		)

	def converse(
		self,
		*,
		model: str,
		system: str,
		messages: list[dict],
		tools: list[dict],
		max_tokens: int,
		timeout: float,
		tool_choice: dict | None = None,
	) -> ChatResponse:
		"""Một lượt Messages API cho trợ lý nội bộ: nhiều tool, mô hình tự chọn (auto) hoặc cấm tool ({"type": "none"})."""
		import anthropic

		client = anthropic.Anthropic(api_key=self._api_key, timeout=timeout, max_retries=0)
		system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
		try:
			resp = client.messages.create(
				model=model,
				max_tokens=max_tokens,
				system=system_blocks,
				messages=messages,
				tools=tools,
				tool_choice=tool_choice or {"type": "auto"},
			)
		except anthropic.APITimeoutError as e:
			raise TransientAIError(self._clean(e), status=408) from None
		except anthropic.APIConnectionError as e:
			raise TransientAIError(self._clean(e)) from None
		except anthropic.APIStatusError as e:
			status = int(e.status_code)
			if status in (408, 409, 429) or status >= 500:
				raise TransientAIError(self._clean(e), status=status) from None
			raise AIError(f"HTTP {status}: {self._clean(e)}") from None
		content: list[dict] = []
		for b in resp.content:
			if b.type == "text":
				content.append({"type": "text", "text": b.text})
			elif b.type == "tool_use":
				content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
		usage = resp.usage
		return ChatResponse(
			content=content,
			stop_reason=str(resp.stop_reason or ""),
			input_tokens=cint(usage.input_tokens),
			output_tokens=cint(usage.output_tokens),
			cache_read_tokens=cint(getattr(usage, "cache_read_input_tokens", 0)),
		)

	def _clean(self, exc: BaseException) -> str:
		text = str(exc)
		if self._api_key:
			text = text.replace(self._api_key, "[đã che]")
		return redact(text)[:ERROR_MAX_CHARS]


def tool_choice_for(model: str, tool: dict) -> dict:
	"""tool_choice ép vào `tool`; mô hình không hỗ trợ ép thì "auto" (đầu ra vẫn được kiểm schema, thiếu thì thử lại)."""
	if model.startswith(NO_FORCED_TOOL_PREFIXES):
		return {"type": "auto"}
	return {"type": "tool", "name": tool["name"]}


def set_client(client: AIClient | None) -> None:
	"""Đặt client thay thế (FakeAIClient trong test); None để quay lại client thật."""
	global _client_override
	_client_override = client


def get_client() -> AIClient | None:
	"""Client đang dùng: client đặt tay, hoặc AnthropicClient từ khóa trong Omni Settings; None nếu chưa có khóa."""
	if _client_override is not None:
		return _client_override
	key = frappe.get_cached_doc("Omni Settings").get_password("ai_api_key", raise_exception=False)
	return AnthropicClient(key) if key else None


def timeout_seconds() -> float:
	"""Timeout mỗi lần gọi AI (giây): site_config `omni_ai_timeout_seconds`, mặc định 60 theo docs/08."""
	try:
		value = float(frappe.conf.get("omni_ai_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
	except (TypeError, ValueError):
		value = DEFAULT_TIMEOUT_SECONDS
	return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


# --- Hạn mức token theo ngày -------------------------------------------------------------------------------


def _counter_key() -> str:
	return frappe.cache().make_key(f"omni:ai:tokens:{today()}")


def _tokens_from_db() -> int:
	row = frappe.db.sql(
		f"select coalesce(sum(input_tokens + output_tokens), 0) from `tab{BOT_RUN}` "
		"where creation >= %s and kind != 'Marketing'",  # Marketing có ngân sách USD/tháng riêng
		today(),
	)
	return cint(row[0][0]) if row else 0


def tokens_today() -> int:
	"""Token đã dùng hôm nay (vào + ra): bộ đếm Redis theo ngày, khởi tạo lại từ tổng Bot Run khi cache trống."""
	try:
		cache = frappe.cache()
		key = _counter_key()
		value = cache.get(key)
		if value is None:
			cache.set(key, _tokens_from_db(), nx=True, ex=COUNTER_TTL_SECONDS)
			value = cache.get(key)
		return cint(value)
	except Exception:
		return _tokens_from_db()


def _add_tokens(n: int) -> None:
	"""Cộng (hoặc trừ, khi hiệu chỉnh giữ chỗ) n token vào bộ đếm hôm nay."""
	if n == 0:
		return
	try:
		tokens_today()  # bảo đảm khóa đã khởi tạo từ DB trước khi cộng
		frappe.cache().incrby(_counter_key(), n)
	except Exception:
		pass


def estimate_tokens(
	system: str, messages: list[dict], cache_blocks: list[str] | None, max_tokens: int
) -> int:
	"""Ước lượng thô số token một lần gọi có thể tốn (đầu vào ~ ký tự/3 + max_tokens), để giữ chỗ trước khi gọi."""
	chars = (
		len(system)
		+ sum(len(x) for x in cache_blocks or [])
		+ sum(_content_chars(m.get("content")) for m in messages)
	)
	return chars // 3 + 1 + max_tokens


def _reserve(n: int) -> int:
	"""Giữ chỗ n token; trả số đã dùng ngay trước khi giữ (gồm phần giữ chỗ của các lần gọi đang chạy)."""
	tokens_today()
	try:
		return cint(frappe.cache().incrby(_counter_key(), n)) - n
	except Exception:
		return tokens_today()


def _alert_budget_once(used: int, budget: int) -> None:
	"""Cảnh báo vượt hạn mức, tối đa 1 lần mỗi ngày."""
	try:
		cache = frappe.cache()
		first = cache.set(
			cache.make_key(f"omni:ai:budget_alert:{today()}"), 1, nx=True, ex=COUNTER_TTL_SECONDS
		)
	except Exception:
		first = True
	if first:
		deliver_now(
			Alert(
				"ai_het_han_muc",
				today(),
				_("Bot AI hết hạn mức token hôm nay"),
				_("Đã dùng {0} token, hạn mức ngày {1}. Bot tạm dừng đến hôm sau.").format(used, budget),
			)
		)


# --- Ngắt mạch: nhà cung cấp lỗi liên tục thì tạm ngừng gọi -----------------------------------------------


def _breaker_keys() -> tuple[str, str]:
	cache = frappe.cache()
	return cache.make_key("omni:ai:fail_streak"), cache.make_key("omni:ai:breaker_open")


def breaker_open() -> bool:
	"""Ngắt mạch đang mở (vừa có nhiều lần lỗi liên tiếp) không; Redis lỗi thì coi là đóng."""
	try:
		return bool(frappe.cache().get(_breaker_keys()[1]))
	except Exception:
		return False


def _breaker_record(ok: bool) -> None:
	try:
		cache = frappe.cache()
		streak_key, open_key = _breaker_keys()
		if ok:
			cache.delete(streak_key)
			return
		streak = cint(cache.incr(streak_key))
		cache.expire(streak_key, BREAKER_OPEN_SECONDS * 2)
		if streak >= BREAKER_FAILURES:
			cache.set(open_key, 1, ex=BREAKER_OPEN_SECONDS)
			cache.delete(streak_key)
	except Exception:
		pass


# --- Công tắc, kiểm schema, ghi nhật ký -------------------------------------------------------------------


def switch_on(kind: str) -> bool:
	"""Công tắc tương ứng với loại việc đang bật không (kiểm ngay trước khi gọi)."""
	if kind == "Analyze":
		return flags.bot_analyze_enabled()
	if kind == "Reply":
		return flags.bot_reply_mode() != "Off"
	if kind == "Summarize":
		return flags.recording_enabled()
	if kind == "Assistant":
		return flags.assistant_enabled()
	if kind == "Marketing":
		from omni_crm.marketing import settings as mk_settings

		return mk_settings.hub_enabled()
	return kind == "Judge"  # bộ đề: chạy tay, chỉ chịu hạn mức token


def validate_output(data: Any, schema: dict) -> str | None:
	"""Kiểm đầu ra theo JSON Schema; trả mô tả lỗi ngắn (đường dẫn + luật, không chứa giá trị) hoặc None nếu đúng."""
	if not isinstance(data, dict):
		return "đầu ra không phải đối tượng"
	errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
	if not errors:
		return None
	e = errors[0]
	path = ".".join(str(p) for p in e.absolute_path) or "(gốc)"
	return f"{path}: vi phạm '{e.validator}'"


def _content_chars(content: Any) -> int:
	if isinstance(content, str):
		return len(content)
	if isinstance(content, list):
		return sum(len(b.get("text", "")) if isinstance(b, dict) else len(str(b)) for b in content)
	return 0


def _digest(batch_id: str | None, system: str, messages: list[dict], cache_blocks: list[str] | None) -> str:
	chars = sum(_content_chars(m.get("content")) for m in messages)
	return (
		f"batch={batch_id or '-'}; luot={len(messages)}; ky_tu={chars}; "
		f"ky_tu_system={len(system)}; khoi_cache={len(cache_blocks or [])}"
	)


@dataclass
class _Outcome:
	status: str = "Error"
	output: dict | None = None
	error: str = ""
	input_tokens: int = 0
	output_tokens: int = 0
	cache_read_tokens: int = 0
	cache_creation_tokens: int = 0
	started: float = field(default_factory=time.monotonic)


@dataclass
class ToolResult:
	"""Kết quả `call_tool_result`: `output` None khi bị bỏ qua/lỗi; `status` theo Omni Bot Run; `run` là tên Bot Run."""

	output: dict | None
	status: str
	error: str = ""
	run: str | None = None
	cost_usd: float = 0.0


def _record(kind: str, model: str, outcome: _Outcome, **meta: Any) -> str:
	settings = frappe.get_cached_doc("Omni Settings")
	doc = frappe.get_doc(
		{
			"doctype": BOT_RUN,
			"kind": kind,
			"status": outcome.status,
			"model": model,
			"lead": meta.get("lead"),
			"identity": meta.get("identity"),
			"batch_id": meta.get("batch_id"),
			"prompt_version": meta.get("prompt_version")
			or settings.get(SETTINGS_PROMPT_FIELD.get(kind, ""))
			or None,
			"kb_version": cint(settings.get("kb_version")),
			"input_tokens": outcome.input_tokens,
			"output_tokens": outcome.output_tokens,
			"cache_read_tokens": outcome.cache_read_tokens,
			"cost_usd": pricing.cost_usd(
				model,
				outcome.input_tokens,
				outcome.output_tokens,
				outcome.cache_read_tokens,
				outcome.cache_creation_tokens,
			),
			"latency_ms": int((time.monotonic() - outcome.started) * 1000),
			"request_digest": meta.get("digest"),
			"output_json": json.dumps(outcome.output, ensure_ascii=False) if outcome.output else None,
			"error": redact(outcome.error)[:ERROR_MAX_CHARS] or None,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()  # một lần gọi = một đơn vị việc
	return doc.name


def _sleep(seconds: float) -> None:
	time.sleep(seconds)


def call_tool(
	kind: str, model: str, system: str, messages: list[dict], tool: dict, **kwargs: Any
) -> dict | None:
	"""Như `call_tool_result` nhưng chỉ trả đầu vào tool đã kiểm schema (None nếu bị bỏ qua hoặc lỗi)."""
	return call_tool_result(kind, model, system, messages, tool, **kwargs).output


def _with_repair_note(messages: list[dict], problem: str) -> list[dict]:
	"""Bản sao hội thoại, thêm vào lượt user cuối lời nhắc sửa đầu ra (mô tả lỗi không chứa dữ liệu khách)."""
	note = (
		"Lần trả lời trước chưa hợp lệ: "
		+ problem
		+ ". Hãy gọi lại công cụ với dữ liệu đúng schema và đúng các ràng buộc đã nêu."
	)
	out = [dict(m) for m in messages]
	last = out[-1]
	content = last.get("content")
	blocks = list(content) if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
	blocks.append({"type": "text", "text": note})
	last["content"] = blocks
	return out


def call_tool_result(
	kind: str,
	model: str,
	system: str,
	messages: list[dict],
	tool: dict,
	*,
	lead: str | None = None,
	identity: str | None = None,
	batch_id: str | None = None,
	cache_blocks: list[str] | None = None,
	prompt_version: str | None = None,
	max_tokens: int = DEFAULT_MAX_TOKENS,
	synthetic: bool = False,
	eval_mode: bool = False,
	customer_data: bool = True,
	budget_gate: Any = None,
	schema_retries: int = SCHEMA_RETRIES,
	repair: bool = False,
	check: Any = None,
	timeout: float | None = None,
) -> ToolResult:
	"""Gọi AI ép dùng `tool`, trả đầu vào tool đã kiểm schema; None nếu bị bỏ qua hoặc lỗi (đã ghi Omni Bot Run).

	Bỏ qua (Skipped Switch) khi công tắc tắt, thiếu khóa, hoặc dữ liệu khách chưa được cho phép gửi sang AI (Q11;
	`synthetic=True` cho hội thoại tổng hợp). `eval_mode=True` (chỉ `bot/eval/run.py`): bộ đề chạy tay trước khi bật
	bot nên không đòi công tắc Analyze/Reply; cổng Q11, hạn mức token và ngắt mạch vẫn áp dụng.
	Bỏ qua (Skipped Budget) khi vượt `daily_token_budget`. Thử lại tối đa 2 lần khi lỗi tạm thời và 1 lần khi sai
	schema.

	Tham số cho Marketing Hub (V3-10, Prompt 4):
	- `customer_data=False`: nội dung gửi đi không phải dữ liệu khách (brief chiến dịch, tài liệu nội bộ) nên không
	  chịu cổng Q11 (docs/12 mục "Đã xác nhận"). Mặc định True: mọi chỗ cũ giữ nguyên hành vi.
	- `budget_gate(estimate_tokens) -> str | None`: thay hạn mức token/ngày bằng hạn mức riêng (ngân sách USD/tháng);
	  trả lý do khi phải chặn.
	- `schema_retries`, `repair=True`: số lần sửa đầu ra sai; khi `repair` mỗi lần sửa gửi kèm mô tả lỗi cho mô hình.
	- `check(output) -> str | None`: kiểm thêm ngoài JSON Schema (ràng buộc nghiệp vụ); lỗi thì tính như sai schema.
	- `timeout`: timeout riêng mỗi lần gọi (giây).
	Mô hình từ chối (`stop_reason == "refusal"`) ghi Error, không thử lại (bên gọi tự chuyển mô hình dự phòng).
	"""
	outcome = _Outcome()
	meta = {
		"lead": lead,
		"identity": identity,
		"batch_id": batch_id,
		"prompt_version": prompt_version,
		"digest": _digest(batch_id, system, messages, cache_blocks),
	}

	reserved = 0  # token đã giữ chỗ trong bộ đếm; finish hiệu chỉnh về số thật

	def finish(status: str, error: str = "", output: dict | None = None) -> ToolResult:
		nonlocal reserved
		outcome.status, outcome.error, outcome.output = status, error, output
		if budget_gate is None:  # lần gọi có hạn mức riêng không tính vào bộ đếm token/ngày của bot
			_add_tokens(outcome.input_tokens + outcome.output_tokens - reserved)
		reserved = 0
		run = None
		try:
			run = _record(kind, model, outcome, **meta)
		except Exception as e:  # lỗi ghi nhật ký không được làm mất kết quả AI đã trả
			try:
				frappe.db.rollback()
				frappe.log_error(
					title="Omni: không ghi được Omni Bot Run",
					message=redact(f"{type(e).__name__}: {e}")[:ERROR_MAX_CHARS],
				)
				frappe.db.commit()
			except Exception:
				pass
		cost = pricing.cost_usd(
			model,
			outcome.input_tokens,
			outcome.output_tokens,
			outcome.cache_read_tokens,
			outcome.cache_creation_tokens,
		)
		return ToolResult(output, status, error, run, cost)

	if not eval_mode and not switch_on(kind):
		return finish("Skipped Switch", _("Công tắc tương ứng đang tắt"))
	estimate = estimate_tokens(system, messages, cache_blocks, max_tokens)
	if budget_gate is not None:
		blocked = budget_gate(estimate)
		if blocked:
			return finish("Skipped Budget", blocked)
		budget = 0
	else:
		budget = cint(frappe.get_cached_doc("Omni Settings").get("daily_token_budget"))
		used = tokens_today()
	if budget_gate is None and (budget <= 0 or used >= budget):
		if budget > 0:
			_alert_budget_once(used, budget)
		return finish("Skipped Budget", _("Đã dùng {0} / hạn mức {1} token").format(used, budget))
	client = get_client()
	if client is None:
		return finish("Skipped Switch", _("Chưa cấu hình khóa Claude API"))
	if client.is_real and customer_data and not synthetic and not cint(frappe.conf.get(ALLOW_DATA_CONF)):
		return finish("Skipped Switch", _("Chính sách gửi dữ liệu khách sang AI (Q11) chưa được chốt"))

	if breaker_open():
		return finish("Skipped Switch", _("Ngắt mạch: AI lỗi liên tiếp, tạm ngừng gọi"))
	# Giữ chỗ token ước lượng để các lần gọi song song thấy nhau; hạn mức vẫn là mềm (có thể vượt chút ít)
	before = _reserve(estimate) if budget_gate is None else 0
	reserved = estimate if budget_gate is None else 0
	if budget_gate is None and before >= budget:
		_add_tokens(-reserved)
		reserved = 0
		_alert_budget_once(before, budget)
		return finish("Skipped Budget", _("Đã dùng {0} / hạn mức {1} token").format(before, budget))
	frappe.db.commit()  # nhả khóa DB trước khi gọi API ngoài
	transient_left, schema_left = TRANSIENT_RETRIES, schema_retries
	attempt_transient = 0
	send = messages
	while True:
		try:
			resp = client.create(
				kind=kind,
				model=model,
				system=system,
				messages=send,
				tool=tool,
				cache_blocks=cache_blocks,
				max_tokens=max_tokens,
				timeout=timeout or timeout_seconds(),
			)
		except TransientAIError as e:
			if transient_left > 0:
				_sleep(BACKOFF_SECONDS[min(attempt_transient, len(BACKOFF_SECONDS) - 1)])
				attempt_transient += 1
				transient_left -= 1
				continue
			_breaker_record(ok=False)
			return finish("Error", f"lỗi tạm thời sau {TRANSIENT_RETRIES} lần thử lại: {redact(str(e))}")
		except AIError as e:
			_breaker_record(ok=False)
			return finish("Error", redact(str(e)))
		except Exception as e:
			_breaker_record(ok=False)
			return finish("Error", f"{type(e).__name__}: {redact(str(e))}")
		_breaker_record(ok=True)
		outcome.input_tokens += resp.input_tokens
		outcome.output_tokens += resp.output_tokens
		outcome.cache_read_tokens += resp.cache_read_tokens
		outcome.cache_creation_tokens += getattr(resp, "cache_creation_tokens", 0) or 0
		if getattr(resp, "stop_reason", "") == "refusal":
			return finish("Error", "mô hình từ chối yêu cầu (refusal)")
		problem = validate_output(resp.tool_input, tool.get("input_schema") or {"type": "object"})
		if problem is None and check is not None:
			problem = check(resp.tool_input)
		if problem is None:
			return finish("Success", output=resp.tool_input)
		if schema_left > 0:
			schema_left -= 1
			if repair:
				send = _with_repair_note(messages, problem)
			continue
		return finish("Invalid Output", problem)


# --- Trợ lý nội bộ (V3-05): một lượt hội thoại nhiều tool -------------------------------------------------------


@dataclass
class ChatOutcome:
	"""Kết quả `call_chat`: `response` None khi bị bỏ qua hoặc lỗi; `error` là thông điệp đã lọc bí mật."""

	response: ChatResponse | None
	status: str
	error: str = ""


def call_chat(
	model: str,
	system: str,
	messages: list[dict],
	tools: list[dict],
	*,
	lead: str | None = None,
	batch_id: str | None = None,
	max_tokens: int = DEFAULT_MAX_TOKENS,
	timeout: float = DEFAULT_TIMEOUT_SECONDS,
	tool_choice: dict | None = None,
) -> ChatOutcome:
	"""Một lượt gọi mô hình cho trợ lý nội bộ, qua đúng các cổng của `call_tool`: công tắc, khóa, Q11, ngắt mạch.

	Hạn mức token riêng của trợ lý do người gọi kiểm trước (`assistant/loop.py`); token vẫn cộng vào bộ đếm chung.
	Ghi một Omni Bot Run kind=Assistant (không chứa nội dung hội thoại). Thử lại 1 lần khi lỗi tạm thời.
	"""
	outcome = _Outcome()
	meta = {"lead": lead, "batch_id": batch_id, "digest": _digest(batch_id, system, messages, None)}

	def finish(status: str, error: str = "", response: ChatResponse | None = None) -> ChatOutcome:
		outcome.status, outcome.error = status, error
		outcome.output = {"stop_reason": response.stop_reason} if response else None
		_add_tokens(outcome.input_tokens + outcome.output_tokens)
		try:
			_record("Assistant", model, outcome, **meta)
		except Exception as e:
			try:
				frappe.db.rollback()
				frappe.log_error(
					title="Omni: không ghi được Omni Bot Run",
					message=redact(f"{type(e).__name__}: {e}")[:ERROR_MAX_CHARS],
				)
				frappe.db.commit()
			except Exception:
				pass
		return ChatOutcome(response, status, error)

	if not switch_on("Assistant"):
		return finish("Skipped Switch", _("Trợ lý AI đang tắt"))
	client = get_client()
	if client is None:
		return finish("Skipped Switch", _("Chưa cấu hình khóa Claude API"))
	if client.is_real and not cint(frappe.conf.get(ALLOW_DATA_CONF)):
		return finish("Skipped Switch", _("Chính sách gửi dữ liệu khách sang AI (Q11) chưa được chốt"))
	if breaker_open():
		return finish("Skipped Switch", _("Ngắt mạch: AI lỗi liên tiếp, tạm ngừng gọi"))
	frappe.db.commit()  # nhả khóa DB trước khi gọi API ngoài
	retries = CHAT_RETRIES
	while True:
		try:
			resp = client.converse(
				model=model,
				system=system,
				messages=messages,
				tools=tools,
				max_tokens=max_tokens,
				timeout=timeout,
				tool_choice=tool_choice,
			)
		except TransientAIError as e:
			if retries > 0:
				retries -= 1
				_sleep(CHAT_BACKOFF_SECONDS)
				continue
			_breaker_record(ok=False)
			return finish("Error", redact(str(e)))
		except AIError as e:
			_breaker_record(ok=False)
			return finish("Error", redact(str(e)))
		except Exception as e:
			_breaker_record(ok=False)
			return finish("Error", f"{type(e).__name__}: {redact(str(e))}")
		_breaker_record(ok=True)
		outcome.input_tokens += resp.input_tokens
		outcome.output_tokens += resp.output_tokens
		outcome.cache_read_tokens += resp.cache_read_tokens
		return finish("Success", response=resp)
