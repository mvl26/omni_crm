"""Client Graph API cho Messenger (ADR 0022): gửi tin chữ qua Send API và lấy tên người gửi.

Mọi lời gọi có timeout 10 giây; token Page đọc bằng `get_password` và không bao giờ vào log hay thông điệp lỗi.
Sandbox (`Omni Settings.sandbox_mode`) là chốt chặn cuối: không có HTTP ra Meta.
"""

from __future__ import annotations

import uuid
from typing import Any

import frappe
import requests
from frappe import _

from omni_crm import flags

GRAPH = "https://graph.facebook.com"
VERSION_CONF = "omni_meta_graph_version"  # site_config, tùy chọn
DEFAULT_VERSION = "v23.0"
TIMEOUT = 10
MAX_CHARS = 2000  # giới hạn chữ của một tin Messenger

# Loại lỗi, khớp pipeline/outbound.py (duck-typing như channels/zalo/errors.py)
TRANSIENT = "transient"
PERMANENT = "permanent"
WINDOW = "window"

# Mã lỗi Graph (Messenger Platform, "Send API error codes"); mã lạ coi là vĩnh viễn kèm mã gốc.
_RATE_LIMIT = {4, 17, 32, 613}
_TRANSIENT_CODES = {1, 2} | _RATE_LIMIT
_WINDOW_SUBCODES = {2018278}  # gửi ngoài cửa sổ 24 giờ
_MESSAGES = {
	190: "Token Page của kênh Messenger không hợp lệ hoặc đã hết hạn",
	200: "Ứng dụng Meta chưa có quyền gửi tin cho người này (chế độ development chỉ gửi được cho người có vai trò trong ứng dụng)",
	551: "Người này hiện không nhận được tin nhắn từ Page",
	100: "Tham số gửi tin Messenger không hợp lệ (người nhận không tồn tại?)",
	10: "Messenger không cho Page gửi tin này (quá 24 giờ kể từ tin cuối của khách?)",
}


class MessengerError(Exception):
	"""Lỗi Graph API; `kind` quyết định cách xử lý ở pipeline, `message_vi` an toàn để hiện cho người dùng."""

	def __init__(
		self,
		code: int | None,
		kind: str,
		message_vi: str,
		http_status: int | None = None,
		subcode: int | None = None,
	):
		super().__init__(f"{kind}:{code}:{subcode}:{message_vi}")
		self.code = code
		self.subcode = subcode
		self.kind = kind
		self.message_vi = message_vi
		self.http_status = http_status
		self.sent_ids: list[str] = []  # tin đã gửi được trước khi lỗi (khi tách tin dài)


def classify(code: int | None, subcode: int | None, http_status: int | None) -> tuple[str, str]:
	"""(loại, thông điệp tiếng Việt) cho lỗi Graph; không bao giờ ném lỗi."""
	if subcode in _WINDOW_SUBCODES:
		return WINDOW, _(
			"Đã quá 24 giờ kể từ tin cuối của khách, Messenger không cho Page nhắn tiếp; hãy liên hệ qua kênh khác."
		)
	if code in _RATE_LIMIT:
		return TRANSIENT, _("Vượt hạn mức gọi API của Meta, thử lại sau")
	if code in _TRANSIENT_CODES or (http_status and http_status >= 500):
		return TRANSIENT, _("Máy chủ Meta đang lỗi, thử lại sau")
	if code in _MESSAGES:
		return PERMANENT, _(_MESSAGES[code])
	if code is None:
		return PERMANENT, _("Phản hồi của Meta không đọc được (HTTP {0})").format(http_status)
	return PERMANENT, _("Meta trả lỗi mã {0}").format(code)


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
	"""Tách tin dài thành các đoạn <= limit ký tự, ưu tiên cắt ở xuống dòng rồi khoảng trắng."""
	text = (text or "").strip()
	parts: list[str] = []
	while len(text) > limit:
		cut = text.rfind("\n", 0, limit)
		if cut < limit // 2:
			cut = text.rfind(" ", 0, limit)
		if cut < limit // 2:
			cut = limit
		parts.append(text[:cut].rstrip())
		text = text[cut:].lstrip()
	if text:
		parts.append(text)
	return parts


