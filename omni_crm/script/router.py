"""Bộ định tuyến bot kịch bản v3 (khung): đọc lượt tin, nhận mã nút `#CODE`, gọi handler đã đăng ký.

`handle_batch(batch_id) -> bool`: True = bot kịch bản đã nhận lượt này, luồng Reply cũ KHÔNG chạy; False = để luồng cũ
chạy (công tắc tắt với khách, không phải tin khách, hoặc chưa có handler cho tình huống).

Điều kiện dừng (trả True, bot im lặng): bot đang tạm dừng (`bot_paused_until` ở tương lai) hoặc nhân viên đã trả lời
sau tin khách cuối của lượt. Lead `omni_do_not_contact` hay `Junk`: bot kịch bản không gửi gì (send chặn) nhưng trả
False để luồng Reply cũ vẫn soạn nháp cho nhân viên, tin khách không biến mất. Handler do K2+ đăng ký trong
`omni_crm/script/flows/`.

Trước khi gọi handler (cho MỌI handler): chặn bấm lặp cùng một nút trong 30 giây, xóa câu hỏi dở khi khách bấm nút mở
luồng khác, báo bộ hẹn giờ là khách vừa nhắn.

Chống trả lời hai lần: tin kịch bản ghi `script_batch`; lượt đã có tin kịch bản thì trả True mà không chạy lại handler
(dấu bền, không phụ thuộc Redis). Khóa hạ tầng: khách ngoài danh sách thử chỉ được bot kịch bản nhận lượt khi
site_config `omni_script_bot_allowed` đúng (xem `script.send`); khóa đóng thì trả False để luồng Reply cũ chạy.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe
from frappe.utils import get_datetime, now_datetime

from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.script import send, state, store, switch

INBOUND = "Omni Inbound Message"
IDENTITY = "Omni Identity"
MENU_CODE = "MENU_CHINH"
MENU_ALIASES = {"MENU"}  # `#MENU` -> MENU_CHINH
FILE_TYPES = ("image", "file")
MENU_WORDS = {"menu"}  # khách gõ "menu" -> MENU_CHINH
SPECIAL_CODES = {"GAP_NHANVIEN", "MENU", MENU_CODE}
DEFAULT_KEY = "*"  # handler cho tin gõ tự do (không phải mã nút)
FOLLOW_KEY = "FOLLOW"  # handler cho sự kiện quan tâm OA
HANDLED_TTL = 3600
EVENTS = ("message", "follow")
SILENT_STOPS = frozenset(
	{send.PAUSED}
)  # bot tạm dừng (nhân viên đang xử lý): bot kịch bản nhận lượt mà im lặng
TAP_SECONDS = 30  # bấm lặp cùng một nút trong khoảng này thì không gửi lại
# Nút mở một luồng cấp cao khác: câu đang hỏi dở (`script_state.flow/field`) của luồng cũ không còn hiệu lực
FLOW_START_PREFIXES = ("MENU", "GAP_", "DEMO_", "DAT_DEMO", "SD_", "C4_", "TAILIEU_", "BAOGIA_")
STALE_KEYS = ("flow", "field", "node", "pending", "wrong")


@dataclass
class RouteContext:
	"""Ngữ cảnh một lượt tin cho handler kịch bản."""

	batch_id: str
	identity: str
	lead: str | None
	channel: str | None
	text: str  # tin khách đã gộp của lượt
	payload_code: str | None  # mã nút (không `#`) nếu khách bấm nút / gõ "menu"
	attachments: list = field(default_factory=list)
	is_first_contact: bool = False
	state: dict = field(default_factory=dict)
	event_type: str = "message"  # bổ sung ngoài giao ước: "message" | "follow"


Handler = Callable[[RouteContext], None]
_handlers: dict[str, Handler] = {}
_flows_loaded = False


def register(code_or_prefix: str, handler: Handler) -> None:
	"""Đăng ký handler cho một mã nút, một tiền tố (kết thúc `*` hoặc `_`), `*` (tin gõ) hoặc `FOLLOW`."""
	key = code_or_prefix.strip().lstrip("#")
	key = key if key == DEFAULT_KEY else key.upper()
	_handlers[key] = handler


def unregister(code_or_prefix: str) -> None:
	"""Gỡ handler (dùng trong test)."""
	key = code_or_prefix.strip().lstrip("#")
	_handlers.pop(key if key == DEFAULT_KEY else key.upper(), None)


def load_flows() -> None:
	"""Import mọi module trong `omni_crm.script.flows` một lần (module tự gọi `register`)."""
	global _flows_loaded
	if _flows_loaded:
		return
	_flows_loaded = True
	from omni_crm.script import flows

	for mod in pkgutil.iter_modules(flows.__path__):
		try:
			importlib.import_module(f"{flows.__name__}.{mod.name}")
		except Exception:
			frappe.log_error(title="Omni: nạp luồng kịch bản lỗi", message=f"module={mod.name}")


def find_handler(ctx: RouteContext) -> Handler | None:
	"""Handler cho lượt: mã nút (khớp đúng, rồi tiền tố dài nhất), FOLLOW cho sự kiện quan tâm, `*` cho tin gõ."""
	if ctx.event_type == "follow":
		return _handlers.get(FOLLOW_KEY)
	code = ctx.payload_code
	if code:
		if code in _handlers:
			return _handlers[code]
		prefixes = [k for k in _handlers if k.endswith(("*", "_")) and k != DEFAULT_KEY]
		for k in sorted(prefixes, key=len, reverse=True):
			if code.startswith(k.rstrip("*")):
				return _handlers[k]
	return _handlers.get(DEFAULT_KEY)


NON_TEXT = re.compile(r"^\[(?:sticker|gif|ảnh|hình ảnh|image|photo|tệp|file|link|vị trí|location|video|voice|ghi âm)\]$", re.I)
CHOICE_NUMBER = re.compile(r"(?:số\s*)?(\d{1,2})[.)]?")


def payload_code(text: str | None, known: set[str] | None = None) -> str | None:
	"""Mã nút trong tin khách: `#CODE` có trong danh sách mã (ô kịch bản + handler + mã đặc biệt), hoặc gõ "menu"."""
	raw = (text or "").strip()
	if raw.lower() in MENU_WORDS:
		return MENU_CODE
	if not raw.startswith("#") or " " in raw:
		return None
	code = store.normalize_code(raw)
	if code in MENU_ALIASES:
		return MENU_CODE
	codes = known if known is not None else store.known_codes() | set(_handlers) | SPECIAL_CODES
	return code if code in codes else None


def typed_choice(text: str | None, st: dict | None) -> str | None:
	"""Mã nút khi khách GÕ thay vì bấm (Zalo máy tính không hiện nút): số thứ tự trong danh sách của tin bot
	gần nhất, hoặc gõ lại đúng nhãn nút. Chỉ xét tin ngắn khớp trọn; không khớp thì None (đi đường hiểu ý như cũ).
	"""
	choices = ((st or {}).get("script_state") or {}).get("choices") or {}
	items = choices.get("items") or []
	raw = " ".join((text or "").split()).strip().lower()
	if not items or not raw or len(raw) > 60:
		return None
	target = None
	m = CHOICE_NUMBER.fullmatch(raw)
	if m and choices.get("numbered"):
		index = int(m.group(1)) - 1
		target = items[index][1] if 0 <= index < len(items) else None
	else:
		target = next((t for label, t in items if str(label).strip().lower() == raw.rstrip(".")), None)
		pending = ((st or {}).get("script_state") or {}).get("field")  # đang chờ câu trả lời gõ tự do: không đoán
		digits = any(ch.isdigit() for label, _t in items for ch in str(label))  # khung giờ: luồng demo tự đọc ngày giờ
		if not target and choices.get("numbered") and not pending and not digits:
			target = _loose_choice(raw, items)
	return payload_code(f"#{target}") if target else None


LOOSE_MAX_WORDS = 6
# có từ phủ định / từ để hỏi: không phải câu chọn, để bộ hiểu ý xử lý
LOOSE_SKIP = frozenset("khong chua dung khoi huy doi hoan nao gi sao bao nhieu khi".split())
LOOSE_FILLERS = frozenset(
	"anh chi toi minh muon can cho xin qua cua cac nhung mot giup nhe voi lam chon lay".split()
)


def _loose_choice(raw: str, items: list, strict: bool = True) -> str | None:
	"""Khách gõ gần đúng một lựa chọn ("Online", "anh muốn demo online" cho nút "Online qua Teams") — thuần.

	Chỉ nhận khi câu ngắn, không có từ phủ định / từ để hỏi, chứa từ riêng của ĐÚNG MỘT lựa chọn và nhiều nhất một
	từ lạ ngoài các lựa chọn; còn lại trả None để đi đường hiểu ý như cũ.
	"""
	if "?" in raw:
		return None
	try:
		from omni_crm.script import classify

		fold = classify.fold
	except Exception:
		return None

	def words(text) -> list[str]:
		return [w for w in fold(str(text)).split() if len(w) >= 3 and w.isalpha()]

	typed = fold(raw).split()
	if not typed or LOOSE_SKIP.intersection(typed) or (strict and len(typed) > LOOSE_MAX_WORDS):
		return None
	content = {w for w in words(raw) if w not in LOOSE_FILLERS}
	labels = [(set(words(label)) - LOOSE_FILLERS, target) for label, target in items]
	every = [w for tokens, _t in labels for w in tokens]
	hits = [t for tokens, t in labels if any(every.count(w) == 1 and w in content for w in tokens)]
	foreign = [w for w in content if w not in every]
	return hits[0] if len(hits) == 1 and (not strict or len(foreign) <= 1) else None


def _code_of(last, st: dict) -> str | None:
	"""Mã nút của tin cuối: bấm nút / gõ "menu", hoặc gõ số / nhãn của danh sách lựa chọn."""
	if last.event_type != "message":
		return None
	return payload_code(last.text) or typed_choice(last.text, st)


def _batch_rows(batch_id: str) -> list:
	return frappe.get_all(
		INBOUND,
		filters={"batch_id": batch_id},
		fields=["name", "event_type", "text", "attachments", "identity", "channel", "lead"],
		order_by="received_at asc, creation asc",
	)


def build_context(batch_id: str) -> RouteContext | None:
	"""RouteContext của lượt, hoặc None nếu tin cuối không phải tin khách/sự kiện quan tâm."""
	rows = _batch_rows(batch_id)
	last = rows[-1] if rows else None
	if not last or not last.identity or last.event_type not in EVENTS:
		return None
	lead = last.lead or frappe.db.get_value(IDENTITY, last.identity, "lead")
	lead = lead_bridge.follow_merged(lead) or lead  # Lead đã gộp: làm việc trên Lead chính
	messages = [r for r in rows if r.event_type == "message"]
	# "[Sticker]", "[Hình ảnh]"…: chữ thế chỗ của tin không có chữ, không phải lời khách nói ("Alo em" + sticker vẫn là lời chào)
	text = "\n".join(
		(r.text or "").strip()
		for r in messages
		if (r.text or "").strip() and not NON_TEXT.match((r.text or "").strip())
	)
	attachments: list = []
	for r in messages:
		attachments.extend(_attachments_of(r.attachments))
	st = state.get(last.identity)
	return RouteContext(
		batch_id=batch_id,
		identity=last.identity,
		lead=lead,
		channel=last.channel,
		text=text,
		payload_code=_code_of(last, st),
		attachments=attachments,
		is_first_contact=not st.get("script_greeted_at"),
		state=st,
		event_type=last.event_type,
	)


def _attachments_of(raw) -> list:
	try:
		value = frappe.parse_json(raw) if raw else []
	except Exception:
		return []
	if not isinstance(value, list):
		return []
	# chỉ ảnh và tệp mới là "tệp khách gửi" (danh mục...); sticker, GIF, vị trí, liên kết không tính (thử thật 17/11)
	return [a for a in value if isinstance(a, dict) and str(a.get("type") or "file").lower() in FILE_TYPES]


def _handled_key(batch_id: str) -> str:
	return f"omni:script:batch:{batch_id}"


def handle_batch(batch_id: str) -> bool:
	"""Xử lý lượt tin bằng bot kịch bản; True nếu đã nhận lượt (luồng Reply cũ dừng), False để luồng cũ chạy."""
	if not batch_id:
		return False
	if frappe.cache().get_value(_handled_key(batch_id), expires=True):
		return True
	rows = frappe.get_all(INBOUND, filters={"batch_id": batch_id}, fields=["identity"], limit=1)
	identity = rows[0].identity if rows else None
	if not switch.enabled_for(identity):  # rẻ: khách ngoài danh sách thử không chạm gì thêm
		return False
	if not send.infra_ok(identity):  # khóa hạ tầng đóng với khách ngoài danh sách thử: luồng cũ chạy
		return False
	if send.batch_has_outbound(
		batch_id
	):  # dấu bền: lượt đã có tin kịch bản (Redis mất / job bị giết giữa chừng)
		_mark(batch_id)
		return True
	ctx = build_context(batch_id)
	if ctx is None:
		return False
	code = send.block_code(ctx.identity)
	if code in SILENT_STOPS and _customer_cancels_handoff(ctx):  # "anh không muốn gặp nhân viên": bot hoạt động lại
		_mark(batch_id)
		frappe.db.commit()
		return True
	if code in SILENT_STOPS or _sale_replied(ctx):  # nhân viên đang xử lý: bot im lặng
		_mark(batch_id)
		return True
	if code:  # không liên hệ / rác / công tắc vừa tắt: bot kịch bản im, luồng cũ soạn nháp cho nhân viên
		return False
	load_flows()
	handler = find_handler(ctx)
	if handler is None:
		return False
	if _repeated_tap(ctx):  # bấm lặp cùng nút trong 30 giây: không gửi lại
		_mark(batch_id)
		return True
	_clear_stale_flow(ctx)
	_notify_scheduler(ctx)
	_expire_stale_drafts(ctx.lead)
	frappe.flags[send.BATCH_FLAG] = batch_id
	try:
		handler(ctx)
	finally:
		frappe.flags.pop(send.BATCH_FLAG, None)
	_mark(batch_id)
	frappe.db.commit()
	return True


def _customer_cancels_handoff(ctx: RouteContext) -> bool:
	"""Khách đang chờ nhân viên mà nói không cần nhân viên nữa (xem `flows.handoff.customer_cancels`). Lỗi → False."""
	if ctx.event_type != "message" or ctx.payload_code:
		return False
	try:
		load_flows()
		from omni_crm.script.flows import handoff

		frappe.flags[send.BATCH_FLAG] = ctx.batch_id
		return bool(handoff.customer_cancels(ctx))
	except Exception as e:
		frappe.db.rollback()
		frappe.logger("omni").info("omni script: không huỷ được chờ nhân viên (%s)", type(e).__name__)
		return False
	finally:
		frappe.flags.pop(send.BATCH_FLAG, None)


def _repeated_tap(ctx: RouteContext) -> bool:
	"""Khách bấm lại đúng nút vừa bấm trong `TAP_SECONDS` giây; tin gõ thì xóa dấu nút vừa bấm. Không commit.

	Dấu nằm trong `script_state` (`tap`, `tap_at`), ghi cùng transaction với handler: handler lỗi thì dấu cũng lùi lại.
	"""
	if ctx.event_type != "message":
		return False
	current = ctx.state.get("script_state") or {}
	code = ctx.payload_code
	if not code:
		if current.get("tap"):
			ctx.state["script_state"] = state.update_state(ctx.identity, tap=None, tap_at=None)
		return False
	now = now_datetime()
	if current.get("tap") == code and current.get("tap_at"):
		try:
			if 0 <= (now - get_datetime(current["tap_at"])).total_seconds() < TAP_SECONDS:
				return True
		except Exception:
			pass
	ctx.state["script_state"] = state.update_state(ctx.identity, tap=code, tap_at=str(now))
	return False


def _clear_stale_flow(ctx: RouteContext) -> None:
	"""Khách bấm nút mở luồng cấp cao khác: bỏ câu đang hỏi dở của luồng cũ (`flow`, `field`...). Không commit."""
	code = ctx.payload_code
	current = ctx.state.get("script_state") or {}
	if not code or not code.startswith(FLOW_START_PREFIXES) or not any(current.get(k) for k in STALE_KEYS):
		return
	ctx.state["script_state"] = state.update_state(ctx.identity, **dict.fromkeys(STALE_KEYS))


def _notify_scheduler(ctx: RouteContext) -> None:
	"""Báo bộ hẹn giờ là khách vừa nhắn (hủy tin nhắc đang chờ); phần đó chưa có hoặc lỗi thì bỏ qua."""
	try:
		from omni_crm.script import scheduler

		scheduler.on_inbound(ctx.identity, ctx.lead)
	except ImportError:
		return
	except Exception as e:
		frappe.logger("omni").info("omni script: scheduler.on_inbound lỗi (%s)", type(e).__name__)


def is_marked(batch_id: str | None) -> bool:
	"""Lượt đã được đánh dấu là bot kịch bản nhận (khóa Redis `omni:script:batch:<id>`)."""
	return bool(batch_id) and bool(frappe.cache().get_value(_handled_key(batch_id), expires=True))


def _sale_replied(ctx: RouteContext) -> bool:
	"""Nhân viên đã trả lời sau tin khách cuối của lượt (như ngắt mạch của luồng Reply)."""
	if not ctx.lead or ctx.event_type != "message":
		return False
	from omni_crm.bot import reply  # import trễ: reply gọi router

	last_at = frappe.db.get_value(
		INBOUND,
		{"batch_id": ctx.batch_id, "event_type": "message"},
		"received_at",
		order_by="received_at desc",
	)
	return bool(last_at) and bool(reply.sale_replies_after(ctx.lead, last_at))


def _expire_stale_drafts(lead: str | None) -> None:
	"""Tin khách mới: nháp Draft cũ của Lead hết hạn trước khi bot kịch bản trả lời. Không commit."""
	if lead:
		from omni_crm.bot import reply  # import trễ: reply gọi router

		reply.expire_stale_for_lead(lead)


def _mark(batch_id: str) -> None:
	frappe.cache().set_value(_handled_key(batch_id), 1, expires_in_sec=HANDLED_TTL)
