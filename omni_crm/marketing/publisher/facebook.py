"""Đăng bài lên Trang Facebook qua Graph API (Pages API).

Đã đối chiếu tài liệu Meta (developers.facebook.com, đọc 2026-11-13):
- fb_link: `POST /{page_id}/feed` với `message`, `link`; trả `id` (page post id).
- fb_photo: `POST /{page_id}/photos` với `url` (ảnh công khai) và `caption` (`message` đã cũ); trả `id`, `post_id`.
- fb_multi_photo: tải từng ảnh `POST /{page_id}/photos` `published=false`, rồi `POST /{page_id}/feed` với `message` và
  `attached_media[n]={"media_fbid": "<photo id>"}`.
- Link bài: `GET /{post_id}?fields=permalink_url`. Quyền: pages_manage_posts, pages_read_engagement (Page token).
Không dùng hẹn giờ của Facebook (`scheduled_publish_time`): CRM tự giữ lịch.
Token đọc từ Omni Channel (meta_page_access_token) bằng get_password, gửi trong header, không vào log.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import frappe
import requests
from frappe import _

from omni_crm.marketing.publisher.base import Adapter, PublishResult
from omni_crm.marketing.publisher.content import Payload
from omni_crm.marketing.publisher.errors import (
	ProviderError,
	PublishValidationError,
	TerminalError,
)

API_BASE = "https://graph.facebook.com"
TIMEOUT = 10  # giây (CLAUDE.md mục 3); thử lại do bộ lập lịch lo (1, 5, 15 phút)
TRANSIENT_CODES = frozenset({1, 2, 4, 17, 32, 341, 368, 613})
TOKEN_CODES = frozenset({10, 102, 190}) | frozenset(range(200, 300))


def graph_version() -> str:
	from omni_crm.channels.meta_leads.client import graph_version as meta_version
	from omni_crm.marketing import settings

	return str(settings.get("graph_api_version") or "").strip() or meta_version()


def classify(status: int | None, body: Any) -> Exception:
	"""Phân loại lỗi Graph: 5xx/429/mã tạm -> ProviderError; 401/403/token/quyền -> TerminalError; 100 -> dữ liệu sai."""
	err = body.get("error") if isinstance(body, dict) else None
	code = err.get("code") if isinstance(err, dict) and isinstance(err.get("code"), int) else None
	label = f"HTTP {status}" + (f", mã {code}" if code is not None else "")
	if (status is not None and (status >= 500 or status == 429)) or code in TRANSIENT_CODES:
		return ProviderError(_("Facebook lỗi tạm thời ({0})").format(label), http_status=status)
	if status in (401, 403) or code in TOKEN_CODES:
		return TerminalError(
			_("Facebook từ chối: mã truy cập Trang hết hạn hoặc thiếu quyền ({0})").format(label),
			http_status=status,
		)
	if code == 100:
		return PublishValidationError(
			_("Facebook báo dữ liệu bài không hợp lệ ({0})").format(label), http_status=status
		)
	return TerminalError(_("Facebook từ chối bài ({0})").format(label), http_status=status)


class FacebookAdapter(Adapter):
	platform = "Facebook"

	# --- nền ---
	def _page(self, channel: str) -> tuple[str, str]:
		doc = frappe.get_doc("Omni Channel", channel)
		page_id = (doc.get("meta_page_id") or "").strip()
		token = doc.get_password("meta_page_access_token", raise_exception=False)
		if not (page_id and token):
			raise TerminalError(_("Kênh Facebook chưa có Page ID hoặc mã truy cập Trang"))
		if not page_id.isdigit():
			raise TerminalError(_("Page ID không hợp lệ"))
		return page_id, token

	def _call(
		self, method: str, path: str, token: str, data: dict | None = None, params: dict | None = None
	) -> dict:
		url = f"{API_BASE}/{graph_version()}/{path.lstrip('/')}"
		try:
			resp = requests.request(
				method,
				url,
				data=data,
				params=params,
				headers={"Authorization": f"Bearer {token}"},
				timeout=TIMEOUT,
			)
		except requests.Timeout:
			raise ProviderError(_("Facebook không trả lời kịp (hết giờ chờ)"), ambiguous=True) from None
		except requests.RequestException:
			raise ProviderError(_("Không kết nối được Facebook")) from None
		try:
			body: Any = resp.json()
		except ValueError:
			body = None
		if resp.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
			raise classify(resp.status_code, body)
		return body if isinstance(body, dict) else {}

	# --- interface ---
	def publish(self, payload: Payload) -> PublishResult:
		page_id, token = self._page(payload.channel)
		message = payload.message()
		if payload.post_format == "fb_link":
			body = self._call(
				"POST", f"{page_id}/feed", token, {"message": message, "link": payload.tracking_url}
			)
			post_id = str(body.get("id") or "")
		elif payload.post_format == "fb_photo":
			body = self._call(
				"POST", f"{page_id}/photos", token, {"url": payload.images[0], "caption": message}
			)
			post_id = str(body.get("post_id") or body.get("id") or "")
		elif payload.post_format == "fb_multi_photo":
			media = []
			for url in payload.images:
				photo = self._call("POST", f"{page_id}/photos", token, {"url": url, "published": "false"})
				if not photo.get("id"):
					raise ProviderError(_("Facebook không trả mã ảnh"))
				media.append(str(photo["id"]))
			data = {"message": message}
			for i, fbid in enumerate(media):
				data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fbid})
			body = self._call("POST", f"{page_id}/feed", token, data)
			post_id = str(body.get("id") or "")
		else:
			raise PublishValidationError(
				_("Định dạng {0} không dùng cho Facebook").format(payload.post_format)
			)
		if not post_id:
			raise ProviderError(_("Facebook không trả mã bài"), ambiguous=True)
		return PublishResult(external_post_id=post_id)

	def reconcile(self, payload: Payload, since_minutes: int = 30) -> PublishResult | None:
		page_id, token = self._page(payload.channel)
		since = int((datetime.now() - timedelta(minutes=since_minutes)).timestamp())
		body = self._call(
			"GET",
			f"{page_id}/posts",
			token,
			params={"fields": "id,message,permalink_url,created_time", "since": since, "limit": 25},
		)
		marker = payload.marker
		for post in body.get("data") or []:
			if isinstance(post, dict) and marker and marker in str(post.get("message") or ""):
				return PublishResult(str(post.get("id") or ""), str(post.get("permalink_url") or ""))
		return None

	def fetch_permalink(self, payload: Payload, external_post_id: str) -> str:
		_page_id, token = self._page(payload.channel)
		try:
			body = self._call("GET", external_post_id, token, params={"fields": "permalink_url"})
		except Exception:
			return ""
		return str(body.get("permalink_url") or "")
