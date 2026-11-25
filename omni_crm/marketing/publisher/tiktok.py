"""TikTok: app chưa được TikTok kiểm duyệt (chỉ đăng được chế độ riêng tư), nên chưa đăng. Giữ sẵn interface."""

from __future__ import annotations

from frappe import _

from omni_crm.marketing.publisher.base import Adapter, PublishResult
from omni_crm.marketing.publisher.errors import TerminalError


class TikTokAdapter(Adapter):
	platform = "TikTok"

	def publish(self, payload) -> PublishResult:
		raise TerminalError(_("TikTok chưa được duyệt quyền đăng"))

	def check(self, channel: str) -> dict:
		return {"status": "Chưa kết nối", "message": _("TikTok chưa được duyệt quyền đăng")}
