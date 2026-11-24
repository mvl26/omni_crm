"""Đăng bài lên Trang Facebook qua Graph API (Pages API).

Đã đối chiếu tài liệu Meta (developers.facebook.com, đọc 2026-11-13):
- fb_link: `POST /{page_id}/feed` với `message`, `link`; trả `id` (page post id).
- fb_photo: `POST /{page_id}/photos` với `url` (ảnh công khai) và `caption` (`message` đã cũ); trả `id`, `post_id`.
- fb_multi_photo: tải từng ảnh `POST /{page_id}/photos` `published=false`, rồi `POST /{page_id}/feed` với `message` và
  `attached_media[n]={"media_fbid": "<photo id>"}`.
- fb_video (ADR 0035): `POST /{page_id}/videos` ở máy `graph-video.facebook.com` với `file_url` (video công khai: Facebook
  tự tải về) và `description`; trả `id` (video id, không có `_`). Video xử lý vài phút sau đó; trạng thái ở
  `GET /{video_id}?fields=status`. Đã thử trên Page thật ngày 23/11/2026 (spd/docs/35 mục 7, tải multipart).
- fb_pdf_link (ADR 0035): Pages API không đính kèm PDF vào bài; đăng `POST /{page_id}/feed` với `message` và
  `link` = địa chỉ công khai của PDF.
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
	PublishError,
	PublishValidationError,
	TerminalError,
)

API_BASE = "https://graph.facebook.com"
VIDEO_BASE = "https://graph-video.facebook.com"  # Meta nhận tải video ở máy riêng
TIMEOUT = 10  # giây (CLAUDE.md mục 3); thử lại do bộ lập lịch lo (1, 5, 15 phút)
VIDEO_TIMEOUT = 120  # với file_url, Facebook tải video về trong lúc trả lời: lâu hơn bài thường
# Bài có link tới PDF: Facebook tải PDF về dựng thẻ xem trước trong lúc trả lời (T3 23/11/2026: quá 10 giây)
LINK_TIMEOUT = 60
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
		self,
		method: str,
		path: str,
		token: str,
		data: dict | None = None,
		params: dict | None = None,
		base: str = API_BASE,
		timeout: int = TIMEOUT,
	) -> dict:
		url = f"{base}/{graph_version()}/{path.lstrip('/')}"
		try:
			resp = requests.request(
				method,
				url,
				data=data,
				params=params,
				headers={"Authorization": f"Bearer {token}"},
				timeout=timeout,
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
		elif payload.post_format == "fb_video":
			body = self._call(
				"POST",
				f"{page_id}/videos",
				token,
				{"file_url": payload.video, "description": message, "published": "true"},
				base=VIDEO_BASE,
				timeout=VIDEO_TIMEOUT,
			)
			post_id = str(body.get("id") or "")  # video id; bài trên Trang có sau khi Facebook xử lý xong
		elif payload.post_format == "fb_pdf_link":
			body = self._call(
				"POST",
				f"{page_id}/feed",
				token,
				{"message": message, "link": payload.document},
				timeout=LINK_TIMEOUT,
			)
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
		if payload.post_format == "fb_video":
			return self._reconcile_video(page_id, token, payload.marker, since)
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

	def _reconcile_video(self, page_id: str, token: str, marker: str, since: int) -> PublishResult | None:
		"""Video vừa tải có mô tả chứa mã theo dõi của lần đăng này (hết giờ chờ khi Facebook đang tải video về)."""
		body = self._call(
			"GET",
			f"{page_id}/videos",
			token,
			params={"fields": "id,description,permalink_url,created_time", "limit": 25},
		)
		for video in body.get("data") or []:
			if not isinstance(video, dict) or not marker or marker not in str(video.get("description") or ""):
				continue
			created = _epoch(video.get("created_time"))
			if created is not None and created < since:
				continue
			return PublishResult(str(video.get("id") or ""), _absolute(video.get("permalink_url")))
		return None

	def fetch_insights(self, channel: str, external_post_id: str) -> dict[str, int | None]:
		"""Số liệu một bài Trang (ADR 0035, đã thử trên Page thật ở bước T3 ngày 23/11/2026, Graph v26).

		- Tiếp cận `post_total_media_view_unique`, lượt xem `post_media_view` (cột `impressions`: Meta thay
		  impressions bằng views, gỡ post_impressions* sau v25), lượt bấm `post_clicks`: quyền read_insights.
		- Cảm xúc, bình luận, chia sẻ: đọc trường của bài khi token có pages_read_user_content (số tức thời); không có
		  quyền đó (token hiện tại) thì đọc metric `post_reactions_by_type_total`, `post_activity_by_action_type`.
		- Video (mã không có `_`; Facebook hiện video thành Reel): lượt xem `total_video_views`, Reel thì
		  `fb_reels_total_plays`; thích, bình luận đọc trên nút video (tương tác Reel không vào metric của bài Trang);
		  các số khác đọc trên bài Trang `<page>_<post_id>` chứa video.
		Mỗi metric một lần gọi: metric bị Meta gỡ hoặc thiếu quyền chỉ để trống số đó (giao diện hiện "—").
		"""
		page_id, token = self._page(channel)
		out: dict[str, int | None] = dict.fromkeys(INSIGHT_KEYS)
		post_id: str | None = str(external_post_id)
		video = "_" not in post_id
		if video:
			video_id = post_id
			out["video_views"] = self._video_views(video_id, token)
			self._video_edges(video_id, token, out)
			post_id = self._video_post_id(page_id, video_id, token)
			if not post_id:
				return out
		self._post_engagement(post_id, token, out, video)
		for metric, key in INSIGHT_METRICS.items():
			value = _int(self._metric_value(f"{post_id}/insights", token, metric))
			if value is not None:
				out[key] = value
		return out

	def _post_engagement(self, post_id: str, token: str, out: dict, video: bool) -> None:
		"""Cảm xúc, bình luận, chia sẻ của bài Trang. Bài thường: bài đã xóa hoặc token hỏng thì ném lỗi (job đồng bộ
		ghi lỗi), như trước."""
		# VERIFY (T3 23/11/2026): đọc reactions/comments của bài cần pages_read_user_content, token chưa có (lỗi mã 10)
		body = self._get_or_none(post_id, token, {"fields": f"shares,{REACTION_FIELDS}"})
		if body is not None:
			out["likes"] = _total(body.get("reactions"))
			out["comments"] = _total(body.get("comments"))
			out["shares"] = int((body.get("shares") or {}).get("count") or 0)
			return
		if not video:
			self._call("GET", post_id, token, params={"fields": "id"})
		reactions = self._metric_value(f"{post_id}/insights", token, "post_reactions_by_type_total")
		activity = self._metric_value(f"{post_id}/insights", token, "post_activity_by_action_type")
		if isinstance(reactions, dict) and out["likes"] is None:
			out["likes"] = sum(_int(v) or 0 for v in reactions.values())
		if isinstance(activity, dict):
			if out["comments"] is None:
				out["comments"] = _int(activity.get("comment")) or 0
			out["shares"] = _int(activity.get("share")) or 0

	def _video_views(self, video_id: str, token: str) -> int | None:
		"""Lượt xem video (từ 3 giây); Reel không có số này (T3: rỗng) thì lấy lượt phát của Reel (gồm phát lại)."""
		for metric in ("total_video_views", "fb_reels_total_plays"):
			value = _int(self._metric_value(f"{video_id}/video_insights", token, metric))
			if value is not None:
				return value
		return None

	def _video_edges(self, video_id: str, token: str, out: dict) -> None:
		"""Thích, bình luận trên nút video (nút video không có `reactions`); chia sẻ từ video_insights nếu có."""
		body = self._get_or_none(video_id, token, {"fields": VIDEO_EDGE_FIELDS})
		if body is not None:
			out["likes"] = _total(body.get("likes"))
			out["comments"] = _total(body.get("comments"))
		stories = self._metric_value(
			f"{video_id}/video_insights", token, "total_video_stories_by_action_type"
		)
		if isinstance(stories, dict):
			out["shares"] = _int(stories.get("share")) or 0

	def _get_or_none(self, path: str, token: str, params: dict) -> dict | None:
		"""GET; lỗi của Graph (metric bị gỡ, thiếu quyền, trường không có) -> None, không ném."""
		try:
			return self._call("GET", path, token, params=params)
		except PublishError:
			return None

	def _metric_value(self, path: str, token: str, metric: str) -> Any:
		"""Giá trị trọn đời của một metric (`values[0].value`: số, hoặc dict với metric theo loại); None nếu không có."""
		body = self._get_or_none(path, token, {"metric": metric, "period": "lifetime"})
		for row in (body or {}).get("data") or []:
			values = row.get("values") or []
			if row.get("name") == metric and values:
				return values[0].get("value")
		return None

	def _video_post_id(self, page_id: str, video_id: str, token: str) -> str | None:
		"""Mã bài Trang chứa video, theo đúng mã Graph trả về. Trường `post_id` của nút video (không có trong tài
		liệu Meta) trả số trần, ví dụ `122175987854723505` (T3 23/11/2026): mã bài là `<page>_<số đó>`. Không có thì
		thử `<page>_<video>`. Không tìm được thì None."""
		body = self._get_or_none(video_id, token, {"fields": "post_id"})
		raw = str((body or {}).get("post_id") or "")
		candidates = [raw if "_" in raw else f"{page_id}_{raw}"] if raw else []
		candidates.append(f"{page_id}_{video_id}")
		for candidate in dict.fromkeys(candidates):
			found = str((self._get_or_none(candidate, token, {"fields": "id"}) or {}).get("id") or "")
			if "_" in found:
				return found
		return None

	def fetch_permalink(self, payload: Payload, external_post_id: str) -> str:
		"""Link "Xem bài đã đăng" (`permalink_url`; video trả đường tương đối, đổi sang tuyệt đối). Chỉ nhận https;
		lỗi (kênh, token, bài đã xóa) thì chuỗi rỗng, không làm hỏng lần đăng."""
		if not external_post_id:
			return ""
		try:
			_page_id, token = self._page(payload.channel)
			body = self._call("GET", external_post_id, token, params={"fields": "permalink_url"})
		except Exception:
			return ""
		link = _absolute(body.get("permalink_url")).strip()
		return link if link.startswith("https://") and " " not in link else ""


def backfill_permalinks(dry_run: int = 1) -> dict:
	"""Lệnh chạy tay: điền link "Xem bài đã đăng" cho bài Facebook đã đăng mà chưa có link (lần lấy link lúc đăng bị
	lỗi, hoặc đăng trước khi có link). Mặc định chỉ xem trước (dry_run=1); dry_run=0 mới ghi. Trả {found, filled,
	missing}.

	bench --site <site> execute omni_crm.marketing.publisher.facebook.backfill_permalinks --kwargs "{'dry_run': 0}"
	"""
	from omni_crm.marketing import rules

	rows = frappe.get_all(
		"Omni Content Target",
		filters={
			"platform": rules.FACEBOOK,
			"status": rules.T_SUCCESS,
			"external_post_id": ["is", "set"],
			"permalink": ["is", "not set"],
		},
		fields=["name", "channel_account", "post_format", "external_post_id"],
	)
	adapter = FacebookAdapter()
	filled = missing = 0
	for row in rows:
		if str(row.external_post_id).startswith("dryrun-"):
			continue
		payload = Payload(
			target=row.name,
			platform=rules.FACEBOOK,
			post_format=row.post_format,
			channel=row.channel_account,
			title="",
			caption="",
			tracking_url="",
		)
		link = adapter.fetch_permalink(payload, row.external_post_id)
		if not link:
			missing += 1
			continue
		filled += 1
		if not int(dry_run):
			frappe.db.set_value("Omni Content Target", row.name, "permalink", link, update_modified=False)
	if not int(dry_run):
		frappe.db.commit()
	return {"found": len(rows), "filled": filled, "missing": missing, "dry_run": int(dry_run)}


def _absolute(link: Any) -> str:
	"""permalink_url của video là đường tương đối (`/{page}/videos/{id}/`): thêm tên miền Facebook."""
	link = str(link or "")
	return f"https://www.facebook.com{link}" if link.startswith("/") else link


def _epoch(value: Any) -> int | None:
	"""created_time của Graph (`2026-11-23T05:00:00+0000`) -> giây; sai dạng thì None."""
	try:
		return int(datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z").timestamp())
	except (TypeError, ValueError):
		return None


INSIGHT_KEYS = ("reach", "impressions", "likes", "comments", "shares", "clicks", "video_views")
# Metric của bài Trang (Graph v26). VERIFY (T3): tên do Meta đổi định kỳ; post_impressions* đã bị gỡ sau v25.
INSIGHT_METRICS = {
	"post_total_media_view_unique": "reach",
	"post_media_view": "impressions",
	"post_clicks": "clicks",
}
REACTION_FIELDS = "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)"
VIDEO_EDGE_FIELDS = "likes.summary(total_count).limit(0),comments.summary(total_count).limit(0)"


def _int(value: Any) -> int | None:
	"""Số nguyên từ giá trị metric; dict / chuỗi lạ / None -> None."""
	if isinstance(value, bool) or value is None:
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _total(block: Any) -> int | None:
	if not isinstance(block, dict):
		return None
	summary = block.get("summary") or {}
	return int(summary.get("total_count") or 0) if "total_count" in summary else None
