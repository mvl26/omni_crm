"""Nhập/xuất bộ đề kiểm thử (eval/README.md mục 1): mỗi dòng `.jsonl` là một `Omni Eval Case`.

Dòng sai chỉ bị báo (kèm số dòng), các dòng đúng vẫn nhập. Ca do `builder.build_from_real` sinh ra mang
`"reviewed": false` (gợi ý chưa có người duyệt) và bị từ chối cho tới khi người duyệt sửa `expected_*` và đổi/bỏ cờ.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import frappe

CASE = "Omni Eval Case"
GROUP = "Omni Question Group"
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")
SOURCES = ("Real", "Synthetic")
ACTIONS = ("Reply", "Handoff")
ROLES = ("customer", "sale")
TURN_KEYS = {
	"role",
	"text",
	"at",
}  # `at` (không bắt buộc): thời điểm lượt, "YYYY-MM-DD HH:MM", dùng tính giờ làm việc
KNOWN_KEYS = {
	"case_id",
	"source",
	"conversation",
	"expected_fields",
	"expected_action",
	"expected_group",
	"reference_answer",
	"critical",
	"active",
	"reviewed",
	"note",
}
REQUIRED_KEYS = ("case_id", "source", "conversation", "expected_action")
MAX_TURNS = 40
MAX_TEXT = 4000


def _root(path: str | Path) -> Path:
	p = Path(path)
	return p if p.is_absolute() else Path(frappe.get_app_path("omni_crm")).parent / p


def validate_case(row: Any) -> list[str]:
	"""Lỗi định dạng của một ca (không kiểm DB); danh sách rỗng = hợp lệ."""
	if not isinstance(row, dict):
		return ["dòng phải là một đối tượng JSON"]
	errors = [f"thiếu khóa {k}" for k in REQUIRED_KEYS if k not in row]
	errors += [f"khóa lạ {k}" for k in sorted(set(row) - KNOWN_KEYS)]
	if errors:
		return errors
	if not isinstance(row["case_id"], str) or not CASE_ID_RE.match(row["case_id"]):
		errors.append("case_id sai định dạng")
	if row["source"] not in SOURCES:
		errors.append(f"source phải là một trong {SOURCES}")
	if row["expected_action"] not in ACTIONS:
		errors.append(f"expected_action phải là một trong {ACTIONS}")
	conv = row["conversation"]
	if not isinstance(conv, list) or not conv or len(conv) > MAX_TURNS:
		errors.append(f"conversation phải là danh sách 1..{MAX_TURNS} lượt")
	else:
		for i, turn in enumerate(conv):
			ok = (
				isinstance(turn, dict)
				and turn.get("role") in ROLES
				and isinstance(turn.get("text"), str)
				and turn["text"].strip()
				and len(turn["text"]) <= MAX_TEXT
				and set(turn) <= TURN_KEYS
			)
			if not ok:
				errors.append(f"lượt {i + 1} sai ({ROLES}, text không rỗng, tối đa {MAX_TEXT} ký tự)")
		if conv and isinstance(conv[-1], dict) and conv[-1].get("role") != "customer":
			errors.append("conversation phải kết thúc bằng lượt của khách")
	if not isinstance(row.get("expected_fields", {}), dict):
		errors.append("expected_fields phải là đối tượng")
	for key in ("critical", "active", "reviewed"):
		if key in row and not isinstance(row[key], bool):
			errors.append(f"{key} phải là true/false")
	for key in ("reference_answer", "expected_group", "note"):
		if key in row and row[key] is not None and not isinstance(row[key], str):
			errors.append(f"{key} phải là chuỗi")
	return errors


def import_jsonl(path: str, allow_unreviewed: bool = False) -> dict[str, Any]:
	"""Nhập bộ đề từ `.jsonl` (cập nhật theo case_id); trả {created, updated, errors: [{line, error}]}.

	Ca có `reviewed = false` bị từ chối (gợi ý của builder chưa được người duyệt) trừ khi `allow_unreviewed`.
	"""
	created = updated = 0
	errors: list[dict[str, Any]] = []
	seen: dict[str, int] = {}
	with open(_root(path), encoding="utf-8") as f:
		lines = f.read().splitlines()
	for number, line in enumerate(lines, 1):
		if not line.strip():
			continue
		try:
			row = json.loads(line)
		except ValueError:
			errors.append({"line": number, "error": "không phải JSON hợp lệ"})
			continue
		problems = validate_case(row)
		if not problems:
			cid = row["case_id"]
			if cid in seen:
				problems.append(f"case_id trùng với dòng {seen[cid]}")
			else:
				seen[cid] = number
			if row.get("reviewed") is False and not allow_unreviewed:
				problems.append("ca chưa được người duyệt (reviewed = false); sửa expected_* rồi đổi/bỏ cờ")
			group = row.get("expected_group")
			if group and not frappe.db.exists(GROUP, group):
				problems.append(f"nhóm câu hỏi {group!r} chưa tồn tại")
		if problems:
			errors.append({"line": number, "error": "; ".join(problems)})
			continue
		values = {
			"source": row["source"],
			"conversation": json.dumps(row["conversation"], ensure_ascii=False),
			"expected_fields": json.dumps(row.get("expected_fields") or {}, ensure_ascii=False),
			"expected_action": row["expected_action"],
			"expected_group": row.get("expected_group") or None,
			"reference_answer": row.get("reference_answer") or "",
			"critical": 1 if row.get("critical") else 0,
			"active": 0 if row.get("active") is False else 1,
		}
		if frappe.db.exists(CASE, row["case_id"]):
			doc = frappe.get_doc(CASE, row["case_id"])
			doc.update(values)
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			frappe.get_doc({"doctype": CASE, "case_id": row["case_id"], **values}).insert(
				ignore_permissions=True
			)
			created += 1
	frappe.db.commit()
	for e in errors:
		print(f"LỖI dòng {e['line']}: {e['error']}")
	return {"created": created, "updated": updated, "errors": errors}


def case_to_row(doc: Any) -> dict[str, Any]:
	"""Bản ghi Omni Eval Case -> dòng JSONL."""

	def load(value: Any, default: Any) -> Any:
		if isinstance(value, str):
			try:
				return json.loads(value)
			except ValueError:
				return default
		return value if value is not None else default

	return {
		"case_id": doc.case_id,
		"source": doc.source,
		"conversation": load(doc.conversation, []),
		"expected_fields": load(doc.expected_fields, {}),
		"expected_action": doc.expected_action,
		"expected_group": doc.expected_group or "",
		"reference_answer": doc.reference_answer or "",
		"critical": bool(doc.critical),
		"active": bool(doc.active),
	}


def export_jsonl(path: str, only_active: bool = False) -> int:
	"""Xuất bộ đề ra `.jsonl` (theo case_id); trả số ca."""
	filters = {"active": 1} if only_active else {}
	names = frappe.get_all(CASE, filters=filters, pluck="name", order_by="name asc")
	out = _root(path)
	out.parent.mkdir(parents=True, exist_ok=True)
	with open(out, "w", encoding="utf-8") as f:
		for name in names:
			f.write(json.dumps(case_to_row(frappe.get_doc(CASE, name)), ensure_ascii=False) + "\n")
	return len(names)
