"""Kiểu dữ liệu sự kiện vào/ra dùng chung giữa các lớp."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class InboundEvent:
	"""Một sự kiện vào đã chuẩn hóa từ bất kỳ kênh nào."""

	channel: str  # tên Omni Channel
	channel_type: str  # "Email" | "Zalo OA" | "Telephony"
	event_type: str  # "message" | "follow" | "unfollow" | "user_info" | "call_ended" | ...
	external_message_id: str  # mã duy nhất của sự kiện trên kênh
	external_sender_id: str  # Zalo user id, email người gửi, SĐT gọi đến
	received_at: datetime  # thời điểm kênh ghi nhận (giờ hệ thống nếu kênh không có)
	text: str | None = None  # nội dung đã làm sạch
	attachments: list[dict] = field(default_factory=list)  # [{"url"|"file", "name", "mime"}]
	sender_profile: dict = field(default_factory=dict)  # tên hiển thị, SĐT/email nếu có
	raw: dict = field(default_factory=dict)  # payload gốc, lưu nguyên văn
	communication: str | None = (
		None  # tên Communication (kênh Email); lưu riêng vì raw_payload bị xóa theo hạn
	)


@dataclass(frozen=True)
class OutboundRequest:
	"""Yêu cầu gửi một tin ra khách qua adapter của kênh."""

	channel: str
	identity: str  # tên Omni Identity
	text: str
	reply_to_external_id: str | None
	source: str  # "Sale" | "Bot" | "System"
	bot_draft: str | None = None  # Omni Bot Draft nếu tin xuất phát từ nháp của bot (Đợt 3)
	purpose: str = "reply"  # V3-07: reply | followup | transaction | request_info (routing.pick_message_kind)
	route: object | None = None  # V3-07: routing.Route đã chọn (người gọi đã qua pick_message_kind)
	meta: dict = field(
		default_factory=dict
	)  # adapter ghi kết quả phụ của lần gửi (vd quota_type Zalo trả về)
	attachment: dict | None = (
		None  # V3-09: {"file_url", "kind" (image|gif|file), "name"}; tệp của Lead trong CRM
	)


def stable_message_id(*parts: object) -> str:
	"""Mã tin ổn định (SHA-256) từ các phần, dùng cho kênh không có mã tin riêng."""
	joined = "\x1f".join("" if p is None else str(p) for p in parts)
	return hashlib.sha256(joined.encode("utf-8")).hexdigest()
