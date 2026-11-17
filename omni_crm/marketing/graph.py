"""Lời gọi Graph API để đăng bài lên Page và đọc chỉ số bài (ADR 0029).

Token System User của kênh chỉ dùng để lấy token Page; mọi token chỉ nằm trong bộ nhớ, không vào log hay thông điệp lỗi.
Sandbox (`Omni Settings.sandbox_mode`) chặn mọi HTTP ra Meta.
"""

from __future__ import annotations

from typing import Any

import frappe
import requests
from frappe import _

from omni_crm import flags
from omni_crm.channels.meta_messenger.client import DEFAULT_VERSION, GRAPH, VERSION_CONF

TIMEOUT = 15
UPLOAD_TIMEOUT = 60
MAX_MESSAGE = 63206  # giới hạn chữ của một bài Facebook
_RATE_LIMIT = {4, 17, 32, 613}
_PERMISSION = {10, 3} | set(range(200, 300))


class ContentGraphError(Exception):
	"""Lỗi khi gọi Meta. `uncertain` = có thể Meta đã nhận yêu cầu (mất kết nối, 5xx) nên KHÔNG được thử lại tự động."""

	def __init__(self, message_vi: str, code: int | None = None, uncertain: bool = False):
		super().__init__(message_vi)
		self.message_vi = message_vi
		self.code = code
		self.uncertain = uncertain


def describe(code: int | None, http_status: int | None) -> str:
	"""Thông điệp tiếng Việt cho mã lỗi Graph, kèm việc cần làm."""
	if code == 190:
		return _("Token của kênh hết hạn hoặc đã bị thu hồi (mã 190): tạo token System User mới cho kênh")
	if code in _PERMISSION:
		return _(
			"Meta từ chối vì thiếu quyền (mã {0}): token cần pages_manage_posts, pages_read_engagement và System User cần quyền Nội dung trên Page"
		).format(code)
	if code == 368:
		return _("Meta tạm chặn Page vì nghi spam (mã 368): chờ rồi đăng lại, không bấm đăng liên tục")
	if code in _RATE_LIMIT:
		return _("Vượt hạn mức gọi API của Meta (mã {0}): đăng lại sau ít phút").format(code)
	if code == 100:
		return _("Meta không nhận tham số của bài (mã 100): kiểm ảnh, link và nội dung")
	if code is None:
		return _("Phản hồi của Meta không đọc được (HTTP {0})").format(http_status)
	return _("Meta trả lỗi mã {0}").format(code)


