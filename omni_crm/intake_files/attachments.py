"""Chép tệp đính kèm của miyano-intake (spool, chỉ đọc) vào File riêng tư của Frappe (ADR 0022 mục B1).

Đường dẫn trong file JSON là tương đối gốc spool (`attachments/<sha1(event_key)>/<tên>`); chỉ chấp nhận tệp nằm trong
`<spool>/attachments/` (chặn `..` và liên kết ra ngoài). Tệp thiếu hoặc quá giới hạn `max_file_size` của site không làm
hỏng tin: mục đính kèm vẫn còn tên, loại và ghi chú lý do. File được gắn vào Omni Inbound Message sau khi lưu tin
(`attach`), để quyền mở tệp theo quyền đọc tin.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe.utils.file_manager import get_max_file_size

INBOUND = "Omni Inbound Message"
SAVEPOINT = "omni_intake_attachment"


def _inside(root: Path, rel: str) -> Path | None:
	"""Đường dẫn thật của tệp nếu nằm trong `<root>/attachments/`, ngược lại None."""
	base = (root / "attachments").resolve()
	try:
		path = (root / rel).resolve()
	except (OSError, RuntimeError):
		return None
	return path if path.is_relative_to(base) else None


def store(root: Path | None, items: list[dict]) -> tuple[list[dict], list[str]]:
	"""Chép các tệp vào File riêng tư; trả (danh sách đính kèm để lưu vào tin, tên các File đã tạo)."""
	out: list[dict] = []
	created: list[str] = []
	limit = get_max_file_size()
	for item in items or []:
		name = str(item.get("name") or item.get("type") or "tep")[:140]
		kind = str(item.get("type") or "file")
		mime = mimetypes.guess_type(name)[0] or ""
		entry: dict[str, Any] = {"name": name, "type": kind, "mime": mime}
		rel = item.get("path")
		path = _inside(root, str(rel)) if (root is not None and rel) else None
		if not rel and kind == "sticker":  # ADR 0027: sticker chỉ có mã, Meta không gửi ảnh
			entry["note"] = _("Sticker")
		elif not rel:
			entry["note"] = _("Meta không cho tải tệp này về")
		elif path is None or not path.is_file():
			entry["note"] = _("Tệp không còn trong thư mục của dịch vụ nhận")
		elif path.stat().st_size > limit:
			entry["note"] = _("Tệp lớn hơn giới hạn {0} MB của hệ thống").format(limit // (1024 * 1024))
			entry["size"] = path.stat().st_size
		else:
			# Một tệp hỏng (vd ảnh PIL không đọc được) không được làm hỏng cả tin: lùi về savepoint, ghi chú, đi tiếp.
			frappe.db.savepoint(SAVEPOINT)
			try:
				file_doc = frappe.get_doc(
					{"doctype": "File", "file_name": name, "is_private": 1, "content": path.read_bytes()}
				).insert(ignore_permissions=True)
			except Exception as e:
				frappe.db.rollback(save_point=SAVEPOINT)
				entry["note"] = _("Không lưu được tệp vào hệ thống ({0})").format(type(e).__name__)
			else:
				created.append(file_doc.name)
				entry.update(url=file_doc.file_url, size=file_doc.file_size)
		out.append(entry)
	return out, created


def attach(file_names: list[str], inbound_name: str) -> None:
	"""Gắn các File vừa tạo vào Omni Inbound Message để quyền mở tệp đi theo quyền đọc tin."""
	for name in file_names:
		frappe.db.set_value(
			"File",
			name,
			{"attached_to_doctype": INBOUND, "attached_to_name": inbound_name},
			update_modified=False,
		)
