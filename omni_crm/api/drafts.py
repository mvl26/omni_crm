"""Duyệt nháp của bot trong Hộp thư (D3-01, docs/08 mục 5.5): Gửi, Sửa rồi gửi, Bỏ, chấm Đúng/Sai/Thiếu.

Nháp ở chế độ Draft không tự gửi: tin ra do Sale bấm, đi qua `queue_outbound` với `source=Sale` và `bot_draft` liên kết.
(Nhóm đang Auto thì `bot/auto_send.py` gửi, D3-05; ở đây chỉ chấm điểm tin bot đã gửi.)
Nháp `Handoff` không có nội dung để gửi và bị từ chối ở cả API này lẫn `queue_outbound`, trừ nháp Handoff có mẫu tin
(hỏi giá/đặt demo, ADR 0031): sale điền chỗ trống `[...]` rồi gửi; còn `[...]` thì bị chặn. Mọi thao tác kiểm quyền
theo Lead (không chỉ theo DocType); trạng thái nháp đổi dưới khóa dòng nên bấm hai lần không gửi hai tin.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.bot import handoff, kb_docs, knowledge, reply, templates

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


def _sources(ids: list[str]) -> list[dict[str, str]]:
	"""Nguồn của nháp: mục hỏi/đáp (KB-…) và đoạn tài liệu Kho tri thức (ADR 0030), theo thứ tự mã trong nháp."""
	found = {k["id"]: k for k in knowledge.kb_items(ids, active_only=False)}
	found.update({k["id"]: k for k in kb_docs.sources(ids)})
	return [found[i] for i in ids if i in found]


def _unique_sources(items: list[dict]) -> list[dict[str, str]]:
	"""Nguồn để hiện: mỗi tài liệu/mục một lần (nhiều đoạn cùng tài liệu chỉ giữ đoạn đầu), giữ thứ tự."""
	seen: set[str] = set()
	out = []
	for k in items:
		key = k.get("question") or k["id"]
		if key in seen:
			continue
		seen.add(key)
		out.append({"id": k["id"], "question": k.get("question"), "group": k.get("group")})
	return out


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
			"guard_flags",
			"creation",
		],
		order_by="creation asc",
	)
	can_write = bool(frappe.has_permission(inbox.LEAD, "write", doc=lead))
	out = []
	for r in rows:
		channel_type = _channel_type(r.channel)
		reply_ok = r.action == "Reply" and bool((r.draft_text or "").strip())
		template = templates.is_template_draft(r)
		out.append(
			{
				"name": r.name,
				"action": r.action,
				"channel_type": channel_type,
				"question_group": r.question_group,
				"text": r.draft_text if reply_ok or template else None,
				"template": template,
				"handoff_reason": handoff.explain_reason(r.handoff_reason, r.guard_flags)
				if r.action == "Handoff"
				else None,
				"kb": _unique_sources(_sources(_kb_ids(r.kb_items))),
				"created": r.creation,
				"can_send": can_write and (reply_ok or template) and channel_type == "Zalo OA",
				"can_decide": can_write,
			}
		)
	return out


def bot_sent_meta(lead: str, drafts: list[str]) -> dict[str, dict]:
	"""Cho tin bot đã tự gửi trong Hộp thư: nhóm câu hỏi, điểm đã chấm, có chấm được không, có tắt nhóm được không.

	Không kèm nội dung nháp. `can_rate`: người dùng ghi được Lead; `can_disable`: Omni Admin và nhóm đang bật.
	"""
	if not drafts:
		return {}
	from omni_crm.api import inbox

	can_write = bool(frappe.has_permission(inbox.LEAD, "write", doc=lead))
	is_admin = "Omni Admin" in frappe.get_roles()
	rows = frappe.get_all(
		DRAFT,
		filters={"name": ["in", list(dict.fromkeys(drafts))], "lead": lead},
		fields=["name", "mode", "question_group", "rating"],
	)
	enabled = {
		g.name: bool(g.auto_send_enabled)
		for g in frappe.get_all(
			"Omni Question Group",
			filters={"name": ["in", [r.question_group for r in rows if r.question_group]]},
			fields=["name", "auto_send_enabled"],
		)
	}
	return {
		r.name: {
			"draft": r.name,
			"question_group": r.question_group,
			"rating": r.rating or None,
			"can_rate": can_write and r.mode == "Auto",
			"can_disable": is_admin and bool(enabled.get(r.question_group)),
		}
		for r in rows
	}


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
	found = _sources([item])
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
	template = templates.is_template_draft(row)
	if not template and (row.action != "Reply" or not (row.draft_text or "").strip()):
		frappe.throw(_("Nháp chuyển người không có nội dung để gửi"), frappe.ValidationError)
	if _channel_type(row.channel) != "Zalo OA":
		frappe.throw(
			_("Kênh này chưa hỗ trợ gửi nháp từ Hộp thư; hãy trả lời trong CRM"), frappe.ValidationError
		)
	final = (row.draft_text if text is None else text or "").strip()
	if not final:
		frappe.throw(_("Nội dung tin nhắn đang trống"), frappe.ValidationError)
	if template and templates.unfilled(final):
		frappe.throw(_("Mẫu tin còn chỗ trống [...] chưa điền"), frappe.ValidationError)
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
	"""Chấm nháp đã gửi/bỏ hoặc tin bot tự gửi (mode Auto): Đúng / Sai / Thiếu (+ ghi chú, đánh dấu nghiêm trọng).

	Chấm "Sai" một tin bot đã tự gửi thì nhóm của nó tự tắt tự động gửi (ngắt mạch, D3-05).
	"""
	from omni_crm.api import inbox
	from omni_crm.bot import auto_send

	inbox.check_lead_write(_get(draft).lead)
	row = _get(draft, lock=True)
	if row.mode not in ("Draft", "Auto") or row.decision not in DECIDED:
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
	tripped = auto_send.on_rated(draft, rating, bool(cint(critical)))
	return {"status": "Rated", "draft": draft, "rating": rating, "auto_disabled": tripped}
