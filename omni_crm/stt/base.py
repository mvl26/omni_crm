"""Interface nhà cung cấp chuyển giọng nói (docs/08 mục 9): `transcribe(audio, mime, *, stereo) -> Transcript`.

Mọi adapter thật phải: đặt `is_real = True`; đặt timeout mỗi lời gọi (mặc định `DEFAULT_TIMEOUT_SECONDS`);
ném `TransientSTTError` cho lỗi tạm thời (429, 5xx, hết thời gian, mất kết nối) và `STTError` cho lỗi còn lại;
đọc khóa từ trường Password (`get_password()`) hoặc biến môi trường, không bao giờ ghi khóa vào log, thông điệp
lỗi hay kết quả. Việc chặn theo Q11 và thử lại có giới hạn nằm ở `omni_crm.stt.client`, adapter không tự làm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_TIMEOUT_SECONDS = 10.0  # CLAUDE.md mục 3: mọi lời gọi ra ngoài có timeout, mặc định 10 giây


class STTError(Exception):
	"""Lỗi chuyển giọng nói không thể thử lại (400/401/403, tệp không hỗ trợ, thiếu khóa, …)."""


class TransientSTTError(STTError):
	"""Lỗi tạm thời đáng thử lại: 429, 5xx, hết thời gian, mất kết nối."""


class STTBlockedError(STTError):
	"""Bị chặn bởi cổng Q11: chưa được phép gửi ghi âm khách thật sang dịch vụ ngoài."""


@dataclass(frozen=True)
class Segment:
	"""Một đoạn lời nói: người nói (nếu nhà cung cấp phân biệt được), thời điểm bắt đầu/kết thúc (giây), nội dung."""

	speaker: str | None
	start: float
	end: float
	text: str


@dataclass(frozen=True)
class Transcript:
	"""Kết quả chuyển giọng nói: toàn văn, các đoạn và độ tin cậy 0..1 (None nếu nhà cung cấp không trả)."""

	text: str
	segments: list[dict] = field(default_factory=list)  # [{speaker, start, end, text}]
	confidence: float | None = None


class STTProvider(Protocol):
	"""Hợp đồng mọi nhà cung cấp chuyển giọng nói phải thực hiện."""

	name: str
	is_real: bool  # True = gửi âm thanh ra dịch vụ ngoài (chịu cổng Q11)

	def transcribe(self, audio: bytes, mime: str, *, stereo: bool) -> Transcript:
		"""Chuyển `audio` (định dạng `mime`) thành văn bản; `stereo` = mỗi bên một kênh, giúp phân biệt người nói."""
		...
