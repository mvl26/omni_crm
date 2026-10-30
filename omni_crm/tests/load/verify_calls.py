"""Chuẩn bị, đối chiếu và dọn dữ liệu tải cuộc gọi (chạy trong bench, KHÔNG thuộc run-tests).

	bench --site omni.test execute omni_crm.tests.load.verify_calls.setup --kwargs '{"state": "/tmp/state.json"}'
	bench --site omni.test execute omni_crm.tests.load.verify_calls.run --kwargs '{"channel": "LOAD tong dai", \
		"expected_calls": 1200, "expected_customers": 150, "wait_minutes": 10}'
	bench --site omni.test execute omni_crm.tests.load.verify_calls.cleanup --kwargs '{"channel": "LOAD tong dai", "state": "/tmp/state.json"}'

`setup`: lưu giá trị Omni Settings sẽ bị đổi vào tệp `state`, tạo kênh `LOAD tong dai` (Stringee, khóa GIẢ, sandbox
bật nên không có HTTP ra ngoài) và in token/secret ngẫu nhiên để đặt vào biến môi trường của send_calls.py.
`run`: chờ tin cuộc gọi xử lý xong, rồi tạo việc gọi lại bù cho cuộc nhỡ (sweep_pending) và in số liệu đối chiếu.
`cleanup`: xóa mọi dữ liệu LOAD và khôi phục Omni Settings.
"""

from __future__ import annotations

import json
import math
import secrets
import time

import frappe

INBOUND = "Omni Inbound Message"
CALL = "Omni Call"
TASK = "CRM Task"
OPEN = ("New", "Processing", "Retry")
PHONE_LIKE = "+84907%"
SETTINGS_KEYS = (
	"sandbox_mode",
	"callback_fallback_user",
	"bot_analyze_enabled",
	"bot_reply_mode",
	"lead_scoring_enabled",
	"daily_token_budget",
)


def _pct(vals: list[float], p: float) -> float | None:
	if not vals:
		return None
	vals = sorted(vals)
	return round(vals[max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))], 2)


def set_settings(**values) -> None:
	"""Đặt Omni Settings (đã commit)."""
	for k, v in values.items():
		frappe.db.set_single_value("Omni Settings", k, v)
	frappe.db.commit()
	frappe.clear_cache(doctype="Omni Settings")


