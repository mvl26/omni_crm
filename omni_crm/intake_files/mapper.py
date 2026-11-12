"""Chuyển một file JSON của miyano-intake (schema "1") thành InboundEvent của cổng tiếp nhận.

Hàm thuần: không đọc DB, không ghi gì. Nơi lưu JSON gốc là `Omni Inbound Message.raw_payload` (khóa `intake`),
nên không cần DocType mới. Chống trùng dùng đúng `dedupe_key` của cổng (`{kênh}:{external_message_id}`), chọn
`external_message_id` trùng với đường nhận trực tiếp để một tin đi cả hai đường vẫn chỉ vào một lần:
- email: đi qua Communication + Email Account có sẵn của CRM (không có đường email thứ hai), khóa là Message-ID;
- cuộc gọi: call_id;
- form landing: sha1(event_key) làm submission_id (giống relay của miyano-intake);
- Messenger (ADR 0022): phần sau "messenger:" của event_key (mid của Meta; Send API trả đúng mã này nên echo của tin
  ta gửi trùng khóa với Omni Outbound Message).
"""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from omni_crm.channels.email.adapter import DATA_LEN
from omni_crm.channels.telephony.base import INBOUND, OUTBOUND, CallEvent
from omni_crm.channels.telephony.calls import serialize_event
from omni_crm.ingest.events import InboundEvent

SCHEMA = "1"
MAX_TEXT = 100_000
DISPOSITION = {
	"answered": "Answered",
	"missed": "Missed",
	"busy": "Busy",
	"failed": "Failed",
	"voicemail": "Voicemail",
}

# Lý do bỏ qua (khóa ổn định để thống kê / test)
SKIP_UNSUPPORTED_SCHEMA = "schema_la"
SKIP_NO_CHANNEL_TYPE = "kenh_chua_ho_tro"  # loại tin CRM chưa có kênh (ví dụ Instagram)
SKIP_LIVE_PATH = "da_co_duong_truc_tiep"  # lead Meta chạy qua webhook meta_leads, không nạp từ file
SKIP_EMPTY = "khong_co_du_lieu"

# Gmail xếp thư vào Spam (intake ghi `review_reasons: ["gmail_spam"]`): vẫn nạp, gắn tiền tố tiêu đề để sale xem lại.
SPAM_PREFIX = "[Gmail xếp vào Spam - cần xem lại] "


@dataclass(frozen=True)
class Mapped:
	"""Kết quả ánh xạ: `kind_channel` là loại Omni Channel cần có; event=None kèm `skip` là lý do bỏ qua."""

	channel_type: str | None = None
	event: InboundEvent | None = None
	body: dict | None = (
		None  # chỉ Web Form: body JSON để WebFormAdapter.parse đọc (bao gồm cả honeypot, giới hạn độ dài)
	)
	skip: str | None = None
	reaction: bool = False  # ADR 0027: sự kiện khách thả/bỏ cảm xúc Messenger, không phải tin mới


def _dt(value: Any) -> datetime | None:
	"""ISO 8601 (có múi giờ) -> datetime NAIVE theo giờ Việt Nam, đúng quy ước của Frappe site; None nếu không đọc được."""
	if not value:
		return None
	try:
		dt = datetime.fromisoformat(str(value))
	except ValueError:
		return None
	return dt.replace(tzinfo=None)  # intake ghi sẵn giờ +07:00, trùng múi giờ hệ thống của site Miyano


def _s(value: Any, limit: int) -> str:
	return str(value or "").strip()[:limit]


def webform_body(env: dict) -> dict:
	"""JSON cho WebFormAdapter, cùng quy tắc với `intake/relay.py::webform_payload` (để submission_id trùng)."""
	raw = env.get("raw") or {}
	f = raw.get("fields") or {}
	camp = env.get("campaign") or {}
	page = (env.get("source") or {}).get("account")
	ident = env.get("identifiers") or {}
	if env.get("kind") == "booking":
		message = (env.get("content") or {}).get("text")
		org = job = None
	else:
		need = f"Vấn đề quan tâm: {f['need']}" if f.get("need") else ""
		message = "\n".join(x for x in (need, f.get("message") or "") if x)
		org, job = f.get("organization"), f.get("job_title")
	out = {
		"form": page,
		"submission_id": hashlib.sha1(str(env["event_key"]).encode()).hexdigest(),
		"full_name": (env.get("sender") or {}).get("display_name") or f.get("full_name"),
		"phone": ident.get("phone"),
		"email": ident.get("email"),
		"company_name": org,
		"job_title": job,
		"message": message,
		"consent_contact": True,  # intake chỉ ghi file khi khách đã tích đồng ý; nhật ký ở consent/*.jsonl
		"landing_page": (raw.get("tracking") or {}).get("page_url") or f.get("page_url"),
		"referrer": f.get("referrer"),
	}
	for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid"):
		if camp.get(key):
			out[key] = camp[key]
	return {k: v for k, v in out.items() if v not in (None, "")}


