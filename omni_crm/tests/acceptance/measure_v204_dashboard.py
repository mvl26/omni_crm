"""Đo hiệu năng `snapshot()` của Tổng quan CSKH (docs/13 mục 4; V2-04: nhắm < 1 giây, p95 < 800 ms với 100.000 tin vào).

KHÔNG phải test đơn vị và KHÔNG chạy trên dữ liệu thật: nạp dữ liệu TỔNG HỢP (tiền tố `TD404P`), đo, rồi dọn sạch theo tiền tố
(kể cả khi lỗi). Chạy trên site test:

	bench --site omni.test execute omni_crm.tests.acceptance.measure_v204_dashboard.run \
		--kwargs '{"leads": 25000, "messages": 100000, "repeats": 10}'

Đo "lạnh": gọi thẳng `blocks.run_blocks` (không qua cache 60 giây). Kết quả in JSON: số liệu của từng kỳ/phạm vi (trung vị, p95, lớn nhất,
mili giây) cho trưởng nhóm (thấy tất cả) và cho một Sales User chỉ có ~1/10 số Lead, với MỌI tính năng Đợt 2-3 bật.
Số đo trên máy dev không phản ánh staging (không nginx, một MariaDB cùng máy, dữ liệu sinh đều).
"""

from __future__ import annotations

import json
import math
import random
import time
from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import now_datetime

PREFIX = "TD404P"
USERS = [f"td404p_sale{i}@example.com" for i in range(10)]
CHUNK = 2000


def _pct(values: list[float], p: float) -> float:
	v = sorted(values)
	return round(v[max(0, min(len(v) - 1, math.ceil(p * len(v)) - 1))], 1)


