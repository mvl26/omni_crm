"""Bot soạn trả lời (docs/08 mục 5) — D2-07: CHỈ chế độ ngầm (Shadow).

Bản nháp chỉ để đo: không hiện cho sale, không gửi cho khách. Module này (và cả thư mục `bot/`) KHÔNG tạo
Omni Outbound Message và không có đường gửi nào; test quét mã nguồn để giữ điều đó. Chế độ Draft/Auto thuộc Đợt 3.

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


def groups_block() -> str:
	"""Danh sách nhóm câu hỏi (kèm cờ luôn chuyển người) đưa cho bot phân loại; nội dung đã thoát thẻ."""
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


def build_user_message(
	lead: str, channel_type: str | None, until: Any = None, include_summary: bool = True, mask: bool = True
) -> str:
	"""Nội dung gửi bot: nhóm câu hỏi, tóm tắt Lead (nếu có), giới hạn kênh và hội thoại (tối đa 20 lượt, dữ liệu)."""
	parts = [groups_block()]
	summary = frappe.db.get_value("CRM Lead", lead, "omni_bot_summary") if include_summary else None
	if summary:
		text = escape_tags(normalize_text(summary))
		parts.append(f"<tom_tat_lead>{mask_pii(text) if mask else text}</tom_tat_lead>")
	parts.append(
		f"<gioi_han_kenh>kenh={channel_type or '?'}; toi_da_ky_tu={channel_max_chars(channel_type)}</gioi_han_kenh>"
	)
	parts.append(build_conversation(lead, until=until, max_turns=MAX_TURNS, mask=mask))
	return "\n".join(parts)


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


def _send_note(identity: str, channel: str) -> str | None:
	"""Ghi chú (không chặn) nếu kênh không cho gửi lúc này; Shadow vẫn chạy để chấm (docs/08 mục 5.1)."""
	try:
		channel_doc = frappe.get_doc("Omni Channel", channel)
		adapter = get_adapter(channel_doc.channel_type)
		ok, reason = adapter.can_send_now(
			frappe.get_doc("Omni Identity", identity), channel_doc, source="Bot"
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
	extra: dict | None = None,
) -> str | None:
	"""Gọi bot một lần và lưu Omni Bot Draft (Shadow); trả tên nháp, hoặc None nếu AI không trả được kết quả.

	Không có đường gửi nào ở đây. `until`: dựng ngữ cảnh chỉ gồm lượt đến thời điểm này (replay). Kho tri thức
	trống thì ghi nháp Handoff mà không gọi AI.
	"""
	settings = frappe.get_cached_doc("Omni Settings")
	items = knowledge.kb_items()
	if len(items) > KB_MAX_ITEMS:
		frappe.log_error(
			title="Omni: kho tri thức vượt ngưỡng truy hồi",
			message=f"so_muc={len(items)} nguong={KB_MAX_ITEMS}; cần ADR chuyển sang truy hồi (docs/08 mục 5.2)",
		)
		return None
	base = {
		"lead": lead,
		"identity": identity,
		"channel": channel,
		"batch_id": batch_id,
		**(extra or {}),
	}
	info = [n for n in (_send_note(identity, channel) if check_channel else None,) if n]
	if not business_hours.is_business_time(at or now_datetime()):
		info.append("info:ngoai_gio")
	if not items:
		name = _insert_draft(
			{
				**base,
				"action": "Handoff",
				"guard_flags": "\n".join(["kb_trong", *info]),
				"handoff_reason": _("Kho tri thức đang trống"),
			}
		)
		frappe.db.commit()
		return name
	prompt = load_prompt("reply")
	mask = not cint(frappe.conf.get(UNMASK_CONF))
	system = render(prompt, kenh=channel_type or "kênh nhắn tin", **hours_context(at))
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
			},
		)
		run = _run_name(batch_id)
		_scrub_run(
			batch_id,
			{
				"action": decision["action"],
				"question_group": decision["group"],
				"kb_item_ids": decision["kb_ids"],
				"confidence": decision["confidence"],
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
				"confidence": decision["confidence"]
				if isinstance(decision["confidence"], int | float)
				else None,
				"guard_flags": "\n".join([*decision["flags"], *info]),
				"handoff_reason": decision["reason"],
			}
		)
		frappe.db.commit()
		return name
	except Exception:
		_scrub_run(batch_id, None)  # lỗi giữa chừng: không để lại output_json thô
		frappe.db.commit()
		raise


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
	"""Job nền: soạn nháp Shadow cho lượt `batch_id`; trả "drafted" hoặc lý do bỏ qua. Không ném lỗi, không gửi gì.

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
		if frappe.db.exists(DRAFT, {"batch_id": batch_id}):
			return "exists"
		if handoff.is_bot_paused(identity):
			return "paused"
		if sale_replies_after(lead, last.received_at):
			return "sale_replied"
		if _recent_drafts(identity) >= MAX_DRAFTS_PER_HOUR:
			return "rate_limited"
		analysis = analysis_of(batch_id)
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
