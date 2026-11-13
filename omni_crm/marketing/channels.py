"""Kênh mạng xã hội của Marketing Hub (V3-10): DÙNG LẠI Omni Channel (một OA Zalo, Page Facebook của kênh Meta).

Không có bảng token riêng: token đọc từ Omni Channel bằng get_password, làm mới Zalo do job sẵn có (token dùng chung với
chat). Nút "Kiểm tra quyền" chỉ GỌI ĐỌC; kết quả (không có token, chỉ 4 ký tự cuối) lưu ở
Omni Marketing Settings.channel_checks theo tên kênh.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from omni_crm.marketing import rules

CHANNEL = "Omni Channel"
SETTINGS = "Omni Marketing Settings"
CONNECTED, RECONNECT, NOT_CONNECTED = "Đã kết nối", "Cần kết nối lại", "Chưa kết nối"
FB_REQUIRED = ("pages_manage_posts", "pages_read_engagement", "pages_manage_engagement", "read_insights")
PLATFORM_DESC = {
	rules.FACEBOOK: _("Đăng bài lên Trang, đọc tương tác và số liệu bài"),
	rules.ZALO: _("Đăng bài viết OA (dùng chung OA với kênh chat Zalo)"),
	rules.TIKTOK: _("Đăng video (chưa được TikTok duyệt quyền đăng)"),
}


def tail(token: str | None) -> str:
	"""4 ký tự cuối của token để hiện / ghi log (không bao giờ hiện cả token)."""
	token = str(token or "")
	return f"…{token[-4:]}" if len(token) >= 8 else ""


def platform_of(channel_type: str | None) -> str | None:
	if (channel_type or "").startswith("Meta"):
		return rules.FACEBOOK
	if channel_type == "Zalo OA":
		return rules.ZALO
	return None


def _checks() -> dict[str, Any]:
	raw = frappe.db.get_single_value(SETTINGS, "channel_checks")
	if isinstance(raw, dict):
		return raw
	try:
		return json.loads(raw or "{}") or {}
	except ValueError:
		return {}


def _save_check(channel: str, result: dict[str, Any]) -> None:
	data = _checks()
	data[channel] = result
	frappe.db.set_single_value(SETTINGS, "channel_checks", json.dumps(data, ensure_ascii=False, default=str))


def accounts() -> list[dict[str, Any]]:
	"""Danh sách tài khoản cho trang Thiết lập > Mạng xã hội: một dòng mỗi Page / OA, kèm TikTok (chưa kết nối)."""
	rows = frappe.get_all(
		CHANNEL,
		filters={"channel_type": ["in", _supported_types()]},
		fields=["name", "channel_type", "enabled", "meta_page_id", "zalo_oa_id", "zalo_token_expires_at"],
		order_by="creation asc",
	)
	checks = _checks()
	out: list[dict[str, Any]] = []
	seen_pages: set[str] = set()
	for r in rows:
		platform = platform_of(r.channel_type)
		if platform == rules.FACEBOOK:
			page = (r.meta_page_id or "").strip()
			if page and page in seen_pages:
				continue  # Lead Ads và Messenger cùng một Page: một dòng
			if page:
				seen_pages.add(page)
		doc = frappe.get_doc(CHANNEL, r.name)
		token = doc.get_password(
			"meta_page_access_token" if platform == rules.FACEBOOK else "zalo_access_token",
			raise_exception=False,
		)
		check = checks.get(r.name) or {}
		status = check.get("status") or (CONNECTED if (token and r.enabled) else NOT_CONNECTED)
		out.append(
			{
				"name": r.name,
				"platform": platform,
				"account_name": check.get("account_name") or r.name,
				"external_id": (r.meta_page_id if platform == rules.FACEBOOK else r.zalo_oa_id) or "",
				"enabled": bool(r.enabled),
				"description": PLATFORM_DESC[platform],
				"status": status,
				"token_tail": tail(token),
				"token_expires_at": str(r.zalo_token_expires_at or "")
				if platform == rules.ZALO
				else check.get("expires_at", ""),
				"last_checked_at": check.get("checked_at"),
				"last_check": check,
			}
		)
	out.append(
		{
			"name": "",
			"platform": rules.TIKTOK,
			"account_name": "TikTok",
			"external_id": "",
			"enabled": False,
			"description": PLATFORM_DESC[rules.TIKTOK],
			"status": NOT_CONNECTED,
			"token_tail": "",
			"token_expires_at": "",
			"last_checked_at": None,
			"last_check": {},
		}
	)
	return out


def _supported_types() -> list[str]:
	options = frappe.get_meta(CHANNEL).get_field("channel_type").options or ""
	return [t for t in options.split("\n") if platform_of(t)]


def check(channel: str, *, meta_client: Any = None, zalo_client: Any = None) -> dict[str, Any]:
	"""Kiểm quyền một kênh (chỉ gọi đọc), lưu và trả kết quả để hiện ở Thiết lập > Mạng xã hội."""
	doc = frappe.get_doc(CHANNEL, channel)
	platform = platform_of(doc.channel_type)
	if not platform:
		frappe.throw(_("Kênh {0} không phải kênh mạng xã hội").format(channel))
	result = (
		_check_facebook(doc, meta_client) if platform == rules.FACEBOOK else _check_zalo(doc, zalo_client)
	)
	result.update(platform=platform, checked_at=str(now_datetime()))
	_save_check(channel, result)
	return result


def _check_facebook(doc, client: Any = None) -> dict[str, Any]:
	from omni_crm.channels.meta_leads.client import MetaClient
	from omni_crm.channels.meta_leads.errors import MetaError

	page_id = (doc.get("meta_page_id") or "").strip()
	token = doc.get_password("meta_page_access_token", raise_exception=False)
	app_id = (doc.get("meta_app_id") or "").strip()
	secret = doc.get_password("meta_app_secret", raise_exception=False)
	base = {"page_id": page_id, "token_tail": tail(token), "granted": [], "missing": list(FB_REQUIRED)}
	if not (page_id and token):
		return {**base, "status": NOT_CONNECTED, "message": _("Kênh chưa có Page ID hoặc mã truy cập Trang")}
	if not (app_id and secret):
		return {**base, "status": RECONNECT, "message": _("Thiếu App ID hoặc App Secret để kiểm quyền")}
	client = client or MetaClient(doc.name)
	try:
		data = client.debug_token(app_id, secret)
	except MetaError as e:
		return {
			**base,
			"status": RECONNECT,
			"message": _("Facebook từ chối kiểm token: {0}").format(_(str(e))[:200]),
		}
	scopes = [s for s in (data.get("scopes") or []) if isinstance(s, str)]
	missing = [s for s in FB_REQUIRED if s not in scopes]
	expires = data.get("expires_at")
	expires_at = (
		str(datetime.fromtimestamp(int(expires))) if str(expires or "").isdigit() and int(expires) > 0 else ""
	)
	name = ""
	try:
		name = str((client._get(page_id, {"fields": "id,name"}) or {}).get("name") or "")
	except MetaError:
		pass
	valid = data.get("is_valid") is True
	if not valid:
		status, msg = RECONNECT, _("Mã truy cập Trang không còn hiệu lực")
	elif missing:
		status, msg = RECONNECT, _("Thiếu quyền {0}").format(", ".join(missing))
	else:
		status, msg = CONNECTED, _("Đủ quyền đăng bài và đọc số liệu")
	return {
		**base,
		"status": status,
		"message": msg,
		"account_name": name,
		"granted": [s for s in FB_REQUIRED if s in scopes],
		"missing": missing,
		"expires_at": expires_at if expires not in (0, "0") else _("Không hết hạn"),
	}


def _check_zalo(doc, client: Any = None) -> dict[str, Any]:
	from omni_crm import flags
	from omni_crm.channels.zalo.client import ZaloClient
	from omni_crm.channels.zalo.errors import ZaloError

	token = doc.get_password("zalo_access_token", raise_exception=False)
	expires = doc.get("zalo_token_expires_at")
	base = {
		"oa_id": doc.get("zalo_oa_id") or "",
		"token_tail": tail(token),
		"expires_at": str(expires or ""),
	}
	if not token:
		return {**base, "status": NOT_CONNECTED, "message": _("OA chưa cấp quyền (chưa có mã truy cập)")}
	if flags.sandbox():
		return {**base, "status": CONNECTED, "message": _("Chế độ sandbox đang bật: không gọi Zalo để kiểm")}
	client = client or ZaloClient(doc.name)
	try:
		info = client.get_oa_info() or {}
	except ZaloError as e:
		return {**base, "status": RECONNECT, "message": _("Zalo từ chối: {0}").format(e.message_vi[:200])}
	expired = bool(expires) and get_datetime(expires) <= now_datetime()
	return {
		**base,
		"oa_id": str(info.get("oa_id") or base["oa_id"]),
		"account_name": str(info.get("name") or ""),
		"status": RECONNECT if expired else CONNECTED,
		"message": _("Mã truy cập đã hết hạn, chờ job tự làm mới hoặc cấp quyền lại")
		if expired
		else _("OA kết nối được. Quyền đăng bài viết OA chưa kiểm được qua API: thử đăng ở Chế độ thử tắt"),
	}
