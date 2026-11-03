"""Lỗi gọi Graph API của Meta, đã làm sạch (không chứa URL, token hay nội dung phản hồi)."""

from __future__ import annotations

TRANSIENT = "transient"
PERMANENT = "permanent"
TOKEN = "token"

# Ghi chú kết quả kiểm token Page (V21-02) khi KHÔNG có vấn đề; màn giám sát coi ghi chú khác là cần chú ý
TOKEN_NOTE_OK = "Còn hạn, đủ quyền"
TOKEN_NOTE_NEVER = "Token không hết hạn, đủ quyền"


class MetaError(Exception):
	"""Lỗi Graph API; `kind` ∈ transient (thử lại được), token (token hết hạn/thiếu quyền), permanent."""

	def __init__(self, kind: str, message: str, code: int | None = None):
		super().__init__(message)
		self.kind = kind
		self.code = code


def to_error(status: int | None, code: int | None) -> MetaError:
	"""Phân loại theo mã HTTP/mã lỗi Graph: 5xx, 429 và mã 1/2/4/17/32/613 (tạm thời, giới hạn tần suất) là tạm thời."""
	if (status is not None and (status >= 500 or status == 429)) or code in (1, 2, 4, 17, 32, 341, 613):
		return MetaError(TRANSIENT, "Graph API lỗi tạm thời", code)
	if status in (401, 403) or code in (10, 190, 200, 102):
		return MetaError(TOKEN, "Token Meta không hợp lệ hoặc thiếu quyền leads_retrieval", code)
	return MetaError(PERMANENT, "Graph API từ chối yêu cầu", code)