class PageClient:
	"""Đăng bài và đọc chỉ số trên Page của một Omni Channel (Meta Messenger) có token System User."""

	def __init__(self, channel_doc: Any):
		self.channel_doc = channel_doc
		self.page_id = str(channel_doc.get("meta_page_id") or "").strip()
		version = frappe.conf.get(VERSION_CONF) or DEFAULT_VERSION
		self.base = f"{GRAPH}/{version}"
		self._page_token: str | None = None

	def _call(
		self,
		method: str,
		path: str,
		token: str,
		params: dict | None = None,
		data: dict | None = None,
		files: dict | None = None,
		timeout: int = TIMEOUT,
		uncertain_on_failure: bool = False,
	) -> dict:
		if flags.sandbox():
			raise ContentGraphError(_("Chế độ thử (sandbox) đang bật: không gọi API Meta"))
		query = dict(params or {})
		query["access_token"] = token
		try:
			resp = requests.request(
				method, self.base + path, params=query, data=data, files=files, timeout=timeout
			)
		except requests.RequestException as e:
			# Không đưa URL (có token) vào thông điệp.
			raise ContentGraphError(
				_("Mất kết nối khi gọi Meta ({0})").format(type(e).__name__), uncertain=uncertain_on_failure
			) from None
		try:
			body = resp.json()
		except ValueError:
			body = {}
		err = body.get("error") if isinstance(body, dict) else None
		if resp.status_code >= 400 or err:
			err = err if isinstance(err, dict) else {}
			code = err.get("code") if isinstance(err.get("code"), int) else None
			# 5xx hoặc lỗi chung (mã 1, 2) khi đăng: Meta có thể đã tạo bài rồi mới lỗi, nên không coi là chắc chắn thất bại
			uncertain = uncertain_on_failure and (resp.status_code >= 500 or code in (1, 2))
			raise ContentGraphError(describe(code, resp.status_code), code, uncertain=uncertain)
		return body if isinstance(body, dict) else {}

	def _system_token(self) -> str:
		token = self.channel_doc.get_password("meta_page_access_token", raise_exception=False) or ""
		if not token or not self.page_id:
			raise ContentGraphError(_("Kênh đăng bài chưa có Page ID hoặc token"))
		return token

	def page_token(self) -> str:
		"""Token Page lấy từ token System User (`GET /{page}?fields=access_token`), giữ trong bộ nhớ của lượt chạy."""
		if self._page_token:
			return self._page_token
		body = self._call("GET", f"/{self.page_id}", self._system_token(), params={"fields": "access_token"})
		token = body.get("access_token")
		if not token:
			raise ContentGraphError(
				_(
					"Không lấy được token Page: System User chưa được giao Page hoặc token thiếu pages_show_list"
				)
			)
		self._page_token = token
		return token

	def publish(self, message: str, link: str | None = None, image: tuple | None = None) -> str:
		"""Đăng bài; trả mã bài dạng `<page>_<post>`. `image` = (tên tệp, bytes, MIME) thì đăng kiểu ảnh kèm chú thích."""
		if len(message or "") > MAX_MESSAGE:
			raise ContentGraphError(_("Nội dung dài quá {0} ký tự").format(MAX_MESSAGE))
		token = self.page_token()
		if image:
			caption = message if not link else f"{message}\n\n{link}".strip()
			body = self._call(
				"POST",
				f"/{self.page_id}/photos",
				token,
				data={"caption": caption, "published": "true"},
				files={"source": image},
				timeout=UPLOAD_TIMEOUT,
				uncertain_on_failure=True,
			)
			post_id = body.get("post_id") or body.get("id")
		else:
			data = {"message": message, "published": "true"}
			if link:
				data["link"] = link
			body = self._call("POST", f"/{self.page_id}/feed", token, data=data, uncertain_on_failure=True)
			post_id = body.get("id")
		if not post_id:
			raise ContentGraphError(_("Meta không trả mã bài: mở Page kiểm bài đã lên chưa"), uncertain=True)
		return str(post_id)

	def permalink(self, post_id: str) -> str | None:
		"""Link bài (`permalink_url`); None nếu không đọc được (không chặn việc ghi kết quả đăng)."""
		try:
			body = self._call("GET", f"/{post_id}", self.page_token(), params={"fields": "permalink_url"})
		except ContentGraphError:
			return None
		return body.get("permalink_url") or None

	def post_stats(self, post_id: str) -> dict:
		"""{"reactions", "comments", "shares"} của bài, từ các trường của bài (cần pages_read_engagement)."""
		body = self._call(
			"GET",
			f"/{post_id}",
			self.page_token(),
			params={
				"fields": "shares,reactions.limit(0).summary(total_count),comments.limit(0).summary(total_count)"
			},
		)
		return {
			"reactions": _summary(body.get("reactions")),
			"comments": _summary(body.get("comments")),
			"shares": int((body.get("shares") or {}).get("count") or 0),
		}

	def post_insights(self, post_id: str, metrics: list[str]) -> dict:
		"""{tên chỉ số: giá trị} của bài (cần read_insights). Giá trị có thể là số hoặc dict (chỉ số theo loại)."""
		metrics = [m for m in metrics if m]
		if not metrics:
			return {}
		body = self._call(
			"GET", f"/{post_id}/insights", self.page_token(), params={"metric": ",".join(metrics)}
		)
		out: dict = {}
		for row in body.get("data") or []:
			values = row.get("values") or []
			if row.get("name") and values:
				out[row["name"]] = values[-1].get("value")
		return out


def _summary(edge: Any) -> int:
	return int(((edge or {}).get("summary") or {}).get("total_count") or 0)
