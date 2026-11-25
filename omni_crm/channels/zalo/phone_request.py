"""Xin số điện thoại qua Zalo bằng thẻ `request_user_info` (V3-07, brief hạng mục A).

Hai điểm kích hoạt, không thêm:
- Tự động: sau khi một lô tin Zalo của khách xử lý xong (Done), nếu đủ điều kiện thì hẹn gửi sau ít nhất 60 giây kể từ tin
  cuối của khách (không đè lên câu hỏi của họ). Hẹn trong Redis (sorted set), job mỗi phút `run_due` lấy ra và gửi; mỗi
  mục được "nhận" bằng ZREM trả 1 nên hai worker không gửi trùng. Không có đường quét toàn bộ định danh.
- Thủ công: sale bấm nút trên tab Zalo; bỏ qua điều kiện 30 ngày nhưng không bỏ qua "chưa có số" và "trong 48 giờ",
  và không gửi lại trong cùng phiên chat (đã xin trong 48 giờ qua).

Điều kiện (đủ cả): định danh Zalo OA đã gắn Lead; `phone_e164` trống; `phone_requested_at` trống hoặc quá 30 ngày;
khách nhắn trong 48 giờ (tin xin số chỉ đi bằng tin miễn phí, `routing.pick_message_kind(..., "request_info")`).
Tự động còn thêm: bot không bị tạm dừng (`bot_paused_until`), khách không từ chối liên hệ.

An toàn khi thử trên site thật: công tắc `phone_request_enabled` (mặc định tắt). Khi tắt, chỉ tài khoản Zalo thử nghiệm
trong `WHITELIST_USER_IDS` nhận được thẻ, chặn ngay trong `ZaloClient.send_request_user_info`.

Mốc `phone_requested_at` được GIỮ CHỖ dưới khóa dòng trước khi gọi Zalo (bấm hai lần / hai worker không gửi hai thẻ) và
trả về giá trị cũ khi Zalo từ chối chắc chắn (có mã lỗi, hoặc chưa gửi đi). Lỗi mạng mơ hồ (không biết thẻ đã tới khách
chưa) thì GIỮ mốc để không gửi lặp cho khách thật; lệch có chủ đích so với brief ("hỏng thì không ghi mốc").
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.channels.zalo import routing
from omni_crm.pipeline.retry import redact

SETTINGS = "Omni Settings"
IDENTITY = "Omni Identity"
OUTBOUND = "Omni Outbound Message"
INBOUND = "Omni Inbound Message"
CHANNEL = "Omni Channel"
CHANNEL_TYPE = "Zalo OA"
# Tài khoản Zalo cá nhân của người vận hành (gắn CRM-LEAD-2026-00003): người DUY NHẤT nhận thẻ khi công tắc tắt.
WHITELIST_USER_IDS = frozenset({"277477470109110101"})
COOLDOWN_DAYS = 30
SESSION_HOURS = 48  # "cùng một phiên chat" với nút tay: đã xin trong 48 giờ qua
DELAY_SECONDS = 60
DUE_KEY = "omni:zalo:phone_request:due"
CUSTOMER_EVENTS = ("message",)
DEFAULT_TITLE = "Miyano xin phép nhận thông tin liên hệ"
DEFAULT_SUBTITLE = (
	"Để Miyano gửi báo giá và thông tin đơn hàng đúng người, anh/chị chia sẻ giúp số điện thoại nhé."
)
DEFAULT_IMAGE_URL = "https://miyano.com.vn/wp-content/uploads/2024/10/logo02.gif"


class PhoneRequestError(frappe.ValidationError):
	"""Không gửi được thẻ xin số (lý do tiếng Việt cho sale)."""


def _setting(fieldname: str) -> Any:
	try:
		return frappe.get_cached_doc(SETTINGS).get(fieldname)
	except Exception:
		return None


def enabled() -> bool:
	"""Công tắc tổng của luồng xin số; chưa lưu hoặc lỗi = tắt."""
	return bool(cint(_setting("phone_request_enabled")))


def recipient_allowed(user_id: str | None) -> bool:
	"""Được gửi thẻ xin số tới mã Zalo này không: công tắc bật, hoặc là tài khoản thử nghiệm."""
	return enabled() or str(user_id or "") in WHITELIST_USER_IDS


def card_texts() -> tuple[str, str, str]:
	"""(tiêu đề <= 100, nội dung <= 500, ảnh HTTPS) của thẻ, lấy từ Omni Settings; trống thì dùng mặc định."""
	title = (_setting("phone_request_title") or "").strip() or _(DEFAULT_TITLE)
	subtitle = (_setting("phone_request_subtitle") or "").strip() or _(DEFAULT_SUBTITLE)
	image = (_setting("phone_request_image_url") or "").strip()
	if not image.startswith("https://"):
		image = DEFAULT_IMAGE_URL
	return title[:100], subtitle[:500], image


# --- Điều kiện ----------------------------------------------------------------------------------------------


def _requested_within(identity_doc: Any, **delta: int) -> bool:
	last = identity_doc.get("phone_requested_at")
	return bool(last) and get_datetime(last) >= add_to_date(now_datetime(), **delta)


def _refused(lead: str) -> bool:
	from omni_crm.api import customer as customer_api

	try:
		return bool(customer_api._person_flags(lead)["refused"])
	except Exception:
		return True  # không đọc được cờ từ chối: coi như từ chối (an toàn)


def ineligible_reason(identity_doc: Any, channel_doc: Any, *, manual: bool) -> str | None:
	"""Lý do KHÔNG gửi thẻ xin số cho định danh này lúc này; None = được gửi."""
	if identity_doc.get("channel_type") != CHANNEL_TYPE or not identity_doc.get("lead"):
		return _("Khách chưa có tài khoản Zalo gắn với Lead")
	if identity_doc.get("phone_e164"):
		return _("Đã có số điện thoại của khách trên Zalo")
	if manual:
		if _requested_within(identity_doc, hours=-SESSION_HOURS):
			return _("Đã xin số điện thoại trong phiên chat này (48 giờ qua)")
	elif _requested_within(identity_doc, days=-COOLDOWN_DAYS):
		return _("Đã xin số điện thoại trong {0} ngày qua").format(COOLDOWN_DAYS)
	if not recipient_allowed(identity_doc.get("external_id")):
		return _("Luồng xin số điện thoại đang tắt (chỉ tài khoản thử nghiệm nhận được thẻ)")
	if channel_doc is None or not cint(channel_doc.get("outbound_enabled")):
		return _("Kênh Zalo đã tắt chiều ra")
	try:
		routing.pick_message_kind(identity_doc, "request_info", channel_doc=channel_doc)
	except routing.RoutingError as e:
		return e.message_vi
	if _refused(identity_doc.lead):
		return _("Người này đã từ chối nhận liên hệ")
	if not manual:
		from omni_crm.bot import handoff
		from omni_crm.script import switch as script_switch

		if script_switch.enabled_for(identity_doc.name):  # ADR 0032: bot kịch bản tự xin SĐT ở Bước 3
			return _("Khách đang chạy bot kịch bản (bot tự xin số điện thoại)")
		if handoff.is_bot_paused(identity_doc.name):
			return _("Bot đang tạm dừng với khách này (sale đang trao đổi)")
	return None


# --- Hẹn gửi tự động ------------------------------------------------------------------------------------------


def _due_key() -> str:
	return frappe.cache().make_key(DUE_KEY)


def _schedule(identity: str, at_ts: float) -> None:
	frappe.cache().zadd(_due_key(), {identity: at_ts})


def _channel_of(identity: str) -> Any:
	channel = frappe.db.get_value(
		INBOUND, {"identity": identity, "channel_type": CHANNEL_TYPE}, "channel", order_by="received_at desc"
	)
	if not channel or not frappe.db.exists(CHANNEL, channel):
		return None
	return frappe.get_doc(CHANNEL, channel)


def on_batch_done(batch_id: str) -> None:
	"""Hậu xử lý lô tin Done: khách Zalo nhắn tin và đủ điều kiện -> hẹn gửi thẻ xin số sau 60 giây. Không ném lỗi."""
	try:
		rows = frappe.get_all(
			INBOUND,
			filters={
				"batch_id": batch_id,
				"channel_type": CHANNEL_TYPE,
				"event_type": ["in", CUSTOMER_EVENTS],
			},
			fields=["identity"],
			distinct=True,
		)
		for r in rows:
			if not r.identity:
				continue
			doc = frappe.get_doc(IDENTITY, r.identity)
			if ineligible_reason(doc, _channel_of(r.identity), manual=False):
				continue
			last = get_datetime(doc.last_inbound_at) if doc.last_inbound_at else now_datetime()
			_schedule(doc.name, add_to_date(last, seconds=DELAY_SECONDS).timestamp())
	except Exception:
		frappe.log_error(title="Omni: hẹn gửi thẻ xin số lỗi", message=redact(frappe.get_traceback())[-1500:])


def run_due() -> dict[str, int]:
	"""Job mỗi phút: gửi thẻ xin số đã đến hẹn; kiểm lại MỌI điều kiện ngay trước khi gửi."""
	cache = frappe.cache()
	key = _due_key()
	now_ts = now_datetime().timestamp()
	stats = {"sent": 0, "skipped": 0, "later": 0, "failed": 0}
	for raw in cache.zrangebyscore(key, "-inf", now_ts, start=0, num=50):
		identity = raw.decode() if isinstance(raw, bytes) else str(raw)
		if cache.zrem(key, identity) != 1:
			continue  # worker khác đã nhận
		try:
			if not frappe.db.exists(IDENTITY, identity):
				stats["skipped"] += 1
				continue
			doc = frappe.get_doc(IDENTITY, identity)
			last = get_datetime(doc.last_inbound_at) if doc.last_inbound_at else None
			if last and last > add_to_date(now_datetime(), seconds=-DELAY_SECONDS):
				_schedule(
					identity, add_to_date(last, seconds=DELAY_SECONDS).timestamp()
				)  # khách vừa nhắn thêm
				stats["later"] += 1
				continue
			channel_doc = _channel_of(identity)
			if ineligible_reason(doc, channel_doc, manual=False):
				stats["skipped"] += 1
				continue
			send(doc, channel_doc, source="System")
			stats["sent"] += 1
		except Exception:
			frappe.db.rollback()
			stats["failed"] += 1
			frappe.log_error(
				title="Omni: gửi thẻ xin số tự động lỗi", message=redact(frappe.get_traceback())[-1500:]
			)
			frappe.db.commit()
	return stats


# --- Gửi ------------------------------------------------------------------------------------------------------


def _reserve(identity: str, manual: bool) -> Any:
	"""Ghi phone_requested_at = bây giờ dưới khóa dòng; trả giá trị cũ, hoặc ném nếu đã xin trong hạn."""
	last = frappe.db.get_value(IDENTITY, identity, "phone_requested_at", for_update=True)
	limit = (
		add_to_date(now_datetime(), hours=-SESSION_HOURS)
		if manual
		else add_to_date(now_datetime(), days=-COOLDOWN_DAYS)
	)
	if last and get_datetime(last) >= limit:
		frappe.db.rollback()
		raise PhoneRequestError(_("Vừa có người xin số điện thoại của khách này"))
	frappe.db.set_value(IDENTITY, identity, "phone_requested_at", now_datetime(), update_modified=False)
	return last


def _definitely_not_sent(exc: BaseException) -> bool:
	"""Chắc chắn thẻ CHƯA tới khách: Zalo trả mã lỗi, lỗi cấu hình/danh sách trắng, hoặc chưa kết nối được."""
	import requests

	if getattr(exc, "code", None) is not None or isinstance(exc, ValueError):
		return True
	if getattr(exc, "kind", None) in ("permanent", "window", "token"):
		return True
	return isinstance(exc.__cause__, requests.ConnectTimeout | requests.ConnectionError) and not isinstance(
		exc.__cause__, requests.ReadTimeout
	)


def _record(identity_doc: Any, channel_doc: Any, *, source: str, user: str | None, **values: Any) -> str:
	doc = frappe.get_doc(
		{
			"doctype": OUTBOUND,
			"channel": channel_doc.name,
			"identity": identity_doc.name,
			"lead": identity_doc.lead,
			"source": source,
			"sent_by": user if source == "Sale" else None,
			"is_sandbox": 1 if flags.sandbox() else 0,
			"message_kind": routing.CS_48H,
			"purpose": "request_info",
			**values,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def send(identity_doc: Any, channel_doc: Any, *, source: str, user: str | None = None) -> dict:
	"""Gửi thẻ xin số ngay (người gọi đã kiểm điều kiện; ở đây kiểm lại dưới khóa). Trả {"status", "outbound"}."""
	from omni_crm.channels.registry import get_adapter
	from omni_crm.crm_bridge import lead as lead_bridge
	from omni_crm.crm_bridge import timeline
	from omni_crm.pipeline import outbound as outbound_pipeline

	manual = source == "Sale"
	routing.pick_message_kind(
		identity_doc, "request_info", channel_doc=channel_doc
	)  # chỉ tin miễn phí trong 48 giờ
	previous = _reserve(identity_doc.name, manual)
	frappe.db.commit()  # nhả khóa dòng trước khi gọi API ngoài
	meta: dict = {}
	try:
		message_id, shown = get_adapter(CHANNEL_TYPE).request_user_info(identity_doc, channel_doc, meta=meta)
	except Exception as e:
		frappe.db.rollback()
		if _definitely_not_sent(e):
			frappe.db.set_value(
				IDENTITY, identity_doc.name, "phone_requested_at", previous, update_modified=False
			)
		message = getattr(e, "message_vi", None) or _("Không gửi được thẻ xin số, thử lại sau")
		_record(
			identity_doc,
			channel_doc,
			source=source,
			user=user,
			text=card_texts()[0],
			status="Failed",
			last_error=redact(str(message))[:500],
		)
		frappe.db.commit()
		frappe.logger("omni").warning("omni: gửi thẻ xin số lỗi: %s", redact(str(e))[:200])
		raise PhoneRequestError(message) from e
	quota_type = meta.get("quota_type")
	name = _record(
		identity_doc,
		channel_doc,
		source=source,
		user=user,
		text=shown,
		status="Sent",
		sent_at=now_datetime(),
		external_message_id=None if flags.sandbox() else message_id,
		quota_consumed=1 if quota_type in outbound_pipeline.PAID_QUOTA_TYPES else 0,
	)
	frappe.db.commit()
	if not flags.sandbox() and message_id:  # echo có thể tới trước khi ta ghi mã tin
		outbound_pipeline.drop_echo_duplicate(frappe._dict(name=name, channel=channel_doc.name), message_id)
	lead = identity_doc.lead
	timeline.append_outbound(lead_bridge.get_deal_for_lead(lead) or lead, name)
	timeline.notify(lead, [CHANNEL_TYPE])
	frappe.db.commit()
	return {"status": "Sent", "outbound": name, "sandbox": bool(flags.sandbox())}
