"""Ba loại lỗi khi đăng bài. Thông điệp tiếng Việt, đã làm sạch (không token, không URL kèm tham số)."""

from __future__ import annotations

VALIDATION, PROVIDER, TERMINAL = "validation", "provider", "terminal"


class PublishError(Exception):
	kind = PROVIDER

	def __init__(self, message: str, *, http_status: int | None = None, ambiguous: bool = False):
		super().__init__(message)
		self.message = message
		self.http_status = http_status
		self.ambiguous = (
			ambiguous  # hết giờ chờ: không rõ nền tảng đã nhận bài chưa -> phải đối chiếu trước khi thử lại
		)


class PublishValidationError(PublishError):
	"""Dữ liệu bài sai (caption rỗng, thiếu ảnh bìa, tiêu đề quá dài, ảnh không công khai): không thử lại."""

	kind = VALIDATION


class ProviderError(PublishError):
	"""Lỗi tạm (mạng, 5xx, giới hạn tần suất): thử lại sau 1, 5, 15 phút."""

	kind = PROVIDER


class TerminalError(PublishError):
	"""Token hết hạn, thiếu quyền, nền tảng chưa duyệt: không thử lại, kênh -> Cần kết nối lại."""

	kind = TERMINAL
