"""Chuyển một file JSON của miyano-intake (schema "1") thành InboundEvent của cổng tiếp nhận.

Hàm thuần: không đọc DB, không ghi gì. Nơi lưu JSON gốc là `Omni Inbound Message.raw_payload` (khóa `intake`),
nên không cần DocType mới. Chống trùng dùng đúng `dedupe_key` của cổng (`{kênh}:{external_message_id}`), chọn
`external_message_id` trùng với đường nhận trực tiếp để một tin đi cả hai đường vẫn chỉ vào một lần:
- email: Message-ID của thư (giống hook Communication);
- cuộc gọi: call_id;
- form landing: sha1(event_key) làm submission_id (giống relay của miyano-intake).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from omni_crm.channels.email.adapter import DATA_LEN, external_message_id
from omni_crm.channels.email.clean import clean_email
from omni_crm.channels.telephony.base import INBOUND, OUTBOUND, CallEvent
from omni_crm.channels.telephony.calls import serialize_event
from omni_crm.identity.extract import extract_contacts
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
SKIP_NO_CHANNEL_TYPE = "kenh_chua_ho_tro"  # ví dụ Messenger: CRM chưa có loại kênh (cần ADR)
SKIP_LIVE_PATH = "da_co_duong_truc_tiep"  # lead Meta chạy qua webhook meta_leads, không nạp từ file
SKIP_EMPTY = "khong_co_du_lieu"


@dataclass(frozen=True)
class Mapped:
	"""Kết quả ánh xạ: `kind_channel` là loại Omni Channel cần có; event=None kèm `skip` là lý do bỏ qua."""

	channel_type: str | None = None
	event: InboundEvent | None = None
	body: dict | None = (
		None  # chỉ Web Form: body JSON để WebFormAdapter.parse đọc (bao gồm cả honeypot, giới hạn độ dài)
	)
	skip: str | None = None


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


def _email(env: dict, channel: str) -> Mapped:
	ident = env.get("identifiers") or {}
	sender = (env.get("sender") or {}).get("value") or ident.get("email") or ""
	sender = sender.strip().lower()[:DATA_LEN]
	raw = env.get("raw") or {}
	key = str(env.get("event_key") or "")
	mid = key.split(":", 1)[1] if key.startswith("gmail:") else key
	reasons = env.get("ignore_reasons") or []
	body, signature = clean_email((env.get("content") or {}).get("text"))
	profile: dict = {}
	name = (env.get("sender") or {}).get("display_name")
	if name and "@" not in name:
		profile["display_name"] = name
	# Gmail đặt địa chỉ khách ở Reply-To khi form website gửi từ địa chỉ công ty: `identifiers.email` là địa chỉ hiệu lực
	if ident.get("email") and ident["email"].lower() != sender:
		profile["reply_to_email"] = ident["email"].lower()
	found = extract_contacts(signature)
	if found["phones"]:
		profile["signature_phones"] = found["phones"]
	if found["emails"]:
		profile["signature_emails"] = found["emails"]
	atts = [
		{
			"file": a.get("path") or a.get("file"),
			"name": a.get("filename") or a.get("name"),
			"mime": a.get("mime_type"),
		}
		for a in (env.get("content") or {}).get("attachments") or []
		if isinstance(a, dict)
	]
	event = InboundEvent(
		channel=channel,
		channel_type="Email",
		event_type="ignored" if (env.get("ignored") or reasons) else "message",
		external_message_id=external_message_id(channel, mid, key),
		external_sender_id=(ident.get("email") or sender)[:DATA_LEN].lower(),
		received_at=_dt(env.get("occurred_at")) or _dt(env.get("received_at")) or datetime.now(),
		text=(body or "")[:MAX_TEXT],
		attachments=atts,
		sender_profile=profile,
		raw={
			"subject": raw.get("subject"),
			"in_reply_to": None,
			"ignored": ", ".join(reasons) or None,
			"intake": env,
		},
	)
	return Mapped("Email", event)


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
	return Mapped(skip=SKIP_NO_CHANNEL_TYPE)  # message (Messenger), v.v.


def with_channel(env: dict, mapped: Mapped, channel: str) -> Mapped:
	"""Dựng InboundEvent cho các loại cần tên kênh (Email, Telephony)."""
	if mapped.channel_type == "Email":
		return _email(env, channel)
	if mapped.channel_type == "Telephony":
		return _call(env, channel)
	return mapped
