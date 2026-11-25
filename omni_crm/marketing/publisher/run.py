"""Job nền: đăng MỘT nền tảng của một bài (`publish_target`). Dòng đã được dispatch khóa ở trạng thái "Đang xử lý".

Thứ tự: kiểm hub/trạng thái -> tăng attempts (commit, để worker chết giữa chừng vẫn biết đã thử) -> Chế độ thử thì
giả lập thành công, không gọi mạng -> lần thử thứ 2 trở đi luôn đối chiếu (reconcile) trước -> đăng -> ghi kết quả,
nhật ký, trạng thái bài, realtime `oc_publish_update`.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import rules
from omni_crm.marketing import settings as mk_settings
from omni_crm.marketing.publisher import content
from omni_crm.marketing.publisher.base import PublishResult, get_adapter
from omni_crm.marketing.publisher.errors import PROVIDER, TERMINAL, PublishError
from omni_crm.pipeline.retry import redact

TARGET = "Omni Content Target"
LOG = "Omni Publish Log"
RETRY_MINUTES = (1, 5, 15)  # thử lại tối đa 3 lần sau lần đầu
REALTIME_EVENT = "oc_publish_update"


def log_attempt(
	target: str,
	*,
	outcome: str,
	attempt_no: int | None = None,
	started_at=None,
	error_type: str = "",
	http_status: int | None = None,
	message: str = "",
) -> None:
	"""Một dòng Omni Publish Log (đã che bí mật)."""
	doc = frappe.get_doc(
		{
			"doctype": LOG,
			"target": target,
			"attempt_no": attempt_no
			if attempt_no is not None
			else frappe.db.get_value(TARGET, target, "attempts"),
			"outcome": outcome,
			"started_at": started_at or now_datetime(),
			"finished_at": now_datetime(),
			"error_type": error_type,
			"http_status": http_status,
			"message": redact(message)[:1000],
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	frappe.db.commit()


def after_change(target: str) -> None:
	"""Tổng hợp trạng thái bài và báo giao diện (lịch đăng tự làm mới)."""
	from omni_crm.marketing import approval

	item = frappe.db.get_value(TARGET, target, "content_item")
	if item:
		frappe.flags.in_marketing_publisher = True
		try:
			approval.refresh_item_status(item)
		finally:
			frappe.flags.in_marketing_publisher = False
	row = frappe.db.get_value(
		TARGET, target, ["status", "content_item", "campaign", "scheduled_at"], as_dict=True
	)
	frappe.publish_realtime(
		REALTIME_EVENT,
		{"target": target, **{k: str(v or "") for k, v in (row or {}).items()}},
		after_commit=True,
	)
	frappe.db.commit()


def _set(target: str, **values: Any) -> None:
	frappe.db.set_value(TARGET, target, values)


def _success(
	target: str, result: PublishResult, payload, adapter, attempt: int, started, outcome: str, note: str
) -> None:
	permalink = result.permalink
	if not permalink and outcome != "dry_run":
		try:
			permalink = adapter.fetch_permalink(payload, result.external_post_id)
		except Exception:
			permalink = ""
	_set(
		target,
		status=rules.T_SUCCESS,
		external_post_id=result.external_post_id,
		permalink=permalink or "",
		published_at=now_datetime(),
		next_retry_at=None,
		error_type="",
		last_error="",
	)
	frappe.db.commit()
	log_attempt(target, outcome=outcome, attempt_no=attempt, started_at=started, message=note)
	after_change(target)


def _mark_reconnect(channel: str | None, message: str) -> None:
	"""Lỗi quyền/token: kênh -> Cần kết nối lại (kết quả kiểm quyền) và báo Omni Admin bằng Notification Log."""
	if channel:
		from omni_crm.marketing import channels

		data = channels._checks().get(channel) or {}
		data.update(status=channels.RECONNECT, message=message, checked_at=str(now_datetime()))
		channels._save_check(channel, data)
	users = frappe.get_all(
		"Has Role", filters={"role": "Omni Admin", "parenttype": "User"}, pluck="parent", distinct=True
	)
	for user in frappe.get_all("User", filters={"name": ["in", users or [""]], "enabled": 1}, pluck="name"):
		doc = frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": _("Marketing Hub: kênh {0} cần kết nối lại").format(channel or "?"),
				"email_content": message,
				"document_type": "Omni Channel" if channel else None,
				"document_name": channel,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
	frappe.db.commit()


def _failure(target: str, err: PublishError, payload_channel: str | None, attempt: int, started) -> None:
	retry = err.kind == PROVIDER and attempt <= len(RETRY_MINUTES)
	message = redact(err.message)[:500]
	if retry:
		_set(
			target,
			next_retry_at=add_to_date(now_datetime(), minutes=RETRY_MINUTES[attempt - 1]),
			error_type=PROVIDER,
			last_error=message,
		)
	else:
		_set(target, status=rules.T_FAILED, next_retry_at=None, error_type=err.kind, last_error=message)
	frappe.db.commit()
	log_attempt(
		target,
		outcome="error",
		attempt_no=attempt,
		started_at=started,
		error_type=err.kind,
		http_status=err.http_status,
		message=message + (_(" (sẽ thử lại)") if retry else ""),
	)
	if err.kind == TERMINAL:
		_mark_reconnect(payload_channel, message)
	if not retry:
		after_change(target)


def publish_target(name: str) -> str:
	"""Đăng một nền tảng; trả trạng thái mới. Không bao giờ ném lỗi ra ngoài (lỗi lạ -> thử lại như lỗi tạm)."""
	row = frappe.db.get_value(TARGET, name, ["status", "next_retry_at", "attempts", "platform"], as_dict=True)
	if not row or row.status != rules.T_PROCESSING or row.next_retry_at:
		return (
			row.status if row else ""
		)  # không phải lượt của mình (đã xong, đã hủy, hoặc chưa đến giờ thử lại)
	if not mk_settings.hub_enabled():
		_set(name, status=rules.T_SCHEDULED)  # hub tắt giữa chừng: trả về hàng chờ, không đăng
		frappe.db.commit()
		return rules.T_SCHEDULED
	attempt = int(row.attempts or 0) + 1
	_set(name, attempts=attempt)
	frappe.db.commit()
	started = now_datetime()
	channel = frappe.db.get_value(TARGET, name, "channel_account")
	try:
		payload = content.build(name)
		if mk_settings.dry_run():
			result = PublishResult(external_post_id=f"dryrun-{name}")
			_success(
				name,
				result,
				payload,
				None,
				attempt,
				started,
				"dry_run",
				_("Chế độ thử: không gọi API nền tảng"),
			)
			return rules.T_SUCCESS
		adapter = get_adapter(payload.platform)
		if attempt > 1:
			found = adapter.reconcile(payload)
			if found and found.external_post_id:
				_success(
					name,
					found,
					payload,
					adapter,
					attempt,
					started,
					"success",
					_("Đối chiếu: bài đã đăng ở lần trước"),
				)
				return rules.T_SUCCESS
		result = adapter.publish(payload)
		_success(name, result, payload, adapter, attempt, started, "success", "")
		return rules.T_SUCCESS
	except PublishError as e:
		frappe.db.rollback()
		_failure(name, e, channel, attempt, started)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			title="Omni Marketing: đăng bài lỗi", message=f"target={name}\n" + redact(frappe.get_traceback())
		)
		_failure(
			name,
			PublishError(_("Lỗi không xác định khi đăng ({0})").format(type(e).__name__)),
			channel,
			attempt,
			started,
		)
	return frappe.db.get_value(TARGET, name, "status")
