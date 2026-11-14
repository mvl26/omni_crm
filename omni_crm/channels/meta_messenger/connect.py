"""Kết nối Page Facebook cho kênh Meta Messenger bằng Facebook Login for Business (ADR 0028).

Luồng, chỉ Omni Admin làm được:

1. `start_url(channel)`: sinh state gắn với người đang đăng nhập, trả URL hộp thoại đăng nhập Facebook
   theo cấu hình Facebook Login for Business (`meta_login_config_id`).
2. `complete(code, state)`: Meta chuyển về callback. Kiểm state (dùng một lần, đúng người), đổi code lấy
   user token, cất user token trong cache tối đa 10 phút dưới một mã phiên; trả (kênh, mã phiên).
3. `list_pages(session)`: `GET /me/accounts` (quyền `pages_show_list`); chỉ trả mã và tên Page, không trả token.
4. `subscribe(session, page_id)`: lấy token Page từ user token, đọc `GET /{page}/subscribed_apps`, gộp các trường
   đang có với `PAGE_FIELDS`, gửi `POST /{page}/subscribed_apps` (quyền `pages_manage_metadata`), đọc lại, lưu kết quả.

Danh sách trường gửi lên thay cho danh sách cũ của Page (không cộng dồn), nên luôn gộp với trường đang có:
thiếu `leadgen` là Lead Ads ngừng vào CRM.

Token Page lấy trong luồng này KHÔNG được lưu: vận hành vẫn dùng token System User ở `meta_page_access_token`.
User token chỉ nằm trong cache và bị xóa ngay khi đăng ký xong. Không token nào vào log hay thông điệp lỗi.
Chuỗi hiện cho người dùng viết bằng tiếng Anh (bản dịch ở translations/vi.csv) vì video App Review quay giao diện tiếng Anh.
"""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import quote, urlencode

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

from omni_crm import flags
from omni_crm.channels.meta_messenger.client import (
	DEFAULT_VERSION,
	GRAPH,
	PERMANENT,
	TIMEOUT,
	TRANSIENT,
	VERSION_CONF,
	MessengerClient,
	MessengerError,
)

CHANNEL_DOCTYPE = "Omni Channel"
CHANNEL_TYPE = "Meta Messenger"
ADMIN_ROLE = "Omni Admin"
DIALOG_BASE = "https://www.facebook.com"
CALLBACK_METHOD = "omni_crm.api.meta_connect.callback"
REDIRECT_CONF = "omni_meta_oauth_redirect_uri"  # site_config, tùy chọn: khi URL tự dựng sai giao thức/cổng
STATE_TTL = 600  # giây: từ lúc bấm nút tới lúc Meta chuyển về
SESSION_TTL = 600  # giây: từ lúc Meta chuyển về tới lúc chọn Page xong
# Trùng PAGE_FIELDS của miyano-intake nhánh deploy/yhct (intake/channels/meta.py), nơi nhận webhook của Page.
PAGE_FIELDS = (
	"messages",
	"message_echoes",
	"message_reactions",
	"messaging_postbacks",
	"messaging_referrals",
	"standby",
	"leadgen",
)


def redirect_uri() -> str:
	"""URL callback phải khai đúng từng ký tự ở Facebook Login for Business > Settings > Valid OAuth Redirect URIs.

	Mặc định Frappe dựng từ request (giao thức theo X-Forwarded-Proto). Sau proxy không gửi header này thì URL ra
	`http://` và Meta chặn; khi đó đặt site_config `omni_meta_oauth_redirect_uri` bằng đúng URL đã khai.
	"""
	return frappe.conf.get(REDIRECT_CONF) or frappe.utils.get_url(f"/api/method/{CALLBACK_METHOD}")


def form_url(channel: str, params: dict[str, str] | None = None) -> str:
	url = frappe.utils.get_url(f"/app/omni-channel/{quote(channel, safe='')}")
	return f"{url}?{urlencode(params)}" if params else url


def require_admin() -> None:
	if ADMIN_ROLE not in frappe.get_roles():
		frappe.throw(_("Only an Omni Admin can connect a Facebook Page"), frappe.PermissionError)


