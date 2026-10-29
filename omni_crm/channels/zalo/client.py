"""Client gọi API Zalo OA: timeout 10 giây, tự làm mới token một lần, không log token.

Mọi tên endpoint/trường là CHƯA XÁC NHẬN VỚI API THẬT (docs/12 G7-G9). Yêu cầu GỬI (POST) không tự thử lại
khi lỗi mạng vì có thể tin đã tới khách (hàng đợi ra D1-09 quyết định thử lại); yêu cầu ĐỌC (GET) thử lại
tối đa `get_retries` lần khi lỗi mạng/5xx.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import frappe
import requests
from frappe import _

from omni_crm import flags
from omni_crm.channels.zalo import config
from omni_crm.channels.zalo import token as token_mod
from omni_crm.channels.zalo.errors import TOKEN, TRANSIENT, ZaloError, to_error

CHANNEL = "Omni Channel"


def split_text(text: str, limit: int | None = None) -> list[str]:
	"""Tách văn bản thành các đoạn không quá `limit` ký tự, ưu tiên ngắt ở xuống dòng/khoảng trắng; giữ thứ tự."""
	limit = limit or config.get("max_text_length")
	rest = (text or "").strip()
	if not rest:
		return []
	parts: list[str] = []
	while len(rest) > limit:
		cut = max(rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
		if cut < limit // 2:
			cut = limit  # không có chỗ ngắt hợp lý: cắt cứng
		parts.append(rest[:cut].strip())
		rest = rest[cut:].strip()
	if rest:
		parts.append(rest)
	return parts


class ZaloClient:
	"""Gọi API Zalo cho một Omni Channel (token đọc mới từ DB mỗi lần gọi)."""

	def __init__(self, channel: str):
		self.channel = channel

	# --- nền ---
	def _access_token(self) -> str:
		doc = frappe.get_doc(CHANNEL, self.channel)
		tok = doc.get_password("zalo_access_token", raise_exception=False)
		if not tok:
			raise ZaloError(None, "permanent", _("Kênh Zalo chưa có access token, cần cấp quyền OA"))
		return tok

	def _request(
		self, method: str, path: str, *, params: dict | None = None, body: dict | None = None
	) -> dict:
		"""Gọi API, trả phần `data` của phản hồi; token lỗi thì làm mới một lần rồi thử lại."""
		refreshed = False
		attempt = 0
		retries = config.get("get_retries") if method == "GET" else 0
		while True:
			tok = self._access_token()
			try:
				resp = requests.request(
					method,
					config.get("api_base") + path,
					headers={"access_token": tok, "Content-Type": "application/json"},
					params=params,
					data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
					timeout=config.get("timeout"),
				)
				result = self._parse(resp)
			except requests.RequestException as e:
				if attempt < retries:
					attempt += 1
					time.sleep(min(attempt, 2))
					continue
				raise ZaloError(None, TRANSIENT, _("Không kết nối được máy chủ Zalo")) from e
			except ZaloError as e:
				if e.kind == TOKEN and not refreshed:
					refreshed = True
					token_mod.refresh_channel(self.channel, force=True, stale_access_token=tok)
					continue
				if e.kind == TRANSIENT and attempt < retries:
					attempt += 1
					time.sleep(min(attempt, 2))
					continue
				raise
			return result

	@staticmethod
	def _parse(resp: requests.Response) -> dict:
		try:
			payload: Any = resp.json()
		except ValueError:
			raise to_error(None, resp.status_code) from None
		code = payload.get("error") if isinstance(payload, dict) else None
		if code not in (0, None) or resp.status_code >= 400:
			raise to_error(code if isinstance(code, int) else None, resp.status_code)
		return payload.get("data") or {}

	# --- API ---
	def send_text(self, user_id: str, text: str) -> list[str]:
		"""Gửi tin tư vấn văn bản; tách tin dài, giữ thứ tự; trả danh sách mã tin. Sandbox: không gọi HTTP."""
		parts = split_text(text)
		if not parts:
			raise ZaloError(None, "permanent", _("Tin nhắn rỗng"))
		if flags.sandbox():
			frappe.logger("omni").info(
				"omni sandbox: bỏ qua gửi Zalo (kênh %s, %s đoạn)", self.channel, len(parts)
			)
			return [f"sandbox:{uuid.uuid4().hex[:16]}" for _p in parts]
		sent: list[str] = []
		for part in parts:
			try:
				data = self._request(
					"POST",
					config.get("send_path"),
					body={"recipient": {"user_id": user_id}, "message": {"text": part}},
				)
			except ZaloError as e:
				e.sent_ids = sent
				raise
			sent.append(str(data.get("message_id") or ""))
		return sent

	def send_request_user_info(
		self, user_id: str, title: str, subtitle: str, image_url: str | None = None
	) -> str:
		"""Gửi tin yêu cầu thông tin người dùng (xin SĐT); trả mã tin. Sandbox: không gọi HTTP."""
		if flags.sandbox():
			frappe.logger("omni").info(
				"omni sandbox: bỏ qua gửi yêu cầu thông tin Zalo (kênh %s)", self.channel
			)
			return f"sandbox:{uuid.uuid4().hex[:16]}"
		element: dict = {"title": title, "subtitle": subtitle}
		if image_url:
			element["image_url"] = image_url
		data = self._request(
			"POST",
			config.get("send_path"),
			body={
				"recipient": {"user_id": user_id},
				"message": {
					"attachment": {
						"type": "template",
						"payload": {"template_type": "request_user_info", "elements": [element]},
					}
				},
			},
		)
		return str(data.get("message_id") or "")

	def get_user_profile(self, user_id: str) -> dict:
		"""Hồ sơ người dùng: {display_name, avatar, phone?, email?}; phone/email chỉ có khi khách đã chia sẻ."""
		data = self._request(
			"GET", config.get("profile_path"), params={"data": json.dumps({"user_id": user_id})}
		)
		shared = data.get("shared_info") or {}
		profile = {"display_name": (data.get("display_name") or shared.get("name") or "").strip()}
		if data.get("avatar"):
			profile["avatar"] = data["avatar"]
		if shared.get("phone"):
			profile["phone"] = str(shared["phone"])
		if shared.get("email"):
			profile["email"] = str(shared["email"])
		return profile
