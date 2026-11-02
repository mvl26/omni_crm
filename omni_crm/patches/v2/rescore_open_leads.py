"""V2-06: chấm lại điểm quan tâm cho Lead đang mở theo bảng điểm B2B (bot/scoring.py).

KHÔNG đăng ký trong patches.txt: `bench migrate` sẽ chạy thật ngay trên staging/production, bỏ qua cổng duyệt của người
phụ trách bảng điểm. Chạy bằng tay, sau khi bảng điểm B2B đã được duyệt và nhập:

    # dry run (mặc định): chỉ in, không ghi gì
    bench --site <site> execute omni_crm.patches.v2.rescore_open_leads.execute
    # chạy thật; mặc định KHÔNG báo Lead nóng (tránh bão thông báo)
    bench --site <site> execute omni_crm.patches.v2.rescore_open_leads.execute --kwargs "{'dry_run': False}"

Chạy lại được: lần chạy thứ hai không đổi điểm nào. Không đụng `hot_notified_at`, không gửi thông báo, không đổi
trường nào ngoài `omni_score`, `omni_score_breakdown`.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from omni_crm.bot import scoring

BATCH = 200


def open_leads() -> list[str]:
	"""Lead đang mở: chưa chuyển Deal, chưa bị gộp, trạng thái không phải Lost."""
	rows = frappe.db.sql(
		"""select l.name from `tabCRM Lead` l left join `tabCRM Lead Status` s on s.name = l.status
        where ifnull(l.converted, 0) = 0 and ifnull(l.omni_merged_into, '') = ''
        and ifnull(s.type, '') != 'Lost' order by l.name"""
	)
	return [r[0] for r in rows]


def execute(dry_run: bool | str = True) -> dict:
	"""Chấm lại Lead đang mở; mặc định dry run. In báo cáo và trả dict {leads, changed, hot_before, hot_after, ...}."""
	if isinstance(dry_run, str):  # `--kwargs` của bench có thể đưa chuỗi
		dry_run = dry_run.strip().lower() not in ("0", "false", "no")
	report = {
		"dry_run": bool(dry_run),
		"leads": 0,
		"changed": 0,
		"hot_before": 0,
		"hot_after": 0,
		"became_hot": 0,
		"stopped_hot": 0,
		"skipped": None,
	}
	rules = scoring.load_rules()
	if not scoring.scoring_enabled() or not rules:
		report["skipped"] = "chấm điểm tắt (lead_scoring_enabled) hoặc chưa có bảng điểm: không làm gì"
		print(report["skipped"])
		return report
	threshold = scoring.hot_threshold()
	leads = open_leads()
	report["leads"] = len(leads)
	for index, lead in enumerate(leads, 1):
		row = frappe.db.get_value("CRM Lead", lead, ["omni_score", "organization"], as_dict=True)
		tier = scoring.fit_tier_of(row.organization)
		old = cint(row.omni_score)
		new, _items = scoring.compute(scoring.collect_signals(lead), rules)
		was, now = scoring.is_hot(old, tier, threshold), scoring.is_hot(new, tier, threshold)
		report["hot_before"] += was
		report["hot_after"] += now
		report["became_hot"] += now and not was
		report["stopped_hot"] += was and not now
		if new != old:
			report["changed"] += 1
		if not dry_run:
			scoring.score_lead(lead)  # ghi điểm + breakdown; KHÔNG gọi check_hot nên không báo Lead nóng
			if index % BATCH == 0:
				frappe.db.commit()
	if not dry_run:
		frappe.db.commit()
	print(
		f"{'DRY RUN - ' if dry_run else ''}Lead đang mở: {report['leads']}; đổi điểm: {report['changed']}; "
		f"nóng trước/sau: {report['hot_before']}/{report['hot_after']} "
		f"(thành nóng {report['became_hot']}, hết nóng {report['stopped_hot']})"
	)
	return report