def _version() -> str:
	return frappe.conf.get(VERSION_CONF) or DEFAULT_VERSION


def _state_key(state: str) -> str:
	return f"omni:meta:connect:state:{state}"


def _session_key(session: str) -> str:
	return f"omni:meta:connect:session:{session}"


def _no_sandbox() -> None:
	if flags.sandbox():
		frappe.throw(_("Sandbox mode is on in Omni Settings, so Miyano CRM does not call Meta"))


def _channel(name: str) -> Any:
	doc = frappe.get_doc(CHANNEL_DOCTYPE, name)
	if doc.channel_type != CHANNEL_TYPE:
		frappe.throw(_("This channel is not a Meta Messenger channel"))
	return doc


def _app(doc: Any) -> tuple[str, str, str]:
	"""(App ID, App Secret, configuration ID); thiếu cái nào thì báo đúng tên ô cần điền."""
	app_id = str(doc.get("meta_app_id") or "").strip()
	secret = doc.get_password("meta_app_secret", raise_exception=False) or ""
	config_id = str(doc.get("meta_login_config_id") or "").strip()
	missing = [
		label
		for label, value in (
			("Meta: App ID", app_id),
			("Meta: App Secret", secret),
			("Facebook Login configuration ID", config_id),
		)
		if not value
	]
	if missing:
		frappe.throw(_("Fill in {0} on this channel first").format(", ".join(missing)))
	return app_id, secret, config_id


def _pop(key: str) -> dict | None:
	"""Đọc rồi xóa; DEL trả 1 cho đúng một lời gọi nên hai callback đua nhau chỉ một cái dùng được."""
	cache = frappe.cache()
	info = cache.get_value(key, expires=True)
	if not info or cache.delete(cache.make_key(key)) != 1:
		return None
	return info


def _session(session: str) -> dict:
	info = frappe.cache().get_value(_session_key(session), expires=True) if session else None
	if not info or info.get("user") != frappe.session.user:
		frappe.throw(
			_("The Facebook login has expired. Click Connect Facebook Page again."), frappe.PermissionError
		)
	return info


def start_url(channel: str) -> str:
	"""URL hộp thoại Facebook Login for Business cho kênh này."""
	require_admin()
	_no_sandbox()
	doc = _channel(channel)
	app_id, _secret, config_id = _app(doc)
	state = secrets.token_urlsafe(24)
	frappe.cache().set_value(
		_state_key(state), {"channel": doc.name, "user": frappe.session.user}, expires_in_sec=STATE_TTL
	)
	query = urlencode(
		{
			"client_id": app_id,
			"redirect_uri": redirect_uri(),
			"state": state,
			"config_id": config_id,
			"response_type": "code",
			"override_default_response_type": "true",
		}
	)
	return f"{DIALOG_BASE}/{_version()}/dialog/oauth?{query}"


def cancel(state: str | None) -> str | None:
	"""Người dùng bấm Hủy trong hộp thoại: bỏ state, trả tên kênh để quay về form (None nếu state lạ)."""
	require_admin()
	info = _pop(_state_key(state)) if state else None
	if not info or info.get("user") != frappe.session.user:
		return None
	return info["channel"]


def complete(code: str, state: str) -> tuple[str, str]:
	"""Kiểm state, đổi code lấy user token, cất vào cache; trả (tên kênh, mã phiên)."""
	require_admin()
	_no_sandbox()
	info = _pop(_state_key(state)) if state else None
	if not info or info.get("user") != frappe.session.user:
		frappe.throw(
			_("The Facebook login is invalid or has expired. Click Connect Facebook Page again."),
			frappe.PermissionError,
		)
	doc = _channel(info["channel"])
	app_id, secret, _config = _app(doc)
	user_token = _exchange_code(app_id, secret, code)
	session = secrets.token_urlsafe(24)
	frappe.cache().set_value(
		_session_key(session),
		{"channel": doc.name, "user": frappe.session.user, "token": user_token},
		expires_in_sec=SESSION_TTL,
	)
	return doc.name, session


