"""Nhà cung cấp chuyển giọng nói giả cho test và dev: không gọi mạng, không dùng âm thanh thật.

Trả văn bản đã định sẵn theo nội dung tệp (khóa là bytes của tệp), hoặc `default_text`. Chỉ nạp được khi
`developer_mode` bật hoặc đang chạy test (xem `omni_crm.stt.registry`).
"""

from __future__ import annotations

from omni_crm.stt.base import STTError, Transcript, TransientSTTError

NAME = "Fake"


class FakeSTTProvider:
	"""Nhà cung cấp giả: kịch bản `transcripts` (bytes -> văn bản), lỗi giả lập qua `faults` (dùng lần lượt)."""

	name = NAME
	is_real = False

	def __init__(
		self,
		transcripts: dict[bytes, str] | None = None,
		default_text: str = "xin chào tôi muốn hỏi giá găng tay",
		confidence: float | None = 0.9,
		faults: list[str] | None = None,
		name: str = NAME,
	):
		self.transcripts = dict(transcripts or {})
		self.default_text = default_text
		self.confidence = confidence
		self.faults = list(faults or [])  # "transient" | "fatal"
		self.name = name
		self.calls: list[dict] = []

	def transcribe(self, audio: bytes, mime: str, *, stereo: bool) -> Transcript:
		"""Trả bản chuẩn có sẵn cho `audio` (hoặc văn bản mặc định); ném lỗi giả lập nếu còn trong `faults`."""
		self.calls.append({"size": len(audio), "mime": mime, "stereo": stereo})
		fault = self.faults.pop(0) if self.faults else None
		if fault == "transient":
			raise TransientSTTError("lỗi tạm thời (giả)")
		if fault == "fatal":
			raise STTError("lỗi không thể thử lại (giả)")
		text = self.transcripts.get(audio, self.default_text)
		words = text.split()
		segments = (
			[{"speaker": "1", "start": 0.0, "end": float(len(words)) * 0.4, "text": text}] if words else []
		)
		return Transcript(text=text, segments=segments, confidence=self.confidence)
