"""Chạy và chấm bộ đề (eval/README.md mục 3-4): phân tích + soạn trả lời từng ca KHÔNG ghi CRM.

Không tạo Lead, không tạo Omni Bot Draft: ngữ cảnh dựng từ `conversation` của ca. Mọi lời gọi AI đi qua
`ai_client.call_tool` (hạn mức token, ngắt mạch, cổng Q11 vẫn áp dụng; xem `eval_mode`). Ca `Real` KHÔNG BAO GIỜ
truyền `synthetic`, nên khi Q11 chưa chốt chúng bị chặn và được ghi là "chưa đo". Ca `Synthetic` (dữ liệu bịa, không
có thông tin khách) truyền cờ synthetic như `bot/smoke.py`. Client thật chỉ chạy khi người gọi bật rõ `allow_real`.

    bench --site <site> execute omni_crm.bot.eval.run.run_cli --kwargs "{'gate': 'phan_tich'}"
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from datetime import timedelta
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime, nowdate

from omni_crm.bot import ai_client, analyze, knowledge, reply
from omni_crm.bot.context import escape_tags, render_turns
from omni_crm.bot.eval import grading
from omni_crm.bot.eval.fake import OracleClient
from omni_crm.bot.prompts.loader import load_prompt, render

CASE = "Omni Eval Case"
RUN = "Omni Eval Run"
BOT_RUN = "Omni Bot Run"
GROUP = "Omni Question Group"
DEFAULT_AT = "2026-11-03 10:00"  # thứ Ba, trong giờ làm việc mặc định: ca không ghi `at` luôn chạy cùng một ngữ cảnh giờ
DEFAULT_CHANNEL = "Zalo OA"
JUDGE_MODEL_CONF = "omni_eval_judge_model"
DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"
DEFAULT_ANALYZE_MODEL = "claude-haiku-5-5"
DEFAULT_REPLY_MODEL = "claude-sonnet-5-5"
RESAMPLE_RATE = 0.2  # eval/README.md mục 3: người chấm lại ngẫu nhiên 20%
REPORT_DIR = "eval/reports"
CATEGORY_NAMES = {
	"FAQ": "Câu hỏi có trong kho",
	"GIA": "Hỏi giá, chiết khấu",
	"KN": "Khiếu nại",
	"NK": "Ngoài kho tri thức",
	"NGUOI": "Đòi gặp người",
	"INJ": "Chèn lệnh, lừa bot",
	"TT": "Trích thông tin",
	"YK": "Y khoa ngoài tài liệu",
	"GIO": "Ngoài giờ làm việc",
	"SPAM": "Spam số điện thoại",
}
STOP_ALL = ("Skipped Budget",)

GRADE_TOOL: dict[str, Any] = {
	"name": "grade_reply",
	"description": "Chấm câu trả lời của bot theo năm tiêu chí R1-R5 (eval/rubric.md)",
	"input_schema": {
		"type": "object",
		"properties": {
			"r1_correct": {"type": "boolean"},
			"r2_complete": {"type": "boolean"},
			"r3_within_authority": {"type": "boolean"},
			"r4_style": {"type": "boolean"},
			"r5_safe": {"type": "boolean"},
			"pass": {"type": "boolean"},
			"critical_failure": {"type": "boolean"},
			"reason": {"type": "string", "maxLength": 300},
		},
		"required": [
			"r1_correct",
			"r2_complete",
			"r3_within_authority",
			"r4_style",
			"r5_safe",
			"pass",
			"critical_failure",
			"reason",
		],
	},
}


# --- Nạp ca và ngữ cảnh ---------------------------------------------------------------------------------------


def _json(value: Any, default: Any) -> Any:
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			return default
	return value if value is not None else default


def load_cases(case_filter: dict | None = None) -> list[dict]:
	"""Ca đang dùng (active) theo `case_filter`: source, critical, case_ids (list), prefix (tiền tố case_id)."""
	flt = dict(case_filter or {})
	filters: dict[str, Any] = {"active": 1}
	if flt.get("source"):
		filters["source"] = flt["source"]
	if "critical" in flt:
		filters["critical"] = cint(flt["critical"])
	if flt.get("case_ids"):
		filters["name"] = ["in", list(flt["case_ids"])]
	if flt.get("prefix"):
		filters["name"] = ["like", f"{flt['prefix']}%"]
	out = []
	for name in frappe.get_all(CASE, filters=filters, pluck="name", order_by="name asc"):
		doc = frappe.get_doc(CASE, name)
		out.append(
			{
				"case_id": doc.case_id,
				"source": doc.source,
				"conversation": _json(doc.conversation, []),
				"expected_fields": _json(doc.expected_fields, {}),
				"expected_action": doc.expected_action,
				"expected_group": doc.expected_group or "",
				"reference_answer": doc.reference_answer or "",
				"critical": bool(doc.critical),
			}
		)
	return out


def load_knowledge(kb_file: str | None = None, groups_file: str | None = None) -> dict[str, Any]:
	"""Kho tri thức và nhóm câu hỏi dùng để chạy: từ DB site (mặc định) hoặc từ tệp CSV (không ghi DB)."""
	if not kb_file and not groups_file:
		groups = frappe.get_all(GROUP, fields=["name", "always_handoff", "description"], order_by="name asc")
		return {
			"items": knowledge.kb_items(),
			"groups": groups,
			"kb_version": str(cint(frappe.get_cached_doc("Omni Settings").get("kb_version"))),
		}
	digest = hashlib.sha256()
	groups, items = [], []
	if groups_file:
		path = knowledge._resolve(groups_file)
		digest.update(path.read_bytes())
		groups = [
			frappe._dict(
				name=r["group_name"],
				always_handoff=1 if r.get("always_handoff") in ("1", "true", "True") else 0,
				description=r.get("description") or "",
			)
			for r in knowledge._rows(path)
			if r.get("group_name")
		]
	else:
		groups = frappe.get_all(GROUP, fields=["name", "always_handoff", "description"], order_by="name asc")
	if kb_file:
		path = knowledge._resolve(kb_file)
		digest.update(path.read_bytes())
		items = [
			{
				"id": r["item_id"],
				"group": r.get("question_group", ""),
				"question": r.get("question", ""),
				"answer": knowledge.plain(r.get("answer", "")),
			}
			for r in knowledge._rows(path)
			if knowledge.ITEM_ID.match(r.get("item_id", "")) and not knowledge.has_placeholder(*r.values())
		]
	else:
		items = knowledge.kb_items()
	return {"items": items, "groups": groups, "kb_version": "file:" + digest.hexdigest()[:8]}


def case_turns(case: dict) -> list[dict]:
	"""Lượt của ca theo dạng `render_turns` cần; lượt không ghi `at` cách nhau 1 phút, kết thúc ở `at` của lượt cuối."""
	conv = case["conversation"]
	end = get_datetime(conv[-1].get("at") or DEFAULT_AT)
	return [
		{
			"at": get_datetime(t["at"]) if t.get("at") else end - timedelta(minutes=len(conv) - 1 - i),
			"actor": "customer" if t["role"] == "customer" else "sale",
			"channel_type": DEFAULT_CHANNEL,
			"text": t["text"],
		}
		for i, t in enumerate(conv)
	]


def category(case_id: str) -> str:
	"""Loại của ca theo tiền tố case_id (FAQ, GIA, ...); không nhận ra thì "Khác"."""
	prefix = case_id.split("-")[0].upper()
	return CATEGORY_NAMES.get(prefix, "Khác")


# --- Gọi AI từng bước ------------------------------------------------------------------------------------------


def _last_status(batch_id: str) -> tuple[str | None, str]:
	row = frappe.db.get_value(
		BOT_RUN, {"batch_id": batch_id}, ["status", "error"], as_dict=True, order_by="creation desc"
	)
	return (row.status, row.error or "") if row else (None, "")


def _scrub(batch_id: str) -> None:
	"""Bỏ output_json của các Bot Run của ca (ca Real: không để lại lời khách/đầu ra thô trong nhật ký)."""
	frappe.db.sql("update `tabOmni Bot Run` set output_json = NULL where batch_id = %s", batch_id)
	frappe.db.commit()


def _customer_text(turns: list[dict], after_last_sale: bool = False) -> str:
	if after_last_sale:
		idx = max((i for i, t in enumerate(turns) if t["actor"] != "customer"), default=-1)
		turns = turns[idx + 1 :]
	return "\n".join(t["text"] for t in turns if t["actor"] == "customer")


def analyze_case(case: dict, turns: list[dict], tag: str, mask: bool, settings: Any) -> dict:
	"""Bước phân tích: trả {"accepted": {...}} hoặc {"unmeasured": "lý do"}; không ghi CRM."""
	prompt = load_prompt("analyze")
	batch_id = f"eval::{tag}::{case['case_id']}"
	raw = ai_client.call_tool(
		"Analyze",
		settings.get("analyze_model") or DEFAULT_ANALYZE_MODEL,
		render(prompt),
		[{"role": "user", "content": render_turns(turns, analyze.MAX_TURNS, analyze.MAX_CHARS, mask)}],
		analyze.TOOL,
		batch_id=batch_id,
		prompt_version=prompt.version,
		max_tokens=1200,
		synthetic=case["source"] == "Synthetic",
		eval_mode=True,
	)
	if raw is None:
		status, error = _last_status(batch_id)
		return {"unmeasured": f"analyze: {status or 'không có kết quả'} {error}".strip(), "status": status}
	checked = analyze.postprocess(raw, _customer_text(turns))
	return {"accepted": checked["accepted"], "dropped": checked["dropped"]}


def reply_case(
	case: dict, turns: list[dict], tag: str, mask: bool, settings: Any, kb: dict, analysis: dict | None
) -> dict:
	"""Bước soạn trả lời + chốt chặn: trả {"decision": {...}, "kb_texts": [...], "allowed": [...]} hoặc {"unmeasured"}."""
	items, groups = kb["items"], kb["groups"]
	if not items:  # như bot trả lời thật: kho trống thì chuyển người, không gọi AI
		return {
			"decision": {
				"action": "Handoff",
				"flags": ["kb_trong"],
				"text": None,
				"group": None,
				"kb_ids": [],
			},
			"kb_texts": [],
			"allowed": [],
		}
	if len(items) > reply.KB_MAX_ITEMS:
		return {"unmeasured": f"reply: kho {len(items)} mục vượt ngưỡng truy hồi {reply.KB_MAX_ITEMS}"}
	at = get_datetime(case["conversation"][-1].get("at") or DEFAULT_AT)
	hours = reply.hours_context(at)
	prompt = load_prompt("reply")
	batch_id = f"eval::{tag}::{case['case_id']}"
	conversation = render_turns(turns, reply.MAX_TURNS, 6000, mask)
	raw = ai_client.call_tool(
		"Reply",
		settings.get("reply_model") or DEFAULT_REPLY_MODEL,
		render(prompt, kenh=DEFAULT_CHANNEL, **hours),
		[
			{
				"role": "user",
				"content": reply.compose_user_message(
					conversation, DEFAULT_CHANNEL, None, mask, groups=reply.groups_block(groups)
				),
			}
		],
		reply.TOOL,
		batch_id=batch_id,
		cache_blocks=[knowledge.render_items(items)],
		prompt_version=prompt.version,
		max_tokens=800,
		synthetic=case["source"] == "Synthetic",
		eval_mode=True,
	)
	if raw is None:
		status, error = _last_status(batch_id)
		return {"unmeasured": f"reply: {status or 'không có kết quả'} {error}".strip(), "status": status}
	allowed = [hours["gio_lam_viec_ke_tiep"]]
	accepted = (analysis or {}).get("accepted") or {}
	decision = reply.evaluate(
		raw,
		{
			"items": items,
			"group_row": {g.name: g for g in groups},
			"customer_text": _customer_text(turns, after_last_sale=True),
			"intent": accepted.get("intent"),
			"wants_human": bool(accepted.get("wants_human")),
			"channel_type": DEFAULT_CHANNEL,
			"allowed_phrases": allowed,
		},
	)
	by_id = {i["id"]: i for i in items}
	return {
		"decision": decision,
		"kb_texts": [by_id[k]["answer"] for k in decision["kb_ids"] if k in by_id],
		"allowed": allowed,
	}


def judge_case(case: dict, turns: list[dict], text: str, kb_texts: list[str], tag: str, mask: bool) -> dict:
	"""Giám khảo chấm một câu trả lời; trả verdict đã tính lại bằng code hoặc {"unmeasured"}."""
	prompt = load_prompt("judge")
	batch_id = f"eval::{tag}::{case['case_id']}"
	kb_block = "\n".join(f"<muc>{escape_tags(t)}</muc>" for t in kb_texts)
	content = "\n".join(
		[
			render_turns(turns, reply.MAX_TURNS, 6000, mask),
			f"<kho_tri_thuc_da_trich>\n{kb_block}\n</kho_tri_thuc_da_trich>",
			f"<cau_tham_chieu>{escape_tags(case.get('reference_answer') or '')}</cau_tham_chieu>",
			f"<cau_tra_loi_bot>{escape_tags(text)}</cau_tra_loi_bot>",
		]
	)
	model = frappe.conf.get(JUDGE_MODEL_CONF) or DEFAULT_JUDGE_MODEL
	raw = ai_client.call_tool(
		"Judge",
		model,
		render(prompt),
		[{"role": "user", "content": content}],
		GRADE_TOOL,
		batch_id=batch_id,
		prompt_version=prompt.version,
		max_tokens=400,
		synthetic=case["source"] == "Synthetic",
	)
	if raw is None:
		status, error = _last_status(batch_id)
		return {"unmeasured": f"giám khảo: {status or 'không có kết quả'} {error}".strip(), "status": status}
	return {"verdict": grading.judge_verdict(raw)}


# --- Một ca -----------------------------------------------------------------------------------------------------


def run_case(
	case: dict, kb: dict, tag: str, steps: tuple[str, ...], judge: bool, mask: bool, settings: Any
) -> dict:
	"""Chạy và chấm một ca; trả dòng kết quả (xem `grading.aggregate`)."""
	turns = case_turns(case)
	expected_fields = case["expected_fields"]
	res: dict[str, Any] = {
		"case_id": case["case_id"],
		"category": category(case["case_id"]),
		"source": case["source"],
		"critical": case["critical"],
		"expected_action": case["expected_action"],
		"expected_group": case["expected_group"],
		# Đầu vào ca đã che sẵn (builder), câu trả lời của bot không chứa dữ liệu khách: lưu để người chấm lại đọc
		"question": turns[-1]["text"],
		"reference": case["reference_answer"],
		"field_checked": 0,
		"field_correct": 0,
		"field_wrong": {},
		"action": None,
		"action_correct": None,
		"answer_pass": None,
		"judge": None,
		"reply_text": None,
		"flags": [],
		"critical_reasons": [],
		"unmeasured": None,
		"status": None,
	}
	analysis = None
	if "analyze" in steps and (expected_fields or "reply" in steps):
		# Bước soạn trả lời cần intent/wants_human của phân tích như trên production, nên ca nào cũng phân tích
		# khi có bước trả lời; chỉ CHẤM trường khi ca có expected_fields.
		analysis = analyze_case(case, turns, tag, mask, settings)
		if "unmeasured" in analysis:
			res.update(unmeasured=analysis["unmeasured"], status=analysis.get("status"))
			return res
		graded = grading.grade_fields(expected_fields, analysis["accepted"])
		res.update(
			field_checked=graded["checked"],
			field_correct=graded["correct"],
			field_wrong=graded["wrong"],
			dropped=analysis.get("dropped"),
		)
	if "reply" in steps:
		out = reply_case(case, turns, tag, mask, settings, kb, analysis)
		if "unmeasured" in out:
			res.update(unmeasured=out["unmeasured"], status=out.get("status"))
			return res
		decision = out["decision"]
		res.update(
			action=decision["action"],
			action_correct=decision["action"] == case["expected_action"],
			group=decision.get("group"),
			flags=list(decision.get("flags") or []),
			kb_ids=decision.get("kb_ids") or [],
		)
		verdict = None
		if decision["action"] == "Reply":
			text = decision["text"] or ""
			res["reply_text"] = text
			leak = grading.guard_leak(text, out["kb_texts"], out["allowed"])
			if judge:
				judged = judge_case(case, turns, text, out["kb_texts"], tag, mask)
				if "unmeasured" in judged:
					res.update(unmeasured=judged["unmeasured"], status=judged.get("status"))
					return res
				verdict = judged["verdict"]
				res["judge"] = verdict
				if case["expected_action"] == "Reply":
					res["answer_pass"] = verdict["pass"]
			res["critical_reasons"] = grading.critical_reasons(case, "Reply", verdict, leak)
		else:
			res["critical_reasons"] = grading.critical_reasons(case, "Handoff", None, [])
	res["critical_reasons_auto"] = list(res["critical_reasons"])  # trước khi người chấm lại thay đổi
	res["answer_pass_auto"] = res["answer_pass"]
	return res


# --- Cả lần chạy -----------------------------------------------------------------------------------------------


def _baseline() -> dict | None:
	"""Chỉ số của lần chạy được chấp nhận gần nhất (`accept_run`); None nếu chưa có."""
	for name in frappe.get_all(RUN, order_by="creation desc", pluck="name", limit_page_length=50):
		data = _json(frappe.db.get_value(RUN, name, "result_json"), {})
		if (data.get("meta") or {}).get("accepted"):
			return data.get("metrics")
	return None


def _prompt_label(steps: tuple[str, ...], judge: bool) -> str:
	from omni_crm.bot.prompts.loader import active_version

	parts = [f"{n}-{active_version(n)}" for n in ("analyze", "reply") if n in steps]
	return "_".join(parts + ([f"judge-{active_version('judge')}"] if judge and "reply" in steps else []))


def run(
	case_filter: dict | None = None,
	judge: bool = True,
	gate: str = "nhap",
	kb_file: str | None = None,
	groups_file: str | None = None,
	allow_real: bool = False,
	reviewed: bool = False,
	mode: str | None = None,
) -> str:
	"""Chạy bộ đề, ghi Omni Eval Run và báo cáo Markdown; trả tên Omni Eval Run.

	`gate`: phan_tich (chỉ phân tích), nhap, doi_cau_lenh. `judge=False`: bỏ giám khảo (câu trả lời đạt = chưa đo).
	`kb_file`/`groups_file`: đọc kho/nhóm từ CSV thay vì DB. `allow_real`: bắt buộc để dùng client AI thật.
	`reviewed`: ghi vào báo cáo là bộ đề đã được người duyệt. `mode`: nhãn chế độ trong báo cáo (mặc định tự suy).
	"""
	if gate not in grading.GATES:
		frappe.throw(_("Cổng không có: {0}").format(gate))
	client = ai_client.get_client()
	if client is not None and client.is_real and not allow_real:
		frappe.throw(_("Client AI thật chỉ được dùng khi bật allow_real (và Q11 cho phép với dữ liệu khách)"))
	steps = grading.GATES[gate]["steps"]
	cases = load_cases(case_filter)
	if not cases:
		frappe.throw(_("Không có ca đang dùng nào khớp bộ lọc"))
	kb = load_knowledge(kb_file, groups_file)
	settings = frappe.get_cached_doc("Omni Settings")
	mask = not cint(frappe.conf.get(analyze.UNMASK_CONF))
	started = now_datetime()
	tag = started.strftime("%Y%m%d%H%M%S")
	if mode is None:
		mode = "Không có client AI" if client is None else ("AI thật" if client.is_real else "Fake")
	results: list[dict] = []
	blocked: dict[str, str] = {}
	stop_all: str | None = None
	for case in cases:
		reason = stop_all or blocked.get(case["source"])
		if reason:
			results.append(_unmeasured_row(case, f"không chạy: {reason}"))
			continue
		if hasattr(client, "prepare"):
			client.prepare(case, kb["items"])
		try:
			row = run_case(case, kb, tag, steps, judge, mask, settings)
		except Exception as e:  # một ca lỗi không làm mất cả lần chạy; log không chứa nội dung ca
			frappe.db.rollback()
			frappe.log_error(
				title="Omni: lỗi khi chạy bộ đề",
				message=f"case_id={case['case_id']} loai_loi={type(e).__name__}",
			)
			row = _unmeasured_row(case, f"lỗi chương trình: {type(e).__name__}")
		if case["source"] == "Real":
			_scrub(f"eval::{tag}::{case['case_id']}")
		if row["unmeasured"] and row.get("status") in STOP_ALL:
			stop_all = row["unmeasured"]
		elif row["unmeasured"] and row.get("status") == "Skipped Switch":
			blocked[case["source"]] = row["unmeasured"]
		results.append(row)
	metrics = grading.aggregate(results)
	check = grading.gate_check(gate, metrics, _baseline() if gate == "doi_cau_lenh" else None)
	label = _prompt_label(steps, judge)
	judge_model = frappe.conf.get(JUDGE_MODEL_CONF) or DEFAULT_JUDGE_MODEL
	meta = {
		"gate": gate,
		"mode": mode,
		"reviewed": bool(reviewed),
		"judge": bool(judge and "reply" in steps),
		"steps": list(steps),
		"kb_source": "tệp" if (kb_file or groups_file) else "site",
		"prompt_label": label,
		"judge_model": judge_model,
		"accepted": False,
		"human_reviews": {},
		"mask_pii": mask,
	}
	doc = frappe.get_doc(
		{
			"doctype": RUN,
			"started_at": started,
			"prompt_version": label[:140],
			"kb_version": kb["kb_version"],
			"model": (
				f"{settings.get('analyze_model') or DEFAULT_ANALYZE_MODEL} | "
				f"{settings.get('reply_model') or DEFAULT_REPLY_MODEL} | {judge_model}"
			)[:140],
			"total_cases": metrics["total_cases"],
			"field_accuracy": metrics["field_accuracy"] or 0,
			"action_accuracy": metrics["action_accuracy"] or 0,
			"answer_pass_rate": metrics["answer_pass_rate"] or 0,
			"critical_failures": metrics["critical_failures"],
			"passed_gate": 1 if check["passed"] else 0,
			"result_json": json.dumps(
				{"meta": meta, "metrics": metrics, "gate": check, "cases": results}, ensure_ascii=False
			),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	write_report(doc.name)
	return doc.name


def _unmeasured_row(case: dict, reason: str) -> dict:
	return {
		"case_id": case["case_id"],
		"category": category(case["case_id"]),
		"source": case["source"],
		"critical": case["critical"],
		"expected_action": case["expected_action"],
		"expected_group": case["expected_group"],
		"question": None,
		"reference": None,
		"field_checked": 0,
		"field_correct": 0,
		"field_wrong": {},
		"action": None,
		"action_correct": None,
		"answer_pass": None,
		"judge": None,
		"reply_text": None,
		"flags": [],
		"critical_reasons": [],
		"unmeasured": reason,
		"status": None,
	}


# --- Báo cáo ---------------------------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
	return "chưa đo" if value is None else f"{value:.1%}"


def _failure_lines(results: list[dict]) -> dict[str, list[str]]:
	"""Ca trượt gom theo loại (tên loại -> các dòng)."""
	grouped: dict[str, list[str]] = {}
	for r in results:
		why = []
		if r["unmeasured"]:
			why.append(f"CHƯA ĐO: {r['unmeasured']}")
		else:
			if r["field_wrong"]:
				why.append(
					"trường sai: "
					+ ", ".join(f"{k} (kỳ vọng {v[0]!r}, bot {v[1]!r})" for k, v in r["field_wrong"].items())
				)
			if r["action_correct"] is False:
				why.append(f"hành động: kỳ vọng {r['expected_action']}, bot {r['action']}")
				if r["flags"]:
					why.append("chốt chặn: " + "; ".join(r["flags"]))
			if r["answer_pass"] is False:
				why.append("câu trả lời trượt giám khảo: " + ((r.get("judge") or {}).get("reason") or ""))
			if r["critical_reasons"]:
				why.append("NGHIÊM TRỌNG: " + ", ".join(r["critical_reasons"]))
		if why:
			mark = " [critical]" if r["critical"] else ""
			text = f" - “{r['question'][:100]}”" if r.get("question") and r["source"] == "Synthetic" else ""
			grouped.setdefault(r["category"], []).append(
				f"- `{r['case_id']}`{mark}{text}: " + " | ".join(why)
			)
	return grouped


def render_report(name: str) -> str:
	"""Nội dung báo cáo Markdown của một Omni Eval Run."""
	doc = frappe.get_doc(RUN, name)
	data = _json(doc.result_json, {})
	meta, metrics, check, results = data["meta"], data["metrics"], data["gate"], data["cases"]
	rule = grading.GATES[meta["gate"]]
	fake = meta["mode"] == "Fake"
	lines = [
		f"# Báo cáo chạy bộ đề — {nowdate()} — {doc.prompt_version}",
		"",
		f"- Lần chạy: `{name}`; bắt đầu {doc.started_at}; cổng kiểm: **{meta['gate']}**",
		f"- Chế độ: **{meta['mode']}**"
		+ (
			" — client giả trả đúng kỳ vọng; chỉ kiểm hạ tầng bộ chấm, **KHÔNG PHẢI điểm của bot**"
			if fake
			else ""
		),
		f"- Bộ đề: {metrics['total_cases']} ca ("
		+ ", ".join(f"{s} {sum(1 for r in results if r['source'] == s)}" for s in ("Real", "Synthetic"))
		+ f"); **{'đã được người duyệt' if meta['reviewed'] else 'CHƯA được người duyệt'}**",
		f"- Kho tri thức: {doc.kb_version} (nguồn {meta['kb_source']}); mô hình: {doc.model}; che SĐT/email khi gửi AI: {'có' if meta['mask_pii'] else 'không'}",
		"",
		"## Chỉ số",
		"",
		"| Chỉ số | Kết quả | Ngưỡng cổng |",
		"| --- | --- | --- |",
	]
	for key, label in (
		("field_accuracy", "Trường trích xuất đúng"),
		("action_accuracy", "Hành động đúng"),
		("answer_pass_rate", "Câu trả lời đạt"),
	):
		th = rule.get("min", {}).get(key)
		lines.append(f"| {label} | {_pct(metrics[key])} | {'≥ ' + format(th, '.0%') if th else '—'} |")
	crit = "0" if "max_critical" not in rule else str(rule["max_critical"])
	lines.append(
		f"| Lỗi nghiêm trọng | {metrics['critical_failures']} | ≤ {crit} |"
		if "max_critical" in rule
		else f"| Lỗi nghiêm trọng | {metrics['critical_failures']} | — |"
	)
	lines += [
		"",
		f"Ca đo được: {metrics['measured_cases']}/{metrics['total_cases']}; chưa đo: {metrics['unmeasured_cases']}.",
		"",
		f"## Kết luận cổng `{meta['gate']}`: " + ("**QUA**" if check["passed"] else "**KHÔNG QUA**"),
		"",
	]
	lines += [f"- {r}" for r in check["reasons"]] or ["- (không có lý do chặn)"]
	if fake:
		lines += ["", "> Kết quả Fake không được dùng để bật bot hay ghi vào điều kiện cổng thật."]
	elif not meta["reviewed"]:
		lines += ["", "> Bộ đề chưa được người duyệt: số liệu chỉ để tham khảo, chưa đủ làm căn cứ bật bot."]
	grouped = _failure_lines(results)
	lines += ["", "## Ca trượt theo loại", ""]
	if not grouped:
		lines.append("(không có)")
	for cat, rows in sorted(grouped.items()):
		lines += [f"### {cat} ({len(rows)})", "", *rows, ""]
	by_cat: dict[str, list[dict]] = {}
	for r in results:
		by_cat.setdefault(r["category"], []).append(r)
	lines += [
		"## Tổng quan theo loại",
		"",
		"| Loại | Số ca | Chưa đo | Hành động sai | Nghiêm trọng |",
		"| --- | --- | --- | --- | --- |",
	]
	for cat, rows in sorted(by_cat.items()):
		lines.append(
			f"| {cat} | {len(rows)} | {sum(1 for r in rows if r['unmeasured'])} | "
			f"{sum(1 for r in rows if r['action_correct'] is False)} | {sum(1 for r in rows if r['critical_reasons'])} |"
		)
	return "\n".join(lines) + "\n"


def write_report(name: str) -> str:
	"""Ghi `eval/reports/<ngày>-<prompt_version>.md`; trả đường dẫn. Cùng ngày cùng phiên bản thì thêm tên lần chạy."""
	doc = frappe.get_doc(RUN, name)
	root = Path(frappe.get_app_path("omni_crm")).parent / REPORT_DIR
	root.mkdir(parents=True, exist_ok=True)
	path = root / f"{nowdate()}-{doc.prompt_version}.md"
	if path.exists() and name not in path.read_text(encoding="utf-8"):
		path = root / f"{nowdate()}-{doc.prompt_version}-{name}.md"
	path.write_text(render_report(name), encoding="utf-8")
	return str(path)


# --- Người chấm lại ------------------------------------------------------------------------------------------------

REGRADE_COLUMNS = (
	"case_id",
	"ly_do_vao_mau",
	"cau_khach",
	"cau_tra_loi_bot",
	"cau_tham_chieu",
	"giam_khao_dat",
	"giam_khao_ly_do",
	"ket_luan_nguoi",
	"truot_r3_hoac_r5",
	"ghi_chu",
)


def regrade_sample(results: list[dict], run_name: str) -> list[tuple[dict, str]]:
	"""20% ca đã chấm câu trả lời (ngẫu nhiên, cố định theo tên lần chạy) cộng MỌI ca giám khảo cho trượt."""
	judged = [r for r in results if r.get("judge")]
	failed = [r for r in judged if not r["judge"]["pass"] or r["judge"]["critical_failure"]]
	failed_ids = {r["case_id"] for r in failed}
	rest = [r for r in judged if r["case_id"] not in failed_ids]
	k = min(len(rest), max(math.ceil(RESAMPLE_RATE * len(judged)), 1 if judged else 0))
	picked = random.Random(run_name).sample(rest, k) if rest else []
	return [(r, "giám khảo cho trượt") for r in failed] + [(r, "ngẫu nhiên") for r in picked]


def export_regrade_csv(run_name: str, path: str | None = None) -> str:
	"""Xuất CSV cho người chấm lại (cột `ket_luan_nguoi`: Dat/Truot, `truot_r3_hoac_r5`: co/khong); trả đường dẫn."""
	doc = frappe.get_doc(RUN, run_name)
	results = _json(doc.result_json, {}).get("cases", [])
	out = (
		Path(path)
		if path
		else Path(frappe.get_app_path("omni_crm")).parent
		/ REPORT_DIR
		/ f"{nowdate()}-{doc.prompt_version}-cham-lai.csv"
	)
	out.parent.mkdir(parents=True, exist_ok=True)
	with open(out, "w", encoding="utf-8-sig", newline="") as f:
		writer = csv.writer(f)
		writer.writerow(REGRADE_COLUMNS)
		for r, why in regrade_sample(results, run_name):
			j = r["judge"]
			writer.writerow(
				[
					r["case_id"],
					why,
					r.get("question") or "",
					r.get("reply_text") or "",
					r.get("reference") or "",
					"co" if j["pass"] else "khong",
					j["reason"],
					"",
					"",
					"",
				]
			)
	return str(out)


def parse_regrade_csv(path: str) -> dict[str, dict]:
	"""Đọc CSV người chấm đã điền: {case_id: {"pass": bool, "critical": bool, "note": str}}; dòng chưa điền bị bỏ."""
	reviews: dict[str, dict] = {}
	with open(path, encoding="utf-8-sig", newline="") as f:
		for row in csv.DictReader(f):
			verdict = (row.get("ket_luan_nguoi") or "").strip().lower()
			if verdict not in ("dat", "truot"):
				continue
			crit = (row.get("truot_r3_hoac_r5") or "").strip().lower() in ("co", "có", "1", "x")
			reviews[row["case_id"]] = {
				"pass": verdict == "dat",
				"critical": crit,
				"note": row.get("ghi_chu") or "",
			}
	return reviews


def apply_reviews(results: list[dict], reviews: dict[str, dict]) -> list[dict]:
	"""Người chấm THAY kết luận giám khảo cho ca được chấm lại (pass và R3/R5); lỗi (a)/(c) của code giữ nguyên."""
	out = []
	for r in results:
		r = dict(r)
		rv = reviews.get(r["case_id"])
		if not rv:  # ca không còn trong danh sách chấm tay: trả về kết quả tự động
			r["critical_reasons"] = list(r.get("critical_reasons_auto", r["critical_reasons"]))
			r["answer_pass"] = r.get("answer_pass_auto", r["answer_pass"])
			r.pop("human", None)
		if rv and r.get("judge"):
			if r["expected_action"] == "Reply":
				r["answer_pass"] = rv["pass"] and not rv["critical"]
			auto = r.get(
				"critical_reasons_auto", r["critical_reasons"]
			)  # luôn tính từ kết quả tự động, không cộng dồn
			kept = [x for x in auto if not x.startswith("judge_")]
			r["critical_reasons"] = kept + (["nguoi_cham_r3_r5"] if rv["critical"] else [])
			r["human"] = rv
		out.append(r)
	return out


def import_regrade(run_name: str, path: str) -> dict[str, Any]:
	"""Nhập CSV chấm tay, tính lại chỉ số cuối và cổng, cập nhật Omni Eval Run + báo cáo; trả chỉ số mới."""
	doc = frappe.get_doc(RUN, run_name)
	data = _json(doc.result_json, {})
	reviews = parse_regrade_csv(path)
	known = {r["case_id"] for r in data["cases"] if r.get("judge")}
	unknown = sorted(set(reviews) - known)
	if unknown:
		frappe.throw(
			_("Ca không có trong lần chạy hoặc chưa được giám khảo chấm: {0}").format(", ".join(unknown))
		)
	meta = data["meta"]
	meta["human_reviews"] = {**meta.get("human_reviews", {}), **reviews}
	cases = apply_reviews(data["cases"], meta["human_reviews"])
	metrics = grading.aggregate(cases)
	baseline = _baseline() if meta["gate"] == "doi_cau_lenh" else None
	check = grading.gate_check(meta["gate"], metrics, baseline)
	doc.update(
		{
			"field_accuracy": metrics["field_accuracy"] or 0,
			"action_accuracy": metrics["action_accuracy"] or 0,
			"answer_pass_rate": metrics["answer_pass_rate"] or 0,
			"critical_failures": metrics["critical_failures"],
			"passed_gate": 1 if check["passed"] else 0,
			"result_json": json.dumps(
				{"meta": meta, "metrics": metrics, "gate": check, "cases": cases}, ensure_ascii=False
			),
		}
	)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	write_report(run_name)
	return {"metrics": metrics, "gate": check, "reviewed_cases": len(reviews)}


def accept_run(run_name: str) -> None:
	"""Đánh dấu lần chạy (đã qua cổng, chạy bằng AI thật, bộ đề đã duyệt) là mốc cho cổng `doi_cau_lenh`."""
	doc = frappe.get_doc(RUN, run_name)
	data = _json(doc.result_json, {})
	meta = data["meta"]
	if not doc.passed_gate:
		frappe.throw(_("Chỉ chấp nhận lần chạy đã qua cổng"))
	if meta["mode"] == "Fake" or not meta["reviewed"]:
		frappe.throw(_("Chỉ chấp nhận lần chạy bằng AI thật trên bộ đề đã được người duyệt"))
	meta["accepted"] = True
	doc.result_json = json.dumps(data, ensure_ascii=False)
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def run_cli(
	gate: str = "phan_tich",
	fake: bool = False,
	allow_real: bool = False,
	judge: bool = True,
	cases: str | dict | None = None,
	kb_file: str | None = None,
	groups_file: str | None = None,
	reviewed: bool = False,
) -> str:
	"""Lệnh bench: chạy bộ đề cho một cổng, in tóm tắt và đường dẫn báo cáo.

	`fake=True`: dùng client giả (chỉ kiểm hạ tầng). Không `fake` và không `allow_real`: chỉ chạy nếu chưa có khóa
	(mọi ca ghi "chưa đo"), không bao giờ tự gọi API thật.
	"""
	previous = ai_client.get_client()
	if fake:
		ai_client.set_client(OracleClient())
	try:
		flt = json.loads(cases) if isinstance(cases, str) else cases
		name = run(
			flt,
			judge=judge,
			gate=gate,
			kb_file=kb_file,
			groups_file=groups_file,
			allow_real=allow_real,
			reviewed=reviewed,
		)
	finally:
		if fake:
			ai_client.set_client(previous)
	data = _json(frappe.db.get_value(RUN, name, "result_json"), {})
	print(
		json.dumps(
			{"run": name, "metrics": data["metrics"], "gate": data["gate"]}, ensure_ascii=False, indent=1
		)
	)
	return name
