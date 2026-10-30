"""Xếp hàng và gửi tin ra qua adapter của kênh (docs/03 mục 8).

Nguyên tắc chống gửi trùng (tin gửi ra khách là việc không hoàn tác được):
- `job_id = omni-out::<tên>` + deduplicate; ngoài ra mỗi lần gửi phải "nhận việc" (claim) dưới khóa dòng:
  chỉ tin `Queued`, chưa có người đang gửi và chưa đến lượt thử lại thì mới được nhận. Nhận việc đặt một
  hạn thuê (lease, `next_attempt_at`) và dấu `SENDING_MARK` rồi commit TRƯỚC khi gọi API ngoài (không giữ khóa DB).
- Kết quả được ghi và commit ngay sau lời gọi (kể cả `external_message_id`, để echo của Zalo nhận ra tin của ta).
- Nếu tiến trình chết giữa lời gọi (hạn thuê quá hạn mà dấu còn): KHÔNG gửi lại, chuyển `Failed` và nhờ người
  kiểm tra (không biết khách đã nhận chưa). Tin tách nhiều đoạn đã gửi được một phần cũng chuyển `Failed`.
- Chỉ thử lại khi chắc tin CHƯA tới khách (kênh trả lỗi quá hạn mức/5xx, hoặc chưa kết nối được); lỗi mạng mơ hồ
  (đọc phản hồi quá hạn...) chuyển `Failed`, không gửi lại.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from omni_crm import flags
from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.ingest.events import OutboundRequest
from omni_crm.pipeline.retry import is_transient, mark_retry, redact

OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
CHANNEL = "Omni Channel"
QUEUE = "omni"
WORKER_METHOD = "omni_crm.pipeline.outbound.send_outbound"
SENDING_MARK = "[đang gửi]"  # last_error khi đã nhận việc nhưng chưa có kết quả
LEASE_MINUTES = 5
PAID_OK_TTL = 900  # giây: hiệu lực của xác nhận khung gửi (không thêm trường DocType, xem docs/12)
MAX_TEXT_CHARS = 6000  # tối đa cho một lần trả lời (client tách tin theo giới hạn từng đoạn của kênh)
LAST_ERROR_MAX = 500
_AMBIGUOUS_MSG = (
	"Không rõ tin đã tới khách chưa (lỗi mạng khi gửi). Hãy kiểm tra trên Zalo rồi gửi lại nếu cần."
)
KIND_TRANSIENT, KIND_WINDOW = (
	"transient",
	"window",
)  # khớp channels/zalo/errors.py (duck-typing, không import kênh)


def job_id_for(name: str) -> str:
	"""job_id chống enqueue trùng cho một tin ra."""
	return f"omni-out::{name}"


def enqueue_outbound(name: str, *, after_commit: bool = True) -> None:
	"""Đẩy job send_outbound; lỗi enqueue chỉ ghi log (tin vẫn Queued, tác vụ quét đẩy lại).

	after_commit=True dùng trong request (hoãn tới sau commit); tác vụ nền gọi với False.
	"""

	def _enqueue() -> None:
		try:
			frappe.enqueue(
				WORKER_METHOD, queue=QUEUE, job_id=job_id_for(name), deduplicate=True, outbound_name=name
			)
		except Exception:
			frappe.log_error(
				title="Omni: enqueue tin ra thất bại",
				message="Không đẩy được job gửi tin ra; tin giữ trạng thái Queued, tác vụ quét sẽ đẩy lại.",
			)
			if after_commit:
				frappe.db.commit()

	if after_commit:
		frappe.db.after_commit.add(_enqueue)
	else:
		_enqueue()


def paid_ok_key(name: str) -> str:
	"""Khóa cache cờ "khung gửi đã được xác nhận" của một tin ra."""
	return f"omni:paid_ok:{name}"


def _window_confirmation(req: OutboundRequest, confirmed_paid: bool) -> str | None:
	""" "paid" nếu khung có phí lúc xếp hàng đã được Sale xác nhận; "free" nếu lúc đó miễn phí; None nếu không có."""
	if req.source != "Sale":
		return None
	try:
		channel_doc = frappe.get_doc(CHANNEL, req.channel)
		ok, reason = get_adapter(channel_doc.channel_type).can_send_now(
			frappe.get_doc(IDENTITY, req.identity), channel_doc, source="Sale"
		)
	except Exception:
		return None
	if not ok:
		return None
	if not reason:
		return "free"
	return "paid" if confirmed_paid else None


def _check_bot_draft(req: OutboundRequest) -> None:
	"""Kiểm nguồn Bot và liên kết nháp.

	- `source="Bot"`: chỉ đi qua `bot.auto_send` (D3-05): cần nháp hợp lệ và `auto_send.assert_can_queue` kiểm lại MỌI điều
	  kiện (khóa hạ tầng, nhóm bật, nháp Draft/Reply/Pending, khung miễn phí, giờ bot...). Mọi tin Bot khác bị từ chối.
	- Tin có `bot_draft` mà không phải Bot: chỉ do Sale gửi, từ nháp Draft còn Pending, action Reply, đúng khách (Handoff
	  không gửi được).
	"""
	if req.source == "Bot":
		from omni_crm.bot import auto_send  # import trễ: auto_send import module này

		auto_send.assert_can_queue(req)
		return
	if not req.bot_draft:
		return
	row = frappe.db.get_value(
		"Omni Bot Draft", req.bot_draft, ["mode", "action", "decision", "identity"], as_dict=True
	)
	if (
		req.source != "Sale"
		or not row
		or (row.mode, row.action, row.decision) != ("Draft", "Reply", "Pending")
		or row.identity != req.identity
	):
		frappe.throw(_("Không gửi được nháp này"), frappe.ValidationError)


def queue_outbound(req: OutboundRequest, user: str | None = None, confirmed_paid: bool = False) -> str:
	"""Tạo Omni Outbound Message (Queued) và đẩy job gửi; trả tên bản ghi.

	Đặt cờ cache `omni:paid_ok:<tên>` (15 phút): "paid" nếu khung có phí đã được Sale xác nhận, "free" nếu lúc đó
	miễn phí. send_outbound chỉ cho tin Sale đi khi khung có phí nếu cờ là "paid" (free chuyển sang paid giữa chừng bị chặn).
	"""
	text = (req.text or "").strip()
	if not text:
		frappe.throw(_("Nội dung tin nhắn đang trống"))
	if len(text) > MAX_TEXT_CHARS:
		frappe.throw(_("Tin nhắn dài quá {0} ký tự").format(MAX_TEXT_CHARS))
	_check_bot_draft(req)
	lead = frappe.db.get_value(IDENTITY, req.identity, "lead")
	doc = frappe.get_doc(
		{
			"doctype": OUTBOUND,
			"channel": req.channel,
			"identity": req.identity,
			"lead": lead,
			"text": text,
			"source": req.source,
			"bot_draft": req.bot_draft,
			"sent_by": user if req.source == "Sale" else None,
			"status": "Queued",
			"attempts": 0,
			"is_sandbox": 1 if flags.sandbox() else 0,
		}
	)
	doc.insert(ignore_permissions=True)
	confirmation = _window_confirmation(req, confirmed_paid)
	if confirmation:
		frappe.cache().set_value(paid_ok_key(doc.name), confirmation, expires_in_sec=PAID_OK_TTL)
	enqueue_outbound(doc.name)
	return doc.name


# --- trạng thái ---------------------------------------------------------------------------------------


def _row(name: str, lock: bool = False) -> Any:
	return frappe.db.get_value(
		OUTBOUND,
		name,
		[
			"name",
			"channel",
			"identity",
			"lead",
			"text",
			"source",
			"sent_by",
			"status",
			"attempts",
			"next_attempt_at",
			"last_error",
			"is_sandbox",
			"bot_draft",
		],
		as_dict=True,
		for_update=lock,
	)


def _notify(row: Any) -> None:
	"""Báo realtime cho Hộp thư (không kèm nội dung) và cho người gửi."""
	channel_type = frappe.db.get_value(CHANNEL, row.channel, "channel_type")
	timeline.notify(row.lead, [channel_type] if channel_type else [])
	if row.sent_by:
		frappe.publish_realtime(
			timeline.REALTIME_EVENT,
			{"lead": row.lead, "channels": [channel_type] if channel_type else []},
			user=row.sent_by,
			after_commit=True,
		)


def _set(row: Any, **values: Any) -> None:
	values.setdefault("next_attempt_at", None)
	if "last_error" in values:
		values["last_error"] = (values["last_error"] or "")[:LAST_ERROR_MAX]
	frappe.db.set_value(OUTBOUND, row.name, values, update_modified=False)


def _block(row: Any, reason: str) -> str:
	_set(row, status="Blocked", last_error=reason)
	frappe.db.commit()
	_notify(row)
	return "Blocked"


def _fail(row: Any, reason: str, **extra: Any) -> str:
	_set(row, status="Failed", last_error=redact(reason), **extra)
	frappe.db.commit()
	_notify(row)
	return "Failed"


def drop_echo_duplicate(row: Any, external_id: str) -> None:
	"""(row cần `channel`, `name`.) Echo (oa_echo) tới trước khi ta ghi mã tin có thể đã tạo một Outbound `Sale` trùng mã: bỏ bản trùng."""
	dups = frappe.get_all(
		OUTBOUND,
		filters={
			"channel": row.channel,
			"external_message_id": external_id,
			"name": ["!=", row.name],
			"sent_by": ["is", "not set"],
			"source": "Sale",
		},
		pluck="name",
	)
	for dup in dups:
		frappe.db.delete("Communication", {"omni_outbound_message": dup})
		frappe.delete_doc(OUTBOUND, dup, force=True, ignore_permissions=True)


def _sent(row: Any, external_id: str | None, sandbox: bool) -> str:
	"""Ghi Sent + mã tin + dòng thời gian trong MỘT commit (mã tin phải có ngay để nhận ra echo)."""
	_set(
		row,
		status="Sent",
		external_message_id=external_id,
		sent_at=now_datetime(),
		is_sandbox=1 if sandbox else 0,
		last_error=None,
	)
	frappe.db.commit()  # trạng thái Sent + mã tin phải bền trước các bước phụ bên dưới
	if external_id and not sandbox:
		try:
			drop_echo_duplicate(row, external_id)
		except Exception:
			frappe.db.rollback()
			frappe.logger("omni").warning(
				"omni: dọn echo trùng của %s lỗi: %s", row.name, redact(frappe.get_traceback())[:300]
			)
	try:
		timeline.append_outbound(lead_bridge.get_deal_for_lead(row.lead) or row.lead, row.name)
	except Exception:
		# Tin đã tới khách: lỗi dòng thời gian không được làm mất trạng thái Sent hay gửi lại.
		frappe.log_error(
			title="Omni: ghi dòng thời gian tin ra thất bại", message=redact(frappe.get_traceback())
		)
	frappe.db.commit()
	_notify(row)
	return "Sent"


def _retry_or_fail(row: Any, exc: BaseException, message: str) -> str:
	"""Lỗi tạm thời: mark_retry; còn lượt -> Queued + next_attempt_at, hết lượt -> Failed."""
	mark_retry([{"doctype": OUTBOUND, "name": row.name}], message)
	status = frappe.db.get_value(OUTBOUND, row.name, "status")
	if status == "Manual":  # mark_retry dùng chung với tin vào: tin ra hết lượt = Failed
		frappe.db.set_value(OUTBOUND, row.name, "status", "Failed", update_modified=False)
		result = "Failed"
	else:
		frappe.db.set_value(OUTBOUND, row.name, "status", "Queued", update_modified=False)
		result = "Queued"
	frappe.db.commit()
	_notify(row)
	return result


# --- gửi ----------------------------------------------------------------------------------------------


def _claim(name: str) -> Any | None:
	"""Nhận việc dưới khóa dòng; trả dòng đã nhận hoặc None nếu không được gửi lúc này."""
	row = _row(name, lock=True)
	now = now_datetime()
	if not row or row.status != "Queued":
		frappe.db.rollback()
		return None
	if (
		row.next_attempt_at and row.next_attempt_at > now
	):  # đang có người gửi (lease) hoặc chưa đến hạn thử lại
		frappe.db.rollback()
		return None
	if row.last_error == SENDING_MARK:  # lần gửi trước chết giữa chừng, không biết khách đã nhận chưa
		_fail(
			row,
			_(
				"Không rõ tin đã tới khách chưa (tiến trình gửi bị gián đoạn). Hãy kiểm tra trên Zalo rồi gửi lại nếu cần."
			),
		)
		return None
	_set(row, next_attempt_at=add_to_date(now, minutes=LEASE_MINUTES), last_error=SENDING_MARK)
	frappe.db.commit()
	return row


def send_outbound(outbound_name: str) -> str:
	"""Gửi một tin ra (docs/03 mục 8); trả trạng thái cuối. Mỗi tin chỉ được gửi tối đa một lần thành công."""
	row = _row(outbound_name)
	if not row or row.status != "Queued":
		return row.status if row else "Missing"
	if (
		row.source == "Bot"
	):  # tin bot: kiểm lại công tắc NGAY trước khi gửi (nhóm có thể vừa bị tắt/ngắt mạch)
		from omni_crm.bot import auto_send

		allowed_now, why_not = auto_send.send_time_check(row)
		if not allowed_now:
			return _block_locked(outbound_name, why_not)
	channel_doc = frappe.get_doc(CHANNEL, row.channel) if frappe.db.exists(CHANNEL, row.channel) else None
	if not channel_doc or not cint(channel_doc.outbound_enabled):
		return _block_locked(outbound_name, _("Kênh đã tắt chiều ra"))
	identity = frappe.db.get_value(IDENTITY, row.identity, "name") if row.identity else None
	if not identity:
		return _fail_locked(outbound_name, _("Không tìm thấy người nhận (định danh)"))
	try:
		adapter = get_adapter(channel_doc.channel_type)
	except AdapterNotFoundError:
		return _fail_locked(outbound_name, _("Chưa có adapter cho kênh {0}").format(channel_doc.channel_type))
	identity_doc = frappe.get_doc(IDENTITY, identity)
	allowed, reason = adapter.can_send_now(identity_doc, channel_doc, source=row.source)
	if not allowed:
		return _block_locked(outbound_name, reason)
	if reason and row.source == "Sale" and frappe.cache().get_value(paid_ok_key(outbound_name)) != "paid":
		return _block_locked(
			outbound_name,
			_("Khung gửi đã chuyển sang có phí trong lúc chờ; hãy gửi lại và xác nhận phí."),
		)
	if flags.sandbox():
		return _sandbox_sent(outbound_name)
	claimed = _claim(outbound_name)
	if claimed is None:
		return frappe.db.get_value(OUTBOUND, outbound_name, "status") or "Missing"
	req = OutboundRequest(
		channel=claimed.channel,
		identity=claimed.identity,
		text=claimed.text,
		reply_to_external_id=_reply_target(claimed),
		source=claimed.source,
	)
	try:
		external_id = adapter.send(req, channel_doc)  # ngoài khóa DB; sandbox đã chặn ở trên
	except Exception as e:
		return _after_error(claimed, e)
	return _sent(claimed, external_id, sandbox=False)


def _reply_target(row: Any) -> str | None:
	"""Mã tin khách gần nhất của định danh (để email nối chuỗi); kênh khác bỏ qua."""
	return frappe.db.get_value(
		"Omni Inbound Message",
		{"identity": row.identity, "event_type": "message"},
		"external_message_id",
		order_by="received_at desc",
	)


def _lock_queued(name: str) -> Any | None:
	row = _row(name, lock=True)
	if not row or row.status != "Queued":
		frappe.db.rollback()
		return None
	return row


def _leased(row: Any) -> bool:
	"""Job khác đang gửi tin này (dấu "đang gửi" và hạn thuê còn hiệu lực)."""
	return (
		row.last_error == SENDING_MARK and bool(row.next_attempt_at) and row.next_attempt_at > now_datetime()
	)


def _block_locked(name: str, reason: str) -> str:
	row = _lock_queued(name)
	if row and _leased(row):
		frappe.db.rollback()
		return "Queued"
	return _block(row, reason) if row else (frappe.db.get_value(OUTBOUND, name, "status") or "Missing")


def _fail_locked(name: str, reason: str) -> str:
	row = _lock_queued(name)
	if row and _leased(row):
		frappe.db.rollback()
		return "Queued"
	return _fail(row, reason) if row else (frappe.db.get_value(OUTBOUND, name, "status") or "Missing")


def _sandbox_sent(name: str) -> str:
	"""Sandbox: coi như đã gửi, đánh dấu is_sandbox, không gọi API."""
	row = _lock_queued(name)
	if not row:
		return frappe.db.get_value(OUTBOUND, name, "status") or "Missing"
	frappe.logger("omni").info("omni sandbox: bỏ qua gửi tin ra %s", name)
	return _sent(row, None, sandbox=True)


def _after_error(row: Any, exc: Exception) -> str:
	"""Phân loại lỗi từ adapter: window -> Blocked, tạm thời -> thử lại, còn lại -> Failed."""
	frappe.db.rollback()
	kind = getattr(exc, "kind", None)
	message = getattr(exc, "message_vi", None) or str(exc)
	sent_ids = getattr(exc, "sent_ids", None) or []
	frappe.logger("omni").warning(
		"omni: gửi tin ra %s lỗi (%s): %s", row.name, kind or type(exc).__name__, redact(message)[:200]
	)
	if sent_ids:  # đã gửi được một phần: gửi lại sẽ làm khách nhận trùng
		return _fail(
			row,
			_("Chỉ gửi được một phần tin (lỗi: {0}). Không tự gửi lại để tránh trùng.").format(message),
			external_message_id=sent_ids[0] or None,
		)
	if kind == KIND_WINDOW:
		return _block(row, message)
	if kind == KIND_TRANSIENT and _safe_to_retry(exc):
		return _retry_or_fail(row, exc, message)
	if kind == KIND_TRANSIENT or (kind is None and is_transient(exc)):
		# Lỗi mạng mơ hồ (vd đọc phản hồi quá hạn): tin có thể ĐÃ tới khách -> không gửi lại, nhờ người kiểm tra.
		return _fail(row, _(_AMBIGUOUS_MSG))
	return _fail(row, message)


def _safe_to_retry(exc: BaseException) -> bool:
	"""Chắc chắn tin CHƯA tới khách: Zalo trả lỗi có mã (trừ 502/504 của proxy), HTTP 429, hoặc chưa kết nối được."""
	status = getattr(exc, "http_status", None)
	if status in (502, 504):  # cổng/proxy hết hạn: Zalo có thể đã xử lý xong
		return False
	if status == 429 or getattr(exc, "code", None) is not None:
		return True
	return isinstance(exc.__cause__, requests.ConnectTimeout)
