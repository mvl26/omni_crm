"""D2-04: call_tool (mọi nhánh) với FakeAIClient, hạn mức token, ngắt mạch, AnthropicClient với SDK giả,
nạp câu lệnh, khối Bot của giám sát. KHÔNG gọi API thật.
"""

from types import SimpleNamespace
from unittest import mock

import anthropic
import frappe
import httpx
from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import ai_client
from omni_crm.bot.ai_client import AIError, AnthropicClient, TransientAIError
from omni_crm.bot.fake_client import FakeAIClient, minimal_valid
from omni_crm.bot.prompts import loader
from omni_crm.monitoring import alerts, snapshot

BOT_RUN = "Omni Bot Run"
BATCH = "T04-"
FAKE_KEY = "sk-ant-khong-that-0123456789abcdef0123456789abcdef"
SETTINGS_FIELDS = (
	"bot_analyze_enabled",
	"bot_reply_mode",
	"daily_token_budget",
	"analyze_model",
	"reply_model",
	"analyze_prompt_version",
	"reply_prompt_version",
)
SECRET_TEXT = "NOI-DUNG-KHACH-BI-MAT-QWERTY"
TOOL = {
	"name": "record_x",
	"description": "ghi kết quả",
	"input_schema": {
		"type": "object",
		"properties": {"intent": {"enum": ["a", "b"]}, "note": {"type": "string", "maxLength": 20}},
		"required": ["intent"],
	},
}
GOOD = {"intent": "a", "note": "ok"}


def set_settings(**values) -> None:
	doc = frappe.get_doc("Omni Settings")
	doc.update(values)
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()


class AITestCase(FrappeTestCase):
	def setUp(self):
		self._orig = {f: frappe.db.get_single_value("Omni Settings", f) for f in SETTINGS_FIELDS}
		self.addCleanup(self._restore)
		self.clean()
		set_settings(bot_analyze_enabled=1, bot_reply_mode="Draft", daily_token_budget=10_000_000)
		self.fake = FakeAIClient()
		ai_client.set_client(self.fake)
		self.addCleanup(ai_client.set_client, None)
		sleep = mock.patch.object(ai_client, "_sleep")
		self.sleep = sleep.start()
		self.addCleanup(sleep.stop)
		alert = mock.patch.object(ai_client, "deliver_now", return_value=True)
		self.deliver = alert.start()
		self.addCleanup(alert.stop)

	def _restore(self):
		self.clean()
		set_settings(**{k: v for k, v in self._orig.items() if v is not None})

	def clean(self):
		frappe.db.rollback()
		frappe.db.delete(BOT_RUN, {"batch_id": ["like", f"{BATCH}%"]})
		frappe.db.commit()
		frappe.cache().delete_keys("omni:ai:")

	def call(self, kind="Analyze", batch="1", **kw):
		kw.setdefault("messages", [{"role": "user", "content": f"<hoi_thoai>{SECRET_TEXT}</hoi_thoai>"}])
		return ai_client.call_tool(
			kind, "claude-test", "system cố định", tool=kw.pop("tool", TOOL), batch_id=f"{BATCH}{batch}", **kw
		)

	def run_of(self, batch="1"):
		return frappe.get_last_doc(BOT_RUN, filters={"batch_id": f"{BATCH}{batch}"})


