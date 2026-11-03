"""Lỗi gọi Graph API của Meta, đã làm sạch (không chứa URL, token hay nội dung phản hồi)."""

from __future__ import annotations

from typing import Any

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


TAM_THOI = "tam_thoi"  # V21-03: loại lỗi lưu trên Omni Inbound Message.error_kind
VINH_VIEN = "vinh_vien"
TRANSIENT_CODES = frozenset({1, 2, 4, 17, 32, 341, 613}) | frozenset(range(80000, 80015))  # docs/12 G-V21-03
TOKEN_CODES = frozenset({10, 102, 190}) | frozenset(range(200, 300))


def to_error(status: int | None, code: int | None) -> MetaError:
	"""Phân loại theo mã HTTP/mã lỗi Graph: 5xx, 429 và mã giới hạn tần suất/tạm thời (TRANSIENT_CODES) là tạm thời."""
	if (status is not None and (status >= 500 or status == 429)) or code in TRANSIENT_CODES:
		return MetaError(TRANSIENT, "Graph API lỗi tạm thời", code)
	if status in (401, 403) or code in TOKEN_CODES:
		return MetaError(TOKEN, "Token Meta không hợp lệ hoặc thiếu quyền leads_retrieval", code)
	return MetaError(PERMANENT, "Graph API từ chối yêu cầu", code)


def _graph_code(payload: Any) -> int | None:
	err = payload.get("error") if isinstance(payload, dict) else None
	code = err.get("code") if isinstance(err, dict) else None
	return code if isinstance(code, int) and not isinstance(code, bool) else None


def classify_meta_error(exc_or_response: Any) -> str:
	"""Phân loại lỗi gọi Meta: "tam_thoi" (thử lại được) hay "vinh_vien" (không kéo lại, cần người).

	Nhận ngoại lệ (MetaError, TransientError, lỗi mạng/timeout, hoặc ngoại lệ bọc chúng qua `__cause__`), phản hồi
	`requests.Response`, hoặc dict JSON `{"error": {"code": ...}}`. Tạm thời: lỗi mạng/timeout, HTTP 5xx/429, mã giới hạn tần suất.
	Vĩnh viễn: token sai/hết hạn, thiếu quyền, lead không tồn tại (mã 100), lỗi dữ liệu và mọi thứ chưa nhận ra (an toàn: có người xem).
	"""
	if isinstance(exc_or_response, BaseException):
		chain, cur = [], exc_or_response
		while cur is not None and cur not in chain:
			chain.append(cur)
			cur = cur.__cause__
		for e in chain:
			if isinstance(e, MetaError):
				return TAM_THOI if e.kind == TRANSIENT else VINH_VIEN
		from omni_crm.pipeline.retry import is_transient

		return TAM_THOI if any(is_transient(e) for e in chain) else VINH_VIEN
	status = getattr(exc_or_response, "status_code", None)
	payload = exc_or_response
	if status is not None:
		try:
			payload = exc_or_response.json()
		except Exception:
			payload = None
	kind = to_error(status if isinstance(status, int) else None, _graph_code(payload)).kind
	return TAM_THOI if kind == TRANSIENT else VINH_VIEN
