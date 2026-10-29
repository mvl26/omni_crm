"""Tra adapter theo loại kênh.

Adapter được tải lười: `get_adapter` tự import module của loại kênh khi chưa đăng ký, nên webhook (tiến
trình web) và worker (tiến trình nền) đều thấy adapter mà không phụ thuộc thứ tự import. Thêm kênh mới =
thêm một dòng vào `_LAZY_MODULES`; module phải có hằng `ADAPTER_CLASS` (và có thể tự gọi register_adapter).
"""

from __future__ import annotations

import importlib

from omni_crm.channels.base import ChannelAdapter

_ADAPTERS: dict[str, ChannelAdapter] = {}

# loại kênh -> module chứa adapter (D1-08: Zalo OA; D1-07: Email; Telephony: D2-01)
_LAZY_MODULES: dict[str, str] = {
	"Email": "omni_crm.channels.email.adapter",
	"Zalo OA": "omni_crm.channels.zalo.adapter",
	"Telephony": "omni_crm.channels.telephony.adapter",
}


class AdapterNotFoundError(KeyError):
	"""Chưa có adapter nào đăng ký cho loại kênh này."""


def register_adapter(channel_type: str, adapter: ChannelAdapter) -> None:
	"""Đăng ký (hoặc thay) adapter cho một loại kênh; test dùng để cắm adapter giả."""
	_ADAPTERS[channel_type] = adapter


def unregister_adapter(channel_type: str) -> ChannelAdapter | None:
	"""Gỡ adapter của loại kênh; trả adapter đã gỡ (nếu có)."""
	return _ADAPTERS.pop(channel_type, None)


def _lazy_load(channel_type: str) -> ChannelAdapter | None:
	path = _LAZY_MODULES.get(channel_type)
	if not path:
		return None
	module = importlib.import_module(path)
	if channel_type not in _ADAPTERS:  # import lại không chạy lại register_adapter ở cuối module
		_ADAPTERS[channel_type] = module.ADAPTER_CLASS()
	return _ADAPTERS[channel_type]


def get_adapter(channel_type: str) -> ChannelAdapter:
	"""Lấy adapter theo loại kênh (tải lười nếu cần); ném AdapterNotFoundError nếu không có."""
	adapter = _ADAPTERS.get(channel_type) or _lazy_load(channel_type)
	if adapter is None:
		raise AdapterNotFoundError(channel_type)
	return adapter
