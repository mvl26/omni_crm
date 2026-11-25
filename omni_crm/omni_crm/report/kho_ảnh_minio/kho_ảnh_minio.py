"""Báo cáo "Kho ảnh MinIO" (ADR 0033, mục "Báo cáo kho ảnh"): ảnh và tệp đính kèm công khai của bài marketing trên
kho S3, chỉ đọc.

Mỗi dòng một khóa object, đối chiếu object trong bucket (dưới thư mục ảnh bài) với dòng File có `omni_s3_key`:
- Khớp: có object và có File.
- Thiếu trên MinIO: File trỏ tới khóa không còn object (ảnh của bài không hiện, đăng bài lỗi "Không tìm thấy ảnh").
- Mồ côi: object không File nào dùng (tốn chỗ; IT xem rồi xóa bằng `mc rm` nếu chắc chắn không cần).

Không có nút sửa hay xóa. Chỉ Omni Admin, System Manager (bảng vai trò của báo cáo, kiểm lại trong `execute`).
Ảnh xem qua `/omni-media/...` (CRM đọc từ MinIO), không cần đường tới MinIO.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import flt, get_system_timezone

from omni_crm.marketing import s3_media

ROLES = ("Omni Admin", "System Manager")
OK, MISSING, ORPHAN = "Khớp", "Thiếu trên MinIO", "Mồ côi"
STATUSES = (OK, MISSING, ORPHAN)
LIMIT = 10000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not set(ROLES) & set(frappe.get_roles()):
		raise frappe.PermissionError(_("Chỉ Omni Admin xem được kho ảnh S3"))
	if not s3_media.configured():
		return columns(), [], _("Chưa cấu hình kho ảnh S3 (Omni Media Storage)."), None, []
	try:
		objects = s3_media.list_objects(limit=LIMIT)
	except s3_media.S3Error as e:
		frappe.throw(str(e))
	rows = build_rows(objects)
	summary = report_summary(rows, objects)
	message = None
	if len(objects) >= LIMIT:
		message = _("Kho có từ {0} ảnh trở lên; báo cáo chỉ đọc {0} ảnh đầu.").format(LIMIT)
	if filters.status:
		rows = [r for r in rows if r["status"] == filters.status]
	if filters.content_item:
		rows = [r for r in rows if r["content_item"] == filters.content_item]
	return columns(), rows, message, None, summary


def columns() -> list[dict]:
	return [
		{"fieldname": "status", "label": _("Trạng thái"), "fieldtype": "Data", "width": 130},
		{"fieldname": "content_item", "label": _("Bài"), "fieldtype": "Link", "options": s3_media.ITEM, "width": 120},
		{"fieldname": "title", "label": _("Tiêu đề bài"), "fieldtype": "Data", "width": 220},
		{"fieldname": "file", "label": _("Tệp"), "fieldtype": "Link", "options": "File", "width": 110},
		{"fieldname": "file_name", "label": _("Tên tệp gốc"), "fieldtype": "Data", "width": 180},
		{"fieldname": "used", "label": _("Số ô ảnh dùng"), "fieldtype": "Int", "width": 110},
		{"fieldname": "view_url", "label": _("Xem"), "fieldtype": "Data", "width": 80},
		{"fieldname": "size_kb", "label": _("Kích thước (KB)"), "fieldtype": "Float", "precision": 1, "width": 120},
		{"fieldname": "modified", "label": _("Ghi lên MinIO lúc"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "key", "label": _("Khóa object"), "fieldtype": "Data", "width": 300},
		{"fieldname": "note", "label": _("Ghi chú"), "fieldtype": "Data", "width": 220},
	]


def _local_time(value):
	"""LastModified của S3 (UTC có múi giờ) -> giờ hệ thống, không múi giờ (như Datetime của Frappe)."""
	if not value or not getattr(value, "tzinfo", None):
		return value
	return value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _item_of(key: str) -> str | None:
	"""Mã bài trong khóa `<thư mục><bài>/<File><đuôi>` (ADR 0033); None nếu khóa không theo mẫu."""
	prefix = s3_media._prefix()
	if not key.startswith(prefix):
		return None
	head, sep, _rest = key[len(prefix) :].partition("/")
	return head if sep and head else None


def _files_by_key() -> dict[str, list]:
	rows = frappe.get_all(
		"File",
		filters={s3_media.KEY_FIELD: ["is", "set"]},
		fields=[
			"name",
			"file_name",
			"file_url",
			s3_media.KEY_FIELD,
			"attached_to_doctype",
			"attached_to_name",
			"attached_to_field",
		],
		order_by="creation asc",
	)
	out: dict[str, list] = {}
	for row in rows:
		out.setdefault(row.get(s3_media.KEY_FIELD), []).append(row)
	return out


def _usage(urls: list[str]) -> dict[str, int]:
	"""Số ô (ảnh bìa, ảnh thêm, video và tài liệu đăng kèm) của bài đang dùng từng địa chỉ."""
	count: dict[str, int] = {}
	if not urls:
		return count
	for url in frappe.get_all(s3_media.ITEM, filters={"media": ["in", urls]}, pluck="media"):
		count[url] = count.get(url, 0) + 1
	for url in frappe.get_all(
		s3_media.MEDIA, filters={"image": ["in", urls], "parenttype": s3_media.ITEM}, pluck="image"
	):
		count[url] = count.get(url, 0) + 1
	for fieldname in s3_media.FILE_FIELDS:  # ô video / tài liệu đăng kèm (ADR 0035)
		for url in frappe.get_all(s3_media.ITEM, filters={fieldname: ["in", urls]}, pluck=fieldname):
			count[url] = count.get(url, 0) + 1
	return count


def build_rows(objects: list[dict]) -> list[dict]:
	by_key = {o["key"]: o for o in objects}
	files = _files_by_key()
	keys = sorted(set(by_key) | set(files))
	urls = [f.file_url for rows in files.values() for f in rows if f.file_url]
	usage = _usage(urls)
	items = {_item_of(k) for k in keys} | {
		f.attached_to_name for rows in files.values() for f in rows if f.attached_to_doctype == s3_media.ITEM
	}
	items.discard(None)
	titles = dict(
		frappe.get_all(s3_media.ITEM, filters={"name": ["in", list(items)]}, fields=["name", "title"], as_list=True)
	) if items else {}

	out = []
	for key in keys:
		obj, rows = by_key.get(key), files.get(key) or []
		f = rows[0] if rows else None
		item = _item_of(key) or (f.attached_to_name if f and f.attached_to_doctype == s3_media.ITEM else None)
		notes = []
		if obj and f:
			status = OK
		elif f:
			status = MISSING
			notes.append(_("Bài không hiện ảnh này; đăng bài sẽ lỗi"))
		else:
			status = ORPHAN
			notes.append(_("Không tệp nào trong CRM dùng; xem rồi xóa bằng mc nếu không cần"))
		if len(rows) > 1:
			notes.append(_("{0} dòng File dùng chung khóa này").format(len(rows)))
		if item and item not in titles:
			notes.append(_("Bài {0} không còn").format(item))
			item = None
		used = sum(usage.get(r.file_url, 0) for r in rows)
		if f and not used:
			if f.attached_to_doctype == s3_media.ITEM and not f.get("attached_to_field"):
				notes.append(_("Tệp đính kèm của bài"))
			else:
				notes.append(_("Không ô ảnh nào của bài dùng tệp này"))
		view = f.file_url if f and s3_media.is_public_url(f.file_url) and obj else None
		out.append(
			{
				"status": status,
				"content_item": item,
				"title": titles.get(item) if item else None,
				"file": f.name if f else None,
				"file_name": f.file_name if f else None,
				"used": used,
				"view_url": view,
				"size_kb": flt(obj["size"] / 1024, 1) if obj else None,
				"modified": _local_time(obj.get("modified")) if obj else None,
				"key": key,
				"note": "; ".join(notes),
			}
		)
	order = {s: i for i, s in enumerate((MISSING, ORPHAN, OK))}
	out.sort(key=lambda r: (order[r["status"]], r["content_item"] or "", r["key"]))
	return out


def report_summary(rows: list[dict], objects: list[dict]) -> list[dict]:
	counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
	pending = s3_media.pending_count()
	total_mb = flt(sum(o["size"] for o in objects) / 1024 / 1024, 2)
	return [
		{"value": len(objects), "label": _("Tệp trên MinIO"), "datatype": "Int", "indicator": "Blue"},
		{"value": total_mb, "label": _("Dung lượng (MB)"), "datatype": "Float", "indicator": "Blue"},
		{"value": counts[OK], "label": _(OK), "datatype": "Int", "indicator": "Green"},
		{
			"value": counts[MISSING],
			"label": _(MISSING),
			"datatype": "Int",
			"indicator": "Red" if counts[MISSING] else "Green",
		},
		{
			"value": counts[ORPHAN],
			"label": _(ORPHAN),
			"datatype": "Int",
			"indicator": "Orange" if counts[ORPHAN] else "Green",
		},
		{
			"value": pending["on_disk"],
			"label": _("Còn trên đĩa, chờ chuyển"),
			"datatype": "Int",
			"indicator": "Orange" if pending["on_disk"] else "Green",
		},
		{
			"value": pending["kept_other_type"],
			"label": _("Ở lại đĩa vì loại tệp"),
			"datatype": "Int",
			"indicator": "Gray",
		},
		{
			"value": pending.get("attach_on_disk", 0),
			"label": _("Tệp đính kèm chờ chuyển"),
			"datatype": "Int",
			"indicator": "Orange" if pending.get("attach_on_disk") else "Green",
		},
		{
			"value": pending.get("attach_kept_other_type", 0) + pending.get("attach_private", 0),
			"label": _("Tệp đính kèm ở lại đĩa (riêng tư, loại hoặc cỡ)"),
			"datatype": "Int",
			"indicator": "Gray",
		},
	]
