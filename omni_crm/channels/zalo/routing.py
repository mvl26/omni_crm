"""Chọn loại tin Zalo trước khi gửi (V3-07, brief mục B): mọi đường gửi tin Zalo đều phải đi qua `pick_message_kind`.

| purpose      | tình huống                         | loại tin                                  |
|--------------|------------------------------------|-------------------------------------------|
| reply        | sale trả lời khách đang chat       | CS 48h; ngoài 48h cần NGƯỜI xác nhận       |
| followup     | chủ động nhắn khách đã im lặng     | CS 48h nếu còn khung, không thì CS ngoài 48h (qua chốt chặn quỹ) |
| transaction  | đơn hàng, giao hàng, lịch, hợp đồng| CHỈ ZBS Template; không bao giờ tin tư vấn |
| request_info | thẻ xin số điện thoại              | CHỈ CS 48h                                |

Máy không bao giờ tự hạ một tin xuống loại tốn quỹ: ngoài 48 giờ mà chưa có người xác nhận thì ném `NeedsApproval`.
Người xác nhận được ghi vào `approved_by`; quỹ còn dưới 5% thì chỉ Omni Admin, hết quỹ thì từ chối (`quota.gate`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _

from omni_crm.channels.zalo import quota

CS_48H = "CS 48h"
CS_PAID = "CS ngoai 48h"
ZBS = "ZBS Template"
PURPOSES = ("reply", "followup", "transaction", "request_info")
FREE, PAID, BLOCKED = "free", "paid", "blocked"  # khớp adapter.send_window


class RoutingError(frappe.ValidationError):
	"""Không gửi được bằng loại tin nào hợp lệ; `code` cho giao diện, thông điệp tiếng Việt cho người dùng."""

	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code
		self.message_vi = message


class NeedsApproval(RoutingError):
	"""Tin sẽ ăn vào quỹ ngoài 48 giờ: phải có người bấm đồng ý (gửi lại với confirmed=True)."""


@dataclass(frozen=True)
class Route:
	kind: str
	purpose: str
	quota_consumed: bool = False
	approved_by: str | None = None
	zbs_template_id: str | None = None

	def fields(self) -> dict[str, Any]:
		"""Giá trị ghi vào Omni Outbound Message."""
		return {
			"message_kind": self.kind,
			"purpose": self.purpose,
			"quota_consumed": 1 if self.quota_consumed else 0,
			"approved_by": self.approved_by,
			"zbs_template_id": self.zbs_template_id,
		}


def _window(identity_doc: Any, channel_doc: Any) -> str:
	from omni_crm.channels.zalo.adapter import ZaloAdapter

	return ZaloAdapter().send_window(identity_doc, channel_doc)


def approval_prompt() -> str:
	"""Câu hỏi giao diện đưa cho sale khi tin sẽ tiêu quỹ."""
	info = quota.usage()
	return _(
		"Khách đã quá 48 giờ không nhắn: tin này dùng quỹ tin ngoài khung của gói OA (còn {0}/{1} tin). "
		"Gửi bằng quỹ, hay thôi?"
	).format(info.remain, info.total)


def pick_message_kind(
	identity_doc: Any,
	purpose: str,
	*,
	channel_doc: Any,
	confirmed: bool = False,
	user: str | None = None,
	zbs_template_id: str | None = None,
) -> Route:
	"""Loại tin cho lần gửi này, hoặc ném RoutingError / NeedsApproval kèm lý do rõ ràng (không tự hạ loại tin)."""
	if purpose not in PURPOSES:
		raise RoutingError("bad_purpose", _("Mục đích gửi tin không hợp lệ: {0}").format(purpose))
	if purpose == "transaction":
		# Luật cứng: thông báo giao dịch KHÔNG BAO GIỜ đi bằng tin tư vấn (500 tin/năm sẽ cạn trong một tháng).
		if not zbs_template_id:
			raise RoutingError(
				"no_zbs_template",
				_("Thông báo giao dịch phải gửi bằng mẫu ZBS đã duyệt; chưa có mẫu nên không gửi."),
			)
		raise RoutingError(
			"zbs_not_ready",
			_(
				"Gửi tin ZBS Template chưa được bật trên hệ thống (chưa xác minh liên kết OA, ứng dụng và tài khoản ZBS)."
			),
		)
	window = _window(identity_doc, channel_doc)
	if window == FREE:
		return Route(CS_48H, purpose)
	if window == BLOCKED or purpose == "request_info":
		raise RoutingError(
			"out_of_window",
			_("Khách đã quá 48 giờ không nhắn: không gửi được loại tin này. Hãy đợi khách nhắn trước."),
		)
	# reply / followup ngoài 48 giờ: chỉ người, có xác nhận, qua chốt chặn quỹ
	if not user or user == "Guest":
		raise RoutingError("needs_human", _("Tin ngoài khung 48 giờ chỉ gửi khi có người xác nhận."))
	if not confirmed:
		raise NeedsApproval("confirm_quota", approval_prompt())
	blocked = quota.gate(user)
	if blocked:
		raise RoutingError("quota_blocked", blocked)
	return Route(CS_PAID, purpose, quota_consumed=True, approved_by=user)
