"""Webhook nhận sự kiện từ Zalo OA và tổng đài."""

from __future__ import annotations

import hashlib
import uuid

import frappe
from frappe import _
from frappe.utils import now_datetime

from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.ingest import gateway
from omni_crm.ingest.events import InboundEvent

MAX_BODY_BYTES = 1024 * 1024  # 1 MB
LAST_SUCCESS_TTL = 60  # giây: tối đa 1 lần ghi last_success_at mỗi phút mỗi kênh
ERROR_LOG_TTL = 60  # giây: chống tràn Error Log khi bị gọi sai liên tục


def _respond(status: int, message: str, **extra) -> dict:
	frappe.local.response["http_status_code"] = status
	return {"message": message, **extra}


def _log_short(kind: str, channel: str | None, body_len: int) -> None:
	"""Ghi Error Log rút gọn (kênh, loại lỗi, độ dài body, mã tham chiếu); tối đa 1 dòng/phút/loại."""
	cache = frappe.cache()
	key = f"omni:errlog:{channel}:{kind}"
	if cache.get_value(key, expires=True):
		return
	cache.set_value(key, 1, expires_in_sec=ERROR_LOG_TTL)
	ref = uuid.uuid4().hex[:12]
	frappe.log_error(
		title=f"Omni webhook: {kind}",
		message=f"kenh={channel}\nloai_loi={kind}\ndo_dai_body={body_len}\nma_tham_chieu={ref}",
	)
	frappe.db.commit()


def _touch_last_success(channel_name: str) -> None:
	"""Ghi Omni Channel.last_success_at, tối đa 1 lần/phút nhờ cache."""
	cache = frappe.cache()
	key = f"omni:last_success:{channel_name}"
	if cache.get_value(key, expires=True):
		return
	cache.set_value(key, 1, expires_in_sec=LAST_SUCCESS_TTL)
	frappe.db.set_value(
		"Omni Channel", channel_name, "last_success_at", now_datetime(), update_modified=False
	)


def _read_body(request) -> bytes:
	"""Đọc body có giới hạn MAX_BODY_BYTES + 1 byte để request chunked không bị đọc hết vào RAM."""
	cached = getattr(request, "_cached_data", None)  # werkzeug đã đọc (ví dụ make_form_dict của Frappe)
	if cached is not None:
		return cached
	stream = getattr(request, "stream", None)
	if stream is not None:
		return stream.read(MAX_BODY_BYTES + 1) or b""
	return request.get_data() or b""


def _unparsed_event(channel_doc, raw: bytes) -> InboundEvent:
	digest = hashlib.sha256(raw).hexdigest()
	return InboundEvent(
		channel=channel_doc.name,
		channel_type=channel_doc.channel_type,
		event_type="unparsed",
		external_message_id=f"unparsed:{digest}",
		external_sender_id="unparsed",
		received_at=now_datetime(),
		text=None,
		raw={"body_sha256": digest, "body_length": len(raw)},
	)


def _handle_webhook(channel_type: str) -> dict:
	"""Khung xử lý chung 7 bước của mọi webhook (docs/03 mục 1)."""
	request = frappe.local.request
	# 1. đúng method, giới hạn kích thước
	if request.method != "POST":
		return _respond(405, _("Chỉ nhận POST"))
	if (request.content_length or 0) > MAX_BODY_BYTES:
		return _respond(413, _("Nội dung quá lớn"))

	# 2. đọc raw body trước khi parse
	raw = _read_body(request)
	if len(raw) > MAX_BODY_BYTES:
		return _respond(413, _("Nội dung quá lớn"))

	# 3. tải kênh theo ch
	channel_name = frappe.form_dict.get("ch")
	channel_doc = None
	if channel_name and frappe.db.exists("Omni Channel", channel_name):
		channel_doc = frappe.get_cached_doc("Omni Channel", channel_name)
	if not channel_doc or channel_doc.channel_type != channel_type:
		return _respond(404, _("Không tìm thấy kênh"))
	try:
		adapter = get_adapter(channel_type)
	except AdapterNotFoundError:
		_log_short("khong_co_adapter", channel_doc.name, len(raw))
		return _respond(404, _("Không tìm thấy kênh"))

	# 4. xác thực
	headers = dict(request.headers)
	try:
		verified = bool(adapter.verify_request(raw, headers, channel_doc))
	except Exception:
		verified = False
	if not verified:
		_log_short("xac_thuc_that_bai", channel_doc.name, len(raw))
		return _respond(401, _("Xác thực thất bại"))

	# 5. parse; lỗi parse vẫn lưu một bản ghi unparsed (Manual) và trả 200
	try:
		events = adapter.parse(raw, headers, channel_doc)
	except Exception:
		_log_short("parse_that_bai", channel_doc.name, len(raw))
		events = [_unparsed_event(channel_doc, raw)]

	# 6. lưu từng sự kiện
	saved = 0
	saved_real = 0  # sự kiện thật được lưu (không tính trùng, không tính unparsed)
	for event in events:
		if gateway.save_inbound(event):
			saved += 1
			if event.event_type != "unparsed":
				saved_real += 1

	# 7. ghi dấu thành công (chỉ khi có sự kiện thật được lưu), commit, trả 200
	if saved_real:
		_touch_last_success(channel_doc.name)
	frappe.db.commit()
	return _respond(200, "ok", received=len(events), saved=saved)


@frappe.whitelist(allow_guest=True)
def zalo() -> dict:
	"""Webhook Zalo OA: POST /api/method/omni_crm.api.webhooks.zalo?ch=<tên kênh>."""
	# Không dùng methods=["POST"] của decorator vì nó trả 403; đặc tả yêu cầu 405 (kiểm trong khung chung).
	return _handle_webhook("Zalo OA")


def _require_omni_admin() -> None:
	if "Omni Admin" not in frappe.get_roles():
		frappe.throw(_("Chỉ Omni Admin được kết nối Zalo OA"), frappe.PermissionError)


def _redirect(location: str) -> None:
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = location


@frappe.whitelist()
def zalo_oauth_start(channel: str) -> None:
	"""Bắt đầu cấp quyền OA (chỉ Omni Admin): sinh state + PKCE rồi chuyển hướng tới trang cấp quyền của Zalo."""
	from omni_crm.channels.zalo import token

	_require_omni_admin()
	_redirect(token.start_authorization(channel))


@frappe.whitelist()
def zalo_oauth_callback(code: str | None = None, state: str | None = None, **_kwargs) -> None:
	"""Zalo gọi về sau khi OA cấp quyền: kiểm state, đổi code lấy token, lưu và quay về form kênh."""
	from omni_crm.channels.zalo import token
	from omni_crm.channels.zalo.errors import ZaloError

	_require_omni_admin()
	if not code or not state:
		frappe.throw(_("Thiếu mã cấp quyền hoặc state"), frappe.PermissionError)
	try:
		channel = token.complete_authorization(code, state)
	except ZaloError as e:
		frappe.throw(e.message_vi)
	_redirect(frappe.utils.get_url(f"/app/omni-channel/{channel}"))
