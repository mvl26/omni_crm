"""Đo các chỉ số nghiệm thu Đợt 3 (docs/00 mục 2, docs/08 mục 10) từ dữ liệu thật trong một khoảng thời gian.

Chạy cuối tuần chạy thử (CHỈ ĐỌC, không ghi gì):

	bench --site <site> execute omni_crm.tests.acceptance.measure_dot3.run \
		--kwargs '{"start": "2026-11-23 00:00:00", "end": "2026-11-30 00:00:00"}'

Bốn nhóm chỉ số:
1. Tỉ lệ nháp đúng = (Sent As Is + Sent Edited có `edit_distance_ratio` <= 0,1) / MỌI nháp Draft đã quyết định
   (kể cả Discarded). ĐÚNG định nghĩa của `auto_send.eligibility` (import hằng số, không định nghĩa lại).
2. Lỗi nghiêm trọng = nháp đánh dấu `critical`, hoặc chấm Sai kèm ghi chú, và số lần ngắt mạch tự tắt nhóm.
3. Tỉ lệ bot tự xử lý theo nhóm = tin Bot đã gửi (mode Auto, Sent As Is) / mọi nháp Draft+Auto của nhóm. Bằng 0 nếu
   chưa nhóm nào được mở Auto.
4. Thời gian phản hồi lần đầu: tin khách cuối của lượt -> tin ra đầu tiên gắn với nháp (sale hoặc bot), theo chế độ; kèm số
   đo Đợt 1 (`measure_dot1.first_response`, giờ hành chính).

QUY TẮC TRUNG THỰC (như measure_dot2): KHÔNG có số nào ở đây là "số đo nghiệm thu" nếu dữ liệu không phải khách thật:
mặc định bỏ Lead dữ liệu giả; tin ra `is_sandbox = 1` không tới khách nên bị loại khỏi thời gian phản hồi (trừ khi
`include_sandbox`); cổng `nhap` chỉ tính là số đo khi lần chạy là AI thật trên bộ đề đã duyệt (`counts_as_measurement`).
Thiếu mẫu thì in "chưa đủ dữ liệu" thay vì ngầm đưa ra tỉ lệ.
"""

from __future__ import annotations

import json
import math
from typing import Any

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from omni_crm.bot import auto_send
from omni_crm.tests.acceptance import measure_dot1
from omni_crm.tests.acceptance.measure_dot2 import _prefix_clause

DRAFT = "Omni Bot Draft"
OUTBOUND = "Omni Outbound Message"
DEFAULT_EXCLUDE = ("LOAD", "PLT", "TD", "T05", "D209", "D306", "DEMO", "RV", "MD")
MIN_SAMPLE = auto_send.MIN_DECIDED  # dưới mức này in "chưa đủ dữ liệu"
GATE_DRAFT_RATE = 0.85  # docs/00 mục 2: câu trả lời của bot đúng >= 85% (nghiệm thu)


def _pct(vals: list[float], p: float) -> float | None:
	if not vals:
		return None
	vals = sorted(vals)
	return round(vals[max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))], 2)


def _draft_rows(start, end, prefixes: tuple[str, ...], modes: tuple[str, ...]) -> list[Any]:
	cond, args = _prefix_clause("coalesce(l.first_name, '')", prefixes)
	return frappe.db.sql(
		f"""select d.name, d.lead, d.mode, d.action, d.decision, d.question_group, d.edit_distance_ratio,
			d.rating, d.rating_note, d.critical, d.batch_id, d.decided_at, d.creation
		from `tab{DRAFT}` d left join `tabCRM Lead` l on l.name = d.lead
		where d.creation between %s and %s and d.mode in %s and {cond}""",
		[start, end, modes, *args],
		as_dict=True,
	)


def _is_good(row: Any) -> bool:
	return row.decision in auto_send.GOOD and (row.edit_distance_ratio or 0) <= auto_send.MAX_EDIT_RATIO


