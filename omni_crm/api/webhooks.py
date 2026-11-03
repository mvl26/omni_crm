"""Webhook nhận sự kiện từ Zalo OA và tổng đài; telephony_lookup là API tra cứu đồng bộ (chỉ đọc)."""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.ingest import gateway
from omni_crm.ingest.events import InboundEvent

MAX_BODY_BYTES = 1024 * 1024  # 1 MB
LAST_SUCCESS_TTL = 60  # giây: tối đa 1 lần ghi last_success_at mỗi phút mỗi kênh
ERROR_LOG_TTL = 60  # giây: chống tràn Error Log khi bị gọi sai liên tục
# site_config; từ V21-04 là thời gian CHỜ KHÓA trước khi chuyển sang hàng dự phòng (không còn dẫn tới 503); docs/10 mục 5
LOCK_WAIT_CONF = "omni_webhook_lock_wait_seconds"
DEFAULT_LOCK_WAIT = 3


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
	frappe.db.set_value(
		"Omni Channel", channel_name, "last_success_at", now_datetime(), update_modified=False
	)
	cache.set_value(
		key, 1, expires_in_sec=LAST_SUCCESS_TTL
	)  # đặt sau khi ghi được: ghi lỗi thì lần sau thử lại


def _set_lock_wait(seconds: int) -> int | None:
	"""Đặt `innodb_lock_wait_timeout` của phiên (mặc định MariaDB 50 giây); trả giá trị cũ để khôi phục, None nếu không đặt được."""
	try:
		old = frappe.db.sql("select @@session.innodb_lock_wait_timeout")[0][0]
		frappe.db.sql(f"set session innodb_lock_wait_timeout = {int(seconds)}")
		return int(old)
	except Exception:
		return None


def _is_lock_trouble(e: BaseException) -> bool:
	"""Hết hạn chờ khóa hoặc deadlock của DB: tạm thời, nhà cung cấp nên gửi lại."""
	if isinstance(
		e, frappe.QueryTimeoutError | frappe.QueryDeadlockError
	):  # Frappe bọc lỗi 1205/1213 của MariaDB
		return True
	try:
		return bool(frappe.db.is_timedout(e) or frappe.db.is_deadlocked(e))
	except Exception:
		return False


def is_db_unavailable(e: BaseException) -> bool:
	"""DB không ghi được lúc này (hết hạn chờ khóa, bế tắc, mất kết nối, máy chủ DB lỗi): tạm thời, đáng ghi hàng dự phòng."""
	import pymysql

	seen = 0
	while e is not None and seen < 4:
		if _is_lock_trouble(e) or isinstance(
			e, pymysql.err.OperationalError | pymysql.err.InterfaceError | ConnectionError | BrokenPipeError
		):
			return True
		e, seen = (e.__cause__ or e.__context__), seen + 1
	return False


def _safe_rollback() -> None:
	"""Rollback không ném lỗi; mất kết nối thì thử nối lại để phần kết thúc request của Frappe không hỏng."""
	try:
		frappe.db.rollback()
	except Exception:
		try:
			frappe.db.connect()
		except Exception:
			pass


def _spool_or_fail(channel_doc, raw: bytes, headers: dict) -> dict:
	"""Ghi hàng dự phòng và trả thành công của kênh; hỏng nốt thì log nghiêm trọng, cảnh báo ngay và trả 503."""
	from omni_crm.ingest import spool

	if not spool.enabled():  # công tắc tắt: giữ hành vi cũ (nhà cung cấp gửi lại)
		_log_short("cho_khoa_db_qua_han", channel_doc.name, len(raw))
		return _respond(503, _("Hệ thống đang bận, vui lòng gửi lại sau"))
	try:
		spool.append(spool.build_entry(channel_doc.name, channel_doc.channel_type, raw, headers))
	except Exception as e:
		# Cả DB lẫn hàng dự phòng đều hỏng: không còn chỗ giữ tin. Log mức nghiêm trọng (tệp), KHÔNG kèm payload/PII.
		frappe.logger("omni").critical(
			"omni spool: DB va hang du phong deu hong, mat tin kenh=%s loai=%s",
			channel_doc.name,
			type(e).__name__,
		)
		_alert_spool_failure(channel_doc.name, type(e).__name__)
		return _respond(503, _("Hệ thống tạm thời chưa nhận được tin, vui lòng gửi lại sau"))
	frappe.logger("omni").warning("omni spool: da ghi hang du phong kenh=%s", channel_doc.name)
	if channel_doc.channel_type == "Web Form" and not frappe.flags.get("omni_web_submission"):
		frappe.flags.omni_web_submission = uuid.uuid4().hex
	return _respond(200, "ok", received=0, saved=0, queued=1)


