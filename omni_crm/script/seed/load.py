"""Nạp dữ liệu khởi tạo kho kịch bản (`nodes.json` + `nodes_*.json`) vào Omni Script Node.

Chạy tay: `bench --site <site> execute omni_crm.script.seed.load.load`; cũng được gọi sau `bench migrate`.
Ô chưa có thì tạo. Ô đã có: chỉ cập nhật khi chưa ai sửa trên site (thời điểm sửa = thời điểm tạo) hoặc `overwrite=True`.
"""

from __future__ import annotations

import json
from pathlib import Path

import frappe

NODE = "Omni Script Node"
SEED_DIR = Path(__file__).resolve().parent
FIELDS = ("title", "step", "service", "channel", "status", "text", "action", "source_ref", "notes")


def seed_files() -> list[Path]:
	"""Các tệp seed theo thứ tự nạp: `nodes.json` trước, rồi `nodes_*.json` theo tên."""
	main = SEED_DIR / "nodes.json"
	return ([main] if main.exists() else []) + sorted(SEED_DIR.glob("nodes_*.json"))


def read_nodes() -> list[dict]:
	"""Mọi ô trong các tệp seed (mã trùng: tệp nạp sau thắng)."""
	nodes: dict[str, dict] = {}
	for path in seed_files():
		for row in json.loads(path.read_text(encoding="utf-8")):
			code = str(row.get("code") or "").strip().lstrip("#").upper()
			if code:
				nodes[code] = {**row, "code": code}
	return list(nodes.values())


def load(overwrite: bool = False) -> dict[str, int]:
	"""Nạp seed vào Omni Script Node; trả số ô {created, updated, skipped, failed}. Không ghi đè ô đã sửa trên site."""
	stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
	for row in read_nodes():
		try:
			stats[_load_one(row, overwrite)] += 1
		except Exception:
			stats["failed"] += 1
			frappe.db.rollback()
			frappe.log_error(title="Omni: nạp ô kịch bản lỗi", message=f"code={row.get('code')}")
		else:
			frappe.db.commit()
	return stats


def _load_one(row: dict, overwrite: bool) -> str:
	code = row["code"]
	values = {f: row.get(f) or "" for f in FIELDS}
	buttons = [{"label": b.get("label"), "target": b.get("target")} for b in row.get("buttons") or []]
	if not frappe.db.exists(NODE, code):
		doc = frappe.get_doc({"doctype": NODE, "code": code, **_clean(values), "buttons": buttons})
		doc.insert(ignore_permissions=True)
		_mark_untouched(code)
		return "created"
	doc = frappe.get_doc(NODE, code)
	if not overwrite and str(doc.modified) != str(doc.creation):
		return "skipped"  # đã sửa trên site (Truyền thông sửa chữ): giữ nguyên
	current = [{"label": b.label, "target": b.target} for b in doc.buttons or []]
	if all((doc.get(f) or "") == v for f, v in values.items()) and current == buttons:
		return "skipped"
	doc.update(_clean(values))
	doc.set("buttons", buttons)
	doc.save(ignore_permissions=True)
	_mark_untouched(code)
	return "updated"


def _clean(values: dict) -> dict:
	"""Bỏ giá trị rỗng của trường Select có mặc định (channel, status) để DocType tự đặt mặc định."""
	return {k: v for k, v in values.items() if v or k not in ("channel", "status")}


def _mark_untouched(code: str) -> None:
	"""Đặt `modified = creation` để lần nạp sau biết ô chưa được sửa tay trên site."""
	creation = frappe.db.get_value(NODE, code, "creation")
	frappe.db.set_value(NODE, code, "modified", creation, update_modified=False)
