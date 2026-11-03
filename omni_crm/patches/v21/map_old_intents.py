"""V21-06 (F6): ánh xạ ý định cũ của Lead đang mở sang ý định theo bối cảnh nhà thầu.

    Hỏi giá -> Hỏi phí;  Đặt hàng -> Yêu cầu đề xuất;  Tư vấn -> Hỏi cách vận hành.  (Khiếu nại, Khác giữ nguyên.)

KHÔNG đăng ký trong patches.txt: `bench migrate` sẽ chạy thật ngay trên staging/production và đánh dấu "đã chạy" kể cả
khi chỉ là dry run. Chạy bằng tay, SAU `bench migrate` (để Select `omni_intent` đã có giá trị mới):

    # dry run (mặc định): chỉ in số Lead theo từng ánh xạ, không ghi gì
    bench --site <site> execute omni_crm.patches.v21.map_old_intents.execute
    # chạy thật
    bench --site <site> execute omni_crm.patches.v21.map_old_intents.execute --kwargs "{'dry_run': False}"

Chỉ Lead đang mở (cùng định nghĩa với `rescore_open_leads.open_leads`). Không động Lead mà sale đã tự sửa `omni_intent`
(có trong `omni_manual_fields`). Chạy lại được: lần hai không còn Lead nào mang giá trị cũ nên không đổi gì. Điểm quan tâm
không đổi sau patch (scoring hiểu cả giá trị cũ lẫn mới), nên không chấm lại.
"""

from __future__ import annotations

import frappe

from omni_crm import flags
from omni_crm.bot import intents
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.patches.v2.rescore_open_leads import open_leads

LEAD = "CRM Lead"
BATCH = 200


def execute(dry_run: bool | str = True) -> dict:
	"""Ánh xạ ý định cũ của Lead đang mở; mặc định dry run. In và trả {dry_run, open, mapped:{cũ: số}, skipped_manual, changed}."""
	if isinstance(dry_run, str):  # `--kwargs` của bench có thể đưa chuỗi
		dry_run = dry_run.strip().lower() not in ("0", "false", "no")
	report: dict = {
		"dry_run": bool(dry_run),
		"open": 0,
		"mapped": dict.fromkeys(intents.LEGACY_TO_B2B, 0),
		"skipped_manual": 0,
		"changed": 0,
	}
	leads = open_leads()
	report["open"] = len(leads)
	for index, lead in enumerate(leads, 1):
		row = frappe.db.get_value(LEAD, lead, ["omni_intent", "omni_manual_fields"], as_dict=True)
		new = intents.LEGACY_TO_B2B.get(row.omni_intent or "")
		if not new:
			continue
		if "omni_intent" in lead_bridge._parse_fields(row.omni_manual_fields):
			report["skipped_manual"] += 1
			continue
		report["mapped"][row.omni_intent] += 1
		report["changed"] += 1
		if not dry_run:
			with flags.omni_bot():
				frappe.db.set_value(LEAD, lead, "omni_intent", new, update_modified=False)
			if index % BATCH == 0:
				frappe.db.commit()
	if not dry_run:
		frappe.db.commit()
	lines = [f"{'DRY RUN - ' if dry_run else ''}Lead đang mở: {report['open']}"]
	lines += [f"  {old} -> {intents.LEGACY_TO_B2B[old]}: {n} Lead" for old, n in report["mapped"].items()]
	lines.append(
		f"  bỏ qua vì sale đã tự sửa ý định: {report['skipped_manual']}; tổng đổi: {report['changed']}"
	)
	print("\n".join(lines))
	return report
