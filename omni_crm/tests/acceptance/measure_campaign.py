"""Đo chỉ số mục tiêu chiến dịch quảng cáo (V2-08, docs/nghiem-thu/v2.md mục 6) từ dữ liệu thật. CHỈ ĐỌC, không ghi gì.

	bench --site <site> execute omni_crm.tests.acceptance.measure_campaign.run \
		--kwargs '{"start": "2026-11-09 00:00:00", "end": "2026-11-16 00:00:00", "platform_reported": {"Meta": 120, "Google": 80}}'

Tham số: `start`, `end`; `platform_reported` = số lead mà Meta/Google Ads báo trong cùng kỳ (để tính "lead mất"; không có thì
chỉ số đó ghi "chưa đo"); `exclude_prefixes` (mặc định bỏ dữ liệu giả LOAD, PLT, TD, DEMO, RV);
`sla_minutes` (mặc định lấy `response_sla_minutes` của Omni Settings, 15).

Định nghĩa (khớp docs/15 mục 7, 9 và khối "Theo chiến dịch" của Tổng quan):
- Lead quảng cáo: CRM Lead có `omni_ad_platform` (Meta/Google/Website), không phải thử (`omni_is_test`), chưa gộp vào Lead khác.
- Lead hợp lệ: có SĐT hoặc email hợp lệ (`omni_phone_e164`/`omni_email_norm`).
- Phản hồi trong hạn: `omni_first_response_at` có và số PHÚT LÀM VIỆC từ lúc Lead tạo tới đó <= SLA.
- Lead trùng: Lead quảng cáo đã bị gộp vào Lead khác + cặp nghi trùng chưa xử lý có Lead quảng cáo.
- Lead mất: lead nền tảng báo - Lead quảng cáo của hệ thống cùng nền tảng (không âm) + tin quảng cáo ở trạng thái Manual.
- Chi phí mỗi Lead hợp lệ: tổng `spend` nhập tay của Omni Campaign có Lead trong kỳ / số Lead hợp lệ.
Mọi số trong cột "Đo được" của docs/nghiem-thu/v2.md phải lấy từ script này sau tuần chạy thử THẬT.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from typing import Any

import frappe
from frappe.utils import get_datetime

from omni_crm.utils import business_hours

DEFAULT_EXCLUDE = ("LOAD", "PLT", "TD", "DEMO", "RV")
AD_CHANNEL_TYPES = ("Meta Lead Ads", "Google Lead Form", "Web Form")


def _fmt(value: Any) -> Any:
	return None if value is None else (round(value, 1) if isinstance(value, float) else value)


def run(
	start: str,
	end: str,
	platform_reported: dict | str | None = None,
	exclude_prefixes: tuple | list | None = None,
	sla_minutes: int | None = None,
) -> dict:
	"""Tính các chỉ số trong [start, end); in JSON và bảng Markdown; trả dict."""
	if isinstance(platform_reported, str):
		platform_reported = json.loads(platform_reported)
	prefixes = tuple(exclude_prefixes or DEFAULT_EXCLUDE)
	start_dt, end_dt = get_datetime(start), get_datetime(end)
	sla = int(sla_minutes or 0) or _sla_minutes()
	leads = frappe.db.sql(
		"""select name, lead_name, creation, omni_ad_platform as platform, omni_campaign as campaign,
			omni_first_response_at as first_response, omni_phone_e164 as phone, omni_email_norm as email
		from `tabCRM Lead` where creation >= %s and creation < %s and ifnull(omni_ad_platform, '') != ''
			and ifnull(omni_is_test, 0) = 0 and ifnull(omni_merged_into, '') = ''""",
		(start_dt, end_dt),
		as_dict=True,
	)
	leads = [r for r in leads if not any((r.lead_name or "").startswith(p) for p in prefixes)]
	valid = [r for r in leads if r.phone or r.email]
	days = max((end_dt - start_dt).total_seconds() / 86400, 1e-9)
	minutes = []
	on_time = 0
	for r in valid:
		if r.first_response:
			m = business_hours.business_minutes_between(r.creation, get_datetime(r.first_response))
			minutes.append(m)
			on_time += m <= sla
	merged = frappe.db.count(
		"CRM Lead",
		{
			"creation": ["between", [start_dt, end_dt]],
			"omni_ad_platform": ["is", "set"],
			"omni_merged_into": ["is", "set"],
		},
	)
	names = [r.name for r in leads] or [""]
	pending_pairs = frappe.db.sql(
		"select count(*) from `tabOmni Duplicate Candidate` where status = 'Open' and (lead_a in %s or lead_b in %s)",
		(names, names),
	)[0][0]
	manual = frappe.db.sql(
		"""select count(*) from `tabOmni Inbound Message` m join `tabOmni Channel` c on c.name = m.channel
		where m.status = 'Manual' and c.channel_type in %s and m.creation >= %s and m.creation < %s""",
		(AD_CHANNEL_TYPES, start_dt, end_dt),
	)[0][0]
	by_platform = Counter(r.platform for r in leads)
	lost: Any = "chưa đo (cần số lead nền tảng báo: tham số platform_reported)"
	if platform_reported:
		lost = sum(max(0, int(v) - by_platform.get(k, 0)) for k, v in platform_reported.items()) + int(manual)
	campaigns = {r.campaign for r in leads if r.campaign}
	spend = (
		frappe.db.sql(
			"select sum(spend) from `tabOmni Campaign` where name in %s", (list(campaigns) or [""],)
		)[0][0]
		if campaigns
		else None
	)
	result = {
		"period": [str(start_dt), str(end_dt)],
		"sla_minutes": sla,
		"leads": len(leads),
		"leads_per_day": _fmt(len(leads) / days),
		"by_platform": dict(by_platform),
		"valid_leads": len(valid),
		"on_time_pct": _fmt(100 * on_time / len(valid)) if valid else None,
		"first_response_median_business_minutes": _fmt(statistics.median(minutes)) if minutes else None,
		"not_yet_responded": len(valid) - len(minutes),
		"duplicates": {"merged_leads": merged, "open_candidate_pairs": pending_pairs},
		"lost": lost,
		"manual_ad_messages": manual,
		"spend_entered": spend,
		"cost_per_valid_lead": _fmt(float(spend) / len(valid)) if spend and valid else None,
	}
	print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
	print(_table(result))
	return result


def _sla_minutes() -> int:
	from omni_crm import flags

	return int(flags.care_setting("response_sla_minutes"))


def _table(r: dict) -> str:
	rows = [
		("Lead mới/ngày", r["leads_per_day"]),
		("% phản hồi trong hạn (giờ làm việc)", r["on_time_pct"]),
		("Phản hồi lần đầu, trung vị (phút làm việc)", r["first_response_median_business_minutes"]),
		(
			"Lead trùng (đã gộp / cặp nghi trùng chờ)",
			f"{r['duplicates']['merged_leads']} / {r['duplicates']['open_candidate_pairs']}",
		),
		("Lead mất", r["lost"]),
		("Chi phí mỗi Lead hợp lệ", r["cost_per_valid_lead"]),
	]
	return "\n".join(
		[
			"| Chỉ số | Đo được |",
			"| --- | --- |",
			*(f"| {k} | {'chưa đo' if v is None else v} |" for k, v in rows),
		]
	)
