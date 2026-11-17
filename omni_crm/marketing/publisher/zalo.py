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

import re
import time
import unicodedata
from html import escape

import frappe
from frappe import _

from omni_crm.channels.zalo import config
from omni_crm.channels.zalo.errors import TOKEN, TRANSIENT, ZaloError
from omni_crm.marketing.publisher.base import Adapter, PublishResult
from omni_crm.marketing.publisher.content import ZALO_DESC_MAX, Payload
from omni_crm.marketing.publisher.errors import ProviderError, PublishValidationError, TerminalError

AUTHOR = "Miyano"
POLL_SECONDS = 5
PROCESSING_CODE = -214  # "Media is being processed. Please wait for a moment" (thấy thật 17/11/2026)
POLL_MAX_SECONDS = 120  # ~2 phút; quá thì ProviderError (lần sau đối chiếu trước khi đăng lại)


def _client(channel: str):
	from omni_crm.channels.zalo.client import ZaloClient

	return ZaloClient(channel)


def _detail(e: ZaloError, step: str, sent: dict | None) -> str:
	"""Phần chẩn đoán gắn sau thông điệp lỗi: bước gọi, mã gốc, câu báo lỗi của Zalo, tóm tắt dữ liệu đã gửi."""
	parts = [_("bước {0}").format(step), _("mã Zalo {0}").format(e.code)]
	raw = getattr(e, "raw_message", "")
	if raw:
		parts.append(_("Zalo nói: {0}").format(raw))
	if sent:
		# Địa chỉ ảnh dài bị bộ che bí mật của nhật ký đăng che mất: ghi riêng vào Error Log để chẩn đoán.
		frappe.log_error(
			title=_("Marketing Hub: Zalo từ chối bài (mã {0})").format(e.code),
			message=f"Zalo: {raw}\n{sent_summary(sent)}",
		)
	return " [" + "; ".join(parts) + "]"


def sent_summary(body: dict) -> str:
	"""Tóm tắt yêu cầu tạo bài (thuần): độ dài các trường và các địa chỉ, không kèm nội dung bài hay token."""
	items = body.get("body") or []
	text_len = sum(len(str(i.get("content") or "")) for i in items if i.get("type") == "text")
	images = [str(i.get("url") or "") for i in items if i.get("type") == "image"]
	return (
		f"title={len(str(body.get('title') or ''))} ký tự, author={body.get('author')}, "
		f"description={len(str(body.get('description') or ''))} ký tự, body_text={text_len} ký tự, "
		f"cover={(body.get('cover') or {}).get('photo_url')}, body_images={images}, "
		f"tracking_link={body.get('tracking_link')!r}"
	)


def to_publish_error(e: ZaloError, step: str = "", sent: dict | None = None) -> Exception:
	detail = _detail(e, step, sent) if step else ""
	if e.kind == TRANSIENT:
		return ProviderError(
			_("Zalo lỗi tạm thời: {0}").format(e.message_vi) + detail,
			http_status=e.http_status,
			ambiguous=True,
		)
	if e.kind == TOKEN:
		return TerminalError(
			_("Zalo từ chối mã truy cập OA: {0}").format(e.message_vi) + detail, http_status=e.http_status
		)
	if e.code in (-201, -102):
		return PublishValidationError(
			_("Zalo báo dữ liệu bài không hợp lệ: {0}").format(e.message_vi) + detail
		)
	return TerminalError(_("Zalo từ chối bài: {0}").format(e.message_vi) + detail, http_status=e.http_status)


def short_description(caption: str | None) -> str:
	"""Mô tả bài viết (thuần): gọn trong 300 BYTE UTF-8, cắt ở ranh giới từ.

	Zalo từ chối mô tả đúng 300 ký tự tiếng Việt ("description allow 1 to 300 characters", 17/11/2026):
	cách Zalo đếm chưa rõ (ký tự hay byte), nên giới hạn theo byte để an toàn với mọi cách đếm.
	"""
	desc = " ".join(unicodedata.normalize("NFC", caption or "").split())
	if len(desc.encode()) <= ZALO_DESC_MAX:
		return desc
	out = ""
	for word in desc.split(" "):
		nxt = f"{out} {word}".strip()
		if len((nxt + "…").encode()) > ZALO_DESC_MAX:
			break
		out = nxt
	if not out:  # một từ dài hơn giới hạn: cắt theo ký tự
		for ch in desc:
			if len((out + ch + "…").encode()) > ZALO_DESC_MAX:
				break
			out += ch
	return out + "…"


def body_html(caption: str | None, tracking_url: str = "") -> str:
	"""Nội dung bài dạng HTML (thuần): Zalo gộp mọi xuống dòng của văn bản thường thành một đoạn (thấy thật
	17/11/2026), nên mỗi đoạn là một <p>, xuống dòng trong đoạn là <br>, link theo dõi là thẻ <a> ở cuối.
	# VERIFY: chưa đọc được tài liệu chính thức về các thẻ HTML Zalo cho phép trong nội dung bài viết.
	"""
	text = (caption or "").replace("\r\n", "\n").strip()
	paragraphs = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
	parts = ["<p>" + "<br>".join(escape(line.strip()) for line in b.split("\n")) + "</p>" for b in paragraphs]
	if tracking_url:
		link = escape(tracking_url, quote=True)
		parts.append(f'<p><a href="{link}">{link}</a></p>')
	return "".join(parts)


def article_body(p: Payload) -> dict:
	"""Yêu cầu tạo bài viết OA (thuần). Link theo dõi nằm trong nội dung và tracking_link (để đối chiếu)."""
	body = [{"type": "text", "content": body_html(p.caption, p.tracking_url)}]
	body += [{"type": "image", "url": u} for u in p.images[1:]]
	desc = short_description(p.caption)
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
		body = article_body(payload)
		try:
			created = client._request("POST", config.get("article_create_path"), body=body)
		except ZaloError as e:
			raise to_publish_error(e, "tạo bài", body) from None
		token = str((created or {}).get("token") or "")
		if not token:
			raise ProviderError(_("Zalo không trả mã xử lý bài viết"), ambiguous=True)
		waited = 0
		while True:
			try:
				data = client._request("POST", config.get("article_verify_path"), body={"token": token}) or {}
			except ZaloError as e:
				if e.code != PROCESSING_CODE:
					raise to_publish_error(e, "kiểm tra bài") from None
				data = {}  # Zalo đang xử lý bài: chờ rồi hỏi lại
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