def _alert_spool_failure(channel: str, kind: str) -> None:
	"""Cảnh báo ngay (best effort: DB đang hỏng nên có thể không gửi được; log tệp mức nghiêm trọng vẫn còn)."""
	try:
		frappe.cache().set_value("omni:spool:failed_at", str(now_datetime()), expires_in_sec=3600)
		from omni_crm.monitoring.alerts import Alert, deliver_now

		deliver_now(
			Alert(
				"webhook_spool_hong",
				channel,
				_("Webhook không lưu được tin: DB và hàng dự phòng đều hỏng"),
				_(
					"Kênh {0}: DB không ghi được và hàng dự phòng ({1}) cũng không ghi được; webhook trả 503 và tin chỉ còn nếu nhà cung cấp gửi lại. Kiểm tra DB và thư mục private/omni_spool (quyền, dung lượng) ngay."
				).format(channel, kind),
			)
		)
	except Exception:
		pass


def _read_body(request, limit: int = MAX_BODY_BYTES) -> bytes:
	"""Đọc body có giới hạn MAX_BODY_BYTES + 1 byte để request chunked không bị đọc hết vào RAM."""
	cached = getattr(request, "_cached_data", None)  # werkzeug đã đọc (ví dụ make_form_dict của Frappe)
	if cached is not None:
		return cached
	stream = getattr(request, "stream", None)
	if stream is not None:
		return stream.read(limit + 1) or b""
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


def _handle_webhook(channel_type: str, *, max_bytes: int = MAX_BODY_BYTES, guard=None) -> dict:
	"""Khung xử lý chung 7 bước của mọi webhook (docs/03 mục 1).

	`max_bytes`: giới hạn kích thước riêng của kênh (form website nhỏ hơn). `guard(channel_doc)`: kiểm tra thêm sau khi tải
	kênh (giới hạn tần suất của form website); trả dict phản hồi để dừng, hoặc None để đi tiếp.
	"""
	request = frappe.local.request
	# 1. đúng method, giới hạn kích thước
	if request.method != "POST":
		return _respond(405, _("Chỉ nhận POST"))
	if (request.content_length or 0) > max_bytes:
		return _respond(413, _("Nội dung quá lớn"))

	# 2. đọc raw body trước khi parse
	raw = _read_body(request, max_bytes)
	if len(raw) > max_bytes:
		return _respond(413, _("Nội dung quá lớn"))
	if channel_type == "Web Form" and not raw and frappe.form_dict:  # form-data đã được werkzeug đọc hết
		raw = json.dumps(
			{k: v for k, v in frappe.form_dict.items() if k != "cmd"}, ensure_ascii=False
		).encode()

	# 3. tải kênh theo ch
	# Frappe bỏ tham số query khi thân là JSON (Zalo luôn gửi JSON), nên đọc `ch` từ query string trước
	query = getattr(request, "args", None)
	channel_name = (query.get("ch") if query is not None else None) or frappe.form_dict.get("ch")
	channel_doc = None
	if channel_name and frappe.db.exists("Omni Channel", channel_name):
		channel_doc = frappe.get_cached_doc("Omni Channel", channel_name)
	if not channel_doc or channel_doc.channel_type != channel_type:
		return _respond(404, _("Không tìm thấy kênh"))
	if guard is not None and (blocked := guard(channel_doc)) is not None:
		return blocked
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

	# 5-7. parse rồi lưu từng sự kiện, commit NGAY để tin đã lưu bền (V21-04, ADR 0014). Chỉ chèn Omni Inbound Message
	# (tên không dùng bộ đếm Series), chống trùng bằng ràng buộc duy nhất dedupe_key. Chờ khóa tối đa vài giây; hết hạn
	# chờ / bế tắc / mất kết nối DB thì rollback rồi GHI HÀNG DỰ PHÒNG (ingest/spool.py) và vẫn trả thành công; tác vụ quét
	# mỗi phút chèn lại. Chỉ trả 503 khi cả DB lẫn hàng dự phòng đều hỏng. Request đến đây ĐÃ qua xác thực.
	previous_wait = _set_lock_wait(cint(frappe.conf.get(LOCK_WAIT_CONF)) or DEFAULT_LOCK_WAIT)
	try:
		try:
			try:
				events = adapter.parse(raw, headers, channel_doc)
			except Exception as e:
				if is_db_unavailable(e):
					raise
				_log_short("parse_that_bai", channel_doc.name, len(raw))
				events = [_unparsed_event(channel_doc, raw)]
			saved = 0
			saved_real = 0  # sự kiện thật được lưu (không tính trùng, không tính unparsed)
			for event in events:
				if gateway.save_inbound(event):
					saved += 1
					if event.event_type != "unparsed":
						saved_real += 1
			frappe.db.commit()
		except Exception as e:
			if not is_db_unavailable(e):
				raise
			_safe_rollback()
			return _spool_or_fail(channel_doc, raw, headers)
		if saved_real:  # chỉ ghi dấu khi có sự kiện thật được lưu
			try:
				_touch_last_success(channel_doc.name)
				frappe.db.commit()
			except Exception as e:
				if not is_db_unavailable(e):
					raise
				_safe_rollback()  # chỉ bỏ dấu giám sát; tin đã commit ở trên
				_log_short("cho_khoa_db_ghi_dau_thanh_cong", channel_doc.name, len(raw))
	finally:
		if previous_wait is not None:
			_set_lock_wait(previous_wait)
	return _respond(200, "ok", received=len(events), saved=saved)


