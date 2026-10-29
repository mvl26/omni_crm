"""Luật cảnh báo Đợt 1 và chống lặp (docs/11 mục 1-2).

Mỗi luật là một `Alert` có khóa chống lặp riêng (loại + phạm vi) giữ 30 phút trong Redis. Nội dung chỉ gồm số liệu
và tên kênh: không bí mật, không nội dung tin của khách. Email gửi tới `Omni Settings.alert_recipients`, đồng thời
tạo Notification Log cho mọi Omni Admin.

Cảnh báo token Zalo làm mới thất bại đã được `channels.zalo.token._alert` gửi thư ngay lúc lỗi (tối đa 1 thư/giờ/kênh);
luật ở đây chỉ tạo Notification Log cho trường hợp đó để một sự cố không sinh hai thư.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_url, now_datetime

from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.pipeline.retry import redact
from omni_crm.utils.business_hours import business_seconds_between, is_business_time

DEDUPE_SECONDS = 30 * 60
NEW_MAX_AGE_SECONDS = 5 * 60
P95_MAX_SECONDS = 2 * 60
OUTBOUND_FAILED_MAX = 5
DUPLICATES_OPEN_MAX = 20
SILENT_CHANNEL_SECONDS = 4 * 3600
TOKEN_MIN_SECONDS = 3600
MONITOR_ROUTE = "/app/omni-monitor"
CRM_TASKS_ROUTE = "/crm/tasks"  # người được giao việc (Sales User) không mở được màn hình giám sát
ZALO_REFRESH_ALERT_KIND = "làm mới token thất bại"  # khớp token._alert


@dataclass
class Alert:
	"""Một cảnh báo: `kind` + `scope` là khóa chống lặp; `email=False` chỉ tạo Notification Log."""

	kind: str
	scope: str
	subject: str
	detail: str
	email: bool = True
	users: tuple[
		str, ...
	] = ()  # người dùng khác ngoài Omni Admin nhận Notification Log (vd người được giao việc)


def evaluate(snap: dict[str, Any], now=None) -> list[Alert]:
	"""Áp các luật ngưỡng lên snapshot; thuần, không chạm DB ngoài việc đọc giờ làm việc."""
	now = now or now_datetime()
	out: list[Alert] = []
	q, timing, outb = snap["queue"], snap["timing"], snap["outbound"]
	age = q["oldest_new_age_seconds"]
	if age is not None and age > NEW_MAX_AGE_SECONDS:
		out.append(
			Alert(
				"tin_cho_lau",
				"",
				_("Tin chờ xử lý quá lâu"),
				_("Tin New cũ nhất đã chờ {0} phút (ngưỡng {1} phút). Đang có {2} tin New.").format(
					age // 60, NEW_MAX_AGE_SECONDS // 60, q["new"]
				),
			)
		)
	if q["manual"] > 0:
		out.append(
			Alert(
				"tin_manual",
				"",
				_("Có tin cần xử lý tay"),
				_("Có {0} tin ở trạng thái Manual, cần xem lỗi và bấm Xử lý lại.").format(q["manual"]),
			)
		)
	if timing["p95"] is not None and timing["p95"] > P95_MAX_SECONDS:
		out.append(
			Alert(
				"p95_cham",
				"",
				_("Xử lý tin chậm"),
				_("p95 từ nhận tin đến Done trong 24 giờ là {0} giây (ngưỡng {1} giây, {2} tin).").format(
					timing["p95"], P95_MAX_SECONDS, timing["count"]
				),
			)
		)
	if outb["failed_last_hour"] > OUTBOUND_FAILED_MAX:
		out.append(
			Alert(
				"gui_loi",
				"",
				_("Gửi tin ra lỗi nhiều"),
				_("Có {0} tin ra Failed trong 1 giờ qua (ngưỡng {1}).").format(
					outb["failed_last_hour"], OUTBOUND_FAILED_MAX
				),
			)
		)
	if snap["duplicates"]["open"] > DUPLICATES_OPEN_MAX:
		out.append(
			Alert(
				"nghi_trung",
				"",
				_("Nhiều cặp nghi trùng chưa xử lý"),
				_("Có {0} cặp nghi trùng đang Open (ngưỡng {1}).").format(
					snap["duplicates"]["open"], DUPLICATES_OPEN_MAX
				),
			)
		)
	tel = snap.get("telephony") or {}
	if cint(tel.get("callbacks_overdue")):
		out.append(
			Alert(
				"goi_lai_qua_han",
				"",
				_("Có việc gọi lại quá hạn"),
				_(
					"Có {0} việc gọi lại khách gọi nhỡ đã quá hạn mà chưa xong. Vào CRM > Tasks để xử lý."
				).format(tel["callbacks_overdue"]),
				users=tuple(tel.get("callbacks_overdue_users") or ()),
			)
		)
	sandbox = cint(snap["switches"]["sandbox_mode"])
	business_now = is_business_time(now)
	for ch in snap["channels"]:
		if not ch["enabled"]:
			continue
		if business_now:
			ref = ch["last_inbound_at"] or ch["created"]
			silent = business_seconds_between(ref, now)
			if silent > SILENT_CHANNEL_SECONDS:
				out.append(
					Alert(
						"kenh_im_lang",
						ch["name"],
						_("Kênh không nhận tin"),
						_("Kênh {0} không nhận tin nào trong {1} giờ làm việc (ngưỡng {2} giờ).").format(
							ch["name"], int(silent // 3600), SILENT_CHANNEL_SECONDS // 3600
						),
					)
				)
		if ch["channel_type"] == "Zalo OA" and not sandbox:
			left = ch["token_seconds_left"]
			if left is not None and left < TOKEN_MIN_SECONDS:
				out.append(
					Alert(
						"token_zalo_sap_het",
						ch["name"],
						_("Token Zalo sắp hết hạn"),
						_(
							"Token Zalo của kênh {0} {1}. Làm mới thất bại hoặc chưa chạy: bấm Làm mới token."
						).format(
							ch["name"],
							_("đã hết hạn") if left <= 0 else _("còn {0} phút").format(max(left, 0) // 60),
						),
					)
				)
			if ch["last_error"]:
				out.append(
					Alert(
						"token_zalo_loi",
						ch["name"],
						_("Làm mới token Zalo lỗi"),
						_("Lần làm mới token gần nhất của kênh {0} lỗi: {1}").format(
							ch["name"], redact(ch["last_error"])[:200]
						),
						email=not _zalo_mail_already_sent(ch["name"]),
					)
				)
	return out


def _zalo_mail_already_sent(channel: str) -> bool:
	"""token._alert đã gửi thư lỗi làm mới cho kênh này trong giờ qua chưa (khóa cache của nó)."""
	try:
		return bool(
			frappe.cache().get_value(f"omni:alert:zalo:{channel}:{ZALO_REFRESH_ALERT_KIND}", expires=True)
		)
	except Exception:
		return False


def _claim(alert: Alert) -> bool:
	"""Giữ khóa chống lặp 30 phút (SET NX); True nếu lượt này được gửi. Redis lỗi thì cho gửi."""
	try:
		cache = frappe.cache()
		return bool(
			cache.set(cache.make_key(f"omni:alert:{alert.kind}:{alert.scope}"), 1, nx=True, ex=DEDUPE_SECONDS)
		)
	except Exception:
		return True


def _release(alert: Alert) -> None:
	try:
		cache = frappe.cache()
		cache.delete(cache.make_key(f"omni:alert:{alert.kind}:{alert.scope}"))
	except Exception:
		pass


def _recipients() -> list[str]:
	raw = frappe.db.get_single_value("Omni Settings", "alert_recipients") or ""
	return [r.strip() for r in raw.replace(",", "\n").splitlines() if r.strip()]


def _admin_users() -> list[str]:
	users = frappe.get_all("Has Role", filters={"role": "Omni Admin", "parenttype": "User"}, pluck="parent")
	if not users:
		return []
	return frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name")


def _notify_admins(alert: Alert, link: str) -> set[str]:
	"""Tạo Notification Log cho Omni Admin; trả về email của người sẽ nhận thư từ chính Notification Log."""
	from frappe.desk.doctype.notification_settings.notification_settings import (
		is_email_notifications_enabled_for_type,
	)

	mailed: set[str] = set()
	admins = set(_admin_users())
	for user in dict.fromkeys([*_admin_users(), *alert.users]):
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": alert.subject,
					"email_content": alert.detail,
					"link": link if user in admins else CRM_TASKS_ROUTE,
				}
			).insert(ignore_permissions=True)
			if is_email_notifications_enabled_for_type(user, "Alert"):
				mailed.add((frappe.db.get_value("User", user, "email") or user).lower())
		except Exception:
			frappe.log_error(title="Omni: không tạo được Notification Log cảnh báo", message=alert.kind)
	return mailed


def _deliver(alert: Alert) -> None:
	link = get_url(MONITOR_ROUTE)
	mailed = _notify_admins(alert, link)
	if not alert.email:
		return
	to = [r for r in _recipients() if r.lower() not in mailed]  # tránh gửi hai thư cho cùng một người
	if to:
		body = redact(f"{alert.detail}\n\n{_('Màn hình giám sát')}: {link}")
		frappe.sendmail(
			recipients=to,
			subject=f"[Omni CRM] {alert.subject}",
			message=body.replace("\n", "<br>"),
		)


def deliver_now(alert: Alert) -> bool:
	"""Gửi ngay một cảnh báo (chống lặp 30 phút như mọi cảnh báo); True nếu đã gửi. Dùng cho tác vụ đêm."""
	if not _claim(alert):
		return False
	try:
		_deliver(alert)
		frappe.db.commit()
		return True
	except Exception:
		frappe.db.rollback()
		_release(alert)
		frappe.log_error(
			title="Omni: không gửi được cảnh báo", message=f"loai={alert.kind} pham_vi={alert.scope}"
		)
		frappe.db.commit()
		return False


def run_checks() -> dict[str, int]:
	"""Tác vụ định kỳ (*/5 phút): tính snapshot, áp các luật, gửi cảnh báo chưa gửi trong 30 phút qua."""
	alerts = evaluate(build_snapshot())
	sent = 0
	for alert in alerts:
		if not _claim(alert):
			continue
		try:
			_deliver(alert)
			sent += 1
			frappe.logger("omni").warning("omni cảnh báo: %s %s", alert.kind, alert.scope)
		except Exception:
			frappe.db.rollback()
			_release(alert)
			frappe.log_error(
				title="Omni: không gửi được cảnh báo", message=f"loai={alert.kind} pham_vi={alert.scope}"
			)
		frappe.db.commit()  # một cảnh báo = một đơn vị việc
	return {"triggered": len(alerts), "sent": sent}
