"""Tra nhà cung cấp tổng đài theo `Omni Channel.telephony_provider` (đối soát đêm thêm ở D2-03).

Nhà cung cấp được tải lười. `Fake` chỉ nạp được khi `developer_mode` bật hoặc đang chạy test.
"""

from __future__ import annotations

import importlib

import frappe
from frappe.utils import cint

from omni_crm.channels.telephony.base import TelephonyProvider

_PROVIDERS: dict[str, TelephonyProvider] = {}

# tên nhà cung cấp -> (module, tên lớp). Stringee thêm ở D2-02.
_LAZY: dict[str, tuple[str, str]] = {
	"Fake": ("omni_crm.channels.telephony.fake", "FakeProvider"),
	"Stringee": ("omni_crm.channels.telephony.stringee", "StringeeProvider"),
}
DEV_ONLY = frozenset({"Fake"})


class ProviderNotFoundError(KeyError):
	"""Chưa có nhà cung cấp tổng đài nào đăng ký cho tên này, hoặc không được phép dùng ở môi trường này."""


def dev_allowed() -> bool:
	"""Môi trường dev/test: developer_mode bật hoặc đang chạy test."""
	return bool(cint(frappe.conf.get("developer_mode")) or frappe.flags.get("in_test"))


def register_provider(provider: TelephonyProvider) -> None:
	"""Đăng ký (hoặc thay) nhà cung cấp theo `provider.name`; test dùng để cắm nhà cung cấp giả."""
	_PROVIDERS[provider.name] = provider


def unregister_provider(name: str) -> TelephonyProvider | None:
	"""Gỡ nhà cung cấp; trả bản đã gỡ (nếu có)."""
	return _PROVIDERS.pop(name, None)


def get_provider(name: str | None) -> TelephonyProvider:
	"""Nhà cung cấp theo tên (tải lười); ném ProviderNotFoundError nếu không có hoặc `Fake` ngoài môi trường dev."""
	if not name:
		raise ProviderNotFoundError("(chưa chọn)")
	if name in DEV_ONLY and not dev_allowed():
		raise ProviderNotFoundError(name)
	if name in _PROVIDERS:
		return _PROVIDERS[name]
	target = _LAZY.get(name)
	if not target:
		raise ProviderNotFoundError(name)
	try:
		module = importlib.import_module(target[0])
	except ImportError:
		raise ProviderNotFoundError(name) from None
	_PROVIDERS.setdefault(name, getattr(module, target[1])())
	return _PROVIDERS[name]


def provider_for_channel(channel_doc) -> TelephonyProvider:
	"""Nhà cung cấp của một Omni Channel loại Telephony."""
	return get_provider(channel_doc.get("telephony_provider"))
