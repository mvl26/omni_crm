# ruff: noqa: RUF001
"""Nhật ký mỗi lần nhập danh sách nhà thầu (docs/14 mục 5, V2-03). Chỉ đọc kết quả; việc chạy nằm ở `prospect/importer.py`."""

import json
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html


def _table(rows: list[list[Any]], head: list[str]) -> str:
	th = "".join(f"<th>{escape_html(str(h))}</th>" for h in head)
	body = "".join("<tr>" + "".join(f"<td>{escape_html(str(c))}</td>" for c in r) + "</tr>" for r in rows)
	return f"<table class='table table-bordered table-sm'><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


class OmniProspectImport(Document):
	def validate(self) -> None:
		if self.file and not str(self.file).startswith("/private/files/"):
			frappe.throw(_("Tệp danh sách chứa dữ liệu cá nhân: phải đính kèm ở chế độ riêng tư (private)"))

	def onload(self) -> None:
		self.set_onload("report_html", self.render_report())

	def render_report(self) -> str:
		"""Bảng báo cáo (đã escape mọi giá trị) hiển thị trên form."""
		try:
			summary = json.loads(self.summary_json or "{}")
			confirm = json.loads(self.confirm_json or "[]")
			reconcile = json.loads(self.reconcile_json or "{}")
		except ValueError:
			return ""
		parts: list[str] = []
		sheets = summary.get("sheets") or {}
		if sheets:
			parts.append(
				_table(
					[
						[n, s["rows"], s["created"], s["updated"], s["skipped"], s["errors"]]
						for n, s in sheets.items()
					],
					["Sheet", "Dòng", "Tạo mới", "Cập nhật", "Bỏ qua", "Lỗi"],
				)
			)
		if confirm:
			parts.append(f"<h6>{escape_html(_('Cần xác nhận'))} ({len(confirm)})</h6>")
			parts.append(
				_table(
					[
						[c.get("kind"), c.get("message"), json.dumps(c.get("records"), ensure_ascii=False)]
						for c in confirm
					],
					["Loại", "Mô tả", "Bản ghi"],
				)
			)
		mism = reconcile.get("mismatches") or []
		if reconcile:
			parts.append(
				f"<p>{escape_html(_('Đối chiếu với tệp'))}: {reconcile.get('compared', 0)} nhà thầu, "
				f"{len(mism)} chênh lệch; top 10 khớp: {escape_html(str(reconcile.get('top10_match')))}</p>"
			)
		if mism:
			parts.append(
				_table(
					[[m["tax"], m["trường"], m["tệp"], m["CRM"], m["lý do dự kiến"]] for m in mism[:100]],
					["MST", "Trường", "Tệp", "CRM", "Lý do dự kiến"],
				)
			)
		return "".join(parts)
