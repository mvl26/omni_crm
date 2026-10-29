"""Worker xử lý tin theo định danh: gộp tin, khóa, ghi vào CRM."""

from __future__ import annotations

import time
import uuid
from typing import Any

import frappe
from frappe.utils import now_datetime
from redis.exceptions import LockError
from redis.lock import Lock

from omni_crm import flags
from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.identity import resolver
from omni_crm.ingest.gateway import enqueue_identity
from omni_crm.pipeline.locks import LockBusy, identity_lock
from omni_crm.pipeline.retry import is_transient, mark_retry, redact

INBOUND = "Omni Inbound Message"
# Sự kiện không phải khách chủ động liên hệ: không tính vào last_inbound_at (khung gửi tin của Zalo).
NON_INBOUND_EVENTS = {"oa_echo", "unfollow"}
MAX_ROUNDS = 5
BATCH_LIMIT = 200
LOCK_TIMEOUT = 120
LOCK_WAIT = 30
FIELDS = [
	"name",
	"channel",
	"channel_type",
	"event_type",
	"external_sender_id",
	"external_message_id",
	"identity_key",
	"communication",
	"received_at",
	"text",
	"sender_profile",
	"attempts",
	"status",
]


def process_identity(
	identity_key: str, debounce_seconds: int | None = None, lock_wait: int = LOCK_WAIT
) -> str:
	"""Xử lý mọi tin New/Retry đến hạn của một khách; trả "done", "busy" hoặc "idle"."""
	if debounce_seconds is None:
		debounce_seconds = frappe.db.get_single_value("Omni Settings", "debounce_seconds") or 0
	if debounce_seconds:
		time.sleep(debounce_seconds)  # gom tin khách gõ liên tiếp
	try:
		with identity_lock(identity_key, timeout=LOCK_TIMEOUT, wait=lock_wait) as lock:
			processed = _run_rounds(identity_key, lock)
	except LockBusy:
		# Hẹn lại: tin giữ nguyên New, lượt đang giữ khóa kiểm tra lại trước khi thoát và tác vụ
		# quét (New > 2 phút) là chốt chặn (ADR 0001: Frappe v15 không có enqueue trì hoãn).
		return "busy"
	# Đã nhả khóa: nếu còn tin chưa xử lý (hết 5 vòng, hoặc tin đến lúc job đang STARTED nên bị
	# deduplicate) thì đẩy tiếp bằng job_id khác.
	if _has_pending(identity_key):
		enqueue_identity(identity_key, after_commit=False, job_suffix=uuid.uuid4().hex[:8])
	return "done" if processed else "idle"


def _pending_filters(identity_key: str) -> list[dict]:
	now = now_datetime()
	return [
		{"identity_key": identity_key, "status": "New"},
		{"identity_key": identity_key, "status": "Retry", "next_attempt_at": ["<=", now]},
	]


def _fetch_pending(identity_key: str, limit: int = BATCH_LIMIT) -> list[Any]:
	rows: list[Any] = []
	for filters in _pending_filters(identity_key):
		rows += frappe.get_all(INBOUND, filters=filters, fields=FIELDS, limit_page_length=limit)
	rows = [r for r in rows if flags.channel_enabled(r.channel)]  # kênh tắt giữa chừng: giữ nguyên
	rows.sort(key=lambda r: (r.received_at or now_datetime(), r.name))
	return rows[:limit]


def _has_pending(identity_key: str) -> bool:
	frappe.db.rollback()  # kết thúc transaction cũ để thấy dữ liệu mới nhất (REPEATABLE READ)
	return bool(_fetch_pending(identity_key, limit=1))


def _run_rounds(identity_key: str, lock: Lock) -> int:
	processed = 0
	for _round in range(MAX_ROUNDS):
		try:
			lock.extend(LOCK_TIMEOUT, replace_ttl=True)  # mỗi vòng có đủ thời hạn khóa
		except LockError:
			break  # khóa đã mất: dừng, tin còn lại do job tiếp nối xử lý
		frappe.db.rollback()  # snapshot mới cho mỗi vòng
		msgs = _fetch_pending(identity_key)
		if not msgs:
			break
		batch_id = uuid.uuid4().hex
		names = [m.name for m in msgs]
		frappe.db.set_value(
			INBOUND,
			names,
			{"status": "Processing", "processing_started_at": now_datetime(), "batch_id": batch_id},
			update_modified=False,
		)
		frappe.db.commit()
		try:
			lead = _process_batch(identity_key, msgs, batch_id)
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			permanent = not is_transient(e)
			mark_retry(msgs, e, permanent_hint=permanent)
			frappe.db.commit()
			if permanent:
				# Chỉ ghi loại lỗi và batch_id, không ghi nội dung (có thể chứa dữ liệu khách).
				frappe.log_error(
					title="Omni: lỗi xử lý lô tin",
					message=redact(
						f"batch_id={batch_id}\nloai_loi={type(e).__name__}\n"
						f"{frappe.get_traceback(with_context=False)[-1500:]}"
					),
				)
				frappe.db.commit()
			break  # dừng vòng; tin mới hơn (nếu có) do job tiếp nối xử lý
		processed += len(msgs)
		_post_batch(batch_id, lead)
	return processed


