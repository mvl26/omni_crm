"""Tra nhà cung cấp chuyển giọng nói theo tên, tải lười (cùng kiểu `channels/telephony/registry.py`).

Chưa có adapter thật nào: danh sách nhà cung cấp chưa được chọn (docs/12 Q3, D3-03). Thêm adapter = thêm một dòng
vào `_LAZY` (module, tên lớp) cộng một module `omni_crm/stt/<ten>.py`. `Fake` chỉ nạp được ở môi trường dev/test.
"""

from __future__ import annotations

import importlib

from omni_crm.channels.telephony.registry import dev_allowed
from omni_crm.stt.base import STTProvider

_PROVIDERS: dict[str, STTProvider] = {}
_LAZY: dict[str, tuple[str, str]] = {
	"Fake": ("omni_crm.stt.fake", "FakeSTTProvider"),
}
DEV_ONLY = frozenset({"Fake"})


class STTProviderNotFoundError(KeyError):
	"""Chưa có nhà cung cấp chuyển giọng nói nào đăng ký cho tên này, hoặc không được phép dùng ở môi trường này."""


def register_provider(provider: STTProvider) -> None:
	"""Đăng ký (hoặc thay) nhà cung cấp theo `provider.name`; test dùng để cắm nhà cung cấp giả."""
	_PROVIDERS[provider.name] = provider


def unregister_provider(name: str) -> STTProvider | None:
	"""Gỡ nhà cung cấp; trả bản đã gỡ (nếu có)."""
	return _PROVIDERS.pop(name, None)


def get_provider(name: str | None) -> STTProvider:
	"""Nhà cung cấp theo tên (tải lười); ném STTProviderNotFoundError nếu không có hoặc `Fake` ngoài môi trường dev."""
	if not name:
		raise STTProviderNotFoundError("(chưa chọn)")
	if name in DEV_ONLY and not dev_allowed():
		raise STTProviderNotFoundError(name)
	if name in _PROVIDERS:
		return _PROVIDERS[name]
	target = _LAZY.get(name)
	if not target:
		raise STTProviderNotFoundError(name)
	try:
		module = importlib.import_module(target[0])
	except ImportError:
		raise STTProviderNotFoundError(name) from None
	_PROVIDERS.setdefault(name, getattr(module, target[1])())
	return _PROVIDERS[name]


def available_names() -> list[str]:
	"""Tên nhà cung cấp dùng được ở môi trường này (đã đăng ký hoặc khai báo lười; bỏ `Fake` ngoài dev/test)."""
	names = set(_PROVIDERS) | set(_LAZY)
	if not dev_allowed():
		names -= DEV_ONLY
	return sorted(names)
