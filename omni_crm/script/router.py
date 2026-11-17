"""Bộ định tuyến bot kịch bản v3 (khung): đọc lượt tin, nhận mã nút `#CODE`, gọi handler đã đăng ký.

`handle_batch(batch_id) -> bool`: True = bot kịch bản đã nhận lượt này, luồng Reply cũ KHÔNG chạy; False = để luồng cũ
chạy (công tắc tắt với khách, không phải tin khách, hoặc chưa có handler cho tình huống).

Điều kiện dừng (trả True, bot im lặng): bot đang tạm dừng (`bot_paused_until` ở tương lai), Lead `omni_do_not_contact`,
Lead ở trạng thái `Junk` (spam). Handler do K2+ đăng ký trong `omni_crm/script/flows/`.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe

from omni_crm.script import send, state, store, switch

INBOUND = "Omni Inbound Message"
IDENTITY = "Omni Identity"
MENU_CODE = "MENU_CHINH"
MENU_ALIASES = {"MENU"}  # `#MENU` -> MENU_CHINH
MENU_WORDS = {"menu"}  # khách gõ "menu" -> MENU_CHINH
SPECIAL_CODES = {"GAP_NHANVIEN", "MENU", MENU_CODE}
DEFAULT_KEY = "*"  # handler cho tin gõ tự do (không phải mã nút)
FOLLOW_KEY = "FOLLOW"  # handler cho sự kiện quan tâm OA
HANDLED_TTL = 3600
EVENTS = ("message", "follow")


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
	messages = [r for r in rows if r.event_type == "message"]
	text = "\n".join((r.text or "").strip() for r in messages if (r.text or "").strip())
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
		payload_code=payload_code(last.text) if last.event_type == "message" else None,
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
	return value if isinstance(value, list) else []


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
	ctx = build_context(batch_id)
	if ctx is None:
		return False
	if send.blocked_reason(ctx.identity):  # tạm dừng / không liên hệ / spam: bot im lặng
		_mark(batch_id)
		return True
	load_flows()
	handler = find_handler(ctx)
	if handler is None:
		return False
	handler(ctx)
	_mark(batch_id)
	frappe.db.commit()
	return True


def _mark(batch_id: str) -> None:
	frappe.cache().set_value(_handled_key(batch_id), 1, expires_in_sec=HANDLED_TTL)
