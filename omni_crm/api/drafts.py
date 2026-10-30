"""Duyệt nháp của bot trong Hộp thư (D3-01, docs/08 mục 5.5): Gửi, Sửa rồi gửi, Bỏ, chấm Đúng/Sai/Thiếu.

Bot KHÔNG tự gửi: tin ra luôn do Sale bấm, đi qua `queue_outbound` với `source=Sale` và `bot_draft` liên kết.
Nháp `Handoff` không có nội dung để gửi và bị từ chối ở cả API này lẫn `queue_outbound`. Mọi thao tác kiểm quyền
theo Lead (không chỉ theo DocType); trạng thái nháp đổi dưới khóa dòng nên bấm hai lần không gửi hai tin.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.bot import knowledge, reply

DRAFT = "Omni Bot Draft"
IDENTITY = "Omni Identity"
CHANNEL = "Omni Channel"
RATINGS = ("Đúng", "Sai", "Thiếu")
DECIDED = ("Sent As Is", "Sent Edited", "Discarded")
NOTE_MAX = 300
ANSWER_MAX = 2000
MIN_EDIT_RATIO = (
	0.001  # văn bản đã sửa nhưng chuẩn hóa (hoa thường, khoảng trắng) giống hệt vẫn tính là có sửa
)


def _kb_ids(raw: str | None) -> list[str]:
	return [k for k in (x.strip() for x in (raw or "").split(",")) if k]


def _get(draft: str, lock: bool = False) -> Any:
	"""Dòng nháp (có thể khóa); ném DoesNotExistError nếu không có."""
	row = frappe.db.get_value(
		DRAFT,
		draft,
		[
			"name",
			"lead",
			"identity",
			"channel",
			"mode",
			"action",
			"decision",
			"draft_text",
			"kb_items",
			"rating",
		],
		as_dict=True,
		for_update=lock,
	)
	if not row:
		frappe.throw(_("Không tìm thấy nháp"), frappe.DoesNotExistError)
	return row


def _channel_type(channel: str | None) -> str | None:
	return frappe.db.get_value(CHANNEL, channel, "channel_type") if channel else None


def thread_drafts(lead: str) -> list[dict]:
	"""Nháp chế độ Draft đang chờ duyệt của Lead cho Hộp thư; không kèm cờ chốt chặn nội bộ. Gọi sau khi kiểm quyền đọc Lead."""
	from omni_crm.api import inbox

	rows = frappe.get_all(
		DRAFT,
		filters={"lead": lead, "mode": "Draft", "decision": "Pending"},
		fields=[
			"name",
			"channel",
			"action",
			"question_group",
			"draft_text",
			"kb_items",
			"handoff_reason",
			"creation",
		],
		order_by="creation asc",
	)
	can_write = bool(frappe.has_permission(inbox.LEAD, "write", doc=lead))
	out = []
	for r in rows:
		channel_type = _channel_type(r.channel)
		reply_ok = r.action == "Reply" and bool((r.draft_text or "").strip())
		out.append(
			{
				"name": r.name,
				"action": r.action,
				"channel_type": channel_type,
				"question_group": r.question_group,
				"text": r.draft_text if reply_ok else None,
				"handoff_reason": r.handoff_reason if r.action == "Handoff" else None,
				"kb": [
					{"id": k["id"], "question": k["question"], "group": k["group"]}
					for k in knowledge.kb_items(_kb_ids(r.kb_items), active_only=False)
				],
				"created": r.creation,
				"can_send": can_write and reply_ok and channel_type == "Zalo OA",
				"can_decide": can_write,
			}
		)
	return out


def leads_with_pending(leads: list[str]) -> set[str]:
	"""Trong `leads` (đã lọc theo quyền), các Lead đang có nháp Draft chờ duyệt."""
	if not leads:
		return set()
	return set(
		frappe.get_all(
			DRAFT,
			filters={
				"lead": ["in", leads],
				"mode": "Draft",
				"decision": "Pending",
			},
			pluck="lead",
		)
	)


@frappe.whitelist()
def get_draft_kb_item(draft: str, item: str) -> dict:
	"""Nội dung một mục kho tri thức mà nháp đã dùng (bấm để xem); chỉ người đọc được Lead của nháp."""
	from omni_crm.api import inbox

	row = _get(draft)
	inbox.check_lead_access(row.lead)
	if item not in _kb_ids(row.kb_items):
		frappe.throw(_("Mục này không thuộc nháp"), frappe.ValidationError)
	found = knowledge.kb_items([item], active_only=False)
	if not found:
		frappe.throw(_("Không tìm thấy mục kho tri thức"), frappe.DoesNotExistError)
	k = found[0]
	return {"id": k["id"], "group": k["group"], "question": k["question"], "answer": k["answer"][:ANSWER_MAX]}


def _decide(draft: str, values: dict) -> None:
	frappe.db.set_value(
		DRAFT,
		draft,
		{"decided_by": frappe.session.user, "decided_at": now_datetime(), **values},
		update_modified=True,
	)


def _must_be_pending(row: Any) -> None:
	if row.mode != "Draft":
		frappe.throw(_("Chỉ duyệt được nháp ở chế độ Nháp"), frappe.ValidationError)
	if row.decision != "Pending":
		frappe.throw(_("Nháp này đã được xử lý hoặc đã hết hạn"), frappe.ValidationError)


def _reject_if_stale(row: Any, lead: str) -> None:
	"""Dưới khóa dòng nháp: loại nháp lỗi thời (khách vừa nhắn mới dù lượt chưa chạy) hoặc khi chế độ Draft đã tắt.

	Commit trạng thái Expired trước khi từ chối để không bị rollback theo lỗi.
	"""
	if row.mode == "Draft" and row.decision == "Pending":
		reply.expire_stale_for_lead(lead)
		if frappe.db.get_value(DRAFT, row.name, "decision", for_update=True) == "Expired":
			frappe.db.commit()
			frappe.throw(
				_("Khách vừa nhắn thêm, nháp này đã lỗi thời; hãy xem tin mới nhất"), frappe.ValidationError
			)
		if flags.bot_reply_mode() != "Draft":
			frappe.throw(_("Chế độ nháp của bot đang tắt, không gửi được nháp này"), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
def send_draft(draft: str, text: str | None = None, confirm_paid: int | str = 0) -> dict:
	"""Sale gửi nháp (nguyên văn nếu không có `text`, hoặc sau khi sửa) qua đường gửi của Sale; không gửi được nháp Handoff.

	Trả như `inbox.send_reply`; nháp chỉ chuyển quyết định khi tin được xếp hàng (Queued). Chặn/cần xác nhận phí
	thì nháp vẫn Pending.
	"""
	from omni_crm.api import inbox

	lead = inbox.check_lead_write(_get(draft).lead)
	row = _get(draft, lock=True)
	_reject_if_stale(row, lead)
	_must_be_pending(row)
	if row.action != "Reply" or not (row.draft_text or "").strip():
		frappe.throw(_("Nháp chuyển người không có nội dung để gửi"), frappe.ValidationError)
	if _channel_type(row.channel) != "Zalo OA":
		frappe.throw(
			_("Kênh này chưa hỗ trợ gửi nháp từ Hộp thư; hãy trả lời trong CRM"), frappe.ValidationError
		)
	final = (row.draft_text if text is None else text or "").strip()
	if not final:
		frappe.throw(_("Nội dung tin nhắn đang trống"), frappe.ValidationError)
	identity_doc = frappe.get_doc(IDENTITY, row.identity)
	from omni_crm.crm_bridge import lead as lead_bridge

	if lead_bridge.follow_merged(identity_doc.lead) != lead:
		frappe.throw(_("Khách của nháp không còn thuộc Lead này"), frappe.ValidationError)
	res = inbox.queue_zalo_reply(
		lead, identity_doc, frappe.get_doc(CHANNEL, row.channel), final, confirm_paid, bot_draft=row.name
	)
	if res.get("status") == "Queued":
		as_is = final == row.draft_text.strip()
		_decide(
			draft,
			{
				"decision": "Sent As Is" if as_is else "Sent Edited",
				"final_text": final,
				"edit_distance_ratio": 0.0
				if as_is
				else max(reply.edit_distance_ratio(row.draft_text, final), MIN_EDIT_RATIO),
			},
		)
		res["decision"] = "Sent As Is" if as_is else "Sent Edited"
	res["draft"] = draft
	return res


@frappe.whitelist(methods=["POST"])
def discard_draft(draft: str) -> dict:
	"""Sale bỏ nháp (kể cả nháp Handoff đã đọc); không gửi gì cho khách."""
	from omni_crm.api import inbox

	inbox.check_lead_write(_get(draft).lead)
	row = _get(draft, lock=True)
	_must_be_pending(row)
	_decide(draft, {"decision": "Discarded"})
	return {"status": "Discarded", "draft": draft}


@frappe.whitelist(methods=["POST"])
def rate_draft(draft: str, rating: str, note: str | None = None, critical: int | str = 0) -> dict:
	"""Chấm nháp đã gửi/bỏ: Đúng / Sai / Thiếu (+ ghi chú, đánh dấu nghiêm trọng); không bắt buộc."""
	from omni_crm.api import inbox

	inbox.check_lead_write(_get(draft).lead)
	row = _get(draft, lock=True)
	if row.mode != "Draft" or row.decision not in DECIDED:
		frappe.throw(_("Chỉ chấm được nháp đã gửi hoặc đã bỏ"), frappe.ValidationError)
	if rating not in RATINGS:
		frappe.throw(_("Điểm chấm phải là Đúng, Sai hoặc Thiếu"), frappe.ValidationError)
	frappe.db.set_value(
		DRAFT,
		draft,
		{
			"rating": rating,
			"rating_note": (note or "").strip()[:NOTE_MAX] or None,
			"critical": 1 if cint(critical) else 0,
		},
	)
	return {"status": "Rated", "draft": draft, "rating": rating}
