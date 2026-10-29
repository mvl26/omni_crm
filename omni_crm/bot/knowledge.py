"""Nhập kho tri thức từ CSV và render cho bot (docs/08 mục 5.2, mau/README.md).

Bot chỉ nói được điều có trong kho này; ô còn dấu ngoặc vuông `[...]` là chỗ chưa điền nên mục đó KHÔNG được nhập.
"""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Any

import frappe
from frappe.utils import strip_html_tags

GROUP = "Omni Question Group"
ITEM = "Omni Knowledge Item"
PLACEHOLDER = re.compile(r"\[[^\]]*\]")
ITEM_ID = re.compile(r"^KB-\d{5,}$")


def _resolve(path: str) -> Path:
	p = Path(path)
	return p if p.is_absolute() else Path(frappe.get_app_path("omni_crm")).parent / p


def _rows(path: str | Path) -> list[dict[str, str]]:
	with open(path, encoding="utf-8-sig", newline="") as f:
		return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]


def has_placeholder(*values: str) -> bool:
	"""Có ô nào còn dấu ngoặc vuông `[...]` (chưa điền thông tin thật) không."""
	return any(PLACEHOLDER.search(v or "") for v in values)


def import_groups(path: str = "mau/nhom-cau-hoi-mau.csv") -> dict[str, Any]:
	"""Nhập nhóm câu hỏi từ CSV (cập nhật theo group_name); trả {created, updated, warnings}."""
	created = updated = 0
	for row in _rows(_resolve(path)):
		name = row.get("group_name")
		if not name:
			continue
		values = {
			"always_handoff": 1 if row.get("always_handoff") in ("1", "true", "True") else 0,
			"description": row.get("description") or "",
		}
		if frappe.db.exists(GROUP, name):
			doc = frappe.get_doc(GROUP, name)
			doc.update(values)
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			frappe.get_doc({"doctype": GROUP, "group_name": name, **values}).insert(ignore_permissions=True)
			created += 1
	frappe.db.commit()
	return {"created": created, "updated": updated, "warnings": []}


def _owner(item_id: str, email: str, warnings: list[str]) -> str | None:
	"""User có email `email`; không thấy thì None và thêm cảnh báo (ô còn `[...]` coi như để trống)."""
	if not email or has_placeholder(email):
		return None
	owner = frappe.db.get_value("User", {"email": email}, "name") or (
		email if frappe.db.exists("User", email) else None
	)
	if not owner:
		warnings.append(f"{item_id}: không tìm thấy User cho {email}, để trống người phụ trách")
	return owner


def import_items(path: str = "mau/kho-tri-thuc-mau.csv") -> dict[str, Any]:
	"""Nhập mục kho tri thức từ CSV: cập nhật theo item_id (không đổi tên mục cũ).

	Dòng còn `[...]` trong câu hỏi/đáp bị bỏ và in cảnh báo. `owner_email` không tra ra User thì để trống và cảnh báo.
	Mục mới nhập ở trạng thái đang dùng. Trả {created, updated, skipped, warnings}.
	"""
	created = updated = 0
	skipped: list[str] = []
	warnings: list[str] = []
	for row in _rows(_resolve(path)):
		item_id = row.get("item_id", "")
		if not ITEM_ID.match(item_id):
			warnings.append(f"Bỏ dòng có item_id không hợp lệ: {item_id!r}")
			continue
		if has_placeholder(*row.values()):
			skipped.append(item_id)
			warnings.append(f"{item_id}: có ô còn trong ngoặc vuông [...], chưa nhập (không bịa nội dung)")
			continue
		group = row.get("question_group", "")
		if not group or not frappe.db.exists(GROUP, group):
			skipped.append(item_id)
			warnings.append(f"{item_id}: nhóm câu hỏi {group!r} chưa tồn tại, chưa nhập")
			continue
		source = row.get("source", "")
		values = {
			"question_group": group,
			"question": row.get("question", ""),
			"answer": row.get("answer", ""),
			"source": "" if has_placeholder(source) else source,
			"owner_user": _owner(item_id, row.get("owner_email", ""), warnings),
		}
		if frappe.db.exists(ITEM, item_id):
			doc = frappe.get_doc(ITEM, item_id)
			doc.update(values)
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			frappe.get_doc({"doctype": ITEM, "item_id": item_id, "active": 1, **values}).insert(
				ignore_permissions=True
			)
			created += 1
	frappe.db.commit()
	for w in warnings:
		print(f"CẢNH BÁO: {w}")
	return {"created": created, "updated": updated, "skipped": skipped, "warnings": warnings}


# --- Render cho bot ------------------------------------------------------------------------------------------


def plain(text: str | None) -> str:
	"""Văn bản thuần từ nội dung Text Editor (bỏ thẻ HTML, giải mã ký tự, gộp khoảng trắng thừa)."""
	text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", text or "", flags=re.IGNORECASE)
	return re.sub(r"[ \t]+", " ", html.unescape(strip_html_tags(text))).strip()


def kb_items(ids: list[str] | None = None, active_only: bool = True) -> list[dict[str, str]]:
	"""Mục kho tri thức (mã, nhóm, hỏi, đáp thuần văn bản) theo mã tăng dần; `ids` lọc theo mã."""
	filters: dict[str, Any] = {}
	if active_only:
		filters["active"] = 1
	if ids is not None:
		filters["name"] = ["in", ids or [""]]
	rows = frappe.get_all(
		ITEM, filters=filters, fields=["name", "question_group", "question", "answer"], order_by="name asc"
	)
	return [
		{
			"id": r.name,
			"group": r.question_group or "",
			"question": r.question or "",
			"answer": plain(r.answer),
		}
		for r in rows
	]


def _esc(text: str) -> str:
	return html.escape(text or "", quote=True)


def render_items(items: list[dict[str, str]]) -> str:
	"""`<kho_tri_thuc>` từ danh sách mục {id, group, question, answer} (dùng chung với bộ đề đọc kho từ tệp)."""
	parts = [
		f'<muc id="{_esc(i["id"])}" nhom="{_esc(i["group"])}"><hoi>{_esc(i["question"])}</hoi>'
		f"<dap>{_esc(i['answer'])}</dap></muc>"
		for i in items
	]
	return "\n".join(["<kho_tri_thuc>", *parts, "</kho_tri_thuc>"])


def render_kb(active_only: bool = True) -> str:
	"""`<kho_tri_thuc><muc id="KB-…" nhom="…"><hoi>…</hoi><dap>…</dap></muc>…</kho_tri_thuc>`, nội dung đã thoát."""
	return render_items(kb_items(active_only=active_only))
