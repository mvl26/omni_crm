"""Adapter Stringee (docs/07 mục 3). Đối chiếu developer.stringee.com ngày 2026-10-30, xem docs/12 mục C (G10).

Điểm CHƯA xác nhận được (không có tài khoản/tài liệu): định dạng phản hồi `get_customer_info_url`, bí mật ký
webhook có phải API key secret hay không, `id` của Call log có trùng `call_id` của sự kiện hay không, giá trị
`type` của sự kiện ghi âm. Mỗi chỗ đều ghi chú ở nơi dùng và ở docs/12.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import frappe
import jwt
import requests
from frappe import _
from frappe.utils import cint, get_system_timezone

from omni_crm import flags
from omni_crm.channels.telephony.base import INBOUND, OUTBOUND, CallEvent, LookupResult
from omni_crm.identity.normalize import normalize_phone

NAME = "Stringee"
API_BASE = "https://api.stringee.com"
API_HOST_SUFFIX = "stringee.com"  # chỉ gửi JWT tới máy chủ thuộc stringee.com
SIGN_HEADER = "x-stringee-signature"
AUTH_HEADER = "X-STRINGEE-AUTH"

JWT_TTL_SECONDS = 3600
JWT_REFRESH_MARGIN = 60
HTTP_TIMEOUT = 10  # giây (CLAUDE.md mục 3)
RECORDING_TIMEOUT = 60
MAX_RECORDING_BYTES = 100 * 1024 * 1024
GET_RETRIES = 2
LIST_PAGE_SIZE = 100
MAX_PAGES = 500  # chặn vòng lặp vô hạn nếu con trỏ không tiến
VERIFY_WINDOW = timedelta(hours=12)

# call_status của Stringee (sự kiện không kết thúc) -> trạng thái chuẩn. `agentEnded` chưa rõ ngữ nghĩa nên coi là
# sự kiện trung gian; cuộc gọi chỉ kết thúc khi có sự kiện `ended`.
STATUS_MAP: dict[str, str] = {
	"created": "Ringing",
	"started": "Ringing",
	"ringing": "Ringing",
	"answered": "Answered",
	"agentEnded": "Answered",
}
END_STATUS = "ended"
# endCallCause (sự kiện ended) -> trạng thái chuẩn khi KHÔNG có thời lượng nghe máy. Mã SIP là chuỗi bắt đầu bằng số.
# Stringee không có trạng thái Voicemail trong tài liệu nên không ánh xạ ra Voicemail.
CAUSE_MAP: dict[str, str] = {
	"CAN_NOT_MAKE_CALL": "Failed",
	"486": "Busy",
	"600": "Busy",
	"480": "Missed",
	"487": "Missed",
	"603": "Missed",
	"USER_END_CALL": "Missed",
	"MAX_CONNECT_TIME": "Missed",
}
INBOUND_REASONS = frozenset({"EXTERNAL_CALL_IN"})
OUTBOUND_REASONS = frozenset(
	{
		"CLIENT_MAKE_CALL",
		"SERVER_CALL_OUT_BY_MAKE_CALL_TO_APP_BEFORE",
		"SERVER_CALL_OUT_BY_MAKE_CALL_TO_EXTERNAL_BEFORE",
	}
)

_RECORD_ID = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_jwt_cache: dict[tuple[str, str, str], tuple[str, float]] = {}


class StringeeError(Exception):
	"""Lỗi gọi API Stringee; thông điệp không chứa bí mật."""


def cause_status(cause: str | None, answered: bool) -> str:
	"""Trạng thái chuẩn của cuộc gọi đã kết thúc theo endCallCause và việc có nghe máy hay không."""
	if answered:
		return "Answered"
	key = str(cause or "").strip()
	if key in CAUSE_MAP:
		return CAUSE_MAP[key]
	code = re.match(r"^(\d{3})\b", key)
	if code and code.group(1) in CAUSE_MAP:
		return CAUSE_MAP[code.group(1)]
	return "Missed"


def _secret(channel_doc) -> str:
	return channel_doc.get_password("telephony_api_key_secret", raise_exception=False) or ""


def _sid(channel_doc) -> str:
	return (channel_doc.get("telephony_api_key_sid") or "").strip()


def _to_local(ms: Any) -> datetime | None:
	"""Mili-giây Unix -> datetime theo múi giờ hệ thống, không tzinfo (kiểu Frappe)."""
	try:
		value = float(ms)
	except (TypeError, ValueError):
		return None
	if value <= 0:
		return None
	utc = datetime.fromtimestamp(value / 1000, timezone.utc)
	return utc.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _to_ms(dt: datetime) -> int:
	return int(dt.replace(tzinfo=ZoneInfo(get_system_timezone())).timestamp() * 1000)


def _party(obj: Any) -> tuple[str, str]:
	"""(số, kiểu internal/external) của đối tượng from/to."""
	if not isinstance(obj, dict):
		return "", ""
	return str(obj.get("number") or obj.get("alias") or ""), str(obj.get("type") or "")


def _number(raw: str) -> str:
	return normalize_phone(raw) or raw


def _direction(d: dict, from_type: str) -> str:
	reason = d.get("callCreatedReason")
	if reason in INBOUND_REASONS:
		return INBOUND
	if reason in OUTBOUND_REASONS:
		return OUTBOUND
	return INBOUND if from_type == "external" else OUTBOUND  # nội bộ -> nội bộ: chưa xác nhận


def _recording_event(d: dict) -> CallEvent:
	"""Sự kiện ghi âm (eventUrl của hành động record): chỉ mang call_id và recording_url, là sự kiện trung gian."""
	return CallEvent(
		call_id=str(d["call_id"]),
		direction=INBOUND,
		from_number="",
		to_number="",
		status="Answered",
		recording_ref=str(d["recording_url"]),
		raw=d,
	)


def event_from_payload(d: dict) -> CallEvent:
	"""CallEvent từ một payload sự kiện của Stringee; thiếu call_id/call_status thì ném KeyError/ValueError."""
	if d.get("recording_url") and "call_status" not in d:
		return _recording_event(d)
	call_id = str(d["call_id"])
	raw_status = str(d["call_status"])
	from_num, from_type = _party(d.get("from"))
	to_num, to_type = _party(d.get("to"))
	direction = _direction(d, from_type)
	at = _to_local(d.get("timestamp_ms"))
	agent = None
	if direction == INBOUND and to_type == "internal":
		agent = to_num
	elif direction == OUTBOUND and from_type == "internal":
		agent = from_num
	common = dict(
		call_id=call_id,
		direction=direction,
		from_number=_number(from_num),
		to_number=_number(to_num),
		agent_ref=agent or None,
		raw=d,
	)
	if raw_status == END_STATUS:
		duration = cint(d.get("duration"))
		answered_for = cint(d.get("answerDuration"))
		status = cause_status(d.get("endCallCause"), answered_for > 0)
		started_at = at - timedelta(seconds=duration) if at and duration else None
		answered_at = at - timedelta(seconds=answered_for) if at and answered_for > 0 else None
		return CallEvent(
			status="Ended" if status == "Answered" else status,
			started_at=started_at,
			answered_at=answered_at,
			ended_at=at,
			duration_seconds=answered_for if answered_for > 0 else 0,
			**common,
		)
	if raw_status not in STATUS_MAP:
		raise ValueError("call_status không biết")
	mapped = STATUS_MAP[raw_status]
	return CallEvent(
		status=mapped,
		started_at=at if mapped == "Ringing" else None,
		answered_at=at if raw_status == "answered" else None,
		**common,
	)


def event_from_log(item: dict) -> CallEvent:
	"""CallEvent từ một bản ghi Call log; log không có lý do kết thúc nên trạng thái suy ra từ answer_time."""
	answered_at = _to_local(item.get("answer_time"))
	answered_for = cint(item.get("answer_duration"))
	direction = OUTBOUND if cint(item.get("from_internal")) else INBOUND
	recorded = bool(cint(item.get("recorded")) and item.get("record_path"))
	return CallEvent(
		call_id=str(item["id"]),
		direction=direction,
		from_number=_number(str(item.get("from_number") or "")),
		to_number=_number(str(item.get("to_number") or "")),
		status="Ended" if answered_at else "Missed",
		started_at=_to_local(item.get("start_time")),
		answered_at=answered_at,
		ended_at=_to_local(item.get("stop_time")),
		duration_seconds=answered_for if answered_at else 0,
		agent_ref=(item.get("from_user_id") if direction == OUTBOUND else None) or None,
		recording_ref=str(item["record_path"]) if recorded else None,
		raw=item,
	)


def _recording_url(ref: str) -> str:
	"""URL tải ghi âm: URL https thuộc stringee.com, hoặc mã ghi âm -> endpoint chuẩn. Khác thì từ chối (chống SSRF)."""
	ref = (ref or "").strip()
	if ref.lower().startswith(("http://", "https://")):
		parts = urlparse(ref)
		host = (parts.hostname or "").lower()
		if parts.scheme != "https" or not (host == API_HOST_SUFFIX or host.endswith("." + API_HOST_SUFFIX)):
			raise StringeeError(_("Địa chỉ ghi âm không thuộc Stringee"))
		return ref
	if not _RECORD_ID.match(ref):
		raise StringeeError(_("Mã ghi âm không hợp lệ"))
	return f"{API_BASE}/v1/call/recording/{ref}"


class StringeeProvider:
	"""Nhà cung cấp tổng đài Stringee."""

	name = NAME

	def _jwt(self, channel_doc) -> str:
		"""JWT HS256 cho REST API (docs/07 mục 3), hạn 1 giờ, dùng lại đến gần hết hạn."""
		sid, secret = _sid(channel_doc), _secret(channel_doc)
		if not sid or not secret:
			raise StringeeError(_("Kênh Stringee chưa có API key SID/secret"))
		key = (channel_doc.name, sid, hashlib.sha256(secret.encode()).hexdigest())
		now = time.time()
		cached = _jwt_cache.get(key)
		if cached and cached[1] - JWT_REFRESH_MARGIN > now:
			return cached[0]
		exp = int(now) + JWT_TTL_SECONDS
		token = jwt.encode(
			{"jti": f"{sid}-{int(now)}", "iss": sid, "exp": exp, "rest_api": True},
			secret,
			algorithm="HS256",
			headers={"typ": "JWT", "cty": "stringee-api;v=1"},
		)
		_jwt_cache[key] = (token, float(exp))
		return token

	def verify_request(self, raw_body: bytes, headers: dict, query: dict, channel_doc) -> bool:
		"""Có header X-STRINGEE-SIGNATURE thì phải đúng HMAC-SHA1(body) base64; không có thì dựa vào token `t`.

		Bí mật ký là API key secret của kênh: CHƯA xác nhận (tài liệu ghi "project's signing secret key").
		"""
		lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
		supplied = lowered.get(SIGN_HEADER)
		if not supplied:
			return True
		secret = _secret(channel_doc)
		if not secret:
			return True  # chưa cấu hình bí mật: chỉ còn token
		digest = hmac.new(secret.encode(), raw_body, hashlib.sha1).digest()
		return hmac.compare_digest(base64.b64encode(digest), str(supplied).strip().encode())

	def parse_event(self, raw_body: bytes, headers: dict, channel_doc) -> list[CallEvent]:
		"""Một đối tượng JSON (hoặc danh sách) sự kiện cuộc gọi; lỗi định dạng ném ValueError/KeyError."""
		data = json.loads(raw_body)
		items = data if isinstance(data, list) else [data]
		return [event_from_payload(d) for d in items if isinstance(d, dict)]

	def parse_lookup_request(self, query: dict, raw_body: bytes) -> tuple[str, str]:
		"""(from, to) trong query GET của get_customer_info_url (tham số from, to, project)."""
		return str(query.get("from") or ""), str(query.get("to") or "")

	def lookup_response(self, result: LookupResult, channel_doc) -> dict | str:
		"""JSON gửi về Stringee. Định dạng CHƯA được Stringee quy định (xem docs/12); khách mới: found = False."""
		if result.is_new or not result.lead:
			return {"found": False}
		return {
			"found": True,
			"customer_name": result.customer_name,
			"lead": result.lead,
			"owner_name": result.owner_name,
			"lead_url": result.lead_url,
		}

	def _get(self, channel_doc, url: str, params: dict | None = None, timeout=HTTP_TIMEOUT, stream=False):
		"""GET kèm JWT; thử lại tối đa GET_RETRIES lần khi lỗi mạng/5xx; không theo chuyển hướng; không log bí mật."""
		if flags.sandbox():
			raise StringeeError(_("Chế độ thử (sandbox) đang bật: không gọi API Stringee"))
		attempt = 0
		while True:
			try:
				resp = requests.get(
					url,
					params=params,
					headers={AUTH_HEADER: self._jwt(channel_doc), "Accept": "*/*"},
					timeout=timeout,
					stream=stream,
					allow_redirects=False,
				)
			except requests.RequestException as e:
				if attempt < GET_RETRIES:
					attempt += 1
					time.sleep(min(attempt, 2))
					continue
				frappe.logger("omni").warning("omni stringee: loi mang (%s)", type(e).__name__)
				raise StringeeError(_("Không kết nối được Stringee")) from e
			if resp.status_code >= 500 and attempt < GET_RETRIES:
				resp.close()
				attempt += 1
				time.sleep(min(attempt, 2))
				continue
			if resp.status_code != 200:
				code = resp.status_code
				resp.close()
				frappe.logger("omni").warning("omni stringee: HTTP %s", code)
				raise StringeeError(_("Stringee trả lỗi HTTP {0}").format(code))
			return resp

	def fetch_recording(self, recording_ref: str, channel_doc) -> tuple[bytes, str]:
		"""Tải ghi âm (GET kèm JWT); từ chối địa chỉ ngoài stringee.com và tệp quá 100 MB."""
		url = _recording_url(recording_ref)
		resp = self._get(channel_doc, url, timeout=RECORDING_TIMEOUT, stream=True)
		try:
			if cint(resp.headers.get("Content-Length")) > MAX_RECORDING_BYTES:
				raise StringeeError(_("Ghi âm quá lớn"))
			ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
			if ctype and not (ctype.startswith("audio/") or ctype == "application/octet-stream"):
				raise StringeeError(_("Ghi âm không phải tệp âm thanh"))
			chunks, size = [], 0
			for chunk in resp.iter_content(64 * 1024):
				size += len(chunk)
				if size > MAX_RECORDING_BYTES:
					raise StringeeError(_("Ghi âm quá lớn"))
				chunks.append(chunk)
		finally:
			resp.close()
		if not size:
			raise StringeeError(_("Ghi âm rỗng"))
		mime = (
			ctype if ctype.startswith("audio/") else ("audio/wav" if url.endswith(".wav") else "audio/mpeg")
		)
		return b"".join(chunks), mime

	def _log_page(self, channel_doc, params: dict) -> dict:
		resp = self._get(channel_doc, f"{API_BASE}/v1/call/log", params=params)
		try:
			payload = resp.json()
		except ValueError as e:
			raise StringeeError(_("Phản hồi Call log không phải JSON")) from e
		finally:
			resp.close()
		if not isinstance(payload, dict) or payload.get("r") not in (0, "0", None):
			raise StringeeError(_("Call log trả lỗi"))
		return payload.get("data") or {}

	def list_calls(self, since: datetime, until: datetime, channel_doc) -> list[CallEvent]:
		"""Call log trong [since, until) theo thời điểm bắt đầu, phân trang search_after tới hết."""
		params: dict[str, Any] = {
			"version": 2,
			"limit": LIST_PAGE_SIZE,
			"sort_by": "start_time",
			"sort_order": "asc",
			"from_start_time": _to_ms(since),
			"to_start_time": _to_ms(until) - 1,
		}
		out: list[CallEvent] = []
		seen: set[str] = set()
		cursor: list | None = None
		for _page in range(MAX_PAGES):
			page_params = dict(params)
			if cursor:
				page_params["search_after[]"] = [str(c) for c in cursor]
			data = self._log_page(channel_doc, page_params)
			calls = data.get("calls") or []
			for item in calls:
				if not isinstance(item, dict) or not item.get("id") or not cint(item.get("stop_time")):
					continue  # chưa kết thúc hoặc thiếu mã
				if str(item["id"]) in seen:
					continue
				seen.add(str(item["id"]))
				out.append(event_from_log(item))
			cursor = data.get("search_after")
			if not calls or not cursor:
				break
		return out

	def call_exists(self, call_id: str, around: datetime, channel_doc) -> bool:
		"""Cuộc gọi `call_id` có trong Call log (cửa sổ ±12 giờ quanh `around`) không; lỗi API -> StringeeError.

		Giả định CHƯA xác nhận: `id` của Call log trùng `call_id` của sự kiện. Không lọc theo `id` vì tài liệu
		không liệt kê tham số đó; so sánh phía ta.
		"""
		found = self.list_calls(around - VERIFY_WINDOW, around + VERIFY_WINDOW, channel_doc)
		return any(c.call_id == str(call_id) for c in found)

	def click_to_call(self, agent_ref: str, to_number: str, channel_doc) -> str:
		"""Chưa hỗ trợ ở Đợt 2."""
		raise NotImplementedError("Stringee: gọi ra từ CRM (click-to-call) chưa được cài đặt")