class MessengerClient:
	"""Lời gọi Graph cho một Omni Channel loại Meta Messenger."""

	def __init__(self, channel_doc: Any):
		self.channel_doc = channel_doc
		self.page_id = str(channel_doc.get("meta_page_id") or "").strip()
		version = frappe.conf.get(VERSION_CONF) or DEFAULT_VERSION
		self.base = f"{GRAPH}/{version}"

	def _token(self) -> str:
		token = self.channel_doc.get_password("meta_page_access_token", raise_exception=False) or ""
		if not token:
			raise MessengerError(None, PERMANENT, _("Kênh Messenger chưa có token Page"))
		return token

	def _request(self, method: str, path: str, **kwargs: Any) -> dict:
		if flags.sandbox():
			raise MessengerError(None, PERMANENT, _("Chế độ thử (sandbox) đang bật: không gọi API Meta"))
		params = dict(kwargs.pop("params", None) or {})
		params["access_token"] = self._token()
		try:
			resp = requests.request(method, self.base + path, params=params, timeout=TIMEOUT, **kwargs)
		except requests.RequestException as e:
			# Không đưa URL (có token) vào thông điệp; giữ nguyên __cause__ để pipeline biết đã kết nối chưa.
			raise MessengerError(
				None, TRANSIENT, _("Không kết nối được tới Meta ({0})").format(type(e).__name__)
			) from e
		try:
			data = resp.json()
		except ValueError:
			data = {}
		err = data.get("error") if isinstance(data, dict) else None
		if resp.status_code >= 400 or err:
			err = err or {}
			code, subcode = err.get("code"), err.get("error_subcode")
			kind, message = classify(code, subcode, resp.status_code)
			raise MessengerError(code, kind, message, resp.status_code, subcode)
		return data if isinstance(data, dict) else {}

	def send_text(self, psid: str, text: str) -> str:
		"""Gửi tin chữ trả lời khách (messaging_type RESPONSE); trả mã tin (đoạn đầu nếu bị tách)."""
		parts = split_text(text)
		if not parts:
			raise MessengerError(None, PERMANENT, _("Nội dung tin nhắn đang trống"))
		if flags.sandbox():
			frappe.logger("omni").info(
				"omni sandbox: bỏ qua gửi Messenger (kênh %s, %s đoạn)", self.channel_doc.name, len(parts)
			)
			return f"sandbox:{uuid.uuid4().hex[:16]}"
		if not self.page_id:
			raise MessengerError(None, PERMANENT, _("Kênh Messenger chưa có Page ID"))
		sent: list[str] = []
		for part in parts:
			try:
				data = self._request(
					"POST",
					f"/{self.page_id}/messages",
					json={"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": {"text": part}},
				)
			except MessengerError as e:
				e.sent_ids = sent
				raise
			sent.append(str(data.get("message_id") or ""))
		return sent[0]

	def get_name(self, psid: str) -> str:
		"""Tên hiển thị của người gửi; rỗng nếu Meta không trả.

		Đi qua hội thoại của Page trước (`/{page}/conversations?user_id=…&fields=participants`): chỉ cần token Page có
		`pages_messaging`, đã kiểm chạy được trên Page Miyano 12/11/2026. Đọc thẳng hồ sơ `/{PSID}` cần tính năng
		"Business Asset User Profile Access" mà ứng dụng chưa có (Meta trả 100/33), nên chỉ để dự phòng.
		"""
		name = ""
		first_error: MessengerError | None = None
		if self.page_id:
			try:
				data = self._request(
					"GET",
					f"/{self.page_id}/conversations",
					params={"platform": "messenger", "user_id": psid, "fields": "participants"},
				)
				for conv in data.get("data") or []:
					for person in (conv.get("participants") or {}).get("data") or []:
						if str(person.get("id")) == str(psid) and person.get("name"):
							name = str(person["name"]).strip()
			except MessengerError as e:
				first_error = e
		if name:
			return name
		try:
			data = self._request("GET", f"/{psid}", params={"fields": "first_name,last_name"})
		except MessengerError as e:
			err = first_error or e
			raise MessengerError(
				err.code,
				err.kind,
				_("Meta không trả tên người gửi (mã {0}/{1})").format(err.code, err.subcode),
				err.http_status,
				err.subcode,
			) from e
		return " ".join(str(data.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
