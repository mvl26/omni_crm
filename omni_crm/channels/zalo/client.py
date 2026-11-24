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


# VERIFY: giới hạn 2000 ký tự lấy từ tài liệu bên thứ ba, chưa kiểm với Zalo OA thật (script.send và Omni Script Node dựa vào số này)
BUTTON_TEXT_MAX = 2000  # nội dung tin tư vấn kèm nút (tài liệu bên thứ ba, docs/bot_v3_plan.md mục 3)


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


def _quota_type(data: dict) -> str | None:
	"""Nguồn quota trong phản hồi gửi tin (`data.quota.quota_type`: reply, sub_quota, purchase_quota...)."""
	q = data.get("quota") if isinstance(data, dict) else None
	return str(q.get("quota_type")) if isinstance(q, dict) and q.get("quota_type") else None


class ZaloClient:
	"""Gọi API Zalo cho một Omni Channel (token đọc mới từ DB mỗi lần gọi)."""

	def __init__(self, channel: str):
		self.channel = channel
		self.last_quota_type: str | None = None  # V3-07: nguồn quota Zalo báo cho lần gửi gần nhất

	# --- nền ---
	def _access_token(self) -> str:
		doc = frappe.get_doc(CHANNEL, self.channel)
		tok = doc.get_password("zalo_access_token", raise_exception=False)
		if not tok:
			raise ZaloError(None, "permanent", _("Kênh Zalo chưa có access token, cần cấp quyền OA"))
		return tok

	def _request(
		self,
		method: str,
		path: str,
		*,
		params: dict | None = None,
		body: dict | None = None,
		files: dict | None = None,
		timeout: int | None = None,
		headers: dict | None = None,
	) -> dict:
		"""Gọi API, trả phần `data` của phản hồi; token lỗi thì làm mới một lần rồi thử lại. `files`: multipart.
		`headers`: header thêm (ví dụ `token` của bước kiểm video bài viết, ADR 0035)."""
		if flags.sandbox():  # chốt chặn cuối: sandbox không có HTTP ra Zalo
			raise ZaloError(None, "permanent", _("Chế độ thử (sandbox) đang bật: không gọi API Zalo"))
		refreshed = False
		attempt = 0
		retries = config.get("get_retries") if method == "GET" else 0
		while True:
			tok = self._access_token()
			try:
				sent_headers = {**(headers or {}), "access_token": tok}
				if files is None:
					sent_headers["Content-Type"] = "application/json"
				resp = requests.request(
					method,
					config.get("api_base") + path,
					headers=sent_headers,
					params=params,
					data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
					files=files,
					timeout=timeout or config.get("timeout"),
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
			raw = payload.get("message") if isinstance(payload, dict) else ""
			raise to_error(code if isinstance(code, int) else None, resp.status_code, raw or "")
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
			self.last_quota_type = _quota_type(data)
		return sent

	def send_buttons(self, user_id: str, text: str, buttons: list[dict]) -> str:
		"""Gửi tin tư vấn kèm nút (template, `oa.query.show`) trong MỘT tin; trả mã tin. Sandbox: không gọi HTTP.

		Payload của nút là CHỮ TRÊN NÚT (Zalo hiện payload thành tin của khách); `script.buttons` đổi về `#mã` khi tin về.

		Đã kiểm thật 17/11 (docs/bot_v3_plan.md mục 3): 5 nút, chỉ hiện trên app điện thoại. # VERIFY: giới hạn ký tự tiêu đề nút.
		"""
		text = (text or "").strip()
		if not text:
			raise ZaloError(None, "permanent", _("Tin nhắn rỗng"))
		if len(text) > BUTTON_TEXT_MAX:
			raise ZaloError(None, "permanent", _("Tin kèm nút dài quá {0} ký tự").format(BUTTON_TEXT_MAX))
		if flags.sandbox():
			frappe.logger("omni").info(
				"omni sandbox: bỏ qua gửi Zalo kèm nút (kênh %s, %s nút)", self.channel, len(buttons)
			)
			return f"sandbox:{uuid.uuid4().hex[:16]}"
		from omni_crm.script.buttons import payload as button_payload

		message = {
			"text": text,
			"attachment": {
				"type": "template",
				"payload": {
					"buttons": [
						{"title": b["label"], "type": "oa.query.show", "payload": button_payload(b)}
						for b in buttons
					]
				},
			},
		}
		data = self._request(
			"POST", config.get("send_path"), body={"recipient": {"user_id": user_id}, "message": message}
		)
		self.last_quota_type = _quota_type(data)
		return str(data.get("message_id") or "")

	def send_request_user_info(
		self, user_id: str, title: str, subtitle: str, image_url: str | None = None
	) -> str:
		"""Gửi tin yêu cầu thông tin người dùng (xin SĐT); trả mã tin. Sandbox: không gọi HTTP.

		Danh sách trắng (V3-07): công tắc `phone_request_enabled` tắt thì chỉ tài khoản Zalo thử nghiệm nhận được thẻ,
		mọi người khác bị chặn NGAY Ở ĐÂY, trước cả nhánh sandbox.
		"""
		from omni_crm.channels.zalo import phone_request

		if not phone_request.recipient_allowed(user_id):
			raise ZaloError(
				None,
				"permanent",
				_("Luồng xin số điện thoại đang tắt: chỉ tài khoản Zalo thử nghiệm nhận được thẻ xin số."),
			)
		if flags.sandbox():
			frappe.logger("omni").info(
				"omni sandbox: bỏ qua gửi yêu cầu thông tin Zalo (kênh %s)", self.channel
			)
			return f"sandbox:{uuid.uuid4().hex[:16]}"
		element: dict = {"title": title[:100], "subtitle": subtitle[:500]}
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
		self.last_quota_type = _quota_type(data)
		return str(data.get("message_id") or "")

	def send_media(
		self,
		user_id: str,
		kind: str,
		content: bytes,
		filename: str,
		*,
		caption: str = "",
		size: tuple[int, int] | None = None,
	) -> str:
		"""Tải ảnh/GIF/tệp lên Zalo rồi gửi tin tư vấn đính kèm (V3-09); trả mã tin. Sandbox: không gọi HTTP.

		Danh sách trắng: công tắc `zalo_media_send_enabled` tắt thì chỉ tài khoản Zalo thử nghiệm nhận được, chặn NGAY Ở
		ĐÂY, trước cả nhánh sandbox. Tải lên KHÔNG thử lại; lỗi sau khi tải mà chưa gửi thì lần thử sau tải lại.
		"""
		from omni_crm.channels.zalo import media_out

		if not media_out.recipient_allowed(user_id):
			raise ZaloError(
				None,
				"permanent",
				_("Gửi ảnh/tệp qua Zalo đang tắt: chỉ tài khoản Zalo thử nghiệm nhận được."),
			)
		if kind not in media_out.KINDS:
			raise ZaloError(None, "permanent", _("Loại đính kèm không hợp lệ"))
		if flags.sandbox():
			frappe.logger("omni").info("omni sandbox: bỏ qua gửi %s Zalo (kênh %s)", kind, self.channel)
			return f"sandbox:{uuid.uuid4().hex[:16]}"
		media_out.reserve_upload(kind)
		uploaded = self._request(
			"POST",
			config.get("upload_paths")[kind],
			files={"file": (filename, content, media_out.mime_of(filename))},
			timeout=config.get("upload_timeout"),
		)
		ref = str(uploaded.get("attachment_id") or uploaded.get("token") or "")
		if not ref:
			raise ZaloError(None, "permanent", _("Zalo không trả mã tệp sau khi tải lên"))
		if kind == media_out.FILE:
			message: dict = {"attachment": {"type": "file", "payload": {"token": ref}}}
		else:
			element: dict = {"media_type": kind, "attachment_id": ref}
			if kind == media_out.GIF and size:
				element["width"], element["height"] = int(size[0]), int(size[1])
			message = {
				"attachment": {
					"type": "template",
					"payload": {"template_type": "media", "elements": [element]},
				}
			}
			if caption:
				message["text"] = caption[: media_out.CAPTION_MAX]
		data = self._request(
			"POST", config.get("send_path"), body={"recipient": {"user_id": user_id}, "message": message}
		)
		self.last_quota_type = _quota_type(data)
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

	def get_oa_info(self) -> dict:
		"""Thông tin OA (V3-10, nút Kiểm tra quyền): {oa_id, name, ...}. Chỉ đọc. VERIFY: GET /v2.0/oa/getoa."""
		return self._request("GET", config.get("oa_info_path"))

	def get_cs_quota(self) -> list[dict]:
		"""Quỹ tin tư vấn của OA theo Zalo (POST /v3.0/oa/quota/message): [{quota_type, total, remain, valid_through}]."""
		data = self._request(
			"POST", config.get("quota_path"), body={"quota_owner": "OA", "product_type": "cs"}
		)
		rows = data if isinstance(data, list) else []
		return [r for r in rows if isinstance(r, dict)]
