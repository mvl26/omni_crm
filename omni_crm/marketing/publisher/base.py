"""Interface chung của adapter đăng bài: publish / reconcile / fetch_permalink / fetch_insights / check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omni_crm.marketing.publisher.content import Payload


@dataclass
class PublishResult:
	external_post_id: str
	permalink: str = ""


class Adapter:
	platform = ""

	def publish(self, payload: Payload) -> PublishResult:
		raise NotImplementedError

	def reconcile(self, payload: Payload, since_minutes: int = 30) -> PublishResult | None:
		"""Tìm bài vừa đăng có chứa mã theo dõi của lần đăng này; thấy thì trả, không thì None."""
		return None

	def fetch_permalink(self, payload: Payload, external_post_id: str) -> str:
		return ""

	def fetch_insights(self, channel: str, external_post_id: str) -> dict[str, int | None]:
		return {}

	def check(self, channel: str) -> dict[str, Any]:
		from omni_crm.marketing import channels

		return channels.check(channel)


def get_adapter(platform: str) -> Adapter:
	from omni_crm.marketing import rules
	from omni_crm.marketing.publisher import facebook, tiktok, zalo

	return {
		rules.FACEBOOK: facebook.FacebookAdapter,
		rules.ZALO: zalo.ZaloAdapter,
		rules.TIKTOK: tiktok.TikTokAdapter,
	}[platform]()
