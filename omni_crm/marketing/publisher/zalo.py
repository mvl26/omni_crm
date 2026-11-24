"""Đăng Bài viết OA (loại thường) lên Zalo OA. Dùng ZaloClient: token của kênh chat, tự làm mới bằng cơ chế khóa sẵn có.

VERIFY (chưa đọc được tài liệu chính thức developers.zalo.me, theo SDK cộng đồng):
- `POST /v2.0/article/create` {type:"normal", title<=150, author<=50, description<=300,
  cover:{cover_type:"photo", photo_url, status:"show"}, body:[{type:"text", content}, {type:"image", url}],
  tracking_link, status:"show", comment:"show"} -> data.token
- `POST /v2.0/article/verify` {token} -> data.article_id khi xử lý xong (đang xử lý thì chưa có)
- `GET /v2.0/article/getslice?offset=0&limit=10&type=normal` -> data.medias[] (id, title, create_date...)
Token cần quyền quản lý bài viết của OA.

ADR 0035 (Zalo), đã thử trên OA thật 23/11/2026 (spd/docs/35 mục 11):
- zalo_video: `POST /v2.0/article/upload_video/preparevideo` (multipart `file`, MP4) -> data.token;
  `GET /v2.0/article/upload_video/verify` (header `token`) -> status (1 sẵn sàng, 3 đang xử lý; 2, 4, 5 hỏng),
  video_id; rồi `article/create` {type:"video", title, description, video_id, avatar, status, comment} như bài thường.
- zalo_pdf_link: bài viết thường, nội dung có thêm link tới PDF công khai của CRM.
- Số liệu: `GET /v2.0/article/getdetail?id=` -> total_view, total_like, total_comment, total_share.
- Link bài: `link_view` của getdetail (thử thật 24/11/2026: `https://post.oa.zalo.me/v?id=…` cho bài video,
  `https://post.oa.zalo.me/d/<oa>?id=…` cho bài thường).
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
VIDEO_MAX_BYTES = 50 * 1024 * 1024  # Zalo nhận video bài viết tới 50 MB (nguồn cộng đồng)
VIDEO_POLL_MAX_SECONDS = 300  # thử thật: video 6 giây xử lý xong trong vài giây
VIDEO_READY = 1
VIDEO_BROKEN = {2: "bị khóa", 4: "lỗi", 5: "đã xóa"}


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


def body_html(caption: str | None, tracking_url: str = "", document: str = "") -> str:
	"""Nội dung bài dạng HTML (thuần): Zalo gộp mọi xuống dòng của văn bản thường thành một đoạn (thấy thật
	17/11/2026), nên mỗi đoạn là một <p>, xuống dòng trong đoạn là <br>, link theo dõi là thẻ <a> ở cuối.
	# VERIFY: chưa đọc được tài liệu chính thức về các thẻ HTML Zalo cho phép trong nội dung bài viết.
	"""
	text = (caption or "").replace("\r\n", "\n").strip()
	paragraphs = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
	parts = ["<p>" + "<br>".join(escape(line.strip()) for line in b.split("\n")) + "</p>" for b in paragraphs]
	if (
		document
	):  # zalo_pdf_link (ADR 0035): bài viết OA không đính kèm được PDF, để link tới PDF công khai của CRM
		parts.append(f'<p><a href="{escape(document, quote=True)}">{escape(_("Xem tài liệu PDF"))}</a></p>')
	if tracking_url:
		link = escape(tracking_url, quote=True)
		parts.append(f'<p><a href="{link}">{link}</a></p>')
	return "".join(parts)


def article_body(p: Payload) -> dict:
	"""Yêu cầu tạo bài viết OA (thuần). Link theo dõi nằm trong nội dung và tracking_link (để đối chiếu)."""
	document = p.document if p.post_format == "zalo_pdf_link" else ""
	body = [{"type": "text", "content": body_html(p.caption, p.tracking_url, document)}]
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


def video_article_body(p: Payload, video_id: str) -> dict:
	"""Yêu cầu tạo bài viết video (thuần). Bài video không có nội dung dài: mô tả là caption rút gọn, ảnh đại diện là
	ảnh bìa của bài."""
	return {
		"type": "video",
		"title": p.title,
		"description": short_description(p.caption),
		"video_id": video_id,
		"avatar": p.images[0],
		"status": "show",
		"comment": "show",
	}


def video_bytes(file_url: str) -> tuple[str, bytes]:
	"""(tên tệp, nội dung) của video đăng kèm: đọc trên đĩa hoặc trên kho S3 (ADR 0033). Ném PublishValidationError."""
	from omni_crm.marketing import s3_media

	name = frappe.db.get_value("File", {"file_url": file_url, "is_private": 0, "is_folder": 0}, "name")
	if not name:
		raise PublishValidationError(
			_("Không tìm thấy video công khai {0}").format(file_url.rsplit("/", 1)[-1])
		)
	doc = frappe.get_doc("File", name)
	size = int(doc.file_size or 0)
	if size > VIDEO_MAX_BYTES:
		raise PublishValidationError(
			_("Video {0} MB, Zalo nhận tối đa {1} MB").format(
				round(size / 1048576, 1), VIDEO_MAX_BYTES // 1048576
			)
		)
	try:
		data = s3_media.file_content(doc)
	except Exception as e:
		raise ProviderError(_("Không đọc được video trong CRM ({0})").format(type(e).__name__)) from None
	if len(data) > VIDEO_MAX_BYTES:
		raise PublishValidationError(
			_("Video lớn hơn {0} MB: Zalo không nhận").format(VIDEO_MAX_BYTES // 1048576)
		)
	return (doc.file_name or "video.mp4"), data


def article_link(channel: str, article_id: str) -> str:
	"""Link xem bài viết OA (`link_view` của article/getdetail); chỉ nhận https, lỗi thì chuỗi rỗng."""
	if not channel or not article_id:
		return ""
	try:
		data = (
			_client(channel)._request("GET", config.get("article_detail_path"), params={"id": article_id})
			or {}
		)
	except Exception:
		return ""
	link = str(data.get("link_view") or "").strip()
	return link if link.startswith("https://") and " " not in link else ""


def backfill_permalinks(dry_run: int = 1) -> dict:
	"""Lệnh chạy tay: điền link "Xem bài đã đăng" cho bài Zalo OA đã đăng trước khi có `fetch_permalink`.
	Mặc định chỉ xem trước (dry_run=1); dry_run=0 mới ghi. Trả {found, filled, missing}.

	bench --site <site> execute omni_crm.marketing.publisher.zalo.backfill_permalinks --kwargs "{'dry_run': 0}"
	"""
	from omni_crm.marketing import rules

	rows = frappe.get_all(
		"Omni Content Target",
		filters={
			"platform": rules.ZALO,
			"status": rules.T_SUCCESS,
			"external_post_id": ["is", "set"],
			"permalink": ["is", "not set"],
		},
		fields=["name", "channel_account", "external_post_id"],
	)
	filled = missing = 0
	for row in rows:
		if str(row.external_post_id).startswith("dryrun-"):
			continue
		link = article_link(row.channel_account, row.external_post_id)
		if not link:
			missing += 1
			continue
		filled += 1
		if not int(dry_run):
			frappe.db.set_value("Omni Content Target", row.name, "permalink", link, update_modified=False)
	if not int(dry_run):
		frappe.db.commit()
	return {"found": len(rows), "filled": filled, "missing": missing, "dry_run": int(dry_run)}


class ZaloAdapter(Adapter):
	platform = "Zalo OA"

	def publish(self, payload: Payload, *, sleep=time.sleep) -> PublishResult:
		if not payload.channel:
			raise TerminalError(_("Chưa chọn kênh Zalo OA"))
		client = _client(payload.channel)
		if payload.post_format == "zalo_video":
			body = video_article_body(payload, self._upload_video(client, payload, sleep))
		else:
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

	def _upload_video(self, client, payload: Payload, sleep) -> str:
		"""Tải video lên Zalo rồi chờ xử lý xong; trả video_id. Chưa tạo bài nên lỗi ở đây thử lại an toàn."""
		name, data = video_bytes(payload.video_file)
		try:
			prepared = client._request(
				"POST",
				config.get("article_video_prepare_path"),
				files={"file": (name, data, "video/mp4")},
				timeout=config.get("article_video_timeout"),
			)
		except ZaloError as e:
			raise to_publish_error(e, "tải video") from None
		token = str((prepared or {}).get("token") or "")
		if not token:
			raise ProviderError(_("Zalo không trả mã tải video"))
		waited = 0
		while True:
			try:
				info = (
					client._request("GET", config.get("article_video_verify_path"), headers={"token": token})
					or {}
				)
			except ZaloError as e:
				raise to_publish_error(e, "kiểm tra video") from None
			status = info.get("status")
			if status == VIDEO_READY and info.get("video_id"):
				return str(info["video_id"])
			if status in VIDEO_BROKEN:
				raise PublishValidationError(
					_("Zalo không nhận video ({0}): {1}").format(
						VIDEO_BROKEN[status], str(info.get("status_message") or "")[:200]
					)
				)
			if waited >= VIDEO_POLL_MAX_SECONDS:
				raise ProviderError(_("Zalo xử lý video quá 5 phút"))
			sleep(POLL_SECONDS)
			waited += POLL_SECONDS

	def reconcile(self, payload: Payload, since_minutes: int = 30) -> PublishResult | None:
		"""Bài viết gần nhất cùng tiêu đề (danh sách bài viết không trả nội dung: khớp theo tiêu đề)."""
		if not payload.channel:
			return None
		kind = "video" if payload.post_format == "zalo_video" else "normal"
		try:
			data = _client(payload.channel)._request(
				"GET", config.get("article_list_path"), params={"offset": 0, "limit": 10, "type": kind}
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

	def fetch_permalink(self, payload: Payload, external_post_id: str) -> str:
		"""Link "Xem bài đã đăng" trên trang bài /crm (run._success gọi sau khi đăng thành công)."""
		return article_link(payload.channel, external_post_id)

	def fetch_insights(self, channel: str, external_post_id: str) -> dict[str, int | None]:
		"""Số liệu bài viết OA (ADR 0035): lượt xem, thích, bình luận, chia sẻ từ `article/getdetail` (thử thật
		23/11/2026). Bài video: lượt xem cũng là lượt xem video. Zalo không có tiếp cận, lượt bấm: để trống."""
		try:
			data = (
				_client(channel)._request(
					"GET", config.get("article_detail_path"), params={"id": external_post_id}
				)
				or {}
			)
		except ZaloError as e:
			raise to_publish_error(e) from None

		def num(key: str) -> int | None:
			value = data.get(key)
			return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

		views = num("total_view")
		return {
			"reach": None,
			"impressions": views,
			"likes": num("total_like"),
			"comments": num("total_comment"),
			"shares": num("total_share"),
			"clicks": None,
			"video_views": views if data.get("type") == "video" else None,
		}
