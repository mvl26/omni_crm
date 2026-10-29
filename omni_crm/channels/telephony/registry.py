"""Tra nhà cung cấp tổng đài theo `Omni Channel.telephony_provider` và đối soát đêm `reconcile_yesterday` (D2-03).

Nhà cung cấp được tải lười. `Fake` chỉ nạp được khi `developer_mode` bật hoặc đang chạy test.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

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


# --- Đối soát đêm (docs/07 mục 2.3) ---------------------------------------------------------------------

RECONCILE_KEY = "omni:telephony:reconcile:{channel}"
RECONCILE_TTL = 3 * 86400
RECONCILE_ALERT_PCT = 1.0  # bù > 1% tổng cuộc gọi => webhook có vấn đề


def reconcile_key(channel: str) -> str:
	"""Khóa cache kết quả đối soát gần nhất của một kênh."""
	return RECONCILE_KEY.format(channel=channel)


def _store_result(channel: str, result: dict) -> None:
	try:
		frappe.cache().set_value(reconcile_key(channel), result, expires_in_sec=RECONCILE_TTL)
	except Exception:
		pass  # Redis chết: mất số liệu hiển thị, không làm hỏng đối soát


def _alert(kind: str, channel: str, subject: str, detail: str) -> None:
	try:
		from omni_crm.monitoring import alerts

		alerts.deliver_now(alerts.Alert(kind, channel, subject, detail))
	except Exception:
		frappe.log_error(title="Omni: không gửi được cảnh báo đối soát", message=f"loai={kind}")


def _reconcile_channel(channel_doc, since: datetime, until: datetime) -> dict:
	"""Bù các cuộc gọi của khoảng [since, until) chưa có Omni Call; trả {total, existing, filled}."""
	from omni_crm.channels.telephony import adapter, calls
	from omni_crm.ingest import gateway

	provider = provider_for_channel(channel_doc)
	frappe.db.commit()  # không giữ transaction mở trong lúc gọi API ngoài
	events = provider.list_calls(since, until, channel_doc)
	total = existing = filled = 0
	seen: set[str] = set()
	for ev in events:
		if not ev.call_id or ev.call_id in seen or not ev.is_final:
			continue
		seen.add(ev.call_id)
		total += 1
		if frappe.db.get_value(
			calls.CALL, {"call_id": ev.call_id, "status": ["in", list(calls_statuses())]}, "name"
		):
			existing += 1
			continue
		# Đi chung đường với webhook: dedupe_key = "{kênh}:{call_id}" nên chạy lại / tin đã có thì save_inbound trả None.
		if gateway.save_inbound(adapter.to_inbound(channel_doc, ev)):
			filled += 1
		else:
			existing += 1  # tin vào đã có (đang xử lý hoặc chờ thử lại)
		frappe.db.commit()  # một cuộc gọi bù = một đơn vị việc
	return {"total": total, "existing": existing, "filled": filled}


def calls_statuses() -> tuple[str, ...]:
	"""Trạng thái cuối của Omni Call (trống = bản tạm, chưa kết thúc)."""
	from omni_crm.channels.telephony.base import CALL_STATUSES

	return CALL_STATUSES


def reconcile_yesterday() -> dict[str, dict]:
	"""Đối soát đêm (01:30): bù cuộc gọi hôm qua thiếu ở mỗi kênh tổng đài đang bật; ghi kết quả vào cache giám sát.

	Công tắc: `Omni Channel.enabled` (kênh tắt thì bỏ qua) và `Omni Settings.sandbox_mode` (bật thì không gọi nhà
	cung cấp). Lỗi một kênh (kể cả list_calls chạm giới hạn trang) được ghi lại và cảnh báo, các kênh khác vẫn chạy.
	Bù > 1% tổng cuộc gọi thì cảnh báo (webhook có vấn đề). Idempotent.
	"""
	from omni_crm import flags

	now = now_datetime()
	until = datetime.combine(now.date(), datetime.min.time())
	since = until - timedelta(days=1)
	out: dict[str, dict] = {}
	channels = frappe.get_all(
		"Omni Channel", filters={"channel_type": "Telephony", "enabled": 1}, pluck="name"
	)
	for name in channels:
		result: dict = {"date": str(since.date()), "at": str(now)}
		if flags.sandbox():
			result["skipped"] = "sandbox"
		else:
			try:
				result.update(_reconcile_channel(frappe.get_doc("Omni Channel", name), since, until))
			except Exception as e:
				frappe.db.rollback()
				result["error"] = type(e).__name__  # chỉ tên lỗi: thông điệp có thể chứa dữ liệu khách/bí mật
				frappe.log_error(
					title="Omni: đối soát đêm lỗi", message=f"kenh={name} loai_loi={type(e).__name__}"
				)
				_alert(
					"doi_soat_loi",
					name,
					_("Đối soát đêm tổng đài không hoàn thành"),
					_(
						"Đối soát cuộc gọi ngày {0} của kênh {1} lỗi ({2}); cuộc gọi thiếu chưa được bù."
					).format(since.date(), name, result["error"]),
				)
			else:
				total, filled = result["total"], result["filled"]
				if total and filled * 100 / total > RECONCILE_ALERT_PCT:
					_alert(
						"doi_soat_bu_nhieu",
						name,
						_("Đối soát đêm bù nhiều cuộc gọi"),
						_(
							"Kênh {0} ngày {1}: bù {2}/{3} cuộc gọi (> {4}%), webhook nhận sự kiện có thể đang lỗi."
						).format(name, since.date(), filled, total, RECONCILE_ALERT_PCT),
					)
		_store_result(name, result)
		out[name] = result
		frappe.db.commit()
	return out