def draft_quality(start, end, prefixes: tuple[str, ...]) -> dict[str, Any]:
	"""Tỉ lệ nháp đúng (theo định nghĩa eligibility), tổng và theo nhóm câu hỏi; chỉ nháp mode Draft."""
	rows = _draft_rows(start, end, prefixes, ("Draft",))
	decided = [r for r in rows if r.decision in auto_send.DECIDED]
	by_group: dict[str, dict[str, Any]] = {}
	for r in decided:
		g = by_group.setdefault(r.question_group or "(không nhóm)", {"decided": 0, "good": 0})
		g["decided"] += 1
		g["good"] += 1 if _is_good(r) else 0
	for g in by_group.values():
		g["ratio"] = round(g["good"] / g["decided"], 4) if g["decided"] >= MIN_SAMPLE else None
		g["enough_data"] = g["decided"] >= MIN_SAMPLE
	good = sum(1 for r in decided if _is_good(r))
	return {
		"drafts_created": len(rows),
		"decided": len(decided),
		"sent_as_is": sum(1 for r in decided if r.decision == "Sent As Is"),
		"sent_edited": sum(1 for r in decided if r.decision == "Sent Edited"),
		"discarded": sum(1 for r in decided if r.decision == "Discarded"),
		"pending": sum(1 for r in rows if r.decision == "Pending"),
		"expired": sum(1 for r in rows if r.decision == "Expired"),
		"handoff_drafts": sum(1 for r in rows if r.action == "Handoff"),
		"good": good,
		"min_sample": MIN_SAMPLE,
		"enough_data": len(decided) >= MIN_SAMPLE,
		"ratio_good": round(good / len(decided), 4) if len(decided) >= MIN_SAMPLE else None,
		"by_group": by_group,
		"note": f"Đúng = gửi nguyên văn hoặc sửa nhẹ (tỉ lệ sửa <= {auto_send.MAX_EDIT_RATIO}); mẫu số gồm cả nháp bị bỏ; Pending/Expired không tính",
	}


def serious_errors(start, end, prefixes: tuple[str, ...]) -> dict[str, Any]:
	"""Lỗi nghiêm trọng: nháp `critical`, chấm Sai kèm ghi chú (mọi chế độ), tin bot tự gửi bị chấm Sai, ngắt mạch."""
	rows = _draft_rows(start, end, prefixes, ("Draft", "Auto"))
	critical = [r.name for r in rows if cint(r.critical)]
	wrong_note = [r.name for r in rows if r.rating == "Sai" and (r.rating_note or "").strip()]
	auto_wrong = [r.name for r in rows if r.mode == "Auto" and r.rating == "Sai"]
	trips = frappe.db.count(
		"Comment",
		{
			"reference_doctype": "Omni Question Group",
			"content": ["like", f"%{auto_send.BREAKER_MARK}%"],
			"creation": ["between", [start, end]],
		},
	)
	return {
		"critical_flagged": len(critical),
		"wrong_with_note": len(wrong_note),
		"auto_sent_rated_wrong": len(auto_wrong),
		"breaker_trips": trips,
		"total_serious": len(set(critical) | set(wrong_note) | set(auto_wrong)),
		"note": "Mục tiêu 0. Cổng nháp (docs/08 mục 10) tính trên bộ đề; đây là số trên khách thật do sale đánh dấu",
	}


def bot_self_handled(start, end, prefixes: tuple[str, ...]) -> dict[str, Any]:
	"""Theo nhóm: tin Bot đã gửi tự động / mọi nháp Draft+Auto của nhóm. 0 nếu chưa nhóm nào mở Auto."""
	rows = _draft_rows(start, end, prefixes, ("Draft", "Auto"))
	groups: dict[str, dict[str, Any]] = {}
	for r in rows:
		g = groups.setdefault(
			r.question_group or "(không nhóm)", {"drafts": 0, "auto_sent": 0, "auto_rated_wrong": 0}
		)
		g["drafts"] += 1
		if r.mode == "Auto" and r.decision == "Sent As Is":
			g["auto_sent"] += 1
			g["auto_rated_wrong"] += 1 if r.rating == "Sai" else 0
	for g in groups.values():
		g["enough_data"] = g["drafts"] >= MIN_SAMPLE
		g["ratio_self_handled"] = (
			round(g["auto_sent"] / g["drafts"], 4) if g["drafts"] >= MIN_SAMPLE else None
		)
	return {
		"groups": groups,
		"auto_sent_total": sum(g["auto_sent"] for g in groups.values()),
		"groups_with_auto": sum(1 for g in groups.values() if g["auto_sent"]),
		"note": "Chưa nhóm nào mở Auto (mặc định) thì mọi tỉ lệ này bằng 0, đó là trạng thái đúng chứ không phải lỗi",
	}