def _exchange_code(app_id: str, secret: str, code: str) -> str:
	"""Đổi code lấy user token. URL và phản hồi có bí mật nên không đưa vào thông điệp lỗi."""
	try:
		resp = requests.get(
			f"{GRAPH}/{_version()}/oauth/access_token",
			params={
				"client_id": app_id,
				"client_secret": secret,
				"redirect_uri": redirect_uri(),
				"code": code,
			},
			timeout=TIMEOUT,
		)
	except requests.RequestException as e:
		raise MessengerError(None, TRANSIENT, _("Could not reach Meta ({0})").format(type(e).__name__)) from e
	try:
		data = resp.json()
	except ValueError:
		data = {}
	data = data if isinstance(data, dict) else {}
	token = data.get("access_token")
	if resp.status_code >= 400 or not token:
		code_ = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else None
		raise MessengerError(
			code_,
			PERMANENT,
			_("Meta did not accept the login (error {0}). Click Connect Facebook Page again.").format(code_),
			resp.status_code,
		)
	return str(token)


def list_pages(session: str) -> dict:
	"""Các Page người dùng vừa cấp quyền (`GET /me/accounts`); chỉ mã và tên."""
	require_admin()
	info = _session(session)
	doc = _channel(info["channel"])
	data = MessengerClient(doc)._request(
		"GET", "/me/accounts", params={"fields": "id,name", "limit": 100}, token=info["token"]
	)
	pages = [
		{"id": str(p["id"]), "name": str(p.get("name") or p["id"])}
		for p in data.get("data") or []
		if isinstance(p, dict) and p.get("id")
	]
	return {
		"channel": doc.name,
		"current_page_id": str(doc.get("meta_page_id") or "").strip(),
		"pages": pages,
	}


def _app_fields(client: MessengerClient, page_id: str, page_token: str, app_id: str) -> list[str]:
	"""Trường webhook mà Page đang đăng ký cho ứng dụng này ([] nếu chưa đăng ký)."""
	data = client._request("GET", f"/{page_id}/subscribed_apps", token=page_token)
	for app in data.get("data") or []:
		if isinstance(app, dict) and str(app.get("id")) == app_id:
			return [str(f) for f in app.get("subscribed_fields") or []]
	return []


def subscribe(session: str, page_id: str) -> dict:
	"""Đăng ký webhook cho Page đã chọn, đọc lại và lưu kết quả lên kênh."""
	require_admin()
	_no_sandbox()
	info = _session(session)
	doc = _channel(info["channel"])
	page_id = str(page_id or "").strip()
	if not page_id.isdigit():
		frappe.throw(_("Invalid Page ID"))
	current = str(doc.get("meta_page_id") or "").strip()
	if current and current != page_id:
		frappe.throw(
			_("This channel is linked to Page {0}. Select that Page, or change Meta: Page ID first.").format(
				current
			)
		)
	app_id = str(doc.get("meta_app_id") or "").strip()
	client = MessengerClient(doc)
	page = client._request(
		"GET", f"/{page_id}", params={"fields": "id,name,access_token"}, token=info["token"]
	)
	page_token = page.get("access_token")
	if not page_token:
		frappe.throw(
			_(
				"Facebook did not give access to this Page. Select the Page in the Facebook dialog and try again."
			)
		)
	before = _app_fields(client, page_id, page_token, app_id)
	wanted = list(dict.fromkeys([*before, *PAGE_FIELDS]))
	client._request(
		"POST",
		f"/{page_id}/subscribed_apps",
		params={"subscribed_fields": ",".join(wanted)},
		token=page_token,
	)
	after = _app_fields(client, page_id, page_token, app_id)
	page_name = str(page.get("name") or page_id)
	if not current:
		doc.meta_page_id = page_id
	doc.meta_page_name = page_name
	doc.meta_connected_at = now_datetime()
	doc.meta_connected_by = frappe.session.user
	doc.meta_subscribed_fields = ", ".join(after)
	doc.flags.ignore_permissions = True
	doc.save()
	cache = frappe.cache()
	cache.delete(cache.make_key(_session_key(session)))
	frappe.logger("omni").info(
		"omni meta connect: kênh %s, Page %s, %s trường webhook", doc.name, page_id, len(after)
	)
	return {
		"page_id": page_id,
		"page_name": page_name,
		"fields": after,
		"missing": [f for f in wanted if f not in after],
	}