def seed(leads: int, messages: int) -> None:
	"""Nạp dữ liệu tổng hợp bằng SQL gộp (nhanh): Lead trải trong 45 ngày, tin vào, ~40% Lead có tin ra, một phần có nhật ký."""
	rnd = random.Random(404)
	now = now_datetime()

	def ts(delta: timedelta) -> str:
		return (now - delta).strftime("%Y-%m-%d %H:%M:%S")

	lead_rows, lead_times = [], {}
	for i in range(leads):
		created = timedelta(days=rnd.random() * 45, minutes=rnd.randint(0, 600))
		name = f"{PREFIX}-{i:06d}"
		answered = rnd.random() < 0.4
		lead_times[name] = (created, answered)
		lead_rows.append(
			(
				name,
				ts(created),
				ts(created),
				"Administrator",
				"Administrator",
				f"{PREFIX} {i}",
				f"{PREFIX} {i}",
				USERS[i % len(USERS)],
				"Tiềm năng" if rnd.random() < 0.7 else "Đã liên hệ",
				rnd.choice(["Zalo OA", "Email", "Telephony"]),
				ts(created),
				ts(created - timedelta(minutes=rnd.randint(2, 300))) if answered else None,
				rnd.randint(0, 100),
			)
		)
	for i in range(0, len(lead_rows), CHUNK):
		frappe.db._cursor.executemany(
			"""insert into `tabCRM Lead` (name, creation, modified, owner, modified_by, first_name, lead_name, lead_owner,
				status, omni_first_channel, omni_last_inbound_at, omni_first_response_at, omni_score)
			values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
			lead_rows[i : i + CHUNK],
		)
	msg_rows, out_rows = [], []
	names = list(lead_times)
	for i in range(messages):
		lead = names[i % leads]
		created, answered = lead_times[lead]
		at = created - timedelta(minutes=rnd.randint(0, 3000)) if i >= leads else created
		msg_rows.append(
			(
				f"{PREFIX}M-{i:07d}",
				ts(at),
				ts(at),
				"Administrator",
				"Administrator",
				f"td404p-{i}",
				"Zalo OA",
				"message",
				f"Zalo OA:{PREFIX}{i % leads}",
				lead,
				ts(at),
				"hỏi giá",
				"Done",
			)
		)
		if answered and i < leads:
			out_rows.append(
				(
					f"{PREFIX}O-{i:07d}",
					ts(created - timedelta(minutes=5)),
					ts(created - timedelta(minutes=5)),
					"Administrator",
					"Administrator",
					lead,
					"Dạ em chào anh",
					"Sale",
					"Sent",
					ts(created - timedelta(minutes=5)),
				)
			)
	cur = frappe.db._cursor
	ident_rows = [
		(
			f"{PREFIX}I{i:06d}",
			ts(lead_times[n][0]),
			ts(lead_times[n][0]),
			"Administrator",
			"Administrator",
			f"Zalo OA:{PREFIX}{i}",
			"Zalo OA",
			f"{PREFIX}{i}",
			n,
			ts(lead_times[n][0]),
		)
		for i, n in enumerate(names)
	]
	for i in range(0, len(ident_rows), CHUNK):
		cur.executemany(
			"""insert into `tabOmni Identity` (name, creation, modified, owner, modified_by, identity_key, channel_type,
				external_id, lead, last_inbound_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
			ident_rows[i : i + CHUNK],
		)
	for i in range(0, len(msg_rows), CHUNK):
		cur.executemany(
			"""insert into `tabOmni Inbound Message` (name, creation, modified, owner, modified_by, dedupe_key, channel_type,
				event_type, identity_key, lead, received_at, text, status) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
			msg_rows[i : i + CHUNK],
		)
	for i in range(0, len(out_rows), CHUNK):
		cur.executemany(
			"""insert into `tabOmni Outbound Message` (name, creation, modified, owner, modified_by, lead, text, source,
				status, sent_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
			out_rows[i : i + CHUNK],
		)
	frappe.db.commit()


def make_sales_user() -> str:
	"""Sales User tổng hợp, là người phụ trách 1/10 số Lead (để đo phạm vi một nhân viên)."""
	user = USERS[0]
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": PREFIX,
				"send_welcome_email": 0,
				"roles": [{"role": "Sales User"}],
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()
	return user


def purge() -> dict[str, int]:
	"""Xóa MỌI dữ liệu tổng hợp theo tiền tố; trả số dòng đã xóa."""
	frappe.db.rollback()
	out = {}
	for table, column, like in (
		("Omni Inbound Message", "name", f"{PREFIX}M-%"),
		("Omni Outbound Message", "name", f"{PREFIX}O-%"),
		("Omni Identity", "name", f"{PREFIX}I%"),
		("Omni Care Log", "lead", f"{PREFIX}-%"),
		("CRM Lead", "name", f"{PREFIX}-%"),
	):
		frappe.db.sql(f"delete from `tab{table}` where `{column}` like %s", like)
		out[table] = frappe.db.sql("select row_count()")[0][0]
	for user in USERS:
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True, delete_permanently=True)
			out["User"] = out.get("User", 0) + 1
	frappe.db.commit()
	return out


def _time(user: str, scope: str, period: str, repeats: int) -> dict:
	from omni_crm.dashboard import blocks

	frappe.set_user(user)
	runs = []
	for _ in range(repeats):
		t = time.perf_counter()
		out = blocks.run_blocks(blocks.make_query(period, scope, user))
		runs.append((time.perf_counter() - t) * 1000)
	frappe.set_user("Administrator")
	return {
		"median_ms": _pct(runs, 0.5),
		"p95_ms": _pct(runs, 0.95),
		"max_ms": round(max(runs), 1),
		"blocks": len(out),
		"waiting": out["tile_waiting"]["value"],
	}


def per_block(user: str, scope: str, period: str) -> dict[str, float]:
	"""Thời gian (ms) từng khối trong MỘT lần chạy lạnh — để biết khối nào chậm."""
	from omni_crm.dashboard import blocks

	frappe.set_user(user)
	q = blocks.make_query(period, scope, user)
	out = {}
	for key, b in blocks.REGISTRY.items():
		if b.enabled(q):
			t = time.perf_counter()
			b.run(q)
			out[key] = round((time.perf_counter() - t) * 1000, 1)
	frappe.set_user("Administrator")
	return out


def run(leads: int = 25000, messages: int = 100000, repeats: int = 10, keep: int = 0) -> dict:
	"""Nạp dữ liệu tổng hợp, đo, dọn. `keep=1` giữ dữ liệu để đo tay (nhớ chạy `purge`)."""
	from omni_crm import flags
	from omni_crm.dashboard import priority

	leads, messages, repeats = int(leads), int(messages), int(repeats)
	result: dict = {
		"leads": leads,
		"messages": messages,
		"repeats": repeats,
		"note": "omni.test, dữ liệu tổng hợp, KHÔNG phải staging",
	}
	frappe.set_user("Administrator")
	purge()
	try:
		t = time.perf_counter()
		seed(leads, messages)
		result["seed_seconds"] = round(time.perf_counter() - t, 1)
		all_on = {k: True for k in ("telephony", "analyze", "scoring", "bot", "draft")}
		manager = "Administrator"  # thấy tất cả
		with mock.patch.object(priority, "features", return_value=all_on):
			result["rows_in_site"] = {
				"inbound": frappe.db.count("Omni Inbound Message"),
				"leads": frappe.db.count("CRM Lead"),
			}
			result["per_block_team_30d_ms"] = per_block(manager, "team", "30d")
			for period in ("today", "7d", "30d"):
				result[f"team_{period}"] = _time(manager, "team", period, repeats)
			sale = make_sales_user()
			result["sales_today"] = _time(sale, "mine", "today", repeats)
			result["sales_30d"] = _time(sale, "mine", "30d", repeats)
		result["dashboard_enabled"] = flags.dashboard_enabled()
	finally:
		if not int(keep):
			result["purged"] = purge()
	print(json.dumps(result, ensure_ascii=False, indent=1))
	return result
