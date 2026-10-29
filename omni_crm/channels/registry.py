"""Tra adapter theo loại kênh."""

from __future__ import annotations

from omni_crm.channels.base import ChannelAdapter

_ADAPTERS: dict[str, ChannelAdapter] = {}


class AdapterNotFoundError(KeyError):
	"""Chưa có adapter nào đăng ký cho loại kênh này."""


def register_adapter(channel_type: str, adapter: ChannelAdapter) -> None:
	"""Đăng ký (hoặc thay) adapter cho một loại kênh; test dùng để cắm adapter giả."""
	_ADAPTERS[channel_type] = adapter


def unregister_adapter(channel_type: str) -> ChannelAdapter | None:
	"""Gỡ adapter của loại kênh; trả adapter đã gỡ (nếu có)."""
	return _ADAPTERS.pop(channel_type, None)


def get_adapter(channel_type: str) -> ChannelAdapter:
	"""Lấy adapter theo loại kênh; ném AdapterNotFoundError nếu chưa đăng ký."""
	try:
		return _ADAPTERS[channel_type]
	except KeyError:
		raise AdapterNotFoundError(channel_type) from None
