"""Khối "Liên kết" trên form chiến dịch: các bài đăng của chiến dịch (ADR 0029)."""

from frappe import _


def get_data() -> dict:
	"""Bài đăng gắn với chiến dịch qua trường `campaign`."""
	return {
		"fieldname": "campaign",
		"transactions": [{"label": _("Bài đăng"), "items": ["Omni Content Post"]}],
	}
