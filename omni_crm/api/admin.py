"""API giám sát và quản trị: snapshot, xử lý lại, xóa dữ liệu khách."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html

from omni_crm.ingest.gateway import enqueue_identity
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.pipeline.retry import redact

MAX_REPROCESS = 200
MAX_MANUAL_LIST = 100
ERASED = "[đã xóa]"
MIN_REASON_CHARS = 5


def require_omni_admin() -> None:
	"""Chỉ cho Omni Admin (và Administrator). Không dùng frappe.only_for vì nó bỏ qua kiểm tra khi chạy test."""
	if "Omni Admin" not in frappe.get_roles():
		frappe.throw(_("Chỉ Omni Admin được thực hiện thao tác này"), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def reprocess(inbound_names: list | str) -> dict:
	"""Đưa tin Manual/Retry về New và enqueue lại (chỉ Omni Admin)."""
	require_omni_admin()
	names = frappe.parse_json(inbound_names) if isinstance(inbound_names, str) else inbound_names
	if not isinstance(names, list) or len(names) > MAX_REPROCESS:
		frappe.throw(_("Danh sách tin không hợp lệ hoặc quá {0} tin").format(MAX_REPROCESS))
	rows = frappe.get_all(
		"Omni Inbound Message",
		filters={"name": ["in", [str(n) for n in names]], "status": ["in", ["Manual", "Retry"]]},
		fields=["name", "identity_key"],
	)
	if rows:
		frappe.db.set_value(
			"Omni Inbound Message",
			[r.name for r in rows],
			{"status": "New", "attempts": 0, "next_attempt_at": None, "processing_started_at": None},
			update_modified=False,
		)
	for key in {r.identity_key for r in rows if r.identity_key}:
		enqueue_identity(key)  # trong request: hoãn tới sau commit của framework
	return {"reset": len(rows), "skipped": len(names) - len(rows)}


@frappe.whitelist()
def monitor_snapshot() -> dict[str, Any]:
	"""Số liệu cho trang omni-monitor (chỉ Omni Admin)."""
	require_omni_admin()
	return build_snapshot()


@frappe.whitelist()
def list_manual(limit: int | str = 50) -> list[dict]:
	"""Tin Manual mới nhất để chọn "Xử lý lại": chỉ thông tin kỹ thuật, không có nội dung tin, SĐT/email đã che."""
	require_omni_admin()
	rows = frappe.get_all(
		"Omni Inbound Message",
		filters={"status": "Manual"},
		fields=["name", "channel", "event_type", "attempts", "received_at", "last_error"],
		order_by="creation desc",
		limit_page_length=min(int(limit or 50), MAX_MANUAL_LIST),
	)
	for r in rows:
		r["last_error"] = redact(r.last_error or "")[:300]
	return rows


@frappe.whitelist(methods=["POST"])
def sweep_now() -> dict[str, Any]:
	"""Nút "Quét ngay": chạy tác vụ quét tin kẹt ngay lập tức (chỉ Omni Admin)."""
	require_omni_admin()
	from omni_crm.pipeline.sweeper import requeue_stuck

	return requeue_stuck()


@frappe.whitelist(methods=["POST"])
def refresh_zalo_token(channel: str) -> dict[str, Any]:
	"""Nút "Làm mới token Zalo": ép làm mới token của một kênh Zalo OA (chỉ Omni Admin)."""
	require_omni_admin()
	from omni_crm.channels.zalo import token
	from omni_crm.channels.zalo.errors import ZaloError

	if frappe.db.get_value("Omni Channel", channel, "channel_type") != "Zalo OA":
		frappe.throw(_("Kênh này không phải Zalo OA"))
	try:
		done = token.refresh_channel(channel, force=True)
	except ZaloError as e:
		frappe.throw(_("Không làm mới được token: {0}").format(redact(e.message_vi)))
	return {"refreshed": bool(done)}


@frappe.whitelist(methods=["POST"])
def erase_customer_data(lead: str, reason: str) -> dict[str, Any]:
	"""Xóa nội dung tin vào/ra của một Lead và các Identity của nó (chỉ Omni Admin); giữ bản ghi kỹ thuật.

	Không xóa CRM Lead. Ghi Comment (lý do, người làm, số liệu) lên Lead.
	"""
	require_omni_admin()
	reason = (reason or "").strip()
	if len(reason) < MIN_REASON_CHARS:
		frappe.throw(_("Phải nhập lý do xóa dữ liệu"))
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Không tìm thấy Lead {0}").format(lead), frappe.DoesNotExistError)
	identities = frappe.get_all("Omni Identity", filters={"lead": lead}, pluck="name")
	where, params = "`lead` = %s", [lead]
	if identities:
		where += " or `identity` in %s"
		params.append(tuple(identities))
	frappe.db.sql(
		f"""update `tabOmni Inbound Message`
		set `text` = %s, `attachments` = null, `raw_payload` = null, `sender_profile` = null
		where {where}""",
		[ERASED, *params],
	)
	inbound_n = frappe.db.sql("select row_count()")[0][0]
	frappe.db.sql(
		f"update `tabOmni Outbound Message` set `text` = %s where {where}",
		[ERASED, *params],
	)
	outbound_n = frappe.db.sql("select row_count()")[0][0]
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"content": _(
				"Omni: {0} đã xóa nội dung tin của khách ({1} tin vào, {2} tin ra). Lý do: {3}"
			).format(frappe.session.user, inbound_n, outbound_n, escape_html(reason)),
		}
	).insert(ignore_permissions=True)
	frappe.logger("omni").warning(
		"omni: xóa dữ liệu khách lead=%s vào=%s ra=%s bởi=%s",
		lead,
		inbound_n,
		outbound_n,
		frappe.session.user,
	)
	return {"inbound": inbound_n, "outbound": outbound_n, "identities": len(identities)}
