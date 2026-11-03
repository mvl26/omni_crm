"""Kiểm hạn và quyền token Page của kênh Meta Lead Ads hằng ngày (V21-02, F2).

Gọi `GET /debug_token` (qua `MetaClient`: timeout 10 giây, thử lại có giới hạn) với app access token `app_id|app_secret`.
Cảnh báo khi: token không hợp lệ; còn <= `meta_token_warn_days` ngày tới `expires_at` hoặc `data_access_expires_at`; thiếu
quyền `leads_retrieval`. `expires_at = 0` = không hết hạn (token System User) -> không cảnh báo hết hạn, vẫn kiểm hợp lệ và quyền.
Lỗi Graph TẠM THỜI (mạng, 5xx, giới hạn tần suất) KHÔNG sinh cảnh báo và KHÔNG ghi đè kết quả cũ: lần chạy kế tiếp (6 giờ) thử lại.
Token, App Secret không bao giờ vào log, Error Log, ghi chú kết quả hay nội dung cảnh báo (chỉ có lý do cố định).
CHƯA THỬ VỚI TOKEN THẬT (docs/12 mục C, V21-02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.channels.meta_leads.client import MetaClient
from omni_crm.channels.meta_leads.errors import TOKEN_NOTE_NEVER, TOKEN_NOTE_OK, TRANSIENT, MetaError
from omni_crm.monitoring.alerts import Alert, deliver_now

CHANNEL = "Omni Channel"
REQUIRED_SCOPE = "leads_retrieval"
RECHECK_HOURS = (
	20  # đã kiểm xong trong 20 giờ qua thì bỏ qua; tác vụ chạy 6 giờ/lần để lỗi tạm thời được thử lại sớm
)
ALERT_KIND = "token_meta"


@dataclass
class Assessment:
	"""Kết quả đánh giá một phản hồi `debug_token`."""

	valid: bool
	expires_at: datetime | None = None
	data_expires_at: datetime | None = None
	never_expires: bool = False
	problems: list[str] = field(default_factory=list)


def _ts(value: Any) -> int | None:
	"""Unix timestamp từ phản hồi Graph; thiếu/không phải số -> None."""
	if isinstance(value, bool) or not isinstance(value, int | float | str):
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _check_expiry(
	label: str, raw: Any, now: datetime, warn_days: int, problems: list[str]
) -> datetime | None:
	ts = _ts(raw)
	if not ts or ts < 0:  # 0 hoặc thiếu: không hết hạn / không biết -> không cảnh báo hết hạn
		return None
	at = datetime.fromtimestamp(ts)
	left = at - now
	if left <= timedelta(0):
		problems.append(_("{0} đã hết hạn").format(label))
	elif left <= timedelta(days=warn_days):
		problems.append(_("{0} còn {1} ngày").format(label, max(left.days, 0)))
	return at


def assess(data: dict[str, Any], now: datetime, warn_days: int) -> Assessment:
	"""Thuần: đánh giá `data` của debug_token. Không chạm DB, không chứa token."""
	valid = data.get("is_valid") is True
	result = Assessment(valid=valid)
	if not valid:
		result.problems.append(_("Token Page không hợp lệ"))
		return result
	result.expires_at = _check_expiry(_("Token"), data.get("expires_at"), now, warn_days, result.problems)
	result.never_expires = _ts(data.get("expires_at")) == 0
	result.data_expires_at = _check_expiry(
		_("Quyền dữ liệu"), data.get("data_access_expires_at"), now, warn_days, result.problems
	)
	scopes = data.get("scopes")
	if isinstance(scopes, list) and REQUIRED_SCOPE not in scopes:
		result.problems.append(_("Thiếu quyền {0}").format(REQUIRED_SCOPE))
	return result


def _save(channel: str, a: Assessment | None, note: str, now: datetime) -> None:
	values: dict[str, Any] = {"meta_token_checked_at": now, "meta_token_note": note[:500]}
	if a is not None:
		values.update(
			meta_token_valid=1 if a.valid else 0,
			meta_token_never_expires=1 if a.never_expires else 0,
			meta_token_expires_at=a.expires_at,
			meta_token_data_expires_at=a.data_expires_at,
		)
	frappe.db.set_value(CHANNEL, channel, values, update_modified=False)
	frappe.db.commit()


def _alert(channel: str, detail: str) -> bool:
	return deliver_now(
		Alert(
			ALERT_KIND,
			channel,
			_("Token Meta cần xử lý"),
			_("Kênh {0}: {1}. Cấp lại token Page (khuyến nghị token System User, docs/15 mục 2).").format(
				channel, detail
			),
		)
	)


def check_channel(channel: str, client: Any = None, now: datetime | None = None) -> dict[str, Any]:
	"""Kiểm một kênh; trả {"status": ok|alert|transient|skipped, "problems": [...]}."""
	now = now or now_datetime()
	doc = frappe.get_doc(CHANNEL, channel)
	app_id = (doc.get("meta_app_id") or "").strip()
	secret = doc.get_password("meta_app_secret", raise_exception=False)
	if not (app_id and secret and doc.get_password("meta_page_access_token", raise_exception=False)):
		_save(channel, None, _("Chưa kiểm: thiếu App ID, App Secret hoặc Page access token"), now)
		return {"status": "skipped", "problems": []}
	client = client or MetaClient(channel)
	try:
		data = client.debug_token(app_id, secret)
	except MetaError as e:
		if e.kind == TRANSIENT:  # không cảnh báo token sai, không ghi đè kết quả cũ: lần sau thử lại
			frappe.logger("omni").warning("omni: debug_token loi tam thoi kenh=%s", channel)
			return {"status": "transient", "problems": []}
		note = _("Không kiểm được token (Graph từ chối: kiểm App ID/App Secret/Page token)")
		_save(channel, None, note, now)
		_alert(channel, note)
		return {"status": "alert", "problems": [note]}
	a = assess(data, now, flags.ads_setting("meta_token_warn_days"))
	note = "; ".join(a.problems) or (TOKEN_NOTE_NEVER if a.never_expires else TOKEN_NOTE_OK)
	_save(channel, a, note, now)
	if a.problems:
		_alert(channel, note)
		return {"status": "alert", "problems": a.problems}
	return {"status": "ok", "problems": []}


def _recently_checked(channel: str, now: datetime) -> bool:
	checked = frappe.db.get_value(CHANNEL, channel, "meta_token_checked_at")
	return bool(checked) and get_datetime(checked) > add_to_date(now, hours=-RECHECK_HOURS)


def run() -> dict[str, int]:
	"""Tác vụ định kỳ (6 giờ/lần, mỗi kênh tối đa ~1 lần/ngày): kiểm token mọi kênh Meta Lead Ads đang bật."""
	if not flags.meta_token_check_enabled():
		return {"channels": 0, "alerts": 0, "transient": 0}
	now = now_datetime()
	counts = {"channels": 0, "alerts": 0, "transient": 0}
	for ch in frappe.get_all(CHANNEL, filters={"channel_type": "Meta Lead Ads", "enabled": 1}, pluck="name"):
		if _recently_checked(ch, now):
			continue
		counts["channels"] += 1
		try:
			status = check_channel(ch, now=now)["status"]
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: kiểm token Meta lỗi", message=f"kenh={ch}")  # không kèm token
			frappe.db.commit()
			continue
		if status == "alert":
			counts["alerts"] += 1
		elif status == "transient":
			counts["transient"] += 1
	return counts