def first_response_times(start, end, prefixes: tuple[str, ...], include_sandbox: bool) -> dict[str, Any]:
	"""Từ tin khách cuối của lượt đến tin ra gắn với nháp của lượt đó (sale hoặc bot), tính bằng phút, theo chế độ."""
	cond, args = _prefix_clause("coalesce(l.first_name, '')", prefixes)
	sandbox_clause = "" if include_sandbox else "and o.is_sandbox = 0"
	rows = frappe.db.sql(
		f"""select d.mode, o.source,
			timestampdiff(second,
				(select max(m.received_at) from `tabOmni Inbound Message` m where m.batch_id = d.batch_id),
				coalesce(o.sent_at, o.creation)) as secs
		from `tab{DRAFT}` d join `tab{OUTBOUND}` o on o.bot_draft = d.name
		left join `tabCRM Lead` l on l.name = d.lead
		where d.creation between %s and %s and o.status in ('Sent', 'Queued') {sandbox_clause} and {cond}""",
		[start, end, *args],
		as_dict=True,
	)
	out: dict[str, Any] = {}
	for key in ("Draft", "Auto"):
		mins = [r.secs / 60 for r in rows if r.mode == key and r.secs is not None and r.secs >= 0]
		out[key] = {
			"n": len(mins),
			"median_min": _pct(mins, 0.5),
			"p90_min": _pct(mins, 0.9),
			"enough_data": len(mins) >= MIN_SAMPLE,
		}
	return out


def handoff_gaps(start, end, prefixes: tuple[str, ...]) -> dict[str, Any]:
	"""Nháp Handoff (mode Draft) theo nhóm câu hỏi và theo lý do chốt chặn; gom riêng các ca "ngoài kho tri thức" để biết kho
	cần bổ sung gì. Chỉ đếm, không có nội dung khách hay nháp."""
	rows = _draft_rows(start, end, prefixes, ("Draft",))
	handoff = [r for r in rows if r.action == "Handoff"]
	flag_rows = frappe.db.sql(
		"select name, guard_flags from `tabOmni Bot Draft` where name in %s",
		(tuple(r.name for r in handoff) or ("",),),
	)
	by_group: dict[str, int] = {}
	by_flag: dict[str, int] = {}
	outside_kb_by_group: dict[str, int] = {}
	names_by_group = {r.name: (r.question_group or "(không nhóm)") for r in handoff}
	for name, raw in flag_rows:
		group = names_by_group[name]
		by_group[group] = by_group.get(group, 0) + 1
		codes = {f.split(":", 1)[0] for f in (raw or "").splitlines() if f and not f.startswith("info:")}
		for code in codes:
			by_flag[code] = by_flag.get(code, 0) + 1
		if any(c.endswith("_ngoai_kho") or c.startswith("kb_") for c in codes):
			outside_kb_by_group[group] = outside_kb_by_group.get(group, 0) + 1
	return {
		"handoff_drafts": len(handoff),
		"by_question_group": dict(sorted(by_group.items(), key=lambda kv: -kv[1])),
		"by_guard_code": dict(sorted(by_flag.items(), key=lambda kv: -kv[1])),
		"outside_knowledge_base_by_group": dict(sorted(outside_kb_by_group.items(), key=lambda kv: -kv[1])),
		"note": "Nhóm có nhiều ca ngoài kho tri thức là chỗ cần bổ sung kho; nhóm luôn chuyển người (Hỏi giá, Khiếu nại) là Handoff theo thiết kế, không phải lỗ hổng",
	}