def communication_values(env: dict, email_account: str, account_email: str) -> dict:
	"""Giá trị Communication (thư đến) cho một envelope email; hook `Communication.after_insert` của omni_crm lo phần còn lại.

	Dùng đường email có sẵn của CRM (Communication + Email Account) thay vì tự dựng sự kiện: thư hiện ở tab email của Lead,
	chống trùng theo Message-ID, làm sạch/lọc thư tự động đều do code hiện có. Địa chỉ người gửi là `identifiers.email`
	(Reply-To nếu có, vì form website gửi từ địa chỉ công ty), tên hiển thị lấy từ `sender.display_name`.
	"""
	ident = env.get("identifiers") or {}
	sender = (ident.get("email") or (env.get("sender") or {}).get("value") or "").strip().lower()
	text = str((env.get("content") or {}).get("text") or "")[:MAX_TEXT]
	subject = _s((env.get("raw") or {}).get("subject"), 140)
	if subject and text.startswith(subject):  # intake ghép "tiêu đề\n\nnội dung"
		text = text[len(subject) :].lstrip("\n")
	key = str(env.get("event_key") or "")
	subject = subject or "(không có tiêu đề)"
	if "gmail_spam" in (env.get("review_reasons") or []):
		subject = (SPAM_PREFIX + subject)[:140]
	return {
		"doctype": "Communication",
		"communication_type": "Communication",
		"communication_medium": "Email",
		"sent_or_received": "Received",
		"email_account": email_account,
		"sender": sender,
		"sender_full_name": (env.get("sender") or {}).get("display_name") or None,
		"recipients": account_email,
		"subject": subject,
		"content": text,
		"text_content": text,
		"message_id": (key.split(":", 1)[1] if key.startswith("gmail:") else key)[:DATA_LEN],
		"communication_date": _dt(env.get("occurred_at")) or _dt(env.get("received_at")),
		"status": "Open",
	}


def _call(env: dict, channel: str) -> Mapped:
	call = env.get("call") or {}
	if call.get("event_type") == "recording" or not call.get("call_id"):
		return Mapped(
			skip=SKIP_EMPTY
		)  # sự kiện ghi âm đến sau: bản ghi âm thuộc Đợt 3, không có cuộc gọi để nối
	direction = OUTBOUND if env.get("direction") == "out" else INBOUND
	ended = _dt(call.get("ended_at"))
	disposition = DISPOSITION.get(str(call.get("disposition") or "").lower())
	status = disposition or ("Ended" if ended else "Ringing")
	recording = call.get("recording")
	ref = None
	if isinstance(recording, dict):
		ref = (
			recording.get("mix")
			or recording.get("path")
			or next((v for v in recording.values() if isinstance(v, str)), None)
		)
	elif isinstance(recording, str):
		ref = recording
	ev = CallEvent(
		call_id=str(call["call_id"]),
		direction=direction,
		from_number=str(call.get("from") or ""),
		to_number=str(call.get("to") or ""),
		status=status,
		started_at=_dt(call.get("started_at")),
		answered_at=_dt(call.get("answered_at")),
		ended_at=ended,
		duration_seconds=call.get("answer_duration"),
		agent_ref=call.get("agent_ref"),
		recording_ref=ref,
		raw={"intake": env},
	)
	if not ev.is_final:
		return Mapped(skip=SKIP_EMPTY)  # chưa kết thúc: chỉ cuộc gọi đã kết thúc mới thành tin vào (docs/07)
	event = InboundEvent(
		channel=channel,
		channel_type="Telephony",
		event_type="call_ended",
		external_message_id=ev.call_id,
		external_sender_id=ev.customer_number or "unknown",
		received_at=ev.ended_at or ev.started_at or _dt(env.get("received_at")) or datetime.now(),
		raw={"call_event": serialize_event(ev)},
	)
	return Mapped("Telephony", event)


MESSENGER = "Meta Messenger"
ECHO = "oa_echo"  # tin Page gửi ngoài hệ thống: cùng loại sự kiện với Zalo (ADR 0022 mục 9)


