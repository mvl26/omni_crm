"""Interface tổng đài chung (docs/07 mục 1): sự kiện cuộc gọi, kết quả tra cứu, hợp đồng của nhà cung cấp."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

INBOUND, OUTBOUND = "Inbound", "Outbound"
# Trạng thái kết thúc: sau sự kiện này cuộc gọi không đổi nữa. "Ringing" và "Answered" (nhấc máy) là sự kiện
# trung gian, trừ khi có `ended_at`.
FINAL_STATUSES = frozenset({"Missed", "Busy", "Failed", "Voicemail", "Ended"})
# Trạng thái lưu trên Omni Call (Select); "Ringing"/"Ended" của nhà cung cấp được quy về các giá trị này.
CALL_STATUSES = ("Answered", "Missed", "Busy", "Failed", "Voicemail")


@dataclass(frozen=True)
class CallEvent:
	"""Một sự kiện cuộc gọi đã chuẩn hóa từ nhà cung cấp."""

	call_id: str
	direction: str  # "Inbound" | "Outbound"
	from_number: str  # đã chuẩn hóa E.164 nếu được
	to_number: str
	status: str  # "Ringing" | "Answered" | "Missed" | "Busy" | "Failed" | "Voicemail" | "Ended"
	started_at: datetime | None = None
	answered_at: datetime | None = None
	ended_at: datetime | None = None
	duration_seconds: int | None = None
	agent_ref: str | None = None  # mã nhân viên/máy nhánh phía nhà cung cấp
	recording_ref: str | None = None
	raw: dict = field(default_factory=dict)

	@property
	def customer_number(self) -> str:
		"""Số của khách: cuộc gọi đến -> số gọi; gọi ra -> số nhận."""
		return self.from_number if self.direction == INBOUND else self.to_number

	@property
	def is_final(self) -> bool:
		"""Sự kiện kết thúc cuộc gọi (có `ended_at` hoặc trạng thái cuối); chỉ loại này tạo tin vào."""
		return self.ended_at is not None or self.status in FINAL_STATUSES

	@property
	def final_status(self) -> str:
		"""Trạng thái lưu trên Omni Call: Ended/Answered -> Answered nếu đã nhấc máy, ngược lại Missed."""
		if self.status in ("Missed", "Busy", "Failed", "Voicemail"):
			return self.status
		return "Answered" if self.answered_at or (self.duration_seconds or 0) > 0 else "Missed"


@dataclass(frozen=True)
class LookupResult:
	"""Kết quả tra cứu khách khi chuông reo."""

	is_new: bool = True  # True = chưa có Lead cho số này
	customer_name: str | None = None
	lead: str | None = None  # tên CRM Lead
	owner: str | None = None  # user phụ trách (email)
	owner_name: str | None = None
	lead_url: str | None = None  # đường dẫn mở Lead trong CRM


class TelephonyProvider(Protocol):
	"""Hợp đồng mọi nhà cung cấp tổng đài phải thực hiện."""

	name: str

	def verify_request(self, raw_body: bytes, headers: dict, query: dict, channel_doc) -> bool:
		"""Xác thực riêng của nhà cung cấp (chữ ký, IP...); token `t` đã được kiểm trước ở adapter."""
		...

	def parse_event(self, raw_body: bytes, headers: dict, channel_doc) -> list[CallEvent]:
		"""Tách body thành các sự kiện cuộc gọi."""
		...

	def lookup_response(self, result: LookupResult, channel_doc) -> dict | str:
		"""Đóng gói kết quả tra cứu đúng định dạng nhà cung cấp chờ."""
		...

	def parse_lookup_request(self, query: dict, raw_body: bytes) -> tuple[str, str]:
		"""(số gọi đến, số nhận) từ yêu cầu tra cứu."""
		...

	def fetch_recording(self, recording_ref: str, channel_doc) -> tuple[bytes, str]:
		"""(nội dung, mime) của ghi âm."""
		...

	def list_calls(self, since: datetime, until: datetime, channel_doc) -> list[CallEvent]:
		"""Nhật ký cuộc gọi trong khoảng, dùng cho đối soát đêm."""
		...

	def click_to_call(self, agent_ref: str, to_number: str, channel_doc) -> str:
		"""Gọi ra từ máy nhánh; để sau, được phép NotImplementedError."""
		...
