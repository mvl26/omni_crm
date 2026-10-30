"""Bot soạn trả lời (docs/08 mục 5): chế độ ngầm (Shadow, D2-07) và Nháp cho sale duyệt (Draft, D3-01).

Shadow: bản nháp chỉ để đo, không hiện cho sale. Draft: nháp hiện trong Hộp thư, sale bấm Gửi/Sửa/Bỏ (api/drafts.py).
Module này (và cả thư mục `bot/`) KHÔNG tạo Omni Outbound Message và không có đường gửi nào; test quét mã nguồn để
giữ điều đó. Chế độ Auto (tự gửi) thuộc D3-05.

Nội dung khách là dữ liệu: nằm trong thẻ, bị chốt chặn bằng code kiểm đầu ra trước khi lưu (guards.py). Mọi lỗi
của bot chỉ ghi log không chứa nội dung khách, không làm hỏng pipeline.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.bot import ai_client, guards, handoff, knowledge
from omni_crm.bot.context import build_conversation, escape_tags, mask_pii, normalize_text
from omni_crm.bot.prompts.loader import load_prompt, render
from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.pipeline.retry import redact
from omni_crm.utils import business_hours

DRAFT = "Omni Bot Draft"
BOT_RUN = "Omni Bot Run"
INBOUND = "Omni Inbound Message"
GROUP = "Omni Question Group"
QUEUE = "omni"
JOB = "omni_crm.bot.reply.reply_batch"
JOB_TIMEOUT = 600
UNMASK_CONF = "omni_ai_unmask_pii"  # như analyze: chỉ bật sau khi chủ dự án chốt Q11
MAX_TURNS = 20
MAX_DRAFTS_PER_HOUR = 6  # giới hạn tần suất nháp cho một khách (chống bão tin, chống đốt hạn mức)
KB_MAX_ITEMS = (
	150  # docs/08 mục 5.2: trên ngưỡng này phải chuyển sang truy hồi (cần ADR), tới lúc đó không soạn
)
COMPARE_WINDOW_HOURS = 24  # docs/08 mục 5.5
COMPARE_BATCH = 200
CHANNEL_MAX_CHARS = {"Zalo OA": 2000, "Email": 1200}
REPLAY_PREFIX = "replay::"
KB_LOG_KEY = "omni:reply:kb_too_large_logged"
KB_LOG_TTL = 3600
TEXT_MAX = 1200
REASON_MAX = 300

TOOL: dict[str, Any] = {
	"name": "propose_reply",
	"description": "Đề xuất câu trả lời cho khách dựa trên kho tri thức, hoặc chuyển cho nhân viên",
	"input_schema": {
		"type": "object",
		"properties": {
			"action": {"enum": ["reply", "handoff"]},
			"question_group": {"type": "string"},
			"reply_text": {"type": "string", "maxLength": TEXT_MAX},
			"kb_item_ids": {"type": "array", "items": {"type": "string"}},
			"ask_contact_info": {"type": "boolean"},
			"confidence": {"type": "number", "minimum": 0, "maximum": 1},
			"handoff_reason": {"type": ["string", "null"]},
		},
		"required": ["action", "question_group", "kb_item_ids", "confidence"],
	},
}


# --- Đầu vào cho bot (docs/08 mục 5.2) -----------------------------------------------------------------------


def _clean(value: Any, limit: int, keep_newlines: bool = False) -> str:
	"""Văn bản thuần từ đầu ra AI: bỏ ký tự điều khiển và < >, cắt theo giới hạn."""
	pattern = r"[\x00-\x08\x0b-\x1f\x7f<>]+" if keep_newlines else r"[\x00-\x1f\x7f<>]+"
	text = re.sub(pattern, " ", str(value or ""))
	text = re.sub(r"[ \t]+", " ", text) if keep_newlines else re.sub(r"\s+", " ", text)
	return text.strip()[:limit]


def hours_context(at: datetime | None = None) -> dict[str, str]:
	"""Biến cho câu lệnh: kênh chưa gồm; trong/ngoài giờ và giờ làm việc kế tiếp tính từ `at` (mặc định bây giờ)."""
	at = at or now_datetime()
	if business_hours.is_business_time(at):
		return {
			"trong_gio_hay_ngoai_gio": "đang trong giờ làm việc",
			"gio_lam_viec_ke_tiep": "ngay trong giờ làm việc",
		}
	nxt = business_hours.next_business_start(at)
	return {
		"trong_gio_hay_ngoai_gio": "đang ngoài giờ làm việc",
		"gio_lam_viec_ke_tiep": nxt.strftime("%H:%M ngày %d/%m/%Y"),
	}


def groups_block(rows: list[Any] | None = None) -> str:
	"""Danh sách nhóm câu hỏi (kèm cờ luôn chuyển người) đưa cho bot phân loại; nội dung đã thoát thẻ.

	`rows`: dòng có name/always_handoff/description; mặc định đọc từ DB (bộ đề có thể đưa dòng đọc từ tệp).
	"""
	if rows is None:
		rows = frappe.get_all(GROUP, fields=["name", "always_handoff", "description"], order_by="name asc")
	lines = [
		f'<nhom ten="{escape_tags(r.name)}" luon_chuyen_nguoi="{1 if r.always_handoff else 0}">'
		f"{escape_tags(r.description or '')}</nhom>"
		for r in rows
	]
	return "\n".join(["<nhom_cau_hoi>", *lines, "</nhom_cau_hoi>"])


def channel_max_chars(channel_type: str | None) -> int:
	"""Giới hạn ký tự của một câu trả lời trên kênh (chốt chặn 5)."""
	return min(CHANNEL_MAX_CHARS.get(channel_type or "", guards.DEFAULT_MAX_CHARS), TEXT_MAX * 2)


def compose_user_message(
	conversation: str,
	channel_type: str | None,
	summary: str | None = None,
	mask: bool = True,
	groups: str | None = None,
) -> str:
	"""Ghép nội dung gửi bot từ hội thoại đã dựng: nhóm câu hỏi, tóm tắt Lead (nếu có), giới hạn kênh, hội thoại."""
	parts = [groups if groups is not None else groups_block()]
	if summary:
		text = escape_tags(normalize_text(summary))
		parts.append(f"<tom_tat_lead>{mask_pii(text) if mask else text}</tom_tat_lead>")
	parts.append(
		f"<gioi_han_kenh>kenh={channel_type or '?'}; toi_da_ky_tu={channel_max_chars(channel_type)}</gioi_han_kenh>"
	)
	parts.append(conversation)
	return "\n".join(parts)


def build_user_message(
	lead: str, channel_type: str | None, until: Any = None, include_summary: bool = True, mask: bool = True
) -> str:
	"""Nội dung gửi bot: nhóm câu hỏi, tóm tắt Lead (nếu có), giới hạn kênh và hội thoại (tối đa 20 lượt, dữ liệu)."""
	summary = frappe.db.get_value("CRM Lead", lead, "omni_bot_summary") if include_summary else None
	conversation = build_conversation(lead, until=until, max_turns=MAX_TURNS, mask=mask)
	return compose_user_message(conversation, channel_type, summary, mask)


# --- Dữ liệu của lượt ---------------------------------------------------------------------------------------


def analysis_of(batch_id: str) -> dict:
	"""Kết quả đã duyệt của lần phân tích cùng lượt (intent, wants_human); {} nếu không có hay AI lỗi."""
	rows = frappe.get_all(
		BOT_RUN,
		filters={"batch_id": batch_id, "kind": "Analyze", "status": "Success"},
		fields=["output_json"],
		order_by="creation desc",
		limit_page_length=1,
	)
	try:
		data = json.loads(rows[0].output_json) if rows and rows[0].output_json else {}
	except ValueError:
		return {}
	accepted = data.get("accepted") if isinstance(data, dict) else None
	return accepted if isinstance(accepted, dict) else {}


def _batch_messages(batch_id: str) -> list[Any]:
	return frappe.get_all(
		INBOUND,
		filters={"batch_id": batch_id},
		fields=["name", "event_type", "text", "received_at", "identity", "channel", "channel_type", "lead"],
		order_by="received_at asc, creation asc",
	)


def _is_sale_turn(turn: dict) -> bool:
	return turn.get("direction") == "out" and (
		turn.get("actor") in ("sale", "external") or turn.get("kind") == "email_out"
	)


def sale_replies_after(lead: str, after: Any, until: Any = None) -> list[dict]:
	"""Các lượt sale đã trả lời (Outbound Sale, oa_echo, email đi) sau thời điểm `after`, cũ trước."""
	after_dt = get_datetime(after)
	limit = get_datetime(until) if until else None
	return [
		t
		for t in timeline.conversation_turns(lead)
		if _is_sale_turn(t)
		and (t.get("text") or "").strip()
		and get_datetime(t["at"]) > after_dt
		and (limit is None or get_datetime(t["at"]) <= limit)
	]


def _recent_drafts(identity: str) -> int:
	since = add_to_date(now_datetime(), hours=-1)
	return frappe.db.count(
		DRAFT,
		{"identity": identity, "creation": [">", since], "batch_id": ["not like", f"{REPLAY_PREFIX}%"]},
	)


def _send_note(identity: str, channel: str, mode: str = "Shadow") -> str | None:
	"""Ghi chú (không chặn) nếu kênh không cho gửi lúc này; Shadow vẫn chạy để chấm (docs/08 mục 5.1).

	Draft: người gửi là Sale (được gửi cả khung có phí, sale xác nhận), nên kiểm theo nguồn Sale.
	"""
	try:
		channel_doc = frappe.get_doc("Omni Channel", channel)
		adapter = get_adapter(channel_doc.channel_type)
		ok, reason = adapter.can_send_now(
			frappe.get_doc("Omni Identity", identity),
			channel_doc,
			source="Sale" if mode == "Draft" else "Bot",
		)
	except (AdapterNotFoundError, frappe.DoesNotExistError, AttributeError):
		return None
	return None if ok else "info:khung_gui:" + re.sub(r"\s+", " ", str(reason))[:120]


# --- Soạn nháp ----------------------------------------------------------------------------------------------


def _scrub_run(batch_id: str, summary: dict | None) -> None:
	"""Thay output_json thô của lần gọi Reply (có thể chứa câu trả lời) bằng bản tóm tắt không có văn bản."""
	run = frappe.get_all(
		BOT_RUN,
		filters={"batch_id": batch_id, "kind": "Reply"},
		order_by="creation desc",
		limit_page_length=1,
		pluck="name",
	)
	if run:
		frappe.db.set_value(BOT_RUN, run[0], "output_json", json.dumps(summary) if summary else None)


def _run_name(batch_id: str) -> str | None:
	rows = frappe.get_all(
		BOT_RUN,
		filters={"batch_id": batch_id, "kind": "Reply"},
		order_by="creation desc",
		limit_page_length=1,
		pluck="name",
	)
	return rows[0] if rows else None


def evaluate(raw: dict, ctx: dict) -> dict:
	"""Biến đầu ra AI thành quyết định: {"action", "text", "flags", "reason", "kb_ids", "group", "confidence"}.

	`ctx`: items (kho đang dùng), group_row, customer_text, intent, wants_human, channel_type. Chạy mọi chốt chặn
	khi bot đề xuất reply; bất kỳ chốt chặn nào kích hoạt thì thành Handoff và không giữ lại văn bản.
	"""
	items = {i["id"]: i for i in ctx["items"]}
	kb_ids = [k for k in (str(x).strip() for x in raw.get("kb_item_ids") or []) if k]
	group = _clean(raw.get("question_group"), 140)
	group_row = ctx["group_row"].get(group)
	confidence = raw.get("confidence")
	out = {
		"kb_ids": kb_ids,
		"group": group if group_row else None,
		"confidence": confidence,
		"flags": [],
		"text": None,
	}
	if raw.get("action") != "reply":
		out.update(
			action="Handoff",
			reason=_clean(raw.get("handoff_reason"), REASON_MAX) or _("Bot đề xuất chuyển người"),
		)
		if ctx.get(
			"wants_human"
		):  # khách đòi gặp người: nhãn lý do cố định cho thông báo (handoff.reason_label)
			out["flags"] = [handoff.FLAG_WANTS_HUMAN]
		return out
	text = _clean(raw.get("reply_text"), TEXT_MAX, keep_newlines=True)
	flags_ = guards.run_guards(
		reply_text=text,
		kb_item_ids=kb_ids,
		valid_kb_ids=frozenset(items),
		kb_texts=[items[k]["answer"] for k in kb_ids if k in items],
		group_known=bool(group_row),
		group_always_handoff=bool(group_row and group_row.always_handoff),
		intent=ctx.get("intent"),
		wants_human=bool(ctx.get("wants_human")),
		customer_text=ctx.get("customer_text"),
		confidence=confidence,
		max_chars=channel_max_chars(ctx.get("channel_type")),
		allowed_phrases=ctx.get("allowed_phrases") or (),
	)
	if flags_:
		out.update(action="Handoff", flags=flags_, reason=_("Chốt chặn: ") + "; ".join(flags_)[:REASON_MAX])
	else:
		out.update(action="Reply", text=text, reason=None)
	return out


def _insert_draft(values: dict) -> str:
	doc = frappe.get_doc({"doctype": DRAFT, "mode": "Shadow", "decision": "Pending", **values})
	doc.insert(ignore_permissions=True)
	return doc.name


# --- Nháp cho sale (chế độ Draft, D3-01) ---------------------------------------------------------------------

DRAFT_TTL_HOURS = 24  # docs/08 mục 5.5: nháp quá 24 giờ -> Expired
NOTIFY_SUBJECT_MAX = 140


def expire_stale_for_lead(lead: str) -> int:
	"""Nháp Draft còn Pending của Lead mà tin khách cuối của lượt của nó cũ hơn tin khách mới nhất của Lead -> Expired.

	So theo mốc tin khách của từng lượt (không theo giờ tạo nháp) nên nháp đến muộn do AI chậm vẫn bị loại đúng.
	Trả số nháp đã hết hạn. Không commit.
	"""
	pending = frappe.get_all(
		DRAFT,
		filters={"lead": lead, "mode": "Draft", "decision": "Pending"},
		fields=["name", "batch_id", "creation"],
	)
	if not pending:
		return 0
	rows = frappe.get_all(
		INBOUND,
		filters={"lead": lead, "event_type": "message", "status": ["!=", "Ignored"]},
		fields=["received_at"],
		order_by="received_at desc",
		limit_page_length=1,
	)
	if not rows or not rows[0].received_at:
		return 0
	latest = get_datetime(rows[0].received_at)
	done = 0
	for d in pending:
		if get_datetime(_customer_anchor(d)) < latest:
			frappe.db.set_value(
				DRAFT,
				{"name": d.name, "decision": "Pending"},
				{"decision": "Expired", "decided_at": now_datetime()},
			)
			done += 1
	return done


def expire_pending_drafts() -> int:
	"""Tác vụ định kỳ: nháp Draft còn Pending quá 24 giờ -> Expired; trả số nháp đã hết hạn."""
	cutoff = add_to_date(now_datetime(), hours=-DRAFT_TTL_HOURS)
	names = frappe.get_all(
		DRAFT,
		filters={"mode": "Draft", "decision": "Pending", "creation": ["<", cutoff]},
		pluck="name",
		limit_page_length=COMPARE_BATCH,
	)
	for name in names:
		frappe.db.set_value(
			DRAFT,
			{"name": name, "decision": "Pending"},
			{"decision": "Expired", "decided_at": now_datetime()},
		)
	frappe.db.commit()
	return len(names)


def notify_pending_draft(lead: str, action: str) -> int:
	"""Báo người phụ trách Lead "có nháp bot chờ duyệt" (Notification Log + realtime Hộp thư); trả số người đã báo.

	Chỉ tên Lead và liên kết Hộp thư, KHÔNG kèm nội dung khách hay nháp. Không commit. Lỗi chỉ ghi log.
	"""
	from omni_crm.bot import scoring

	name = lead  # mã Lead, không dùng tên khách tự khai (nội dung từ khách)
	subject = (
		_("Bot chuyển người, mời xem: {0}") if action == "Handoff" else _("Có nháp bot chờ duyệt: {0}")
	).format(name)[:NOTIFY_SUBJECT_MAX]
	link = f"/app/omni-inbox?lead={lead}"
	done = 0
	for user in scoring._recipients(lead):
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": f"{frappe.utils.escape_html(subject)}. "
					f'<a href="{frappe.utils.escape_html(link)}">{frappe.utils.escape_html(_("Mở Hộp thư"))}</a>',
					"document_type": "CRM Lead",
					"document_name": lead,
				}
			).insert(ignore_permissions=True)
			done += 1
		except Exception:
			frappe.log_error(title="Omni: không báo được nháp bot", message=f"lead={lead}")
	timeline.notify(lead, [])
	return done


def _log_kb_too_large(count: int) -> None:
	"""Ghi Error Log khi kho vượt ngưỡng truy hồi, tối đa một lần mỗi giờ (không ghi theo từng tin)."""
	cache = frappe.cache()
	if cache.get_value(KB_LOG_KEY, expires=True):
		return
	cache.set_value(KB_LOG_KEY, 1, expires_in_sec=KB_LOG_TTL)
	frappe.log_error(
		title="Omni: kho tri thức vượt ngưỡng truy hồi",
		message=f"so_muc={count} nguong={KB_MAX_ITEMS}; cần ADR chuyển sang truy hồi (docs/08 mục 5.2)",
	)


def _valid_confidence(value: Any) -> float | None:
	"""Độ tự tin hợp lệ (số thực/nguyên hữu hạn trong [0, 1], không phải bool) để lưu vào cột Float; còn lại None."""
	if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
		return None
	return float(value)


def make_draft(
	*,
	lead: str,
	identity: str,
	channel: str,
	channel_type: str | None,
	batch_id: str,
	customer_text: str,
	intent: str | None = None,
	wants_human: bool = False,
	until: Any = None,
	at: datetime | None = None,
	include_summary: bool = True,
	check_channel: bool = True,
	notes: list[str] | None = None,
	extra: dict | None = None,
	mode: str = "Shadow",
) -> str | None:
	"""Gọi bot một lần và lưu Omni Bot Draft (`mode` Shadow hoặc Draft); trả tên nháp, hoặc None nếu AI không trả được kết quả.

	Không có đường gửi nào ở đây. `until`: dựng ngữ cảnh chỉ gồm lượt đến thời điểm này (replay). Kho tri thức
	trống thì ghi nháp Handoff mà không gọi AI.
	"""
	settings = frappe.get_cached_doc("Omni Settings")
	items = knowledge.kb_items()
	if len(items) > KB_MAX_ITEMS:
		_log_kb_too_large(len(items))
		return None
	base = {
		"lead": lead,
		"identity": identity,
		"channel": channel,
		"batch_id": batch_id,
		"mode": mode,
		**(extra or {}),
	}
	info = [n for n in (_send_note(identity, channel, mode) if check_channel else None,) if n]
	if not business_hours.is_business_time(at or now_datetime()):
		info.append("info:ngoai_gio")
	info.extend(notes or [])
	if not items:
		name = _insert_draft(
			{
				**base,
				"action": "Handoff",
				"guard_flags": "\n".join(["kb_trong", *info]),
				"handoff_reason": _("Kho tri thức đang trống"),
			}
		)
		_after_insert(name, base, "Handoff")
		frappe.db.commit()
		return name
	prompt = load_prompt("reply")
	mask = not cint(frappe.conf.get(UNMASK_CONF))
	hours = hours_context(at)
	system = render(prompt, kenh=channel_type or "kênh nhắn tin", **hours)
	raw = ai_client.call_tool(
		"Reply",
		settings.get("reply_model") or "claude-sonnet-5-5",
		system,
		[
			{
				"role": "user",
				"content": build_user_message(
					lead, channel_type, until, include_summary=include_summary, mask=mask
				),
			}
		],
		TOOL,
		lead=lead,
		identity=identity,
		batch_id=batch_id,
		cache_blocks=[knowledge.render_kb()],
		prompt_version=prompt.version,
		max_tokens=800,
	)  # KHÔNG truyền synthetic: hội thoại khách luôn qua cổng Q11
	if raw is None:
		if mode == "Draft" and wants_human:  # khách đòi gặp người thì vẫn phải qua chuyển người dù AI lỗi
			return _handoff_without_ai(base, info)
		return None
	try:
		group_rows = {r.name: r for r in frappe.get_all(GROUP, fields=["name", "always_handoff"])}
		decision = evaluate(
			raw,
			{
				"items": items,
				"group_row": group_rows,
				"customer_text": customer_text,
				"intent": intent,
				"wants_human": wants_human,
				"channel_type": channel_type,
				# chuỗi mốc giờ do hệ thống sinh (prompt quy tắc 7) được miễn chốt chặn 3, chỉ đúng chuỗi này
				"allowed_phrases": [hours["gio_lam_viec_ke_tiep"]],
			},
		)
		run = _run_name(batch_id)
		_scrub_run(
			batch_id,
			{
				"action": decision["action"],
				"question_group": decision["group"],
				"kb_item_ids": decision["kb_ids"],
				"confidence": _valid_confidence(decision["confidence"]),
				"guard_flags": decision["flags"],
			},
		)
		name = _insert_draft(
			{
				**base,
				"bot_run": run,
				"action": decision["action"],
				"question_group": decision["group"],
				"draft_text": decision["text"],
				"kb_items": ", ".join(decision["kb_ids"]),
				"confidence": _valid_confidence(decision["confidence"]),
				"guard_flags": "\n".join([*decision["flags"], *info]),
				"handoff_reason": decision["reason"],
			}
		)
		_after_insert(name, base, decision["action"])
		frappe.db.commit()
		return name
	except Exception:
		_scrub_run(batch_id, None)  # lỗi giữa chừng: không để lại output_json thô
		frappe.db.commit()
		raise


def _handoff_without_ai(base: dict, info: list[str]) -> str:
	"""AI không trả được kết quả nhưng khách đòi gặp người: ghi nháp Handoff (không văn bản) và chuyển người."""
	name = _insert_draft(
		{
			**base,
			"action": "Handoff",
			"guard_flags": "\n".join([handoff.FLAG_WANTS_HUMAN, "ai_khong_tra_ket_qua", *info]),
			"handoff_reason": _("Khách muốn gặp nhân viên"),
		}
	)
	_after_insert(name, base, "Handoff")
	frappe.db.commit()
	return name


def _after_insert(name: str, base: dict, action: str) -> None:
	"""Chế độ Draft: loại nháp cũ của Lead; nháp mới còn chờ duyệt thì báo người phụ trách. Nháp Handoff đi qua
	`handoff.handoff` (im bot + một thông báo duy nhất); nháp Reply báo "có nháp chờ duyệt". Không commit."""
	if base.get("mode") != "Draft":
		return
	expire_stale_for_lead(base["lead"])
	if frappe.db.get_value(DRAFT, name, "decision") == "Pending":
		if action == "Handoff":
			handoff.handoff(name)
		else:
			notify_pending_draft(base["lead"], action)


# --- Điểm vào -------------------------------------------------------------------------------------------------


def _log_failure(batch_id: str, error: Exception) -> None:
	"""Error Log không chứa nội dung khách (chỉ mã lượt và loại lỗi)."""
	try:
		frappe.db.rollback()
		frappe.log_error(
			title="Omni: lỗi bot trả lời",
			message=redact(
				f"batch_id={batch_id}\nloai_loi={type(error).__name__}\n"
				f"{frappe.get_traceback(with_context=False)[-1500:]}"
			),
		)
		frappe.db.commit()
	except Exception:
		pass


def reply_batch(batch_id: str) -> str:
	"""Job nền: soạn nháp (Shadow hoặc Draft theo công tắc) cho lượt `batch_id`; trả "drafted" hoặc lý do bỏ qua. Không ném lỗi, không gửi gì.

	Điều kiện (docs/08 mục 5.1): chế độ khác Off; tin cuối của lượt là tin khách; bot chưa bị tạm dừng với khách;
	sale chưa trả lời sau tin đó (ngắt mạch); chưa quá giới hạn tần suất.
	"""
	try:
		if flags.bot_reply_mode() == "Off":
			return "off"
		rows = _batch_messages(batch_id)
		last = rows[-1] if rows else None
		if not last or last.event_type != "message" or not (last.text or "").strip():
			return "not_customer_message"
		lead = lead_bridge.follow_merged(last.lead) if last.lead else None
		identity = last.identity
		if not lead or not identity or not last.channel:
			return "no_lead"
		if (
			last.event_type == "message"
		):  # tin khách mới: nháp Draft cũ của Lead hết hạn dù lượt này có soạn nháp hay không
			expire_stale_for_lead(lead)
			frappe.db.commit()
		if frappe.db.exists(DRAFT, {"batch_id": batch_id}):
			return "exists"
		if handoff.is_bot_paused(identity):
			return "paused"
		if sale_replies_after(lead, last.received_at):
			return "sale_replied"
		if _recent_drafts(identity) >= MAX_DRAFTS_PER_HOUR:
			return "rate_limited"
		analysis = analysis_of(batch_id)
		notes = ["info:khong_co_phan_tich"] if flags.bot_analyze_enabled() and not analysis else []
		customer_text = "\n".join(
			r.text for r in rows if r.event_type == "message" and (r.text or "").strip()
		)
		name = make_draft(
			lead=lead,
			identity=identity,
			channel=last.channel,
			channel_type=last.channel_type,
			batch_id=batch_id,
			customer_text=customer_text,
			intent=analysis.get("intent"),
			wants_human=bool(analysis.get("wants_human")),
			at=get_datetime(last.received_at),
			notes=notes,
			mode=flags.bot_reply_mode(),
		)
		return "drafted" if name else "no_result"
	except Exception as e:
		_log_failure(batch_id, e)
		return "error"


def enqueue_reply(batch_id: str, lead: str | None) -> bool:
	"""Đẩy `reply_batch` khi công tắc không Off và lượt có tin khách; lỗi enqueue không ném ra.

	Gọi từ `analyze.after_analyze` (bot phân tích bật) hoặc `post_batch_hooks` (bot phân tích tắt).
	"""
	if not lead or flags.bot_reply_mode() == "Off":
		return False
	try:
		frappe.enqueue(
			JOB,
			queue=QUEUE,
			job_id=f"omni-reply::{batch_id}",
			deduplicate=True,
			timeout=JOB_TIMEOUT,
			batch_id=batch_id,
		)
	except Exception:
		frappe.log_error(title="Omni: không đẩy được job bot trả lời", message=f"batch_id={batch_id}")
		return False
	return True


# --- So sánh với sale (docs/08 mục 5.5) -----------------------------------------------------------------------


def edit_distance_ratio(a: str, b: str) -> float:
	"""Độ khác biệt chuẩn hóa 0-1 giữa hai văn bản (0 = giống hệt), so không phân biệt hoa thường và khoảng trắng."""
	x = re.sub(r"\s+", " ", normalize_text(a or "")).strip().lower()[:2000]
	y = re.sub(r"\s+", " ", normalize_text(b or "")).strip().lower()[:2000]
	if not x and not y:
		return 0.0
	return round(1 - SequenceMatcher(None, x, y, autojunk=False).ratio(), 4)


def sale_beyond_kb(sale_text: str) -> bool:
	"""Câu sale có tiền/thời hạn/cam kết/liên hệ ngoài toàn bộ kho tri thức đang dùng (chốt chặn 3-4 áp lên câu sale)."""
	texts = [i["answer"] for i in knowledge.kb_items()]
	return guards.sale_goes_beyond_kb(sale_text, texts)


def record_comparison(draft: str, sale_text: str) -> None:
	"""Điền câu sale, độ khác biệt, cờ ngoài kho tri thức và chuyển nháp sang Shadow Compared."""
	row = frappe.db.get_value(DRAFT, draft, ["action", "draft_text"], as_dict=True)
	values = {
		"sale_reply_text": sale_text,
		"decision": "Shadow Compared",
		"sale_handoff": 1 if sale_beyond_kb(sale_text) else 0,
		"decided_at": now_datetime(),
	}
	if (
		row and row.action == "Reply"
	):  # cột Float không nhận NULL: nháp Handoff giữ 0 và bị loại khỏi thống kê độ khác biệt
		values["edit_distance_ratio"] = edit_distance_ratio(row.draft_text, sale_text)
	frappe.db.set_value(DRAFT, draft, values)


def _customer_anchor(draft_row: Any) -> Any:
	"""Thời điểm tin khách cuối của lượt (mốc để tìm câu sale trả lời); không có thì lấy lúc tạo nháp."""
	times = [m.received_at for m in _batch_messages(draft_row.batch_id) if m.received_at]
	return max(times) if times else draft_row.creation


def compare_pending() -> dict[str, int]:
	"""Tác vụ định kỳ: nháp Shadow Pending có câu sale trong 24 giờ -> Shadow Compared; quá 24 giờ chưa có -> Expired."""
	now = now_datetime()
	done = {"compared": 0, "expired": 0}
	rows = frappe.get_all(
		DRAFT,
		filters={"mode": "Shadow", "decision": "Pending"},
		fields=["name", "lead", "batch_id", "creation"],
		order_by="creation asc",
		limit_page_length=COMPARE_BATCH,
	)
	for row in rows:
		try:
			deadline = add_to_date(get_datetime(row.creation), hours=COMPARE_WINDOW_HOURS)
			replies = sale_replies_after(row.lead, _customer_anchor(row), until=deadline) if row.lead else []
			if replies:
				record_comparison(row.name, replies[0]["text"])
				done["compared"] += 1
			elif now > deadline:
				frappe.db.set_value(DRAFT, row.name, {"decision": "Expired", "decided_at": now})
				done["expired"] += 1
			frappe.db.commit()
		except Exception as e:
			_log_failure(str(row.batch_id), e)
	return done
