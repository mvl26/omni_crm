"""Đối soát hằng giờ lead Meta (docs/15 mục 2): bù lead mà webhook bị sót.

Với mỗi Omni Lead Form Meta đang bật: lấy lead có `time_created` sau mốc đối soát cuối và lưu sự kiện cho lead chưa có (chống
trùng theo `leadgen_id`, cùng khóa với webhook nên hai đường không tạo đôi). Gọi Graph API SAU khi đã commit (không giữ khóa DB).
Lỗi một biểu mẫu chỉ ghi log loại lỗi và đi tiếp biểu mẫu khác. CHƯA THỬ VỚI NỀN TẢNG THẬT.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

from omni_crm.channels import form_common
from omni_crm.channels.meta_leads.client import MetaClient
from omni_crm.channels.meta_leads.errors import MetaError
from omni_crm.crm_bridge import form_lead
from omni_crm.ingest import gateway

FORM = "Omni Lead Form"
INBOUND = "Omni Inbound Message"
FIRST_LOOKBACK_HOURS = 24
OVERLAP_MINUTES = 5  # lùi mốc một chút để không sót lead sát ranh giới; trùng đã được chống bằng dedupe_key


def run() -> dict[str, int]:
	"""Tác vụ theo giờ: đối soát mọi kênh Meta đang bật có Page access token. Trả {"channels", "created"}."""
	created = channels = 0
	for ch in frappe.get_all(
		"Omni Channel", filters={"channel_type": "Meta Lead Ads", "enabled": 1}, pluck="name"
	):
		channels += 1
		try:
			created += reconcile_channel(ch)["created"]
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: đối soát Meta lỗi", message=f"kenh={ch}")
			frappe.db.commit()
	return {"channels": channels, "created": created}


def _ts(value: datetime) -> int:
	return int(value.timestamp())


def reconcile_channel(channel: str, client: Any = None, now: datetime | None = None) -> dict[str, int]:
	"""Đối soát một kênh; trả {"forms", "seen", "created", "errors"}."""
	now = now or now_datetime()
	channel_doc = frappe.get_doc("Omni Channel", channel)
	if not channel_doc.get_password("meta_page_access_token", raise_exception=False):
		return {"forms": 0, "seen": 0, "created": 0, "errors": 0}
	client = client or MetaClient(channel)
	stats = {"forms": 0, "seen": 0, "created": 0, "errors": 0}
	forms = frappe.get_all(FORM, filters={"platform": "Meta", "enabled": 1}, pluck="name")
	for name in forms:
		form_doc = frappe.get_doc(FORM, name)
		since = (
			get_datetime(form_doc.last_reconciled_at)
			if form_doc.last_reconciled_at
			else now - timedelta(hours=FIRST_LOOKBACK_HOURS)
		) - timedelta(minutes=OVERLAP_MINUTES)
		frappe.db.commit()  # không giữ giao dịch/khóa nào mở khi gọi ra ngoài
		stats["forms"] += 1
		try:
			leads = client.list_form_leads(form_doc.external_form_id, _ts(since))
		except MetaError as e:
			stats["errors"] += 1
			frappe.logger("omni").warning("omni: doi soat meta loi (%s)", e.kind)
			continue
		for lead in leads:
			stats["seen"] += 1
			lead_id = form_common.clean(lead.get("id"), 140)
			if not lead_id or frappe.db.exists(INBOUND, {"dedupe_key": f"{channel}:{lead_id}"}):
				continue
			try:
				ad = client.get_ad_info(str(lead.get("ad_id") or ""))
			except MetaError:
				ad = {}
			hook = {
				"lead_id": lead_id,
				"form_id": form_doc.external_form_id,
				"page_id": channel_doc.meta_page_id,
			}
			form = form_lead.meta_form_from_graph(lead, ad, hook, form_doc)
			event = form_common.to_event(
				channel_doc, form, received_at=form_common.parse_time(lead.get("created_time")) or now
			)
			if gateway.save_inbound(event):
				stats["created"] += 1
			frappe.db.commit()
		frappe.db.set_value(FORM, name, "last_reconciled_at", now, update_modified=False)
		frappe.db.commit()
	return stats