@frappe.whitelist(allow_guest=True)
def zalo() -> dict:
	"""Webhook Zalo OA: POST /api/method/omni_crm.api.webhooks.zalo?ch=<tên kênh>."""
	# Không dùng methods=["POST"] của decorator vì nó trả 403; đặc tả yêu cầu 405 (kiểm trong khung chung).
	return _handle_webhook("Zalo OA")


def _query_arg(name: str) -> str:
	args = getattr(frappe.local.request, "args", None)
	return str((args.get(name) if args is not None else None) or frappe.form_dict.get(name) or "")


def _enabled_guard(channel_doc) -> dict | None:
	"""Kênh quảng cáo đã TẮT: bỏ qua có log, KHÔNG lưu gì (trả 200 để nền tảng không thử lại hay hủy đăng ký)."""
	if channel_doc.enabled:
		return None
	_log_short("kenh_tat", channel_doc.name, 0)
	return _respond(200, "ok", received=0, saved=0)


@frappe.whitelist(allow_guest=True)
def meta_leads() -> dict | None:
	"""Webhook Meta Lead Ads: GET = handshake (hub.mode/hub.verify_token/hub.challenge), POST = sự kiện leadgen có chữ ký.

	POST /api/method/omni_crm.api.webhooks.meta_leads?ch=<tên kênh>. Chưa thử với nền tảng thật.
	"""
	import re

	from omni_crm.channels.meta_leads.adapter import verify_token

	if frappe.local.request.method != "GET":
		return _handle_webhook("Meta Lead Ads", guard=_enabled_guard)
	name = _query_arg("ch")
	channel_doc = (
		frappe.get_cached_doc("Omni Channel", name)
		if name and frappe.db.exists("Omni Channel", name)
		else None
	)
	challenge = _query_arg("hub.challenge")
	ok = (
		channel_doc is not None
		and channel_doc.channel_type == "Meta Lead Ads"
		and _query_arg("hub.mode") == "subscribe"
		and verify_token(channel_doc, _query_arg("hub.verify_token"))
		and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", challenge) is not None
	)
	if not ok:
		return _respond(403, _("Xác minh thất bại"))
	frappe.local.response["http_status_code"] = 200
	return _send_raw(challenge)


@frappe.whitelist(allow_guest=True)
def google_leads() -> dict | None:
	"""Webhook Google Ads lead form: POST JSON có `google_key`. 200 + `{}` khi đã lưu, 4xx sai khóa, 503 khi lỗi tạm thời."""
	result = _handle_webhook("Google Lead Form", guard=_enabled_guard)
	if frappe.local.response.get("http_status_code") == 200:
		return _send_raw({})
	return result


WEB_FORM_MAX_BYTES = 64 * 1024


def _web_form_guard(channel_doc) -> dict | None:
	"""Kênh đang bật và chưa quá giới hạn tần suất theo IP (Redis, TTL = cửa sổ); quá thì 429."""
	from omni_crm import flags

	if not channel_doc.enabled:
		return _respond(404, _("Không tìm thấy kênh"))
	limit, window = (
		flags.ads_setting("webform_rate_limit"),
		flags.ads_setting("webform_rate_window_minutes") * 60,
	)
	ip = str(getattr(frappe.local, "request_ip", None) or "unknown")[:64]
	cache = frappe.cache()
	key = cache.make_key(f"omni:webform:rl:{channel_doc.name}:{ip}")
	count = cache.incr(key)
	if count == 1 or cache.ttl(key) < 0:
		cache.expire(key, window)
	if count > limit:
		return _respond(429, _("Bạn gửi quá nhanh, vui lòng thử lại sau"))
	# trần toàn kênh theo giờ (chống tấn công xoay IP): vượt thì 429 cho mọi người và ghi dấu để giám sát cảnh báo
	gkey = cache.make_key(f"omni:webform:global:{channel_doc.name}")
	total = cache.incr(gkey)
	if total == 1 or cache.ttl(gkey) < 0:
		cache.expire(gkey, 3600)
	if total > flags.ads_setting("webform_global_rate_per_hour"):
		cache.set(cache.make_key(f"omni:webform:global_hit:{channel_doc.name}"), 1, ex=3600)
		return _respond(429, _("Hệ thống đang bận, vui lòng thử lại sau"))
	return None