def _process_batch(identity_key: str, msgs: list[Any], batch_id: str) -> str | None:
	"""Khớp định danh, ghi dòng thời gian, đánh dấu Done. Không commit; trả tên Lead (hoặc None)."""
	with flags.omni_bot():  # code hệ thống ghi Lead/Communication: không tính là sale sửa tay
		to_resolve = _adapter_step("prepare_batch", msgs)  # kênh có thể xử lý trước/loại bớt tin (vd follow)
		if to_resolve:
			res = resolver.resolve_full(to_resolve)
			identity, lead = res.identity, res.lead
			if lead:
				lead_bridge.ensure_inbox_link(lead)  # sau commit của resolver, trước khi ghi Communication
			_adapter_step("after_resolve", to_resolve, res=res)
			# Q10: Lead đã chuyển thành Deal thì tin mới ghi vào Deal.
			timeline.append(res.deal or lead, to_resolve)
		else:  # mọi tin đã được adapter xử lý (vd chỉ có follow/unfollow): không tạo Lead
			row = frappe.db.get_value(
				"Omni Identity", {"identity_key": identity_key}, ["name", "lead"], as_dict=True
			)
			identity, lead = (row.name, row.lead) if row else (None, None)
	timeline.notify(lead, [m.channel_type for m in msgs])
	inbound_times = [m.received_at for m in msgs if m.received_at and m.event_type not in NON_INBOUND_EVENTS]
	last_inbound = max(inbound_times, default=None)
	frappe.db.set_value(
		INBOUND,
		[m.name for m in msgs],
		{
			"status": "Done",
			"identity": identity,
			"lead": lead,
			"last_error": None,
			"next_attempt_at": None,
			"processing_started_at": None,
		},
		# Cố ý đổi `modified`: đây là thời điểm Done, màn hình giám sát lấy p50/p95 = modified - creation (D1-10).
		update_modified=True,
	)
	if identity and last_inbound:
		frappe.db.set_value("Omni Identity", identity, "last_inbound_at", last_inbound, update_modified=False)
	return lead


def _adapter_step(hook: str, msgs: list[Any], res: Any = None) -> list[Any]:
	"""Gọi hook tùy chọn `prepare_batch`/`after_resolve` của adapter theo từng kênh; adapter không có hook thì giữ nguyên.

	Trả danh sách tin còn lại sau `prepare_batch` (giữ thứ tự gốc); `after_resolve` trả danh sách đầu vào.
	"""
	by_channel: dict[str, list[Any]] = {}
	for m in msgs:
		by_channel.setdefault(m.channel, []).append(m)
	kept: set[str] = set()
	for channel, group in by_channel.items():
		try:
			fn = getattr(get_adapter(group[0].channel_type), hook, None)
		except AdapterNotFoundError:
			fn = None
		if fn is None:
			kept.update(m.name for m in group)
			continue
		channel_doc = frappe.get_cached_doc("Omni Channel", channel)
		if hook == "prepare_batch":
			kept.update(m.name for m in fn(group, channel_doc))
		else:
			fn(res, group, channel_doc)
			kept.update(m.name for m in group)
	return [m for m in msgs if m.name in kept]


def _post_batch(batch_id: str, lead: str | None) -> None:
	"""Gọi hậu xử lý; lỗi ở đây không được làm hỏng lô đã Done."""
	try:
		post_batch_hooks(batch_id, lead)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: lỗi hậu xử lý lô tin", message=f"batch_id={batch_id}")
		frappe.db.commit()


def post_batch_hooks(batch_id: str, lead: str | None) -> None:
	"""Điểm nối Đợt 2: việc gọi lại cho cuộc gọi nhỡ (D2-03), bot phân tích (D2-05), chấm điểm (D2-06); reply_batch ở prompt sau."""
	from omni_crm.bot import analyze, scoring
	from omni_crm.channels.telephony import callbacks

	try:
		callbacks.after_batch(batch_id)
	finally:  # lỗi ở việc gọi lại không được làm mất lượt phân tích, và ngược lại enqueue_analyze tự bắt lỗi
		if not analyze.enqueue_analyze(batch_id, lead):
			scoring.on_batch_done(batch_id, lead)  # không qua bot (cuộc gọi, bot tắt): chấm điểm ngay (D2-06)