class TestCallTool(AITestCase):
	def test_success_logs_full_run(self):
		self.fake.add_rule(GOOD)
		out = self.call(lead=None, prompt_version="v1", cache_blocks=["kho tri thức"])
		self.assertEqual(out, GOOD)
		run = self.run_of()
		self.assertEqual(
			(run.kind, run.status, run.model, run.prompt_version), ("Analyze", "Success", "claude-test", "v1")
		)
		self.assertGreater(run.input_tokens, 0)
		self.assertGreater(run.output_tokens, 0)
		self.assertIsNotNone(run.latency_ms)
		self.assertEqual(frappe.parse_json(run.output_json), GOOD)
		call = self.fake.calls[0]
		self.assertEqual(call["tool"]["name"], "record_x")
		self.assertEqual(call["cache_blocks"], ["kho tri thức"])
		self.assertEqual(call["timeout"], ai_client.timeout_seconds())

	def test_cache_read_tokens_on_second_call(self):
		self.call(batch="1")
		self.call(batch="2")
		self.assertEqual(self.run_of("1").cache_read_tokens, 0)
		self.assertGreater(self.run_of("2").cache_read_tokens, 0)

	def test_invalid_schema_then_valid(self):
		self.fake.faults = ["bad_schema"]
		self.fake.add_rule(GOOD)
		self.assertEqual(self.call(), GOOD)
		self.assertEqual(len(self.fake.calls), 2)
		run = self.run_of()
		self.assertEqual(run.status, "Success")
		self.assertEqual(frappe.db.count(BOT_RUN, {"batch_id": f"{BATCH}1"}), 1)

	def test_invalid_schema_twice_gives_up(self):
		self.fake.faults = ["bad_schema", "bad_schema"]
		self.assertIsNone(self.call())
		self.assertEqual(len(self.fake.calls), 2)  # 1 lần thử lại, không hơn
		run = self.run_of()
		self.assertEqual(run.status, "Invalid Output")
		self.assertNotIn("sai_schema", run.error or "")  # không chép giá trị đầu ra vào lỗi
		self.assertGreater(run.input_tokens, 0)  # token của cả hai lượt đều tính

	def test_enum_and_maxlength_violations_are_invalid(self):
		self.assertIn("intent", ai_client.validate_output({"intent": "zzz"}, TOOL["input_schema"]))
		self.assertIn(
			"note", ai_client.validate_output({"intent": "a", "note": "x" * 21}, TOOL["input_schema"])
		)
		self.assertIn("required", ai_client.validate_output({}, TOOL["input_schema"]))
		self.assertIsNone(ai_client.validate_output(GOOD, TOOL["input_schema"]))
		self.assertIsNotNone(ai_client.validate_output(None, TOOL["input_schema"]))

	def test_missing_tool_use_is_invalid(self):
		with mock.patch.object(
			self.fake, "create", return_value=ai_client.AIResponse(tool_input=None, input_tokens=5)
		):
			self.assertIsNone(self.call())
		self.assertEqual(self.run_of().status, "Invalid Output")

	def test_429_then_success_waits_increasing(self):
		self.fake.faults = ["429"]
		self.fake.add_rule(GOOD)
		self.assertEqual(self.call(), GOOD)
		self.assertEqual(len(self.fake.calls), 2)
		self.sleep.assert_called_once_with(ai_client.BACKOFF_SECONDS[0])
		self.assertEqual(self.run_of().status, "Success")

	def test_transient_errors_exhausted(self):
		self.fake.faults = ["500", "529", "timeout"]
		self.assertIsNone(self.call())
		self.assertEqual(len(self.fake.calls), 3)  # 1 + tối đa 2 lần thử lại
		self.assertEqual([c.args[0] for c in self.sleep.call_args_list], list(ai_client.BACKOFF_SECONDS))
		run = self.run_of()
		self.assertEqual(run.status, "Error")
		self.assertIn("tạm thời", run.error)

	def test_fatal_error_not_retried(self):
		self.fake.faults = ["fatal"]
		self.assertIsNone(self.call())
		self.assertEqual(len(self.fake.calls), 1)
		self.sleep.assert_not_called()
		self.assertEqual(self.run_of().status, "Error")

	def test_unexpected_exception_is_recorded_not_raised(self):
		with mock.patch.object(self.fake, "create", side_effect=RuntimeError(f"hỏng {FAKE_KEY}")):
			self.assertIsNone(self.call())
		run = self.run_of()
		self.assertEqual(run.status, "Error")
		self.assertNotIn(FAKE_KEY, run.error)

	def test_budget_exceeded_skips_and_alerts_once(self):
		ai_client._add_tokens(50)
		set_settings(daily_token_budget=1)
		self.assertIsNone(self.call(batch="1"))
		self.assertIsNone(self.call(batch="2"))
		self.assertEqual(self.fake.calls, [])
		self.assertEqual(self.run_of("1").status, "Skipped Budget")
		self.assertEqual(self.run_of("2").status, "Skipped Budget")
		self.assertEqual(self.deliver.call_count, 1)  # cảnh báo 1 lần/ngày
		self.assertEqual(self.deliver.call_args.args[0].kind, "ai_het_han_muc")

	def test_budget_is_enforced_after_usage_accumulates(self):
		set_settings(daily_token_budget=ai_client.tokens_today() + 10)
		first = self.call(batch="1")  # tốn > 30 token giả: lần sau phải bị chặn
		self.assertEqual(first, minimal_valid(TOOL["input_schema"]))
		self.assertIsNone(self.call(batch="2"))
		self.assertEqual(self.run_of("2").status, "Skipped Budget")
		self.assertEqual(len(self.fake.calls), 1)

	def test_reservation_visible_in_flight_and_settled_after(self):
		before = ai_client.tokens_today()
		seen = []
		real_create = self.fake.create

		def spy(**kw):
			seen.append(ai_client.tokens_today() - before)
			return real_create(**kw)

		with mock.patch.object(self.fake, "create", side_effect=spy):
			self.call()
		run = self.run_of()
		self.assertGreaterEqual(seen[0], ai_client.DEFAULT_MAX_TOKENS)  # đã giữ chỗ trước khi gọi
		self.assertEqual(ai_client.tokens_today() - before, run.input_tokens + run.output_tokens)

	def test_reservation_released_on_failure(self):
		before = ai_client.tokens_today()
		self.fake.faults = ["fatal"]
		self.call()
		self.assertEqual(ai_client.tokens_today(), before)

	def test_parallel_in_flight_reservation_blocks_next_call(self):
		set_settings(daily_token_budget=ai_client.tokens_today() + ai_client.DEFAULT_MAX_TOKENS)
		inner = []

		def spy(**kw):
			inner.append(self.call(batch="inner"))  # lần gọi thứ hai chạy khi lần đầu còn giữ chỗ
			return ai_client.AIResponse(tool_input=GOOD, input_tokens=5, output_tokens=5)

		with mock.patch.object(self.fake, "create", side_effect=spy):
			self.call(batch="outer")
		self.assertEqual(inner, [None])
		self.assertEqual(self.run_of("inner").status, "Skipped Budget")

	def test_bot_run_insert_failure_still_returns_result(self):
		self.fake.add_rule(GOOD)
		with (
			mock.patch.object(ai_client, "_record", side_effect=ValueError(f"hỏng {FAKE_KEY} 0912345678")),
			mock.patch("frappe.log_error") as log_error,
		):
			self.assertEqual(self.call(), GOOD)
		log_error.assert_called_once()
		message = log_error.call_args.kwargs["message"]
		self.assertNotIn(FAKE_KEY, message)
		self.assertNotIn("0912345678", message)

	def test_zero_budget_stops_everything_without_alert(self):
		set_settings(daily_token_budget=0)
		self.assertIsNone(self.call())
		self.assertEqual(self.run_of().status, "Skipped Budget")
		self.assertEqual(self.fake.calls, [])
		self.deliver.assert_not_called()

	def test_counter_rebuilt_from_bot_runs_when_cache_empty(self):
		self.call(batch="1")
		self.call(batch="2")
		in_cache = ai_client.tokens_today()
		frappe.cache().delete_keys("omni:ai:")
		rebuilt = ai_client.tokens_today()
		db = frappe.db.sql(
			"select coalesce(sum(input_tokens + output_tokens), 0) from `tabOmni Bot Run` where creation >= %s",
			frappe.utils.today(),
		)[0][0]
		self.assertEqual(rebuilt, int(db))
		self.assertEqual(rebuilt, in_cache)

	def test_switch_off_skips_without_calling(self):
		set_settings(bot_analyze_enabled=0)
		self.assertIsNone(self.call("Analyze", batch="1"))
		set_settings(bot_reply_mode="Off")
		self.assertIsNone(self.call("Reply", batch="2"))
		self.assertIsNone(self.call("Summarize", batch="3"))  # recording_enabled chưa có/tắt
		self.assertEqual(self.fake.calls, [])
		for b in "123":
			self.assertEqual(self.run_of(b).status, "Skipped Switch")

	def test_switch_on_for_each_kind(self):
		set_settings(bot_analyze_enabled=1, bot_reply_mode="Shadow")
		self.assertIsNotNone(self.call("Analyze", batch="1"))
		self.assertIsNotNone(self.call("Reply", batch="2"))
		self.assertIsNotNone(self.call("Judge", batch="3"))  # bộ đề không có công tắc riêng

	def test_switch_checked_right_before_call(self):
		def turn_off_then_fail(**kw):
			raise AssertionError("không được gọi khi công tắc đã tắt")

		set_settings(bot_analyze_enabled=0)
		with mock.patch.object(self.fake, "create", side_effect=turn_off_then_fail):
			self.assertIsNone(self.call())

	def test_no_api_key_skips(self):
		ai_client.set_client(None)
		with mock.patch.object(ai_client, "get_client", return_value=None):
			self.assertIsNone(self.call())
		self.assertIn("khóa", self.run_of().error)

	def test_real_client_blocked_until_q11_unless_synthetic(self):
		real = FakeAIClient(is_real=True)
		ai_client.set_client(real)
		self.assertIsNone(self.call(batch="1"))
		self.assertEqual(real.calls, [])
		self.assertIn("Q11", self.run_of("1").error)
		self.assertIsNotNone(self.call(batch="2", synthetic=True))
		with mock.patch.dict(frappe.conf, {ai_client.ALLOW_DATA_CONF: 1}):
			self.assertIsNotNone(self.call(batch="3"))

	def test_bot_run_has_no_conversation_text(self):
		self.fake.faults = ["500", "500", "500"]
		self.call(batch="1", lead=None)
		self.fake.add_rule(GOOD)
		self.call(batch="2")
		for b in ("1", "2"):
			run = self.run_of(b)
			blob = " ".join(str(run.get(f)) for f in run.meta.get_valid_columns())
			self.assertNotIn(SECRET_TEXT, blob)
		digest = self.run_of("2").request_digest
		self.assertIn(f"batch={BATCH}2", digest)
		self.assertIn("luot=1", digest)
		self.assertIn(f"ky_tu={len('<hoi_thoai>' + SECRET_TEXT + '</hoi_thoai>')}", digest)

	def test_circuit_breaker_opens_and_resets(self):
		self.fake.faults = ["fatal"] * ai_client.BREAKER_FAILURES
		for i in range(ai_client.BREAKER_FAILURES):
			self.assertIsNone(self.call(batch=f"f{i}"))
		calls_before = len(self.fake.calls)
		self.assertTrue(ai_client.breaker_open())
		self.assertIsNone(self.call(batch="open"))
		self.assertEqual(len(self.fake.calls), calls_before)  # bị ngắt: không gọi, không thử lại
		self.assertEqual(self.run_of("open").status, "Skipped Switch")
		self.assertIn("Ngắt mạch", self.run_of("open").error)
		frappe.cache().delete_keys("omni:ai:")
		self.assertFalse(ai_client.breaker_open())
		self.assertIsNotNone(self.call(batch="ok"))

	def test_success_resets_failure_streak(self):
		for i in range(ai_client.BREAKER_FAILURES - 1):
			self.fake.faults = ["fatal"]
			self.call(batch=f"f{i}")
		self.call(batch="ok")  # thành công: chuỗi lỗi về 0
		self.fake.faults = ["fatal"]
		self.call(batch="again")
		self.assertFalse(ai_client.breaker_open())

	def test_timeout_is_configurable_default_60(self):
		self.assertEqual(ai_client.timeout_seconds(), 60)
		with mock.patch.dict(frappe.conf, {"omni_ai_timeout_seconds": 12}):
			self.assertEqual(ai_client.timeout_seconds(), 12)
		with mock.patch.dict(frappe.conf, {"omni_ai_timeout_seconds": "abc"}):
			self.assertEqual(ai_client.timeout_seconds(), 60)


