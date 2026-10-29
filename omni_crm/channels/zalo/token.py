"""Quản lý token Zalo OA: cấp quyền OAuth v4 (PKCE), làm mới có khóa, cảnh báo khi lỗi (docs/06 mục 2).

CHƯA XÁC NHẬN VỚI API THẬT (docs/12 G8): endpoint, tên tham số, `secret_key` trong header, PKCE S256, đơn vị
`expires_in` (giây). Refresh token dùng một lần: mỗi lần làm mới nhận cặp token mới và lưu ngay trong một
transaction trước khi dùng.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, now_datetime

from omni_crm.channels.zalo import config
from omni_crm.channels.zalo.errors import PERMANENT, ZaloError
from omni_crm.pipeline.retry import redact

CHANNEL = "Omni Channel"
LOCK_WAIT = 15  # giây chờ khóa làm mới của tiến trình khác
STATE_TTL = 600  # giây: state/PKCE verifier của luồng cấp quyền
ALERT_TTL = 3600  # giây: tối đa 1 thư cảnh báo mỗi giờ mỗi kênh/loại


def redirect_uri() -> str:
	"""URL callback OAuth của site này."""
	return frappe.utils.get_url("/api/method/omni_crm.api.webhooks.zalo_oauth_callback")


def _state_key(state: str) -> str:
	return f"omni:zalo:oauth:{state}"


def _alert(channel: str, kind: str, detail: str) -> None:
	"""Gửi email cảnh báo tới alert_recipients (đã lọc bí mật); lỗi gửi chỉ ghi log. Tối đa 1 thư/giờ."""
	try:
		cache = frappe.cache()
		key = f"omni:alert:zalo:{channel}:{kind}"
		if cache.get_value(key, expires=True):
			return
		cache.set_value(key, 1, expires_in_sec=ALERT_TTL)
		raw = frappe.db.get_single_value("Omni Settings", "alert_recipients") or ""
		recipients = [r.strip() for r in raw.replace(",", "\n").splitlines() if r.strip()]
		subject = _("[Omni CRM] Cảnh báo Zalo OA: {0}").format(kind)
		body = redact(f"Kênh: {channel}\n{detail}")
		frappe.logger("omni").warning("omni zalo cảnh báo: %s (kênh %s)", kind, channel)
		if recipients:
			frappe.sendmail(recipients=recipients, subject=subject, message=body.replace("\n", "<br>"))
	except Exception:
		frappe.log_error(title="Omni: không gửi được cảnh báo Zalo", message=f"kenh={channel}")


def _parse_token_response(resp: requests.Response) -> dict[str, Any]:
	try:
		data = resp.json()
	except ValueError:
		raise ZaloError(None, PERMANENT, _("Phản hồi cấp token của Zalo không đọc được"), resp.status_code)
	if not data.get("access_token"):
		code = data.get("error")
		reason = redact(str(data.get("error_reason") or data.get("error_name") or ""))[:200]
		raise ZaloError(
			code if isinstance(code, int) else None,
			PERMANENT,
			_("Zalo từ chối cấp/làm mới token (mã {0}): {1}").format(code, reason),
			resp.status_code,
		)
	return data


def _post_token(app_id: str, app_secret: str, form: dict) -> dict[str, Any]:
	"""POST tới endpoint token của OAuth v4; không ghi log nội dung (có token)."""
	url = config.get("oauth_base") + config.get("oauth_token_path")
	try:
		resp = requests.post(
			url,
			headers={"secret_key": app_secret},
			data={"app_id": app_id, **form},
			timeout=config.get("timeout"),
		)
	except requests.RequestException as e:
		raise ZaloError(None, "transient", _("Không kết nối được máy chủ cấp token Zalo")) from e
	return _parse_token_response(resp)


def save_tokens(channel: str, data: dict) -> None:
	"""Lưu access token, refresh token và hạn trong MỘT transaction rồi commit."""
	doc = frappe.get_doc(CHANNEL, channel)
	doc.zalo_access_token = data["access_token"]
	if data.get("refresh_token"):
		doc.zalo_refresh_token = data["refresh_token"]
	doc.zalo_token_expires_at = now_datetime() + timedelta(seconds=int(data.get("expires_in") or 0))
	doc.last_error = None
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()


def needs_refresh(doc) -> bool:
	"""Hết hạn hoặc còn dưới `refresh_before_hours` giờ (chưa có hạn mà có refresh token cũng làm mới)."""
	if not doc.get_password("zalo_refresh_token", raise_exception=False):
		return False
	expires = doc.zalo_token_expires_at
	if not expires:
		return True
	return expires <= add_to_date(now_datetime(), hours=config.get("refresh_before_hours"))


def refresh_channel(channel: str, force: bool = False, stale_access_token: str | None = None) -> bool:
	"""Làm mới token của kênh dưới khóa Redis; True nếu đã gọi API thành công.

	Hai tiến trình đồng thời: tiến trình vào sau lấy khóa xong sẽ đọc lại DB và bỏ qua nếu token đã mới
	(`force` + `stale_access_token` = token vừa bị Zalo từ chối; khác token hiện tại nghĩa là đã làm mới).
	Không lấy được khóa trong LOCK_WAIT giây thì trả False. Lỗi API -> cảnh báo rồi ném ZaloError.
	"""
	cache = frappe.cache()
	lock = cache.lock(
		cache.make_key(f"omni:zalo:refresh:{channel}"),
		timeout=config.get("refresh_lock_seconds"),
		blocking_timeout=LOCK_WAIT,
	)
	if not lock.acquire():
		return False
	try:
		frappe.db.commit()  # kết thúc transaction cũ -> thấy token do tiến trình khác vừa lưu
		doc = frappe.get_doc(CHANNEL, channel)
		current = doc.get_password("zalo_access_token", raise_exception=False)
		if force and stale_access_token and current != stale_access_token:
			return False
		if not force and not needs_refresh(doc):
			return False
		refresh_token = doc.get_password("zalo_refresh_token", raise_exception=False)
		if not refresh_token:
			raise ZaloError(None, PERMANENT, _("Kênh chưa có refresh token, cần cấp quyền lại"))
		try:
			data = _post_token(
				doc.zalo_app_id,
				doc.get_password("zalo_app_secret", raise_exception=False) or "",
				{"refresh_token": refresh_token, "grant_type": "refresh_token"},
			)
		except ZaloError as e:
			frappe.db.set_value(
				CHANNEL, channel, "last_error", redact(e.message_vi)[:500], update_modified=False
			)
			frappe.db.commit()
			_alert(channel, "làm mới token thất bại", e.message_vi)
			raise
		save_tokens(channel, data)
		return True
	finally:
		try:
			lock.release()
		except Exception:  # khóa đã hết hạn: bỏ qua
			pass


def refresh_if_needed() -> None:
	"""Tác vụ định kỳ (*/30 phút): làm mới token mọi kênh Zalo OA đang bật khi sắp hết hạn."""
	for name in frappe.get_all(CHANNEL, filters={"channel_type": "Zalo OA", "enabled": 1}, pluck="name"):
		try:
			doc = frappe.get_doc(CHANNEL, name)
			if needs_refresh(doc):
				refresh_channel(name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi làm mới token Zalo", message=f"kenh={name}")
			frappe.db.commit()


# --- Luồng cấp quyền ban đầu (OAuth v4 + PKCE) ---


def _pkce_pair() -> tuple[str, str]:
	verifier = secrets.token_urlsafe(48)
	digest = hashlib.sha256(verifier.encode()).digest()
	return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def start_authorization(channel: str) -> str:
	"""Sinh state + PKCE, lưu state vào kênh và trả URL trang cấp quyền của Zalo."""
	doc = frappe.get_doc(CHANNEL, channel)
	if doc.channel_type != "Zalo OA" or not doc.zalo_app_id:
		frappe.throw(_("Kênh không phải Zalo OA hoặc thiếu App ID"))
	state = secrets.token_urlsafe(24)
	verifier, challenge = _pkce_pair()
	frappe.cache().set_value(
		_state_key(state), {"channel": channel, "verifier": verifier}, expires_in_sec=STATE_TTL
	)
	frappe.db.set_value(CHANNEL, channel, "zalo_oauth_state", state, update_modified=False)
	frappe.db.commit()
	query = urlencode(
		{
			"app_id": doc.zalo_app_id,
			"redirect_uri": redirect_uri(),
			"code_challenge": challenge,
			"code_challenge_method": "S256",
			"state": state,
		}
	)
	return config.get("oauth_base") + config.get("oauth_permission_path") + "?" + query


def complete_authorization(code: str, state: str) -> str:
	"""Kiểm state (chống giả mạo), đổi code lấy token, lưu; trả tên kênh. Ném lỗi nếu state sai/hết hạn."""
	info = frappe.cache().get_value(_state_key(state or ""), expires=True) if state else None
	if not info:
		frappe.throw(_("Phiên cấp quyền không hợp lệ hoặc đã hết hạn"), frappe.PermissionError)
	channel = info["channel"]
	stored = frappe.db.get_value(CHANNEL, channel, "zalo_oauth_state") or ""
	if not secrets.compare_digest(stored.encode(), state.encode()):
		frappe.throw(_("Phiên cấp quyền không hợp lệ hoặc đã hết hạn"), frappe.PermissionError)
	frappe.cache().delete_value(_state_key(state))  # state dùng một lần
	frappe.db.set_value(CHANNEL, channel, "zalo_oauth_state", None, update_modified=False)
	frappe.db.commit()
	doc = frappe.get_doc(CHANNEL, channel)
	data = _post_token(
		doc.zalo_app_id,
		doc.get_password("zalo_app_secret", raise_exception=False) or "",
		{"code": code, "grant_type": "authorization_code", "code_verifier": info["verifier"]},
	)
	save_tokens(channel, data)
	return channel
