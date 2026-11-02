"""API nhập danh sách nhà thầu (V2-03): chạy thử / nhập thật một nhật ký đã đính kèm tệp, tải mẫu trống. Chỉ Omni Admin."""

from __future__ import annotations

from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.prospect import importer

TEMPLATE = "Mau_DanhSach_NhaThau_TiemNang.xlsx"


@frappe.whitelist(methods=["POST"])
def run_import(log: str, dry_run: int | str = 1) -> dict:
	"""Chạy thử (dry_run=1, không ghi gì vào CRM) hoặc nhập thật một nhật ký `Omni Prospect Import`; việc chạy nền."""
	user = importer.require_runner()
	if not frappe.has_permission(importer.LOG, "write", log):
		frappe.throw(_("Không có quyền với lần nhập này"), frappe.PermissionError)
	importer.start_log(log, bool(cint(dry_run)), user)
	return {"log": log, "status": "Queued", "dry_run": bool(cint(dry_run))}


@frappe.whitelist()
def download_template() -> None:
	"""Tải mẫu trống nguyên bản (giữ dòng ví dụ; importer luôn bỏ qua dòng 3)."""
	importer.require_runner()
	path = Path(frappe.get_app_path("omni_crm")).parent / "mau" / TEMPLATE
	frappe.response["filename"] = TEMPLATE
	frappe.response["filecontent"] = path.read_bytes()
	frappe.response["type"] = "download"