def setup(state: str, channel: str = "LOAD tong dai") -> None:
	"""Lưu trạng thái Omni Settings, tạo kênh tổng đài tải và in thông tin truy cập (khóa giả)."""
	saved = {k: frappe.db.get_single_value("Omni Settings", k) for k in SETTINGS_KEYS}
	with open(state, "w", encoding="utf-8") as f:
		json.dump(saved, f)
	set_settings(
		sandbox_mode=1, callback_fallback_user="Administrator", bot_analyze_enabled=0, bot_reply_mode="Off"
	)
	token, sec = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
	frappe.db.delete("Omni Channel", {"channel_name": channel})
	frappe.get_doc(
		{
			"doctype": "Omni Channel",
			"channel_name": channel,
			"channel_type": "Telephony",
			"telephony_provider": "Stringee",
			"telephony_api_key_sid": "SKload",
			"telephony_api_key_secret": sec,
			"telephony_hotline": "02873009999",
			"webhook_token": token,
			"enabled": 1,
			"agent_map": [
				{"agent_ref": a, "user": "Administrator", "extension": str(100 + i)}
				for i, a in enumerate(("agent101", "agent102", "agent103"))
			],
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	print(json.dumps({"OMNI_LOAD_TEL_TOKEN": token, "OMNI_LOAD_TEL_SECRET": sec}))


def _counts(channel: str, like: str = "%") -> dict:
	return dict(
		frappe.db.sql(
			f"select status, count(*) from `tab{INBOUND}` where channel=%s and external_message_id like %s group by status",
			(channel, like),
		)
	)


def run(
	channel: str,
	expected_calls: int,
	expected_customers: int,
	wait_minutes: int = 10,
	sweep: int = 0,
	run_id: str = "",
) -> None:
	"""Chờ hàng đợi cạn, rồi in báo cáo JSON cho các cuộc gọi `LOADCALL-<run_id>-*` (rỗng = mọi lượt).

	Cuộc nhỡ chưa có việc được đếm TRƯỚC khi bù (`missed_without_task_before_sweep`); `sweep=1` mới bù bằng job thủ công."""
	from omni_crm.channels.telephony import callbacks

	t0 = time.time()
	like = f"LOADCALL-{run_id}-%" if run_id else "LOADCALL-%"
	counts = _counts(channel, like)
	while sum(counts.get(s, 0) for s in OPEN) and time.time() - t0 < int(wait_minutes) * 60:
		time.sleep(5)
		frappe.db.rollback()
		counts = _counts(channel, like)
	drain_s = round(time.time() - t0, 1)
	pending_filters = {
		"direction": "Inbound",
		"status": "Missed",
		"lead": ["is", "set"],
		"callback_task": ["is", "not set"],
		"call_id": ["like", like],
	}
	# Job việc gọi lại chạy SAU khi tin Done: đợi tối đa 90 giây cho chúng xong rồi mới đếm cuộc nhỡ còn thiếu việc
	t1 = time.time()
	pending_before_sweep = frappe.db.count(CALL, pending_filters)
	while pending_before_sweep and time.time() - t1 < 90:
		time.sleep(5)
		frappe.db.rollback()
		pending_before_sweep = frappe.db.count(CALL, pending_filters)
	callback_wait_s = round(time.time() - t1, 1)
	swept = 0
	if int(sweep):
		# sweep_pending bỏ qua cuộc nhỡ < 5 phút: gọi thẳng job cho từng cuộc còn thiếu việc
		for name in frappe.get_all(CALL, filters=pending_filters, pluck="name"):
			if callbacks.process_missed_call(name):
				swept += 1
		frappe.db.rollback()
	rows = frappe.db.sql(
		f"select external_message_id, lead from `tab{INBOUND}` where channel=%s and external_message_id like %s",
		(channel, like),
		as_dict=True,
	)
	call_ids = [r.external_message_id for r in rows]
	calls = frappe.db.sql(
		f"select name, call_id, status, direction, lead, crm_call_log, callback_task, unverified from `tab{CALL}` where call_id in %s",
		(tuple(call_ids) or ("",),),
		as_dict=True,
	)
	leads = {c.lead for c in calls if c.lead}
	by_number = frappe.db.sql(
		"select count(*) from (select omni_phone_e164 from `tabCRM Lead` where omni_phone_e164 like %s "
		"group by omni_phone_e164 having count(*)>1) t",
		PHONE_LIKE,
	)[0][0]
	missed = [c for c in calls if c.status == "Missed"]
	open_tasks_per_lead = frappe.db.sql(
		f"""select count(*) from (select c.lead from `tab{CALL}` c join `tab{TASK}` t on t.name = c.callback_task
		where c.call_id in %s and t.status in ('Backlog','Todo','In Progress') group by c.lead having count(distinct t.name) > 1) x""",
		(tuple(call_ids) or ("",),),
	)[0][0]
	task_names = {c.callback_task for c in calls if c.callback_task}
	logs = {c.crm_call_log for c in calls if c.crm_call_log}
	secs = [
		float(r[0])
		for r in frappe.db.sql(
			f"select timestampdiff(second, creation, modified) from `tab{INBOUND}` where channel=%s and status='Done' and external_message_id like %s",
			(channel, like),
		)
		if r[0] is not None
	]
	cust_with_missed = {c.lead for c in missed}
	print(
		json.dumps(
			{
				"status_counts": counts,
				"waited_for_drain_s": drain_s,
				"drained": not sum(counts.get(s, 0) for s in OPEN),
				"inbound_rows": len(rows),
				"expected_calls": expected_calls,
				"lost": max(0, expected_calls - len(rows)),
				"extra_rows": max(0, len(rows) - expected_calls),
				"omni_call_rows": len(calls),
				"omni_call_unique_ids": len({c.call_id for c in calls}),
				"crm_call_logs": len(logs),
				"calls_without_call_log": sum(1 for c in calls if not c.crm_call_log),
				"leads_distinct": len(leads),
				"expected_customers": expected_customers,
				"phones_with_more_than_one_lead": by_number,
				"missed_calls": len(missed),
				"callback_jobs_extra_wait_s": callback_wait_s,
				"missed_without_task_before_sweep": pending_before_sweep,
				"missed_bu_bang_sweep_thu_cong": swept,
				"missed_without_task_after": sum(1 for c in missed if not c.callback_task),
				"customers_with_missed": len(cust_with_missed),
				"callback_tasks_distinct": len(task_names),
				"leads_with_more_than_one_open_task": open_tasks_per_lead,
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


def cleanup(channel: str, state: str | None = None, call_prefix: str = "LOADCALL-") -> None:
	"""Xóa dữ liệu tải cuộc gọi (tin, Omni Call, Call Log, việc, định danh, Lead), kênh; khôi phục Omni Settings."""
	frappe.db.rollback()
	call_ids = frappe.db.sql_list(f"select name from `tab{CALL}` where call_id like %s", f"{call_prefix}%")
	leads = set(
		frappe.db.sql_list(
			f"select distinct lead from `tab{CALL}` where call_id like %s and lead is not null",
			f"{call_prefix}%",
		)
	)
	leads |= set(frappe.get_all("CRM Lead", filters={"omni_phone_e164": ["like", PHONE_LIKE]}, pluck="name"))
	tasks = frappe.db.sql_list(
		f"select callback_task from `tab{CALL}` where call_id like %s and callback_task is not null",
		f"{call_prefix}%",
	)
	logs = frappe.db.sql_list(
		f"select crm_call_log from `tab{CALL}` where call_id like %s and crm_call_log is not null",
		f"{call_prefix}%",
	)
	lead_list = sorted(leads)
	if tasks:
		frappe.db.delete("ToDo", {"reference_type": TASK, "reference_name": ["in", tasks]})
		frappe.db.delete("Notification Log", {"document_type": TASK, "document_name": ["in", tasks]})
	frappe.db.delete(CALL, {"name": ["in", call_ids or [""]]})
	frappe.db.delete(TASK, {"name": ["in", tasks or [""]]})
	frappe.db.delete("Dynamic Link", {"parenttype": "CRM Call Log", "parent": ["in", logs or [""]]})
	frappe.db.delete("CRM Call Log", {"name": ["in", logs or [""]]})
	frappe.db.delete(INBOUND, {"channel": channel})
	idents = frappe.get_all(
		"Omni Identity", filters={"identity_key": ["like", "Telephony:+84907%"]}, pluck="name"
	)
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	if lead_list:
		for f in ("lead_a", "lead_b"):
			frappe.db.delete("Omni Duplicate Candidate", {f: ["in", lead_list]})
		for dt in ("Comment", "Communication", "ToDo"):
			frappe.db.delete(dt, {"reference_name": ["in", lead_list]})
	frappe.db.commit()
	for i, lead in enumerate(lead_list):
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
		if i % 50 == 49:
			frappe.db.commit()
	frappe.db.delete("Omni Channel", {"channel_name": channel})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": channel})
	frappe.db.commit()
	if state:
		with open(state, encoding="utf-8") as f:
			set_settings(**{k: v for k, v in json.load(f).items() if v is not None})
	print(f"đã xóa {len(lead_list)} Lead, {len(call_ids)} cuộc gọi, {len(tasks)} việc của kênh {channel}")


def setup_zalo(channel: str = "LOAD kenh", state: str | None = None) -> None:
	"""Kênh Zalo OA tải (KHÔNG token, sandbox bật); in app_id/secret ngẫu nhiên cho send_burst.py. Lưu Settings nếu có `state`."""
	if state and not frappe.db.exists("Omni Channel", {"channel_name": ["like", "LOAD%"]}):
		with open(state, "w", encoding="utf-8") as f:
			json.dump({k: frappe.db.get_single_value("Omni Settings", k) for k in SETTINGS_KEYS}, f)
	set_settings(sandbox_mode=1)
	app_id, sec = "app-load-" + secrets.token_hex(4), secrets.token_urlsafe(32)
	frappe.db.delete("Omni Channel", {"channel_name": channel})
	frappe.get_doc(
		{
			"doctype": "Omni Channel",
			"channel_name": channel,
			"channel_type": "Zalo OA",
			"enabled": 1,
			"zalo_app_id": app_id,
			"zalo_oa_secret_key": sec,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	print(json.dumps({"OMNI_LOAD_APP_ID": app_id, "OMNI_LOAD_OA_SECRET": sec}))


def bot_report(channel: str = "LOAD kenh", lead_prefix: str = "LOADC") -> None:
	"""Báo cáo bot sau tải Zalo: Bot Run theo trạng thái, Lead có trường bot, nháp, tin ra (phải 0), Lead nóng."""
	leads = frappe.db.sql_list(
		f"select distinct lead from `tab{INBOUND}` where channel=%s and lead is not null", channel
	)
	batches = frappe.db.sql_list(
		f"select distinct batch_id from `tab{INBOUND}` where channel=%s and batch_id is not null", channel
	)
	runs = dict(
		frappe.db.sql(
			"select concat(kind, ':', status), count(*) from `tabOmni Bot Run` where batch_id in %s group by 1",
			(tuple(batches) or ("",),),
		)
	)
	filled = frappe.db.sql(
		"select count(*) from `tabCRM Lead` where name in %s and (omni_need is not null and omni_need != '')",
		(tuple(leads) or ("",),),
	)[0][0]
	scored = frappe.db.sql(
		"select count(*) from `tabCRM Lead` where name in %s and omni_score is not null and omni_score != 0",
		(tuple(leads) or ("",),),
	)[0][0]
	drafts = dict(
		frappe.db.sql(
			"select concat(mode, ':', action, ':', decision), count(*) from `tabOmni Bot Draft` where batch_id in %s group by 1",
			(tuple(batches) or ("",),),
		)
	)
	outbound = frappe.db.count("Omni Outbound Message", {"lead": ["in", leads or [""]]})
	statuses = _counts(channel)
	print(
		json.dumps(
			{
				"inbound_status": statuses,
				"bot_runs": runs,
				"leads": len(leads),
				"leads_with_omni_need": filled,
				"leads_with_score": scored,
				"bot_drafts": drafts,
				"outbound_rows_for_these_leads": outbound,
			},
			ensure_ascii=False,
			indent=1,
		)
	)


def cleanup_bot(channel: str = "LOAD kenh") -> None:
	"""Xóa Bot Run/Draft/Notification của kênh Zalo tải (gọi TRƯỚC verify_burst.cleanup)."""
	frappe.db.rollback()
	batches = frappe.db.sql_list(
		f"select distinct batch_id from `tab{INBOUND}` where channel=%s and batch_id is not null", channel
	)
	leads = frappe.db.sql_list(
		f"select distinct lead from `tab{INBOUND}` where channel=%s and lead is not null", channel
	)
	for dt in ("Omni Bot Run", "Omni Bot Draft"):
		frappe.db.delete(dt, {"batch_id": ["in", batches or [""]]})
	frappe.db.delete("Notification Log", {"document_name": ["in", leads or [""]]})
	frappe.db.commit()
	print(f"đã xóa dữ liệu bot của {len(batches)} lô")


SCORE_RULES = [
	("has_phone", 15, "Có số điện thoại hợp lệ"),
	("intent_price", 15, "Hỏi giá"),
	("intent_purchase", 25, "Muốn đặt hàng"),
	("quantity_given", 15, "Nêu số lượng"),
	("repeat_contact", 5, "Liên hệ lại"),
]


def bot_on(reply_mode: str = "Shadow", budget: int = 100_000_000, scoring: int = 1) -> None:
	"""Bật bot phân tích + chấm điểm + chế độ trả lời cho đo tải (FakeAIClient ở worker; hạn mức lớn)."""
	frappe.db.rollback()
	doc = frappe.get_doc("Omni Settings")
	doc.update(
		{
			"bot_analyze_enabled": 1,
			"bot_reply_mode": reply_mode,
			"daily_token_budget": int(budget),
			"lead_scoring_enabled": int(scoring),
			"hot_lead_threshold": 60,
			"assignment_fallback_users": "Administrator",
			"score_rules": [{"signal": s, "points": p, "note": n} for s, p, n in SCORE_RULES],
		}
	)
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	frappe.cache().delete_keys("omni:ai:")
	print("bot bật:", reply_mode, "budget", budget)


def bot_off() -> None:
	"""Tắt bot và chấm điểm, xóa bảng điểm tải."""
	doc = frappe.get_doc("Omni Settings")
	doc.update(
		{"bot_analyze_enabled": 0, "bot_reply_mode": "Off", "lead_scoring_enabled": 0, "score_rules": []}
	)
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()


def alerts_now() -> None:
	"""In khối bot của snapshot giám sát và các cảnh báo đang bật (chỉ đọc, không gửi thư)."""
	from omni_crm.monitoring import alerts
	from omni_crm.monitoring.snapshot import build_snapshot

	snap = build_snapshot()
	print(
		json.dumps(
			{"bot": snap["bot"], "alerts": [a.kind for a in alerts.evaluate(snap)]},
			ensure_ascii=False,
			indent=1,
		)
	)
