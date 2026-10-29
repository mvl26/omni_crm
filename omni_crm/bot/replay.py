"""Chạy lại dữ liệu cũ ở chế độ ngầm (docs/08 mục 5.6).

Với mỗi lượt khách mà sau đó sale có trả lời, dựng lại ngữ cảnh CHỈ gồm tin trước câu trả lời của sale, cho bot
soạn nháp Shadow rồi so với câu sale. Chỉ đọc dữ liệu cũ: không sửa Lead/định danh, không gửi gì cho khách.
Chạy theo lô 20, tôn trọng hạn mức token, dừng/tiếp tục được nhờ con trỏ lưu trong cache.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

from omni_crm.bot import reply
from omni_crm.crm_bridge import timeline
from omni_crm.pipeline.retry import redact

BATCH_SIZE = 20
CURSOR_TTL = 7 * 24 * 3600
STOP_KEY = "omni:replay:stop"
STOP_REASONS = {"Skipped Budget": "budget", "Skipped Switch": "switch"}


def _cursor_key(since: Any, until: Any) -> str:
	return f"omni:replay:cursor:{get_datetime(since).isoformat()}:{get_datetime(until).isoformat()}"


def request_stop() -> None:
	"""Yêu cầu replay đang chạy dừng sau lượt hiện tại (gọi `replay` lại để tiếp tục từ con trỏ)."""
	frappe.cache().set_value(STOP_KEY, 1, expires_in_sec=3600)


def _sale_leads(since: Any, until: Any) -> list[str]:
	"""Các Lead có tin sale trả lời trong khoảng thời gian (Outbound Sale, oa_echo, email đi)."""
	leads: set[str] = set()
	leads.update(
		frappe.get_all(
			"Omni Outbound Message",
			filters={"source": "Sale", "status": "Sent", "sent_at": ["between", [since, until]]},
			pluck="lead",
		)
	)
	leads.update(
		frappe.get_all(
			"Omni Inbound Message",
			filters={"event_type": "oa_echo", "received_at": ["between", [since, until]]},
			pluck="lead",
		)
	)
	leads.update(
		frappe.get_all(
			"Communication",
			filters={
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"reference_doctype": "CRM Lead",
				"communication_date": ["between", [since, until]],
			},
			pluck="reference_name",
		)
	)
	return sorted(x for x in leads if x)


def candidates_for_lead(lead: str, since: Any, until: Any) -> list[dict]:
	"""Các lượt (khách hỏi -> sale trả lời đầu tiên) của một Lead trong khoảng; bỏ lượt trống nội dung."""
	lo, hi = get_datetime(since), get_datetime(until)
	turns = timeline.conversation_turns(lead)
	found: list[dict] = []
	for i, turn in enumerate(turns):
		at = get_datetime(turn["at"])
		is_sale = turn["direction"] == "out" and (
			turn["actor"] in ("sale", "external") or turn["kind"] == "email_out"
		)
		if not is_sale or not (turn.get("text") or "").strip() or not lo <= at <= hi:
			continue
		streak: list[dict] = []
		j = i - 1
		while j >= 0 and turns[j]["direction"] == "in" and turns[j]["actor"] == "customer":
			if (turns[j].get("text") or "").strip():
				streak.insert(0, turns[j])
			j -= 1
		if not streak:
			continue
		found.append(
			{
				"lead": lead,
				"sale_ref": turn["ref"],
				"sale_at": at,
				"sale_text": turn["text"],
				"customer_ref": streak[-1]["ref"],
				"customer_at": get_datetime(streak[-1]["at"]),
				"customer_text": "\n".join(t["text"] for t in streak),
				"until": at - timedelta(microseconds=1),
			}
		)
	return found


def collect(since: Any, until: Any, limit: int | None = None) -> list[dict]:
	"""Mọi lượt cần chạy lại trong khoảng, theo thời điểm sale trả lời tăng dần; `limit` lấy N lượt gần nhất."""
	rows = [c for lead in _sale_leads(since, until) for c in candidates_for_lead(lead, since, until)]
	rows.sort(key=lambda c: (c["sale_at"], c["sale_ref"]))
	return rows[-limit:] if limit else rows


def _stop_reason(batch_id: str) -> str | None:
	"""Lý do phải dừng cả replay sau một lượt không có kết quả (hết hạn mức, công tắc/Q11 chặn); None = lỗi riêng lượt."""
	status = frappe.db.get_value(
		"Omni Bot Run", {"batch_id": batch_id, "kind": "Reply"}, "status", order_by="creation desc"
	)
	return STOP_REASONS.get(status) if status else "kb"


def _run_one(item: dict) -> tuple[str, str | None]:
	"""Chạy một lượt: ("drafted" | "skipped" | "failed" | "stop", lý do dừng)."""
	batch_id = f"{reply.REPLAY_PREFIX}{item['sale_ref']}"
	if frappe.db.exists(reply.DRAFT, {"batch_id": batch_id}):
		return "skipped", None
	msg = frappe.db.get_value(
		"Omni Inbound Message", item["customer_ref"], ["identity", "channel", "channel_type"], as_dict=True
	)
	if not msg or not msg.identity or not msg.channel:
		return "skipped", None
	name = reply.make_draft(
		lead=item["lead"],
		identity=msg.identity,
		channel=msg.channel,
		channel_type=msg.channel_type,
		batch_id=batch_id,
		customer_text=item["customer_text"],
		until=item["until"],
		at=item["customer_at"],  # giờ làm việc tính theo lúc khách nhắn, không phải lúc sale trả lời
		include_summary=False,  # tóm tắt Lead tính tới nay có thể chứa điều khách nói SAU câu trả lời của sale
		check_channel=False,
	)
	if not name:
		reason = _stop_reason(batch_id)
		return ("stop", reason) if reason else ("failed", None)
	reply.record_comparison(name, item["sale_text"])
	frappe.db.commit()
	return "drafted", None


def replay(since: Any, until: Any, limit: int | None = None) -> dict[str, Any]:
	"""Chạy lại các lượt có sale trả lời trong [since, until]; trả tóm tắt {total, drafted, skipped, failed, stopped}.

	Lô 20 lượt; dừng khi hết hạn mức token / công tắc tắt / có yêu cầu dừng, con trỏ giữ chỗ để gọi lại tiếp tục.
	"""
	cache = frappe.cache()
	cache.delete_value(STOP_KEY)
	cursor_key = _cursor_key(since, until)
	cursor = cache.get_value(cursor_key, expires=True)
	items = collect(since, until, limit)
	result: dict[str, Any] = {"total": len(items), "drafted": 0, "skipped": 0, "failed": 0, "stopped": None}
	ran = 0
	for item in items:
		mark = f"{item['sale_at'].strftime('%Y-%m-%d %H:%M:%S.%f')}|{item['sale_ref']}"
		if cursor and mark <= cursor:
			result["skipped"] += 1
			continue
		if ran % BATCH_SIZE == 0 and cache.get_value(STOP_KEY, expires=True):
			result["stopped"] = "requested"
			break
		try:
			state, why = _run_one(item)
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				title="Omni: lỗi replay bot",
				message=redact(f"sale_ref={item['sale_ref']}\nloai_loi={type(e).__name__}"),
			)
			state, why = "failed", None
		if state == "stop":
			result["stopped"] = why
			break
		result["drafted" if state == "drafted" else state] += 1
		ran += 1
		cache.set_value(cursor_key, mark, expires_in_sec=CURSOR_TTL)
	else:
		cache.delete_value(
			cursor_key
		)  # chạy hết: lần sau chạy lại từ đầu (nháp đã có được bỏ qua theo batch_id)
	result["finished_at"] = str(now_datetime())
	return result
