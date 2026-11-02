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


def import_groups(
	path: str = "mau/nhom-cau-hoi-mau.csv", shadow_only: bool = False, retire_missing: bool = False
) -> dict[str, Any]:
	"""Nhập nhóm câu hỏi từ CSV (cập nhật theo group_name, chạy lại không tạo trùng); trả {created, updated, retired, warnings}.

	`shadow_only` (V2-06): nhóm trong CSV được đặt "chỉ chạy ngầm" (nháp trả lời không hiện cho sale). `retire_missing`:
	nhóm CÓ trong DB mà KHÔNG còn trong CSV bị đặt ngừng hoạt động (`active = 0`), không xóa, để giữ lịch sử Bot Run.
	Nhóm trong CSV luôn được bật lại (`active = 1`). Không bao giờ đụng `auto_send_enabled`.
	"""
	created = updated = 0
	seen: list[str] = []
	for row in _rows(_resolve(path)):
		name = row.get("group_name")
		if not name:
			continue
		seen.append(name)
		values = {
			"always_handoff": 1 if row.get("always_handoff") in ("1", "true", "True") else 0,
			"description": row.get("description") or "",
		}
		if shadow_only:
			values["shadow_only"] = 1
		if frappe.db.exists(GROUP, name):
			doc = frappe.get_doc(GROUP, name)
			doc.update({**values, "active": 1})
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			frappe.get_doc({"doctype": GROUP, "group_name": name, "active": 1, **values}).insert(
				ignore_permissions=True
			)
			created += 1
	retired: list[str] = []
	if retire_missing and seen:
		for name in frappe.get_all(GROUP, filters={"active": 1, "name": ["not in", seen]}, pluck="name"):
			doc = frappe.get_doc(GROUP, name)
			doc.active = 0  # qua save để validate tắt luôn cờ tự gửi của nhóm bị ngừng
			doc.save(ignore_permissions=True)
			retired.append(name)
	frappe.db.commit()
	return {"created": created, "updated": updated, "retired": retired, "warnings": []}


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


FEE_OR_COMMITMENT = re.compile(
	r"(?:\d[\d.,]*\s*(?:%|đồng|vnđ|vnd|triệu|tỷ|tỉ|nghìn|ngàn|k|đ)(?!\w))|\b(?:phí|chiết khấu|hoa hồng|cam kết|đảm bảo|bảo lãnh"
	r"|miễn phí|giảm giá)\b",
	re.IGNORECASE,
)


def fee_or_commitment_terms(*values: str) -> list[str]:
	"""Cụm nêu tiền/phí/chiết khấu/cam kết trong nội dung THẬT (đã bỏ phần `[...]` còn trống). Dùng cảnh báo khi nhập."""
	text = PLACEHOLDER.sub(" ", " ".join(values))
	return sorted({m.group(0).lower() for m in FEE_OR_COMMITMENT.finditer(text)})


def import_items(
	path: str = "mau/kho-tri-thuc-mau.csv", as_sample: bool = False, activate_new: bool = True
) -> dict[str, Any]:
	"""Nhập mục kho tri thức từ CSV: cập nhật theo item_id (không đổi tên mục cũ), chạy lại không tạo trùng.

	Mặc định (v1): dòng còn `[...]` bị bỏ và in cảnh báo; mục mới nhập ở trạng thái đang dùng. `owner_email` không tra
	ra User thì để trống và cảnh báo.

	`as_sample` (V2-06): CSV là DỮ LIỆU MẪU chưa được phòng kinh doanh duyệt. Mọi mục (kể cả dòng còn `[...]`) được
	nhập với `active = 0`, `reviewed_at` trống, để người phụ trách điền/duyệt rồi tự bật; mục đang dùng (`active = 1`)
	không bị ghi đè; chạy lại không bao giờ bật mục nào. `activate_new = False`: mục mới luôn `active = 0`.
	Trả {created, updated, skipped, warnings}.
	"""
	created = updated = 0
	skipped: list[str] = []
	warnings: list[str] = []
	for row in _rows(_resolve(path)):
		item_id = row.get("item_id", "")
		if not ITEM_ID.match(item_id):
			warnings.append(f"Bỏ dòng có item_id không hợp lệ: {item_id!r}")
			continue
		if has_placeholder(*row.values()) and not as_sample:
			skipped.append(item_id)
			warnings.append(f"{item_id}: có ô còn trong ngoặc vuông [...], chưa nhập (không bịa nội dung)")
			continue
		group = row.get("question_group", "")
		if not group or not frappe.db.exists(GROUP, group):
			skipped.append(item_id)
			warnings.append(f"{item_id}: nhóm câu hỏi {group!r} chưa tồn tại, chưa nhập")
			continue
		terms = fee_or_commitment_terms(row.get("question", ""), row.get("answer", ""))
		if terms:
			warnings.append(f"{item_id}: nội dung có cụm phí/cam kết {terms}: phòng kinh doanh phải duyệt kỹ")
		source = row.get("source", "")
		values = {
			"question_group": group,
			"question": row.get("question", ""),
			"answer": row.get("answer", ""),
			"source": "" if has_placeholder(source) else source,
			"owner_user": _owner(item_id, row.get("owner_email", ""), warnings),
		}
		if frappe.db.exists(ITEM, item_id):
			if as_sample and frappe.db.get_value(ITEM, item_id, "active"):
				skipped.append(item_id)
				warnings.append(f"{item_id}: đang dùng, không ghi đè bằng dữ liệu mẫu")
				continue
			doc = frappe.get_doc(ITEM, item_id)
			doc.update(values)  # KHÔNG đụng `active`/`reviewed_at`: nhập lại không bao giờ bật hay tắt mục
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			active = 0 if (as_sample or not activate_new) else 1
			frappe.get_doc({"doctype": ITEM, "item_id": item_id, "active": active, **values}).insert(
				ignore_permissions=True
			)
			created += 1
	frappe.db.commit()
	for w in warnings:
		print(f"CẢNH BÁO: {w}")
	return {"created": created, "updated": updated, "skipped": skipped, "warnings": warnings}


def import_b2b(
	groups_path: str = "mau/nhom-cau-hoi-mau.csv",
	kb_path: str = "mau/kho-tri-thuc-mau.csv",
	sample: bool = True,
) -> dict[str, Any]:
	"""Nhập bộ nhóm câu hỏi + kho tri thức B2B của V2-06 (chạy lại được).

	Nhóm: đặt Shadow (`shadow_only`), nhóm cũ không còn trong CSV bị ngừng hoạt động (không xóa). Kho tri thức: `sample`
	(mặc định) = dữ liệu mẫu chờ phòng kinh doanh duyệt, nhập KHÔNG hoạt động; chỉ đặt `sample=False` với CSV đã được duyệt
	(khi đó dòng còn `[...]` vẫn bị bỏ và mục mới vẫn nhập không hoạt động để người duyệt bật tay).
	Lệnh: `bench --site <site> execute omni_crm.bot.knowledge.import_b2b`.
	"""
	if isinstance(sample, str):  # `--kwargs` của bench có thể đưa chuỗi
		sample = sample.strip().lower() not in ("0", "false", "no")
	groups = import_groups(groups_path, shadow_only=True, retire_missing=True)
	items = import_items(kb_path, as_sample=bool(sample), activate_new=False)
	if sample:
		print(
			"CHỜ PHÒNG KINH DOANH DUYỆT: kho tri thức mẫu đã nhập ở trạng thái KHÔNG hoạt động; bot chưa dùng mục nào."
		)
	return {"groups": groups, "items": items}


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
