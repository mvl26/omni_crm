"""Kho ô kịch bản (Omni Script Node): đọc ô và điền biến. Câu gửi khách nằm trong DocType, không nằm trong code."""

from __future__ import annotations

import re
from typing import Any

import frappe

from omni_crm.script import switch

NODE = "Omni Script Node"
ACTIVE, REVIEW, OFF = "Đang dùng", "Cần duyệt", "Tắt"
_VAR = re.compile(r"\{(\w+)\}")
OPTIONAL_VARS = frozenset(
	{"ten"}
)  # biến được phép trống (seed/VARIABLES.md); biến khác thiếu thì không gửi ô


def normalize_code(code: str | None) -> str:
	"""Mã ô chuẩn: bỏ `#`, khoảng trắng, viết hoa."""
	return (code or "").strip().lstrip("#").strip().upper()


def get_node(code: str | None) -> dict | None:
	"""Ô kịch bản theo mã: {code, text, buttons:[{label, target}], status, action, service, step, channel}; không có trả None."""
	code = normalize_code(code)
	if not code or not frappe.db.exists(NODE, code):
		return None
	doc = frappe.get_doc(NODE, code)
	return {
		"code": doc.code,
		"text": doc.text or "",
		"buttons": [{"label": b.label, "target": b.target} for b in doc.buttons or []],
		"status": doc.status or ACTIVE,
		"action": doc.action or "",
		"service": doc.service or "",
		"step": doc.step or "",
		"channel": doc.channel or "Tất cả",
	}


def fill(text: str, ctx: dict | None) -> str:
	"""Điền `{bien}` từ ctx; biến thiếu thành chuỗi rỗng (không để lộ `{ten}` cho khách), gọn khoảng trắng thừa."""
	ctx = ctx or {}

	def _sub(m: re.Match) -> str:
		value = ctx.get(m.group(1))
		return "" if value is None else str(value)

	out = _VAR.sub(_sub, text or "")
	out = re.sub(r"[ \t]{2,}", " ", out)
	out = re.sub(r" +([,.!?])", r"\1", out)
	return "\n".join(line.strip() for line in out.split("\n")).strip()


def missing_vars(text: str | None, ctx: dict | None) -> list[str]:
	"""Các `{bien}` bắt buộc trong câu mà ctx không có giá trị (None hoặc chuỗi trắng); `ten` được phép trống."""
	ctx = ctx or {}
	names = dict.fromkeys(_VAR.findall(text or ""))
	return [
		n for n in names if n not in OPTIONAL_VARS and (ctx.get(n) is None or not str(ctx.get(n)).strip())
	]


def render(node: dict | None, ctx: dict | None = None) -> tuple[str, list[dict]] | None:
	"""(text, buttons) đã điền biến; None nếu ô không có, `Tắt`, nội dung trống, thiếu biến bắt buộc, hoặc `Cần duyệt` mà không phải danh sách thử.
	Nút dẫn tới ô `Tắt` (hay `Cần duyệt` với khách ngoài danh sách thử) bị bỏ; hết nút thì vẫn gửi phần chữ.

	ctx có thể chứa `identity` (để kiểm danh sách thử) và các biến của câu.
	"""
	if not node:
		return None
	status = node.get("status") or ACTIVE
	if status == OFF:
		return None
	if status == REVIEW and not switch.is_test((ctx or {}).get("identity")):
		return None
	if not (node.get("text") or "").strip():
		return None
	parts = [node["text"], *((b.get("label") or "") for b in node.get("buttons") or [])]
	if any(missing_vars(part, ctx) for part in parts):
		return None  # thiếu biến bắt buộc: không gửi câu cụt
	text = fill(node["text"], ctx)
	if not text:
		return None
	buttons = [
		{"label": fill(b.get("label") or "", ctx), "target": normalize_code(b.get("target"))}
		for b in node.get("buttons") or []
	]
	buttons = [b for b in buttons if b["label"] and b["target"]]
	return text, _usable_buttons(buttons, (ctx or {}).get("identity"))


def _usable_buttons(buttons: list[dict], identity: str | None) -> list[dict]:
	"""Bỏ nút dẫn tới ô `Tắt` (và ô `Cần duyệt` khi khách không thuộc danh sách thử); mã không phải ô kịch bản thì giữ."""
	if not buttons:
		return buttons
	rows = frappe.get_all(
		NODE, filters={"name": ["in", [b["target"] for b in buttons]]}, fields=["name", "status"]
	)
	hidden = {OFF} if switch.is_test(identity) else {OFF, REVIEW}
	dead = {r.name for r in rows if (r.status or ACTIVE) in hidden}
	return [b for b in buttons if b["target"] not in dead]


def known_codes() -> set[str]:
	"""Mọi mã ô kịch bản đang có (dùng nhận diện mã nút `#CODE`)."""
	return set(frappe.get_all(NODE, pluck="name"))


def as_dict(value: Any) -> dict:
	"""JSON/dict → dict (lỗi hoặc rỗng trả {})."""
	if isinstance(value, dict):
		return value
	try:
		parsed = frappe.parse_json(value) if value else {}
	except Exception:
		return {}
	return parsed if isinstance(parsed, dict) else {}
