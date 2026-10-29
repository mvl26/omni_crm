"""Tổng đài giả dùng cho test và dev (docs/07 mục 6). Chỉ nạp khi developer_mode hoặc đang test.

Định dạng payload do ta định nghĩa: một đối tượng JSON (hoặc danh sách) có `call_id`, `direction`
("Inbound"/"Outbound"), `from`, `to`, `status` và tùy chọn `started_at`, `answered_at`, `ended_at` (ISO),
`duration`, `agent_ref`, `recording_ref`. Tra cứu: query `from` và `to`. Xác thực riêng: header
`X-Fake-Sign` = `fake-ok`.
"""

from __future__ import annotations

import json
from datetime import datetime

from frappe.utils import cint, get_datetime

from omni_crm.channels.telephony.base import CallEvent, LookupResult
from omni_crm.identity.normalize import normalize_phone

NAME = "Fake"
SIGN_HEADER = "X-Fake-Sign"
SIGN_VALUE = "fake-ok"
# WAV PCM 8 kHz mono, 0,1 giây im lặng: tệp mẫu nhỏ cho fetch_recording.
SAMPLE_AUDIO = (
	b"RIFF\x24\x06\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x40\x1f\x00\x00\x01\x00\x08\x00"
	b"data\x00\x06\x00\x00" + b"\x80" * 1536
)


def _dt(value) -> datetime | None:
	return get_datetime(value) if value else None


def _event(d: dict) -> CallEvent:
	return CallEvent(
		call_id=str(d["call_id"]),
		direction=d.get("direction") or "Inbound",
		from_number=normalize_phone(d.get("from")) or str(d.get("from") or ""),
		to_number=normalize_phone(d.get("to")) or str(d.get("to") or ""),
		status=d.get("status") or "Ended",
		started_at=_dt(d.get("started_at")),
		answered_at=_dt(d.get("answered_at")),
		ended_at=_dt(d.get("ended_at")),
		duration_seconds=cint(d["duration"]) if d.get("duration") is not None else None,
		agent_ref=d.get("agent_ref") or None,
		recording_ref=d.get("recording_ref") or None,
		raw=d,
	)


class FakeProvider:
	"""Nhà cung cấp giả: không gọi mạng, `calls` là danh sách `list_calls` cấu hình được."""

	name = NAME

	def __init__(self, calls: list[CallEvent] | None = None):
		self.calls: list[CallEvent] = list(calls or [])

	def verify_request(self, raw_body: bytes, headers: dict, query: dict, channel_doc) -> bool:
		"""Header X-Fake-Sign phải bằng giá trị mẫu."""
		lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
		return lowered.get(SIGN_HEADER.lower()) == SIGN_VALUE

	def parse_event(self, raw_body: bytes, headers: dict, channel_doc) -> list[CallEvent]:
		"""Một đối tượng JSON hoặc danh sách; lỗi định dạng ném ValueError/KeyError (cổng lưu bản unparsed)."""
		data = json.loads(raw_body)
		items = data if isinstance(data, list) else [data]
		return [_event(d) for d in items if isinstance(d, dict)]

	def lookup_response(self, result: LookupResult, channel_doc) -> dict | str:
		"""JSON đơn giản; khách mới thì `found` = False."""
		if result.is_new or not result.lead:
			return {"found": False, "customer_name": "Khách mới"}
		return {
			"found": True,
			"customer_name": result.customer_name,
			"lead": result.lead,
			"owner": result.owner,
			"owner_name": result.owner_name,
			"lead_url": result.lead_url,
		}

	def parse_lookup_request(self, query: dict, raw_body: bytes) -> tuple[str, str]:
		"""(from, to) trong query."""
		return str(query.get("from") or ""), str(query.get("to") or "")

	def fetch_recording(self, recording_ref: str, channel_doc) -> tuple[bytes, str]:
		"""Tệp âm thanh mẫu nhỏ."""
		return SAMPLE_AUDIO, "audio/wav"

	def list_calls(self, since: datetime, until: datetime, channel_doc) -> list[CallEvent]:
		"""Các cuộc gọi cấu hình sẵn có thời điểm bắt đầu/kết thúc rơi trong khoảng."""
		out = []
		for c in self.calls:
			at = c.ended_at or c.started_at
			if at is None or since <= at < until:
				out.append(c)
		return out

	def click_to_call(self, agent_ref: str, to_number: str, channel_doc) -> str:
		"""Chưa hỗ trợ."""
		raise NotImplementedError
