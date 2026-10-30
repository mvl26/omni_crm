"""Đo các chỉ số nghiệm thu Đợt 2 (docs/00 mục 2) từ dữ liệu thật trong một khoảng thời gian.

Chạy cuối tuần chạy thử (CHỈ ĐỌC, không ghi gì):

	bench --site <site> execute omni_crm.tests.acceptance.measure_dot2.run \
		--kwargs '{"start": "2026-11-09 00:00:00", "end": "2026-11-16 00:00:00"}'

Hai chỉ số của Đợt 2:
1. "Gọi nhỡ được gọi lại đúng hạn" (ngưỡng >= 80%): đo trực tiếp từ DB (mục `callbacks_on_time`).
2. "Trường trích xuất đúng" (ngưỡng >= 90%): số đo chính thức là kết quả bộ đề đã duyệt (cổng `phan_tich`), script chỉ
   in lần chạy bộ đề gần nhất (mục `eval_gate`). Từ DB chỉ có ĐẠI DIỆN: tỉ lệ giá trị bot điền mà sale không sửa
   (mục `extraction_proxy`). Sale không sửa KHÔNG có nghĩa là đúng (sale có thể không đọc), nên đại diện này chỉ để theo
   dõi xu hướng, không thay cho bộ đề.

Tham số tùy chọn: `exclude_prefixes` (bỏ Lead/cuộc gọi dữ liệu giả; mặc định LOAD, PLT, TD, T05, D209, DEMO, RV, MD).
Định nghĩa từng chỉ số: docs/nghiem-thu/dot-2.md mục 4.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

DEFAULT_EXCLUDE = ("LOAD", "PLT", "TD", "T05", "D209", "DEMO", "RV", "MD", "call-vn-1-FAKE")
# Trường bot ghi vào Lead: khóa trong output_json["accepted"] -> trường của CRM Lead
FIELD_MAP = {
	"need": "omni_need",
	"products": "omni_products",
	"quantity": "omni_quantity",
	"timeframe": "omni_timeframe",
	"urgency": "omni_urgency",
	"intent": "omni_intent",
}
OPEN_TASK = ("Backlog", "Todo", "In Progress")


def _prefix_clause(column: str, prefixes: tuple[str, ...]) -> tuple[str, list[str]]:
	"""Mệnh đề `column not like 'p%' and ...` (không phân biệt hoa thường) cho các tiền tố."""
	if not prefixes:
		return "1=1", []
	return " and ".join(f"lower({column}) not like %s" for _p in prefixes), [
		f"{p.lower()}%" for p in prefixes
	]


def _norm(value: Any) -> str:
	if isinstance(value, list | tuple):
		value = ", ".join(str(v) for v in value)
	return " ".join(str(value or "").lower().split())


def _call_time(row: Any) -> datetime | None:
	return row.answered_at or row.ended_at or row.started_at


def callbacks_on_time(start, end, prefixes: tuple[str, ...], now=None) -> dict[str, Any]:
	"""Mỗi CRM Task gọi lại tạo trong kỳ là một mẫu; đúng hạn = có cuộc gọi nghe máy (đến hoặc đi) sau lần nhỡ cuối
	trước `due_date`, hoặc (không có cuộc gọi) sale đóng việc trước `due_date`. Việc mở chưa đến hạn không tính."""
	now = now or now_datetime()
	cond, args = _prefix_clause("c.call_id", prefixes)
	tasks = frappe.db.sql(
		f"""select t.name, t.status, t.due_date, t.modified, t.assigned_to, c.lead,
			max(coalesce(c.ended_at, c.started_at)) as last_missed
		from `tabCRM Task` t join `tabOmni Call` c on c.callback_task = t.name
		where t.creation between %s and %s and {cond}
		group by t.name, t.status, t.due_date, t.modified, t.assigned_to, c.lead""",
		[start, end, *args],
		as_dict=True,
	)
	on_time = late = pending = by_call = by_hand = 0
	unassigned = 0
	for t in tasks:
		unassigned += 0 if t.assigned_to else 1
		answer = None
		if t.lead and t.last_missed:
			rows = frappe.get_all(
				"Omni Call",
				filters={"lead": t.lead, "status": "Answered"},
				fields=["answered_at", "ended_at", "started_at"],
			)
			times = [x for x in (_call_time(r) for r in rows) if x and x >= t.last_missed]
			answer = min(times) if times else None
		due = get_datetime(t.due_date) if t.due_date else None
		if answer is not None:
			by_call += 1
			ok = due is None or answer <= due
			on_time, late = on_time + ok, late + (not ok)
		elif t.status not in OPEN_TASK:
			by_hand += 1  # đóng tay (Done/Cancelled) mà không có cuộc gọi nghe máy ghi nhận
			ok = due is None or t.modified <= due
			on_time, late = on_time + ok, late + (not ok)
		elif due is not None and due < now:
			late += 1
		else:
			pending += 1
	measured = on_time + late
	cond2, args2 = _prefix_clause("call_id", prefixes)
	base = f"creation between %s and %s and direction = 'Inbound' and status = 'Missed' and {cond2}"
	missed_total = frappe.db.sql(f"select count(*) from `tabOmni Call` where {base}", [start, end, *args2])[
		0
	][0]
	hidden = frappe.db.sql(
		f"select count(*) from `tabOmni Call` where {base} and lead is null", [start, end, *args2]
	)[0][0]
	no_task = frappe.db.sql(
		f"select count(*) from `tabOmni Call` where {base} and lead is not null and callback_task is null",
		[start, end, *args2],
	)[0][0]
	return {
		"missed_inbound_calls": missed_total,
		"missed_hidden_number_no_task": hidden,
		"missed_with_lead_but_no_task": no_task,
		"tasks_in_period": len(tasks),
		"tasks_unassigned": unassigned,
		"measured": measured,
		"on_time": on_time,
		"late": late,
		"pending_not_due_yet": pending,
		"closed_by_answered_call": by_call,
		"closed_by_hand_without_call": by_hand,
		"ratio_on_time": round(on_time / measured, 4) if measured else None,
		"note": "Mẫu số là việc gọi lại (một khách nhỡ nhiều lần = một việc); cuộc gọi ẩn số không có việc và không nằm trong mẫu số",
	}


def extraction_proxy(start, end, prefixes: tuple[str, ...]) -> dict[str, Any]:
	"""Đại diện cho "trường trích xuất đúng": trong số giá trị bot đã ghi vào Lead (Bot Run Analyze Success), bao nhiêu
	giá trị còn nguyên sau đó. KHÔNG thay cho bộ đề đã duyệt."""
	cond, args = _prefix_clause("coalesce(l.first_name, '')", prefixes)
	runs = frappe.db.sql(
		f"""select r.name, r.lead, r.output_json from `tabOmni Bot Run` r join `tabCRM Lead` l on l.name = r.lead
		where r.kind = 'Analyze' and r.status = 'Success' and r.creation between %s and %s and {cond}
		order by r.creation""",
		[start, end, *args],
		as_dict=True,
	)
	filled = kept = changed = 0
	latest: dict[tuple[str, str], str] = {}  # (lead, trường) -> giá trị bot ghi gần nhất trong kỳ
	for r in runs:
		try:
			accepted = (json.loads(r.output_json or "{}") or {}).get("accepted") or {}
		except ValueError:
			continue
		for key, field in FIELD_MAP.items():
			if _norm(accepted.get(key)):
				latest[(r.lead, field)] = _norm(accepted[key])
	for (lead, field), bot_value in latest.items():
		current = _norm(frappe.db.get_value("CRM Lead", lead, field))
		filled += 1
		if current == bot_value:
			kept += 1
		else:
			changed += 1
	total_runs = frappe.db.count("Omni Bot Run", {"kind": "Analyze", "creation": ["between", [start, end]]})
	by_status = dict(
		frappe.db.sql(
			"select status, count(*) from `tabOmni Bot Run` where kind = 'Analyze' and creation between %s and %s group by status",
			(start, end),
		)
	)
	return {
		"analyze_runs": total_runs,
		"analyze_runs_by_status": by_status,
		"leads_with_bot_fields": len({lead for lead, _f in latest}),
		"values_filled_by_bot": filled,
		"values_kept": kept,
		"values_changed_or_cleared_since": changed,
		"ratio_kept_proxy": round(kept / filled, 4) if filled else None,
		"note": "Đại diện, không phải độ đúng: sale không sửa không có nghĩa là bot đúng. Số đo chính thức là cổng phan_tich của bộ đề đã duyệt",
	}


def eval_gate() -> dict[str, Any]:
	"""Lần chạy bộ đề gần nhất (Omni Eval Run): chế độ, bộ đề đã duyệt chưa, cổng có qua không."""
	row = frappe.db.get_value(
		"Omni Eval Run",
		{},
		[
			"name",
			"started_at",
			"total_cases",
			"field_accuracy",
			"critical_failures",
			"passed_gate",
			"result_json",
		],
		as_dict=True,
		order_by="started_at desc",
	)
	if not row:
		return {"run": None, "note": "Chưa có lần chạy bộ đề nào"}
	try:
		meta = (json.loads(row.result_json or "{}") or {}).get("meta") or {}
	except ValueError:
		meta = {}
	real = meta.get("mode") == "AI thật"
	return {
		"run": row.name,
		"started_at": str(row.started_at),
		"mode": meta.get("mode"),
		"gate": meta.get("gate"),
		"reviewed": meta.get("reviewed"),
		"total_cases": row.total_cases,
		"field_accuracy": row.field_accuracy if real else None,
		"passed_gate": bool(row.passed_gate),
		"counts_as_measurement": bool(real and meta.get("reviewed")),
		"note": "Chỉ lần chạy bằng AI thật trên bộ đề đã duyệt mới là số đo; Fake/không client chỉ kiểm hạ tầng",
	}


def run(start: str, end: str | None = None, exclude_prefixes: str | list | None = None) -> None:
	"""Tính chỉ số Đợt 2 trong [start, end) (giờ theo múi giờ của site) và in JSON + bảng Markdown."""
	s = get_datetime(start)
	e = get_datetime(end) if end else now_datetime()
	prefixes = DEFAULT_EXCLUDE
	if isinstance(exclude_prefixes, str):
		prefixes = tuple(x for x in exclude_prefixes.split(",") if x)
	elif exclude_prefixes:
		prefixes = tuple(exclude_prefixes)
	res = {
		"period": [str(s), str(e)],
		"callbacks_on_time": callbacks_on_time(s, e, prefixes),
		"extraction_proxy": extraction_proxy(s, e, prefixes),
		"eval_gate": eval_gate(),
	}
	print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
	print(_table(res))


def _verdict(ok: bool | None) -> str:
	return "chưa đủ dữ liệu" if ok is None else ("ĐẠT" if ok else "KHÔNG ĐẠT")


def _table(r: dict[str, Any]) -> str:
	cb = r["callbacks_on_time"]["ratio_on_time"]
	ev = r["eval_gate"]
	gate_ok = ev["passed_gate"] if ev.get("counts_as_measurement") else None
	px = r["extraction_proxy"]["ratio_kept_proxy"]
	rows = [
		("Gọi nhỡ được gọi lại đúng hạn", ">= 80%", cb, _verdict(None if cb is None else cb >= 0.8)),
		(
			"Trường trích xuất đúng (cổng phan_tich, bộ đề đã duyệt, AI thật)",
			">= 90%",
			ev.get("field_accuracy"),
			_verdict(gate_ok),
		),
		("Đại diện: giá trị bot điền còn nguyên (không phải độ đúng)", "tham khảo", px, "không có ngưỡng"),
	]
	lines = ["| Chỉ số | Ngưỡng | Đo được | Kết luận |", "| --- | --- | --- | --- |"]
	lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
	return "\n".join(lines)