def nhap_gate() -> dict[str, Any]:
	"""Lần chạy bộ đề cổng `nhap` gần nhất: chỉ là số đo khi là AI thật trên bộ đề đã duyệt."""
	runs = frappe.get_all(
		"Omni Eval Run",
		fields=[
			"name",
			"started_at",
			"total_cases",
			"field_accuracy",
			"action_accuracy",
			"answer_pass_rate",
			"critical_failures",
			"passed_gate",
			"result_json",
		],
		order_by="started_at desc",
		limit_page_length=50,
	)
	for row in runs:
		try:
			meta = (json.loads(row.result_json or "{}") or {}).get("meta") or {}
		except ValueError:
			continue
		if meta.get("gate") != "nhap":
			continue
		real = meta.get("mode") == "AI thật"
		counts = bool(real and meta.get("reviewed"))
		return {
			"run": row.name,
			"started_at": str(row.started_at),
			"mode": meta.get("mode"),
			"reviewed": meta.get("reviewed"),
			"total_cases": row.total_cases,
			"action_accuracy": row.action_accuracy if real else None,
			"answer_pass_rate": row.answer_pass_rate if real else None,
			"critical_failures": row.critical_failures if real else None,
			"passed_gate": bool(row.passed_gate),
			"counts_as_measurement": counts,
			"note": "Chỉ AI thật trên bộ đề đã duyệt mới là số đo; Fake/không client chỉ kiểm hạ tầng chấm điểm",
		}
	return {"run": None, "counts_as_measurement": False, "note": "Chưa có lần chạy cổng nhap nào"}


def run(
	start: str,
	end: str | None = None,
	exclude_prefixes: str | list | None = None,
	include_sandbox: int | str = 0,
) -> None:
	"""Tính chỉ số Đợt 3 trong [start, end) (giờ theo múi giờ của site) và in JSON + bảng Markdown."""
	s = get_datetime(start)
	e = get_datetime(end) if end else now_datetime()
	prefixes = DEFAULT_EXCLUDE
	if isinstance(exclude_prefixes, str):
		prefixes = tuple(x for x in exclude_prefixes.split(",") if x)
	elif exclude_prefixes:
		prefixes = tuple(exclude_prefixes)
	excl_channels = measure_dot1._excluded_channels(measure_dot1.DEFAULT_EXCLUDE)
	res = {
		"period": [str(s), str(e)],
		"draft_quality": draft_quality(s, e, prefixes),
		"serious_errors": serious_errors(s, e, prefixes),
		"bot_self_handled": bot_self_handled(s, e, prefixes),
		"first_response_after_draft": first_response_times(s, e, prefixes, bool(cint(include_sandbox))),
		"first_response_dot1": measure_dot1.first_response(s, e, excl_channels, bool(cint(include_sandbox))),
		"handoff_gaps": handoff_gaps(s, e, prefixes),
		"nhap_gate": nhap_gate(),
	}
	print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
	print(_table(res))


def _verdict(ok: bool | None) -> str:
	return "chưa đủ dữ liệu" if ok is None else ("ĐẠT" if ok else "KHÔNG ĐẠT")


def _table(r: dict[str, Any]) -> str:
	dq, se, nh = r["draft_quality"], r["serious_errors"], r["nhap_gate"]
	ratio = dq["ratio_good"]
	gate_ok = bool(nh["passed_gate"]) if nh.get("counts_as_measurement") else None
	fr = r["first_response_dot1"]
	rows = [
		(
			"Nháp đúng (nguyên văn hoặc sửa nhẹ)",
			">= 85% (nghiệm thu)",
			ratio,
			_verdict(None if ratio is None else ratio >= GATE_DRAFT_RATE),
		),
		(
			"Lỗi nghiêm trọng trên khách thật",
			"0",
			se["total_serious"] if dq["enough_data"] else None,
			_verdict(None if not dq["enough_data"] else se["total_serious"] == 0),
		),
		(
			"Cổng nhap (AI thật, bộ đề đã duyệt)",
			"hành động >= 90%, trả lời >= 85%, nghiêm trọng 0",
			nh.get("answer_pass_rate"),
			_verdict(gate_ok),
		),
		(
			"Phản hồi lần đầu giờ hành chính (Đợt 1)",
			"< 15 phút (trung vị)",
			fr.get("median_min"),
			_verdict(None if fr.get("median_min") is None else fr["median_min"] < 15),
		),
	]
	lines = ["| Chỉ số | Ngưỡng | Đo được | Kết luận |", "| --- | --- | --- | --- |"]
	lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
	return "\n".join(lines)
