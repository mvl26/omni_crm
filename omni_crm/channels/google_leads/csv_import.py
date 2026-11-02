"""Nhập lead Google tải về dạng CSV từ giao diện Google Ads (docs/15 mục 3, bù khi webhook sót).

Dùng CHUNG kênh (`Omni Channel`) và `lead_id` với webhook nên chống trùng với nhau. Chạy bằng tay (quản trị), không có API công khai:

    bench --site <site> execute omni_crm.channels.google_leads.csv_import.run --kwargs "{'channel': '<kênh>', 'file': '/đường/dẫn.csv', 'form_id': '<mã biểu mẫu>'}"

Tên cột CSV của Google Ads CHƯA ĐƯỢC XÁC NHẬN với tệp tải thật (chưa có tài khoản): hỗ trợ các tên thường gặp và quy mọi cột còn lại về
`column_id` dạng CHỮ_HOA_GẠCH_DƯỚI; cột `Lead ID`/`lead_id` là bắt buộc. Chưa thử với CSV thật.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import frappe

from omni_crm.channels.google_leads.adapter import build
from omni_crm.crm_bridge.form_lead import get_form
from omni_crm.ingest import gateway

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
META_HEADERS = {
	"lead_id": "lead_id",
	"lead id": "lead_id",
	"form_id": "form_id",
	"form id": "form_id",
	"campaign_id": "campaign_id",
	"campaign id": "campaign_id",
	"gcl_id": "gcl_id",
	"google click id": "gcl_id",
	"gclid": "gcl_id",
	"adgroup_id": "adgroup_id",
	"ad group id": "adgroup_id",
	"lead_submit_time": "lead_submit_time",
	"lead submit time": "lead_submit_time",
	"submit time": "lead_submit_time",
	"time submitted": "lead_submit_time",
	"is_test": "is_test",
}
ALIASES = {
	"full name": "FULL_NAME",
	"name": "FULL_NAME",
	"first name": "FIRST_NAME",
	"last name": "LAST_NAME",
	"email": "EMAIL",
	"user email": "EMAIL",
	"phone number": "PHONE_NUMBER",
	"user phone": "PHONE_NUMBER",
	"phone": "PHONE_NUMBER",
	"company name": "COMPANY_NAME",
	"job title": "JOB_TITLE",
	"city": "CITY",
}


def column_id(header: str) -> str:
	"""Tên cột CSV -> `column_id` của Google (các tên quen thuộc, còn lại CHỮ_HOA_GẠCH_DƯỚI)."""
	key = header.strip().lower()
	return ALIASES.get(key) or re.sub(r"[^A-Za-z0-9]+", "_", header.strip()).strip("_").upper()


def row_to_payload(row: dict[str, Any], form_id: str | None) -> dict:
	"""Một dòng CSV -> payload kiểu webhook (không có google_key)."""
	data: dict[str, Any] = {"form_id": form_id or "", "user_column_data": []}
	for header, value in row.items():
		if header is None or value is None:
			continue
		key = header.strip().lower()
		if key in META_HEADERS:
			target = META_HEADERS[key]
			data[target] = (
				str(value).strip().lower() in ("1", "true", "yes") if target == "is_test" else value.strip()
			)
		elif value.strip():
			data["user_column_data"].append({"column_id": column_id(header), "string_value": value.strip()})
	return data


def run(channel: str, file: str, form_id: str | None = None, dry_run: bool | str = False) -> dict[str, int]:
	"""Nhập CSV; trả {"rows", "created", "duplicates", "errors"}. dry_run chỉ đếm, không lưu."""
	if isinstance(dry_run, str):
		dry_run = dry_run.strip().lower() not in ("0", "false", "no")
	channel_doc = frappe.get_doc("Omni Channel", channel)
	if channel_doc.channel_type != "Google Lead Form":
		frappe.throw("Kênh phải là loại Google Lead Form")
	path = Path(file)
	if path.stat().st_size > MAX_BYTES:
		frappe.throw("Tệp CSV quá lớn")
	stats = {"rows": 0, "created": 0, "duplicates": 0, "errors": 0}
	with path.open(encoding="utf-8-sig", newline="") as fh:
		for row in csv.DictReader(fh):
			stats["rows"] += 1
			if stats["rows"] > MAX_ROWS:
				frappe.throw("Tệp CSV có quá nhiều dòng")
			try:
				data = row_to_payload(row, form_id or row.get("form_id") or row.get("Form ID"))
				form_doc = get_form("Google", str(data.get("form_id") or ""))
				if form_doc is None:
					raise ValueError("form")
				event = build(channel_doc, data, form_doc)
				if frappe.db.exists(
					"Omni Inbound Message", {"dedupe_key": f"{channel}:{event.external_message_id}"}
				):
					stats["duplicates"] += 1
				elif dry_run:
					stats["created"] += 1
				elif gateway.save_inbound(event):
					stats["created"] += 1
				else:
					stats["duplicates"] += 1
			except (ValueError, KeyError):
				stats["errors"] += 1
			if not dry_run:
				frappe.db.commit()
	return stats
