"""Nhận diện thư tự động, thư hệ thống và thư báo lỗi gửi (docs/05 mục 2 bước 2)."""

from __future__ import annotations

import re

from omni_crm.identity.normalize import is_system_email

_BOUNCE_RE = re.compile(
	r"(undeliver|delivery status notification|mail delivery (failed|subsystem)|returned mail|"
	r"failure notice|delivery (has )?failed|delivery failure|không thể gửi|gửi thư thất bại|"
	r"thư không gửi được)",
	re.I,
)
_AUTO_REPLY_RE = re.compile(
	r"^\s*(auto(matic)?[\s\-]?(reply|response)|autoreply|out of (the )?office|tự động trả lời|"
	r"trả lời tự động|thư tự động|vắng mặt)",
	re.I,
)
_BULK_PRECEDENCE = {"bulk", "list", "junk"}


def _header(headers: dict | None, name: str) -> str:
	"""Giá trị header không phân biệt hoa thường; rỗng nếu không có."""
	for key, value in (headers or {}).items():
		if str(key).lower() == name.lower():
			return str(value or "").strip()
	return ""


def ignore_reason(
	sender: str | None,
	subject: str | None,
	headers: dict | None = None,
	system_emails: list[str] | tuple[str, ...] = (),
) -> str | None:
	"""Lý do bỏ qua thư (không tạo Lead): "bounce", "auto_reply", "bulk", "system_sender"; None nếu là thư người.

	`headers` (nếu có) kiểm tra Auto-Submitted, Precedence, List-Unsubscribe. Communication của Frappe không
	lưu header gốc nên hook chỉ dựa vào người gửi và tiêu đề (xem docs/12 mục D).
	"""
	subj = str(subject or "")
	if _BOUNCE_RE.search(subj) or _header(headers, "X-Failed-Recipients"):
		return "bounce"
	auto_submitted = _header(headers, "Auto-Submitted").lower()
	if (auto_submitted and auto_submitted != "no") or _AUTO_REPLY_RE.search(subj):
		return "auto_reply"
	if _header(headers, "Precedence").lower() in _BULK_PRECEDENCE or _header(headers, "List-Unsubscribe"):
		return "bulk"
	if is_system_email(sender, system_emails):
		return "system_sender"
	return None