def _usage(**kw):
	return SimpleNamespace(
		**{
			"input_tokens": 10,
			"output_tokens": 5,
			"cache_read_input_tokens": 7,
			"cache_creation_input_tokens": 3,
			**kw,
		}
	)


class TestAnthropicClient(FrappeTestCase):
	"""AnthropicClient với SDK giả: kiểm hình dạng yêu cầu và ánh xạ lỗi; không có lời gọi mạng."""

	def create(self, sdk):
		with mock.patch.object(anthropic, "Anthropic", return_value=sdk) as ctor:
			client = AnthropicClient(FAKE_KEY)
			resp = client.create(
				kind="Analyze",
				model="claude-test",
				system="hệ thống",
				messages=[{"role": "user", "content": "xin chào"}],
				tool=TOOL,
				cache_blocks=["kho 1", "kho 2"],
				max_tokens=256,
				timeout=12,
			)
		return ctor, resp

	def test_request_shape_and_parse(self):
		sdk = mock.MagicMock()
		sdk.messages.create.return_value = SimpleNamespace(
			content=[SimpleNamespace(type="tool_use", name="record_x", input=GOOD)], usage=_usage()
		)
		ctor, resp = self.create(sdk)
		self.assertEqual(ctor.call_args.kwargs, {"api_key": FAKE_KEY, "timeout": 12, "max_retries": 0})
		kw = sdk.messages.create.call_args.kwargs
		self.assertEqual(kw["tool_choice"], {"type": "tool", "name": "record_x"})
		self.assertEqual(kw["tools"], [TOOL])
		self.assertEqual(kw["model"], "claude-test")
		self.assertEqual(kw["max_tokens"], 256)
		self.assertEqual([b["text"] for b in kw["system"]], ["hệ thống", "kho 1", "kho 2"])
		self.assertEqual(
			[("cache_control" in b) for b in kw["system"]], [False, False, True]
		)  # điểm cache ở cuối phần cố định
		self.assertNotIn(FAKE_KEY, str(kw))  # khóa chỉ ở constructor, không vào nội dung gửi
		self.assertEqual(
			(
				resp.tool_input,
				resp.input_tokens,
				resp.output_tokens,
				resp.cache_read_tokens,
				resp.cache_creation_tokens,
			),
			(GOOD, 10, 5, 7, 3),
		)

	def test_forced_tool_choice_depends_on_model(self):
		self.assertEqual(
			ai_client.tool_choice_for("claude-haiku-5-5", TOOL), {"type": "tool", "name": "record_x"}
		)
		for model in ("claude-sonnet-5-5", "claude-opus-5-5-20270101"):
			self.assertEqual(ai_client.tool_choice_for(model, TOOL), {"type": "auto"})

	def test_no_tool_use_block(self):
		sdk = mock.MagicMock()
		sdk.messages.create.return_value = SimpleNamespace(
			content=[SimpleNamespace(type="text", text="xin lỗi")], usage=_usage()
		)
		self.assertIsNone(self.create(sdk)[1].tool_input)

	def _status_error(self, status, message="lỗi"):
		req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
		return anthropic.APIStatusError(message, response=httpx.Response(status, request=req), body=None)

	def test_error_mapping_and_key_never_leaks(self):
		for status, transient in ((429, True), (500, True), (529, True), (400, False), (401, False)):
			sdk = mock.MagicMock()
			sdk.messages.create.side_effect = self._status_error(status, f"loi {FAKE_KEY} x")
			with self.assertRaises(TransientAIError if transient else AIError) as cm:
				self.create(sdk)
			self.assertEqual(isinstance(cm.exception, TransientAIError), transient, status)
			self.assertNotIn(FAKE_KEY, str(cm.exception))

	def test_timeout_and_connection_are_transient(self):
		req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
		for exc in (anthropic.APITimeoutError(request=req), anthropic.APIConnectionError(request=req)):
			sdk = mock.MagicMock()
			sdk.messages.create.side_effect = exc
			with self.assertRaises(TransientAIError):
				self.create(sdk)

	def test_get_client_reads_password_field(self):
		ai_client.set_client(None)
		original = frappe.get_doc("Omni Settings").get_password("ai_api_key", raise_exception=False)
		self.addCleanup(set_settings, ai_api_key=original or "")
		set_settings(ai_api_key=FAKE_KEY)
		client = ai_client.get_client()
		self.assertIsInstance(client, AnthropicClient)
		self.assertTrue(client.is_real)
		self.assertNotIn(FAKE_KEY, repr(frappe.get_doc("Omni Settings").as_dict().get("ai_api_key")))


