# ruff: noqa: RUF001, RUF002
"""V2-05: điền ngược `omni_ad_platform` và `omni_campaign` cho Lead đã nhập từ danh sách nhà thầu ở V2-03 (docs/15 mục 8).

Nhận diện Lead nhập từ danh sách bằng `source = "Danh sách mua sắm công"` (CRM Lead Source do importer tạo). Chỉ điền khi trường
còn TRỐNG (không ghi đè); chiến dịch là "Liên hệ chủ động – <tháng tạo Lead>". Chạy lại được; mặc định DRY RUN.

KHÔNG đăng ký trong patches.txt (cổng duyệt của người phụ trách, như V2-02/V2-03):

    bench --site <site> execute omni_crm.patches.v2.backfill_ad_fields.execute
    bench --site <site> execute omni_crm.patches.v2.backfill_ad_fields.execute --kwargs "{'dry_run': False}"

Chưa chạy trên staging/dữ liệu thật; mới chạy trên dữ liệu tổng hợp trong test.
"""

from __future__ import annotations

import frappe

from omni_crm.crm_bridge import campaign as campaign_bridge

LEAD = "CRM Lead"
SOURCE = "Danh sách mua sắm công"
BATCH = 200


def execute(dry_run: bool | str = True) -> dict[str, int]:
	"""Trả {"candidates", "platform_to_set", "campaign_to_set", "campaigns_to_create"}; chạy thật thì các số là số đã ghi."""
	if isinstance(dry_run, str):
		dry_run = dry_run.strip().lower() not in ("0", "false", "no")
	rows = frappe.get_all(
		LEAD,
		filters={"source": SOURCE},
		fields=["name", "creation", "omni_ad_platform", "omni_campaign", "omni_last_campaign"],
		order_by="creation asc",
		limit_page_length=0,
	)
	report = {"candidates": len(rows), "platform_to_set": 0, "campaign_to_set": 0, "campaigns_to_create": 0}
	new_campaigns: set[str] = set()
	for i, r in enumerate(rows, 1):
		values: dict[str, str] = {}
		if not r.omni_ad_platform:
			values["omni_ad_platform"] = campaign_bridge.LEAD_PLATFORM
			report["platform_to_set"] += 1
		if not r.omni_campaign:
			name = campaign_bridge.proactive_campaign_name(r.creation)
			if not frappe.db.exists(campaign_bridge.CAMPAIGN, name):
				new_campaigns.add(name)
			values["omni_campaign"] = name
			if not r.omni_last_campaign:
				values["omni_last_campaign"] = name
			report["campaign_to_set"] += 1
		if values and not dry_run:
			campaign_bridge.ensure_proactive_campaign(r.creation)
			frappe.db.set_value(LEAD, r.name, values, update_modified=False)
		if not dry_run and i % BATCH == 0:
			frappe.db.commit()
	report["campaigns_to_create"] = len(new_campaigns)
	if not dry_run:
		frappe.db.commit()
	print(("DRY RUN " if dry_run else "ĐÃ GHI ") + str(report))
	return report
