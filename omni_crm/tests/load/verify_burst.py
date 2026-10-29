"""Đối chiếu kết quả tải với DB (chạy trong bench, KHÔNG thuộc run-tests).

	bench --site omni.test execute omni_crm.tests.load.verify_burst.run \
		--kwargs '{"channel": "LOAD kenh", "expected_unique": 6000, "expected_customers": 300, "wait_minutes": 10}'
	bench --site omni.test execute omni_crm.tests.load.verify_burst.cleanup --kwargs '{"channel": "LOAD kenh"}'

`run` thăm dò mỗi 5 giây tới khi New+Processing+Retry = 0 hoặc hết `wait_minutes`, rồi in: thời gian chờ
hết hàng đợi, số tin vào / số mong đợi, số Lead / số khách, số Duplicate Candidate, p50/p95 nhận->Done.
"""

from __future__ import annotations

import json
import math
import time

import frappe

INBOUND = "Omni Inbound Message"
OPEN = ("New", "Processing", "Retry")


def _counts(channel: str) -> dict:
	rows = frappe.db.sql(
		"select status, count(*) from `tabOmni Inbound Message` where channel=%s group by status", channel
	)
	return {s: n for s, n in rows}


def _pct(vals: list[float], p: float) -> float | None:
	if not vals:
		return None
	vals = sorted(vals)
	return round(vals[max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))], 2)


def run(channel: str, expected_unique: int, expected_customers: int, wait_minutes: int = 10) -> None:
	"""Chờ hàng đợi cạn rồi in báo cáo JSON."""
	t0 = time.time()
	counts = _counts(channel)
	while sum(counts.get(s, 0) for s in OPEN) and time.time() - t0 < int(wait_minutes) * 60:
		time.sleep(5)
		frappe.db.rollback()  # đọc lại ảnh chụp mới (REPEATABLE READ)
		counts = _counts(channel)
	drain_s = round(time.time() - t0, 1)
	total = sum(counts.values())
	leads = frappe.db.sql(
		"select count(distinct lead) from `tabOmni Inbound Message` where channel=%s and lead is not null",
		channel,
	)[0][0]
	ident = frappe.db.count("Omni Identity", {"identity_key": ["like", "Zalo OA:LOADC%"]})
	lead_names = frappe.db.sql_list(
		"select distinct lead from `tabOmni Inbound Message` where channel=%s and lead is not null", channel
	)
	cand = (
		frappe.db.count("Omni Duplicate Candidate", {"lead_a": ["in", lead_names]}) if lead_names else 0
	) + (frappe.db.count("Omni Duplicate Candidate", {"lead_b": ["in", lead_names]}) if lead_names else 0)
	dup_dedupe = frappe.db.sql(
		"select count(*) from (select dedupe_key from `tabOmni Inbound Message` where channel=%s "
		"group by dedupe_key having count(*)>1) t",
		channel,
	)[0][0]
	secs = frappe.db.sql(
		"select timestampdiff(second, creation, modified) from `tabOmni Inbound Message` "
		"where channel=%s and status='Done'",
		channel,
	)
	secs = [float(r[0]) for r in secs if r[0] is not None]
	crm_leads_per_identity = frappe.db.sql(
		"select count(*) from (select identity from `tabOmni Inbound Message` where channel=%s and lead is not null "
		"group by identity having count(distinct lead)>1) t",
		channel,
	)[0][0]
	print(
		json.dumps(
			{
				"status_counts": counts,
				"waited_for_drain_s": drain_s,
				"drained": not sum(counts.get(s, 0) for s in OPEN),
				"inbound_total": total,
				"expected_unique": expected_unique,
				"lost": max(0, expected_unique - total),
				"unexpected_extra_rows": max(0, total - expected_unique),
				"dedupe_key_duplicates": dup_dedupe,
				"leads_distinct": leads,
				"identities": ident,
				"expected_customers": expected_customers,
				"identities_with_more_than_one_lead": crm_leads_per_identity,
				"duplicate_candidates_touching_these_leads": cand,
				"receive_to_done_s": {
					"n": len(secs),
					"p50": _pct(secs, 0.5),
					"p95": _pct(secs, 0.95),
					"max": max(secs) if secs else None,
				},
			},
			ensure_ascii=False,
			indent=1,
		)
	)


def cleanup(channel: str) -> None:
	"""Xóa dữ liệu tải: tin, định danh LOADC*, Lead sinh ra và bản ghi phụ; commit."""
	frappe.db.rollback()
	leads = frappe.db.sql_list(
		"select distinct lead from `tabOmni Inbound Message` where channel=%s and lead is not null", channel
	)
	idents = frappe.get_all(
		"Omni Identity", filters={"identity_key": ["like", "Zalo OA:LOADC%"]}, pluck="name"
	)
	leads += [
		x
		for x in frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead")
		if x
	]
	leads = sorted(set(leads))
	if leads:
		for f in ("lead_a", "lead_b"):
			frappe.db.delete("Omni Duplicate Candidate", {f: ["in", leads]})
		for dt in ("Comment", "Communication", "ToDo"):
			frappe.db.delete(dt, {"reference_name": ["in", leads]})
		frappe.db.delete("Communication Link", {"link_doctype": "CRM Lead", "link_name": ["in", leads]})
		frappe.db.delete("Omni Outbound Message", {"lead": ["in", leads]})
	frappe.db.delete(INBOUND, {"channel": channel})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	for i, lead in enumerate(leads):
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
		if i % 50 == 49:
			frappe.db.commit()
	frappe.db.commit()
	print(f"đã xóa {len(leads)} Lead tải và dữ liệu liên quan của kênh {channel}")
