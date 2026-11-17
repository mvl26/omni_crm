"""Bộ chạy ô kịch bản (K2): gửi một ô rồi thực hiện chuỗi `action` của ô (xem `seed/VARIABLES.md` mục 2).

Các luồng (K3–K7) đăng ký lệnh action của mình bằng `register_action`; không sửa tệp này ngoài người làm K2.
"""

from __future__ import annotations

from collections.abc import Callable

from omni_crm.script.router import RouteContext

# fn(ctx, arg, node) -> True nếu lệnh đã "nuốt" lượt (dừng chuỗi action còn lại), None/False để chạy tiếp
ActionFn = Callable[[RouteContext, str, dict], "bool | None"]
# fn(ctx) -> True nếu đã xử lý tin gõ tự do của khách (đang giữa một luồng), False để phân loại bình thường
StateFn = Callable[[RouteContext], bool]

_actions: dict[str, ActionFn] = {}
_state_handlers: dict[str, StateFn] = {}


def register_action(name: str, fn: ActionFn) -> None:
	"""Đăng ký lệnh action (`handoff`, `demo`, `stop`...); `name` là phần trước dấu `:`."""
	_actions[name.strip().lower()] = fn


def register_state(flow: str, fn: StateFn) -> None:
	"""Đăng ký bộ xử lý tin gõ khi `script_state["flow"] == flow` (khách đang ở giữa luồng đó)."""
	_state_handlers[flow.strip().lower()] = fn


def run_node(ctx: RouteContext, code: str, extra: dict | None = None) -> bool:
	"""Gửi ô `code` (biến lấy từ Lead/trạng thái + `extra`), ghi bước đang đứng, chạy chuỗi action. True nếu đã gửi/chạy."""
	raise NotImplementedError  # K2


def variables(ctx: RouteContext, extra: dict | None = None) -> dict:
	"""Giá trị các biến `{...}` cho lượt này (ten, dich_vu, ngay_lam_viec_tiep_theo, thoi_diem, email, sdt...)."""
	raise NotImplementedError  # K2
