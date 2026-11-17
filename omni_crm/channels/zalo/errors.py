"""Ánh xạ mã lỗi Zalo -> (loại, thông điệp tiếng Việt).

Các mã dưới đây CHƯA ĐƯỢC XÁC NHẬN với bảng mã lỗi chính thức (không đọc được developers.zalo.me); nguồn là
README SDK bên thứ ba và ghi chú cộng đồng, xem docs/12 mục B. Mã lạ được xử lý như lỗi vĩnh viễn kèm mã gốc
để người vận hành tra cứu; bảng có thể bổ sung mà không đổi logic gọi.
"""

from __future__ import annotations

from frappe import _

TRANSIENT = "transient"  # thử lại sau được (quá hạn mức, máy chủ Zalo lỗi)
PERMANENT = "permanent"  # gửi lại cũng vô ích (người dùng chặn, tham số sai)
TOKEN = "token"  # access token hết hạn/không hợp lệ: làm mới rồi thử lại một lần
WINDOW = "window"  # ngoài khung thời gian được gửi tin tư vấn

# mã -> (loại, thông điệp gốc tiếng Việt; bọc _() khi tra)
_TABLE: dict[int, tuple[str, str]] = {
	-216: (TOKEN, "Access token Zalo không hợp lệ hoặc đã hết hạn"),
	-124: (TOKEN, "Access token Zalo hết hạn"),
	-223: (TRANSIENT, "Vượt hạn mức gọi API của Zalo, thử lại sau"),
	-201: (PERMANENT, "Tham số gọi API Zalo không hợp lệ"),
	-102: (PERMANENT, "Thiếu tham số bắt buộc khi gọi API Zalo"),
	-108: (PERMANENT, "Mã tệp gửi Zalo không hợp lệ"),
	-235: (PERMANENT, "Loại Official Account không hỗ trợ thao tác này"),
	-224: (
		PERMANENT,
		"Gói OA hiện tại không hỗ trợ gửi tin qua API; hãy nhắn từ trang Zalo OA (oa.zalo.me/chat)",
	),
	-213: (PERMANENT, "Người dùng chưa quan tâm hoặc đã bỏ quan tâm OA"),
	-230: (WINDOW, "Quá khung thời gian được gửi tin tư vấn cho người dùng này"),
	-233: (WINDOW, "Quá khung thời gian được gửi tin tư vấn cho người dùng này"),
}


class ZaloError(Exception):
	"""Lỗi từ API Zalo; `kind` quyết định cách xử lý, `message_vi` an toàn để hiện cho người dùng."""

	def __init__(self, code: int | None, kind: str, message_vi: str, http_status: int | None = None):
		super().__init__(f"{kind}:{code}:{message_vi}")
		self.code = code
		self.kind = kind
		self.message_vi = message_vi
		self.http_status = http_status
		self.sent_ids: list[str] = []  # tin đã gửi được trước khi lỗi (khi tách tin dài)
		self.raw_message = ""  # câu báo lỗi nguyên văn của Zalo (để chẩn đoán; không hiện cho khách)


def classify(code: int | None, http_status: int | None = None) -> tuple[str, str]:
	"""(loại, thông điệp tiếng Việt) cho mã lỗi Zalo hoặc mã HTTP; không bao giờ ném lỗi."""
	if code in _TABLE:
		kind, msg = _TABLE[code]
		return kind, _(msg)
	if http_status and http_status >= 500:
		return TRANSIENT, _("Máy chủ Zalo đang lỗi (HTTP {0}), thử lại sau").format(http_status)
	if http_status == 429:
		return TRANSIENT, _("Vượt hạn mức gọi API của Zalo, thử lại sau")
	if code is None:
		return PERMANENT, _("Phản hồi của Zalo không đọc được")
	return PERMANENT, _("Zalo trả lỗi mã {0}").format(code)


def to_error(code: int | None, http_status: int | None = None, raw_message: str = "") -> ZaloError:
	"""Dựng ZaloError từ mã lỗi/HTTP; giữ câu báo lỗi gốc của Zalo (cắt 300 ký tự)."""
	kind, msg = classify(code, http_status)
	err = ZaloError(code, kind, msg, http_status)
	err.raw_message = str(raw_message or "")[:300]
	return err