class TestPromptLoader(FrappeTestCase):
	def test_files_have_version_and_content(self):
		for name in ("analyze", "reply"):
			p = loader.load_prompt(name)
			self.assertEqual(p.version, "v1")
			self.assertEqual(loader.active_version(name), "v1")
			self.assertIn("<hoi_thoai>", p.text)
			self.assertFalse(p.text.startswith("version:"))
		self.assertIn("record_lead_signals", loader.load_prompt("analyze").text)
		self.assertIn("propose_reply", loader.load_prompt("reply").text)

	def test_missing_prompt(self):
		self.assertIsNone(loader.active_version("khong_co"))
		with self.assertRaises(FileNotFoundError):
			loader.load_prompt("khong_co")
		with self.assertRaises(FileNotFoundError):
			loader.load_prompt("analyze", "v99")

	def test_render_variables(self):
		p = loader.load_prompt("reply")
		text = loader.render(p, kenh="Zalo OA", trong_gio_hay_ngoai_gio="đang trong giờ làm việc")
		self.assertIn("Zalo OA", text)
		self.assertNotIn("{kenh}", text)

	def test_newer_file_is_not_loaded_until_pinned(self):
		# V2-01 (gate 1, ĐQ-6): trước đây tệp số cao nhất tự thành bản chạy thật; nay phải ghim mới có hiệu lực.
		# V2-06: analyze_v2.md là tệp thật trong kho mã nên test dùng số cao hơn (v9), không ghi đè/xóa tệp thật
		tmp = loader.PROMPT_DIR / "analyze_v9.md"
		tmp.write_text("version: v9\n\nnội dung mới\n", encoding="utf-8")
		self.addCleanup(tmp.unlink)
		self.assertEqual(loader.latest_version("analyze"), "v9")
		self.assertEqual(loader.active_version("analyze"), "v1")
		self.assertEqual(loader.load_prompt("analyze").version, "v1")
		self.assertEqual(loader.load_prompt("analyze", "v1").version, "v1")
		orig = frappe.db.get_single_value("Omni Settings", "analyze_prompt_version")
		self.addCleanup(frappe.db.set_single_value, "Omni Settings", "analyze_prompt_version", orig)
		self.assertNotIn("analyze", loader.sync_settings())  # đã có bản ghim: không ghi đè
		self.assertEqual(frappe.db.get_single_value("Omni Settings", "analyze_prompt_version"), "v1")

	def test_bad_header_rejected(self):
		tmp = loader.PROMPT_DIR / "zz_v1.md"
		tmp.write_text("khong co dong version\n", encoding="utf-8")
		self.addCleanup(tmp.unlink)
		with self.assertRaises(ValueError):
			loader.load_prompt("zz", "v1")


