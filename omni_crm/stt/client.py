"""Lời gọi chuyển giọng nói qua một cửa: cổng Q11, thử lại có giới hạn, lỗi không chứa bí mật hay nội dung khách.

Cổng Q11 giống `bot/ai_client.py`: nhà cung cấp `is_real` chỉ nhận âm thanh khi site_config
`omni_ai_allow_customer_data` bật (chủ dự án đã chốt Q11) hoặc ghi âm được gắn nhãn tổng hợp (`synthetic=True`,
không có thông tin khách). Nhà cung cấp giả không chịu cổng vì không gửi gì ra ngoài.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.pipeline.retry import redact
from omni_crm.stt.base import STTBlockedError, STTError, STTProvider, Transcript, TransientSTTError

ALLOW_DATA_CONF = "omni_ai_allow_customer_data"  # cùng cờ với bot/ai_client.py
TRANSIENT_RETRIES = 2
BACKOFF_SECONDS = (2, 6)
ERROR_MAX_CHARS = 300


def check_gate(provider: STTProvider, synthetic: bool = False) -> None:
	"""Ném STTBlockedError nếu gửi âm thanh tới nhà cung cấp thật khi Q11 chưa cho phép và dữ liệu không phải tổng hợp."""
	if provider.is_real and not synthetic and not cint(frappe.conf.get(ALLOW_DATA_CONF)):
		raise STTBlockedError(_("Chính sách gửi dữ liệu khách sang dịch vụ ngoài (Q11) chưa được chốt"))


def transcribe(
	provider: STTProvider,
	audio: bytes,
	mime: str,
	*,
	stereo: bool = False,
	synthetic: bool = False,
	sleep: Callable[[float], None] = time.sleep,
) -> Transcript:
	"""Chuyển giọng nói qua cổng Q11; thử lại tối đa 2 lần khi lỗi tạm thời; lỗi cuối được che bí mật và cắt ngắn."""
	check_gate(provider, synthetic)
	attempt = 0
	while True:
		try:
			return provider.transcribe(audio, mime, stereo=stereo)
		except TransientSTTError as e:
			if attempt >= TRANSIENT_RETRIES:
				raise STTError(redact(str(e))[:ERROR_MAX_CHARS]) from None
			sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
			attempt += 1
		except STTError as e:
			raise STTError(redact(str(e))[:ERROR_MAX_CHARS]) from None