@frappe.whitelist(allow_guest=True)
def web_form() -> dict:
	"""Form website/landing page: POST JSON hoặc form-data tới ?ch=<tên kênh Web Form>; trả `submission_id` để trang cảm ơn."""
	result = _handle_webhook("Web Form", max_bytes=WEB_FORM_MAX_BYTES, guard=_web_form_guard)
	if frappe.local.response.get("http_status_code") == 200:
		# chỉ trả mã gửi; không trả thông tin nội bộ (số sự kiện, trùng hay không, honeypot)
		return {"message": "ok", "submission_id": frappe.flags.get("omni_web_submission")}
	return result


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


@frappe.whitelist(allow_guest=True)
def telephony_event() -> dict:
	"""Webhook sự kiện cuộc gọi: POST /api/method/omni_crm.api.webhooks.telephony_event?ch=<kênh>&t=<webhook_token>."""
	return _handle_webhook("Telephony")


def _lookup_channel(query: dict) -> tuple[object | None, bool]:
	"""(kênh Telephony có thật hoặc None, token hợp lệ và kênh đang bật?)."""
	from omni_crm.channels.telephony.adapter import verify_token

	name = query.get("ch")
	if not name or not frappe.db.exists("Omni Channel", name):
		return None, False
	channel_doc = frappe.get_cached_doc("Omni Channel", name)
	if channel_doc.channel_type != "Telephony":
		return None, False
	return channel_doc, bool(channel_doc.enabled) and verify_token(channel_doc, query.get("t"))


def _send_raw(payload: dict | str) -> None:
	"""Trả nguyên văn phản hồi của nhà cung cấp (không bọc trong {"message": ...} của Frappe).

	Luôn đi qua kiểu `download` (nội dung là bytes) để khóa do nhà cung cấp đặt (type, message, location...)
	không bao giờ ghi đè khóa điều khiển của Frappe; dict được tuần tự hóa thành JSON.
	"""
	if isinstance(payload, dict):
		body, filename, content_type = (
			json.dumps(payload, ensure_ascii=False).encode(),
			"lookup.json",
			"application/json",
		)
	else:
		body, filename, content_type = str(payload).encode(), "lookup.txt", "text/plain"
	frappe.local.response.update(
		{
			"type": "download",
			"filename": filename,
			"filecontent": body,
			"display_content_as": "inline",
			"content_type": content_type,
		}
	)


@frappe.whitelist(allow_guest=True)
def telephony_lookup() -> None:
	"""Tra cứu khách khi chuông reo (GET/POST): CHỈ ĐỌC, không ghi DB, không enqueue, không Error Log.

	Mọi lỗi (sai token, kênh tắt/không có, số sai, DB chậm hoặc lỗi) vẫn trả HTTP 200 với phản hồi "khách mới"
	hợp lệ của nhà cung cấp, để không làm hỏng cuộc gọi và không lộ khách có trong hệ thống hay không.
	"""
	from omni_crm.channels.telephony import lookup
	from omni_crm.channels.telephony.adapter import request_query
	from omni_crm.channels.telephony.base import LookupResult
	from omni_crm.channels.telephony.registry import provider_for_channel

	deadline = time.monotonic() + lookup.BUDGET_SECONDS
	frappe.local.response["http_status_code"] = 200
	channel_doc = provider = None
	try:
		query = request_query()
		channel_doc, authed = _lookup_channel(query)
		if channel_doc is None:
			return _send_raw({})
		provider = provider_for_channel(channel_doc)
		if not authed:
			# Sai token / kênh tắt: cùng phản hồi "khách mới" của nhà cung cấp, không phân biệt được với khách mới
			return _send_raw(provider.lookup_response(LookupResult(), channel_doc))
		result = LookupResult()
		try:
			body = _read_body(frappe.local.request)[: 64 * 1024]
			caller, _to = provider.parse_lookup_request(query, body)
			with lookup.statement_limit():
				result = lookup.find_customer(caller, deadline)
		except Exception as e:
			# Không ghi DB (log_error sẽ ghi): chỉ log tệp, không kèm số điện thoại hay nội dung lỗi.
			frappe.logger("omni").warning("omni: tra cuu tong dai loi (%s)", type(e).__name__)
			result = LookupResult()
		return _send_raw(provider.lookup_response(result, channel_doc))
	except Exception as e:
		frappe.logger("omni").warning("omni: phan hoi tra cuu tong dai loi (%s)", type(e).__name__)
		try:
			if provider is not None:
				return _send_raw(provider.lookup_response(LookupResult(), channel_doc))
		except Exception:
			pass
		return _send_raw({})
