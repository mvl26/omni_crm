"""Đăng Bài viết OA (loại thường) lên Zalo OA. Dùng ZaloClient: token của kênh chat, tự làm mới bằng cơ chế khóa sẵn có.

VERIFY (chưa đọc được tài liệu chính thức developers.zalo.me, theo SDK cộng đồng):
- `POST /v2.0/article/create` {type:"normal", title<=150, author<=50, description<=300,
  cover:{cover_type:"photo", photo_url, status:"show"}, body:[{type:"text", content}, {type:"image", url}],
  tracking_link, status:"show", comment:"show"} -> data.token
- `POST /v2.0/article/verify` {token} -> data.article_id khi xử lý xong (đang xử lý thì chưa có)
- `GET /v2.0/article/getslice?offset=0&limit=10&type=normal` -> data.medias[] (id, title, create_date...)
Token cần quyền quản lý bài viết của OA.
"""

from __future__ import annotations

import time

from frappe import _

from omni_crm.channels.zalo import config
from omni_crm.channels.zalo.errors import TOKEN, TRANSIENT, ZaloError
from omni_crm.marketing.publisher.base import Adapter, PublishResult
from omni_crm.marketing.publisher.content import ZALO_DESC_MAX, Payload
from omni_crm.marketing.publisher.errors import ProviderError, PublishValidationError, TerminalError

AUTHOR = "Miyano"
POLL_SECONDS = 5
POLL_MAX_SECONDS = 120  # ~2 phút; quá thì ProviderError (lần sau đối chiếu trước khi đăng lại)


def _client(channel: str):
	from omni_crm.channels.zalo.client import ZaloClient

	return ZaloClient(channel)


def to_publish_error(e: ZaloError) -> Exception:
	if e.kind == TRANSIENT:
		return ProviderError(
			_("Zalo lỗi tạm thời: {0}").format(e.message_vi), http_status=e.http_status, ambiguous=True
		)
	if e.kind == TOKEN:
		return TerminalError(
			_("Zalo từ chối mã truy cập OA: {0}").format(e.message_vi), http_status=e.http_status
		)
	if e.code in (-201, -102):
		return PublishValidationError(_("Zalo báo dữ liệu bài không hợp lệ: {0}").format(e.message_vi))
	return TerminalError(_("Zalo từ chối bài: {0}").format(e.message_vi), http_status=e.http_status)


def article_body(p: Payload) -> dict:
	"""Yêu cầu tạo bài viết OA (thuần). Link theo dõi nằm trong nội dung và tracking_link (để đối chiếu)."""
	text = p.message()
	body = [{"type": "text", "content": text}]
	body += [{"type": "image", "url": u} for u in p.images[1:]]
	desc = (p.caption or "").strip().replace("\n", " ")
	if len(desc) > ZALO_DESC_MAX:
		desc = desc[: ZALO_DESC_MAX - 1].rstrip() + "…"
	return {
		"type": "normal",
		"title": p.title,
		"author": AUTHOR,
		"description": desc,
		"cover": {"cover_type": "photo", "photo_url": p.images[0], "status": "show"},
		"body": body,
		"tracking_link": p.tracking_url,
		"status": "show",
		"comment": "show",
	}


class ZaloAdapter(Adapter):
	platform = "Zalo OA"

	def publish(self, payload: Payload, *, sleep=time.sleep) -> PublishResult:
		if not payload.channel:
			raise TerminalError(_("Chưa chọn kênh Zalo OA"))
		client = _client(payload.channel)
		try:
			created = client._request("POST", config.get("article_create_path"), body=article_body(payload))
		except ZaloError as e:
			raise to_publish_error(e) from None
		token = str((created or {}).get("token") or "")
		if not token:
			raise ProviderError(_("Zalo không trả mã xử lý bài viết"), ambiguous=True)
		waited = 0
		while True:
			try:
				data = client._request("POST", config.get("article_verify_path"), body={"token": token}) or {}
			except ZaloError as e:
				raise to_publish_error(e) from None
			article_id = str(data.get("article_id") or data.get("id") or "")
			if article_id:
				return PublishResult(external_post_id=article_id)
			if str(data.get("status") or "").lower() == "failed":
				raise PublishValidationError(
					_("Zalo không duyệt bài viết: {0}").format(str(data.get("error_message") or "")[:200])
				)
			if waited >= POLL_MAX_SECONDS:
				raise ProviderError(_("Zalo xử lý bài viết quá 2 phút"), ambiguous=True)
			sleep(POLL_SECONDS)
			waited += POLL_SECONDS

	def reconcile(self, payload: Payload, since_minutes: int = 30) -> PublishResult | None:
		"""Bài viết gần nhất cùng tiêu đề (danh sách bài viết không trả nội dung: khớp theo tiêu đề)."""
		if not payload.channel:
			return None
		try:
			data = _client(payload.channel)._request(
				"GET", config.get("article_list_path"), params={"offset": 0, "limit": 10, "type": "normal"}
			)
		except ZaloError as e:
			raise to_publish_error(e) from None
		since_ms = (time.time() - since_minutes * 60) * 1000
		for row in (data or {}).get("medias") or []:
			if not isinstance(row, dict) or str(row.get("title") or "").strip() != payload.title:
				continue
			created = row.get("create_date")
			if isinstance(created, int | float) and created < since_ms:
				continue  # bài cũ trùng tiêu đề: không nhận nhầm
			return PublishResult(external_post_id=str(row.get("id") or ""))
		return None