def _messenger(env: dict, channel: str) -> Mapped:
	"""Tin Messenger -> InboundEvent của kênh Meta Messenger (ADR 0022). Đính kèm giữ `path` trong spool; runner chép
	vào File rồi thay bằng `url` trước khi lưu.

	- Tin của khách và postback (khách bấm nút) -> `message`; tin Page gửi ngoài hệ thống (`direction = "out"`) -> `oa_echo`.
	- Referral đứng riêng (khách mở hội thoại từ quảng cáo, chưa nhắn) -> `ignored`: lưu để tra cứu, không tạo Lead.
	"""
	key = str(env.get("event_key") or "")
	if not key.startswith("messenger:") or len(key) <= len("messenger:"):
		return Mapped(skip=SKIP_EMPTY)
	ext = key.split(":", 1)[1]
	echo = env.get("direction") == "out"
	if echo:
		thread = str(env.get("thread_ref") or "")
		psid = thread.split(":", 1)[1] if thread.startswith("messenger:") else ""
	else:
		psid = str((env.get("sender") or {}).get("value") or "")
	if not psid:
		return Mapped(skip=SKIP_EMPTY)
	content = env.get("content") or {}
	text = str(content.get("text") or "")[:MAX_TEXT] or None
	attachments = [
		{
			"type": str(a.get("type") or "file"),
			"path": a.get("path"),
			"name": posixpath.basename(str(a.get("path") or "")) or str(a.get("type") or "file"),
		}
		for a in content.get("attachments") or []
		if isinstance(a, dict)
	]
	if echo:
		event_type = ECHO
	elif ext.startswith("referral:"):
		event_type = "ignored"
	else:
		event_type = "message"
	if event_type == "message" and not text and not attachments:
		return Mapped(skip=SKIP_EMPTY)
	profile = {"psid": psid, "page_id": str((env.get("source") or {}).get("account") or "")}
	name = (env.get("sender") or {}).get("display_name")
	if name and not echo:
		profile["display_name"] = str(name)[:140]
	event = InboundEvent(
		channel=channel,
		channel_type=MESSENGER,
		event_type=event_type,
		external_message_id=ext[:140],
		external_sender_id=psid,
		received_at=_dt(env.get("occurred_at")) or _dt(env.get("received_at")) or datetime.now(),
		text=text,
		attachments=attachments,
		sender_profile=profile,
		raw={"intake": env},
	)
	return Mapped(MESSENGER, event)


def echo_app_id(env: dict) -> str:
	"""`app_id` của tin echo (ứng dụng đã gửi tin); rỗng nếu không có."""
	message = ((env.get("raw") or {}).get("event") or {}).get("message") or {}
	return str(message.get("app_id") or "")


def reply_to_of(env: dict) -> str:
	"""Mã tin (mid) mà tin Messenger này trả lời (`message.reply_to.mid`, ADR 0027); rỗng nếu không trả lời tin nào."""
	message = ((env.get("raw") or {}).get("event") or {}).get("message") or {}
	reply = message.get("reply_to") if isinstance(message, dict) else None
	return str((reply or {}).get("mid") or "")[:140] if isinstance(reply, dict) else ""


# Meta gửi cả tên (`reaction`) lẫn emoji; thiếu emoji thì dùng bảng này.
REACTION_EMOJI = {
	"love": "❤",
	"like": "👍",
	"smile": "😆",
	"wow": "😮",
	"sad": "😢",
	"angry": "😠",
	"dislike": "👎",
}


def reaction_of(env: dict) -> dict | None:
	"""Sự kiện cảm xúc Messenger (webhook `message_reactions`, intake ghi `kind = "reaction"`) -> dữ liệu cần áp; None nếu thiếu."""
	reaction = ((env.get("raw") or {}).get("event") or {}).get("reaction") or {}
	if not isinstance(reaction, dict):
		return None
	mid, action = str(reaction.get("mid") or ""), str(reaction.get("action") or "")
	psid = str((env.get("sender") or {}).get("value") or "")
	if not mid or not psid or action not in ("react", "unreact"):
		return None
	emoji = str(reaction.get("emoji") or "") or REACTION_EMOJI.get(str(reaction.get("reaction") or ""), "")
	return {
		"mid": mid[:140],
		"psid": psid,
		"action": action,
		"emoji": emoji[:32] if action == "react" else "",
		"at": _dt(env.get("occurred_at")) or _dt(env.get("received_at")) or datetime.now(),
	}


def map_envelope(env: Any) -> Mapped:
	"""Phân loại và ánh xạ một envelope. `channel` thật được gắn sau (cần tra DB) bằng `with_channel`."""
	if not isinstance(env, dict) or str(env.get("schema")) != SCHEMA:
		return Mapped(skip=SKIP_UNSUPPORTED_SCHEMA)
	kind = env.get("kind")
	if kind == "email":
		return Mapped("Email")
	if kind == "call":
		return Mapped("Telephony")
	if kind == "booking" or (kind == "lead_form" and env.get("channel") == "web_form"):
		return Mapped("Web Form", body=webform_body(env))
	if kind == "lead_form":
		return Mapped(skip=SKIP_LIVE_PATH)
	if kind == "message" and env.get("channel") == "messenger":
		return Mapped(MESSENGER)
	if kind == "reaction" and env.get("channel") == "messenger":
		return Mapped(MESSENGER, reaction=True)
	return Mapped(skip=SKIP_NO_CHANNEL_TYPE)  # Instagram, v.v.


def with_channel(env: dict, mapped: Mapped, channel: str) -> Mapped:
	"""Dựng InboundEvent cho loại cần tên kênh (Telephony, Meta Messenger)."""
	if mapped.channel_type == "Telephony":
		return _call(env, channel)
	if mapped.channel_type == MESSENGER:
		return _messenger(env, channel)
	return mapped
