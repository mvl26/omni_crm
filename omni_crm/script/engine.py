"""Bộ chạy ô kịch bản (K2): gửi một ô rồi thực hiện chuỗi `action` của ô (xem `seed/VARIABLES.md` mục 2).

Các luồng (K3-K7) đăng ký lệnh action của mình bằng `register_action`; không sửa tệp này ngoài người làm K2.

Quy tắc chính:
- Biến thể theo dịch vụ: có ô `<CODE>_<VATTU|PHANMEM|SPD>` khớp dịch vụ đang tư vấn thì dùng thay `<CODE>`.
- Ô điều hướng (text trống + action) chỉ chạy action, không gửi gì.
- Ô có `collect:<field>` mà thông tin đã có thì bỏ câu hỏi, chạy tiếp phần còn lại của chuỗi (3.1).
- Lệnh chưa ai đăng ký: ghi log một lần rồi chạy tiếp.
- Nút dẫn tới ô Tắt/trống/không gửi được: `run_button` chuyển nhân viên (nếu đã có lệnh `handoff`), không thì gửi lại menu.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

import frappe
from frappe import _
from frappe.utils import now_datetime

from omni_crm.script import router, send, state, store, switch, timeutil, validate
from omni_crm.script.router import MENU_CODE, RouteContext

# fn(ctx, arg, node) -> True nếu lệnh đã "nuốt" lượt (dừng chuỗi action còn lại), None/False để chạy tiếp
ActionFn = Callable[[RouteContext, str, dict], "bool | None"]
# fn(ctx) -> True nếu đã xử lý tin gõ tự do của khách (đang giữa một luồng), False để phân loại bình thường
StateFn = Callable[[RouteContext], bool]

_actions: dict[str, ActionFn] = {}
_state_handlers: dict[str, StateFn] = {}

LEAD = "CRM Lead"
SERVICES = ("VATTU", "SUPPLYCORE", "SPD")
SUFFIX = {
	"VATTU": "VATTU",
	"SUPPLYCORE": "PHANMEM",
	"SPD": "SPD",
}  # mã dịch vụ -> hậu tố mã ô (VARIABLES mục 3)
DEFAULT_PRIVACY_URL = "miyano.com.vn/chinh-sach-bao-mat"  # URL nguyên văn kịch bản 3.4
LEAD_IN_KEY = "_lead_in"  # câu dẫn (đã soạn từ ô kịch bản) ghép lên đầu tin kế tiếp của lượt
OPTIONAL_VARS = {"ten"}  # biến được phép trống khi gửi
QUOTE_NO_SERVICE = "BAOGIA_HOISANPHAM"
FORCED_ASK = ("NL_DOIEMAIL", "NL_BG_DOIEMAIL")  # "Đổi email khác": luôn hỏi (= collect.FORCED_NODES)
MAX_NAME = 20
MAX_RESEND = (
	2  # tin gõ: cùng một ô không gửi quá 2 lần liên tiếp (chống lặp vô hạn), lần sau chuyển nhân viên
)
FELL_BACK_KEY = "_fell_back"
MAX_DEPTH = 8  # chặn vòng lặp then/goto
REST_KEY, DEPTH_KEY = "_rest", "_depth"  # ô nhớ tạm trên ctx.state trong lúc chạy chuỗi action
_VAR = re.compile(r"\{(\w+)\}")
_PLACEHOLDER = re.compile(r"^(\w*_)(?:<.*|&lt;.*)?$")
_warned: set[str] = set()


def register_action(name: str, fn: ActionFn) -> None:
	"""Đăng ký lệnh action (`handoff`, `demo`, `stop`...); `name` là phần trước dấu `:`."""
	_actions[name.strip().lower()] = fn


def register_state(flow: str, fn: StateFn) -> None:
	"""Đăng ký bộ xử lý tin gõ khi `script_state["flow"] == flow` (khách đang ở giữa luồng đó)."""
	_state_handlers[flow.strip().lower()] = fn


def run_node(ctx: RouteContext, code: str, extra: dict | None = None) -> bool:
	"""Gửi ô `code` (biến lấy từ Lead/trạng thái + `extra`), ghi bước đang đứng, chạy chuỗi action. True nếu đã gửi/chạy."""
	router.load_flows()  # các luồng đăng ký lệnh của mình khi được import (idempotent)
	return _run(ctx, code, extra, 0)


def variables(ctx: RouteContext, extra: dict | None = None) -> dict:
	"""Giá trị các biến `{...}` cho lượt này (ten, dich_vu, ngay_lam_viec_tiep_theo, thoi_diem, email, sdt...)."""
	now = now_datetime()
	st = script_state(ctx)
	lead = _lead_row(ctx.lead)
	next_day = timeutil.next_working_day_text(now)
	email = lead.get("email") or ""
	phone = validate.phone_local(lead.get("mobile_no") or lead.get("phone") or "")
	missing = [w for w, v in ((_("email"), email), (_("số điện thoại"), phone)) if not v]
	# lời khách gõ không bao giờ được chèn lại vào tin bot: tài liệu khách tự ghi -> cụm trung tính
	doc_name = st.get("tai_lieu") or ""
	if doc_name and st.get("tai_lieu_typed"):
		doc_name = _("tài liệu anh/chị yêu cầu")
	out = {
		"ten": safe_name(st.get("ten")),
		"dich_vu": service_label(current_service(ctx)),
		"ngay_lam_viec_tiep_theo": next_day,
		"thoi_diem": next_day if timeutil.cutoff_1630(now) else _("trong hôm nay"),
		"email": email,
		"sdt": phone,
		"tai_lieu": doc_name,
		"tai_lieu_bao_gia": _("báo giá")
		if (st.get("branch") or st.get("last_branch")) == "3b"
		else (doc_name or _("tài liệu")),
		"thong_tin_con_thieu": _(" và ").join(missing),
		"chinh_sach_bao_mat": (switch.setting("script_privacy_url") or "").strip() or DEFAULT_PRIVACY_URL,
		"kenh_chat": _channel_type(ctx.channel),
	}
	out.update(extra or {})
	return out


def safe_name(value: str | None) -> str:
	"""`{ten}` an toàn để chèn vào câu: chỉ chữ cái (và khoảng trắng), tối đa 20 ký tự; khác thì để trống."""
	name = " ".join(str(value or "").split())
	ok = bool(name) and len(name) <= MAX_NAME and all(ch.isalpha() or ch == " " for ch in name)
	return name if ok else ""


def script_state(ctx: RouteContext) -> dict:
	"""`script_state` mới nhất của định danh (đọc lại DB vì action trong lượt có thể vừa ghi)."""
	return state.get(ctx.identity)["script_state"]


def current_service(ctx: RouteContext) -> str | None:
	"""Dịch vụ đang tư vấn: `script_state.service`, không có thì dịch vụ ghi sau cùng trên Lead; None nếu chưa có."""
	svc = (script_state(ctx).get("service") or "").upper()
	if svc in SERVICES:
		return svc
	raw = (frappe.db.get_value(LEAD, ctx.lead, "omni_services") if ctx.lead else "") or ""
	items = [s.strip().upper() for s in raw.split(",") if s.strip().upper() in SERVICES]
	return items[-1] if items else None


def service_label(svc: str | None) -> str:
	"""Tên dịch vụ viết thường để chèn giữa câu (`{dich_vu}`); chưa có dịch vụ trả chuỗi rỗng."""
	labels = {
		"VATTU": _("cung ứng vật tư y tế"),
		"SUPPLYCORE": _("phần mềm SupplyCore"),
		"SPD": _("hệ thống SPD"),
	}
	return labels.get(svc or "", "")


def service_code(ctx: RouteContext, base: str) -> str | None:
	"""Mã ô `<base>_<hậu tố dịch vụ đang tư vấn>` (vd BAOGIA -> BAOGIA_SPD); chưa có dịch vụ trả None."""
	svc = current_service(ctx)
	return f"{base}_{SUFFIX[svc]}" if svc else None


def resolve(ctx: RouteContext, code: str | None) -> str:
	"""Mã ô thật sẽ chạy: thay `<dich_vu>`, rồi áp quy tắc biến thể theo dịch vụ."""
	raw = (code or "").strip().lstrip("#")
	svc = current_service(ctx)
	placeholder = _PLACEHOLDER.match(raw)  # goto:BAOGIA_<dich_vu>; Frappe có thể đã lọc/thoát thẻ khi lưu
	if placeholder:
		if not svc:
			return QUOTE_NO_SERVICE
		raw = placeholder.group(1) + SUFFIX[svc]
	code = store.normalize_code(raw)
	if svc and code and not code.endswith(f"_{SUFFIX[svc]}"):
		variant = f"{code}_{SUFFIX[svc]}"
		if frappe.db.exists(store.NODE, variant):
			return variant
	return code


def split_actions(action: str | None) -> list[tuple[str, str]]:
	"""Chuỗi action `a;b:x` -> [(lệnh viết thường, tham số)]."""
	out = []
	for part in html.unescape(action or "").split(";"):  # Frappe lưu `<dich_vu>` thành `&lt;dich_vu&gt;`
		part = part.strip()
		if part:
			name, _sep, arg = part.partition(":")
			out.append((name.strip().lower(), arg.strip()))
	return out


def join_actions(cmds: list) -> str:
	"""Ngược của `split_actions` (để lưu phần chuỗi còn lại vào `script_state`)."""
	return ";".join(f"{n}:{a}" if a else n for n, a in cmds)


def run_actions(ctx: RouteContext, action: str | list, node: dict | None = None, depth: int = 0) -> bool:
	"""Chạy chuỗi action theo thứ tự; True nếu có ít nhất một lệnh đã chạy (lệnh chưa đăng ký không tính)."""
	cmds = split_actions(action) if isinstance(action, str) else [tuple(c) for c in action]
	node = node or {}
	did = False
	for i, (name, arg) in enumerate(cmds):
		fn = _OWN.get(name) or _actions.get(name)
		if fn is None:
			_warn_once(name)
			continue
		ctx.state[REST_KEY] = cmds[i + 1 :]  # lệnh `collect` cần biết phần còn lại của chuỗi
		ctx.state[DEPTH_KEY] = depth
		did = True
		if fn(ctx, arg, node):
			break
	return did


def run_button(ctx: RouteContext, code: str) -> bool:
	"""Chạy ô đích của nút; không gửi/chạy được thì chuyển nhân viên (nếu có lệnh `handoff`) hoặc gửi lại menu chính."""
	router.load_flows()
	if _run(ctx, code, None, 0):
		return True
	return fallback(ctx, code)


def fallback(ctx: RouteContext, code: str | None = None, arg: str = "") -> bool:
	"""Không bao giờ im lặng: gọi lệnh `handoff` nếu đã đăng ký, không thì gửi lại `MENU_CHINH`."""
	ctx.state[FELL_BACK_KEY] = True
	fn = _actions.get("handoff")
	if fn is not None:
		node = store.get_node(resolve(ctx, code)) if code else None
		fn(ctx, arg, node or {"code": store.normalize_code(code)})
		return True
	if store.normalize_code(code) == MENU_CODE:
		return False
	return _run(ctx, MENU_CODE, None, 0)


def missing_vars(text: str, values: dict) -> list[str]:
	"""Biến trong câu chưa có giá trị (trừ biến được phép trống): có thì KHÔNG gửi ô."""
	return [
		v
		for v in dict.fromkeys(_VAR.findall(text or ""))
		if v not in OPTIONAL_VARS and not str(values.get(v) or "").strip()
	]


def _run(ctx: RouteContext, code: str | None, extra: dict | None, depth: int) -> bool:
	if depth > MAX_DEPTH:
		return False
	code = resolve(ctx, code)
	node = store.get_node(code)
	if not node or node["status"] == store.OFF:
		return False
	cmds = split_actions(node["action"])
	has_text = bool(node["text"].strip())
	if (
		has_text and code not in FORCED_ASK and _collect_known(ctx, cmds)
	):  # 3.1: thông tin đã có thì không hỏi lại
		skipped = [("collect_skipped", a) if n == "collect" else (n, a) for n, a in cmds]
		return run_actions(ctx, skipped, node, depth)
	if has_text:
		values = variables(ctx, extra)
		if missing_vars(node["text"], values):
			return False
		repeat = _repeat_count(ctx, code)
		if repeat >= MAX_RESEND:
			return False
		prefix = ctx.state.pop(LEAD_IN_KEY, "") or _ack_prefix(ctx, code, values)
		if not send.send_node(ctx.identity, code, values, prefix=prefix):
			return False
		router._mark(ctx.batch_id)  # đã có tin gửi khách: luồng Reply cũ không được trả lời thêm
		state.set_node(ctx.identity, code, node["step"] or None)
		state.update_state(ctx.identity, resend=repeat or None)
	elif not cmds:
		return False
	did = run_actions(ctx, cmds, node, depth)
	return has_text or did


def _ack_prefix(ctx: RouteContext, code: str, values: dict) -> str:
	"""Câu "Miyano xác nhận thông tin…" ghép lên đầu tin sắp gửi, nếu khách vừa trả lời một câu hỏi trong lượt này."""
	try:
		from omni_crm.script.flows import collect

		return "" if code == collect.ACK_NODE else collect.take_ack(ctx, values)
	except Exception:
		return ""


def _repeat_count(ctx: RouteContext, code: str) -> int:
	"""Số lần liên tiếp ô `code` sắp được gửi lại trong các lượt khách gõ (bấm nút thì không tính)."""
	if ctx.payload_code:
		return 0
	st = state.get(ctx.identity)
	if st.get("script_node") != code:
		return 0
	return int(st["script_state"].get("resend") or 0) + 1


def _collect_known(ctx: RouteContext, cmds: list[tuple[str, str]]) -> bool:
	names = [name for name, _arg in cmds]
	fields = [arg for name, arg in cmds if name == "collect"]
	if not fields:
		return False
	from omni_crm.script.flows import collect

	# `branch:` đứng trước `collect` sẽ xoá câu trả lời cũ: chỉ coi là đã biết khi thông tin nằm trên Lead
	lead_only = "branch" in names[: names.index("collect")]
	prefill = ctx.state.get(collect.PREFILL_KEY) or {}
	return all(collect.known(ctx, f, lead_only=lead_only) or f.strip().lower() in prefill for f in fields)


def _warn_once(name: str) -> None:
	if name not in _warned:
		_warned.add(name)
		frappe.logger("omni").info("omni script: lệnh action chưa đăng ký, bỏ qua: %s", name[:40])


def _lead_row(lead: str | None) -> dict:
	if not lead:
		return {}
	return frappe.db.get_value(LEAD, lead, ["email", "mobile_no", "phone"], as_dict=True) or {}


def _channel_type(channel: str | None) -> str:
	return (frappe.db.get_value("Omni Channel", channel, "channel_type") if channel else "") or ""


# --- Lệnh của K2 ----------------------------------------------------------------------------------------------


def _noop(ctx: RouteContext, arg: str, node: dict) -> None:
	return None


def _service(ctx: RouteContext, arg: str, node: dict) -> None:
	svc = arg.upper()
	state.add_service(ctx.lead, svc)
	state.update_state(ctx.identity, service=svc)


def _need(ctx: RouteContext, arg: str, node: dict) -> None:
	if ctx.lead and arg:
		frappe.db.set_value(LEAD, ctx.lead, "omni_journey_need", arg, update_modified=False)


def _journey(ctx: RouteContext, arg: str, node: dict) -> None:
	if arg:
		state.set_journey(ctx.lead, arg)


def _branch(ctx: RouteContext, arg: str, node: dict) -> None:
	# vào nhánh thu thông tin mới: bỏ câu trả lời của lần trước (file khách gửi giữ ở `files`)
	from omni_crm.script.flows import collect

	collect.reset_answers(ctx.identity, branch=arg.lower() or None)
	collect.apply_prefill(ctx)  # thông tin khách đã nêu ngay trong tin mở nhánh: ghi lại để không hỏi lại


def _answer(ctx: RouteContext, arg: str, node: dict) -> None:
	field, _sep, value = arg.partition("=")
	if field.strip():
		from omni_crm.script.flows import collect

		collect.save_answer(ctx, field.strip(), value.strip())


def _then(ctx: RouteContext, arg: str, node: dict) -> None:
	rest, depth = ctx.state.get(REST_KEY), int(ctx.state.get(DEPTH_KEY) or 0)
	ok = _run(ctx, arg, None, depth + 1)
	ctx.state[REST_KEY], ctx.state[DEPTH_KEY] = rest, depth
	if not ok and not ctx.state.get(FELL_BACK_KEY):
		fallback(
			ctx, arg, "task"
		)  # ô kế không gửi được (Tắt, Cần duyệt, thiếu biến): không để khách chờ trong im lặng


def _goto(ctx: RouteContext, arg: str, node: dict) -> bool:
	depth = int(ctx.state.get(DEPTH_KEY) or 0)
	if not _run(ctx, arg, None, depth + 1):
		fallback(ctx, arg)
	return True


def _collect(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	from omni_crm.script.flows import collect

	return collect.start(ctx, arg, node, list(ctx.state.get(REST_KEY) or []))


def _collect_skipped(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	from omni_crm.script.flows import collect

	return collect.after_skip(ctx, arg, node, list(ctx.state.get(REST_KEY) or []))


def _task(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	from omni_crm.script.flows import collect

	return collect.task_action(ctx, arg, node)


def _kb_answer(ctx: RouteContext, arg: str, node: dict) -> bool | None:
	from omni_crm.script.flows import menu

	return menu.kb_action(ctx, arg, node)


_OWN: dict[str, ActionFn] = {
	"menu": _noop,
	"reask": _noop,  # việc đếm số lần hỏi lại nằm ở flows/menu.py và flows/collect.py
	"kb_answer": _kb_answer,
	"service": _service,
	"need": _need,
	"journey": _journey,
	"branch": _branch,
	"collect": _collect,
	"collect_skipped": _collect_skipped,  # nội bộ: ô hỏi bị bỏ qua vì đã có thông tin
	"answer": _answer,
	"task": _task,  # K4 giữ `task` (docs/quote) để không tạo việc hai lần; loại khác chuyển cho bên đã đăng ký
	"then": _then,
	"goto": _goto,
}