class TestBotMonitoring(AITestCase):
	def test_snapshot_bot_block(self):
		self.call(batch="1")
		self.fake.faults = ["fatal"]
		self.call(batch="2")
		set_settings(daily_token_budget=100_000_000)
		bot = snapshot.build_snapshot()["bot"]
		self.assertEqual(bot["daily_token_budget"], 100_000_000)
		self.assertGreater(bot["tokens_today"], 0)
		self.assertGreaterEqual(bot["errors_24h"], 1)
		self.assertGreaterEqual(bot["runs_24h"], 2)
		self.assertNotIn(SECRET_TEXT, str(bot))

	def test_alert_rules(self):
		def snap(**bot):
			return {
				"queue": {"new": 0, "manual": 0, "oldest_new_age_seconds": None},
				"timing": {"count": 0, "p95": None},
				"outbound": {"failed_last_hour": 0},
				"duplicates": {"open": 0},
				"channels": [],
				"switches": {"sandbox_mode": 0},
				"bot": bot,
			}

		kinds = lambda b: {a.kind for a in alerts.evaluate(snap(**b))}  # noqa: E731
		self.assertEqual(kinds({"usage_pct": 79.0, "runs_24h": 100, "errors_24h": 5}), set())
		self.assertIn(
			"ai_gan_han_muc",
			kinds(
				{
					"usage_pct": 81.0,
					"tokens_today": 81,
					"daily_token_budget": 100,
					"runs_24h": 0,
					"errors_24h": 0,
				}
			),
		)
		self.assertIn("ai_loi_nhieu", kinds({"usage_pct": None, "runs_24h": 100, "errors_24h": 6}))
		self.assertEqual(kinds({"usage_pct": None, "runs_24h": 10, "errors_24h": 10}), set())  # mẫu quá ít


class TestEraseCoversBotRun(AITestCase):
	def test_erase_clears_output_json(self):
		from omni_crm.api import admin

		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "TD204 Xoa", "omni_first_channel": "Zalo OA"}
		)
		lead.flags.ignore_permissions = True
		with mock.patch("frappe.enqueue"):
			lead.insert()
		self.addCleanup(frappe.db.commit)  # chạy SAU xóa Lead (LIFO) để `clean()` không rollback lần xóa
		self.addCleanup(
			frappe.delete_doc,
			"CRM Lead",
			lead.name,
			force=True,
			ignore_permissions=True,
			delete_permanently=True,
		)
		self.fake.add_rule({"intent": "a", "note": "trích: 0912345678"})
		self.call(lead=lead.name)
		self.assertIn("0912345678", self.run_of().output_json)
		with mock.patch("frappe.enqueue"):
			admin.erase_customer_data(lead.name, "Khách yêu cầu xóa dữ liệu")
		self.assertIsNone(self.run_of().output_json)
