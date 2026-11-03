"""API giám sát và quản trị: snapshot, xử lý lại, xóa dữ liệu khách."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html

from omni_crm.ingest.gateway import enqueue_identity
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.pipeline.retry import redact
from omni_crm.prospect.contacts import contacts_of_lead, erase_contact

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
	# Bot Run không lưu hội thoại nhưng output_json có thể chứa trích dẫn lời khách
	frappe.db.sql(
		f"update `tabOmni Bot Run` set output_json = null, request_digest = null where {where}", params
	)
	# Bot Draft (D2-07) chứa nháp của bot và câu sale trả lời
	frappe.db.sql(
		f"""update `tabOmni Bot Draft`
		set draft_text = null, final_text = null, sale_reply_text = null, rating_note = null
		where {where}""",
		params,
	)
	# Nhật ký chăm sóc (V2-04): `summary` là trích tin khách đầu phiên (hoặc tóm tắt) -> xóa nội dung; giữ kết quả, hạn
	frappe.db.sql("update `tabOmni Care Log` set `summary` = null where `lead` = %s", lead)
	care_logs_n = frappe.db.sql("select row_count()")[0][0]
	spool_pending = _spool_pending()
	calls_n, call_logs_n = _erase_calls(lead)
	contacts_n = _erase_contacts(lead, reason)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"content": _(
				"Omni: {0} đã xóa nội dung tin của khách ({1} tin vào, {2} tin ra, {3} cuộc gọi, {5} người liên hệ). Lý do: {4}"
			).format(frappe.session.user, inbound_n, outbound_n, calls_n, escape_html(reason), contacts_n),
		}
	).insert(ignore_permissions=True)
	frappe.logger("omni").warning(
		"omni: xóa dữ liệu khách lead=%s vào=%s ra=%s cuộc_gọi=%s bởi=%s",
		lead,
		inbound_n,
		outbound_n,
		calls_n,
		frappe.session.user,
	)
	return {
		"inbound": inbound_n,
		"outbound": outbound_n,
		"identities": len(identities),
		"calls": calls_n,
		"call_logs": call_logs_n,
		"contacts": contacts_n,
		"care_logs": care_logs_n,
		"spool_pending": spool_pending,  # V21-04: tin còn trong hàng dự phòng webhook KHÔNG xóa được theo Lead (docs/12)
	}


def _spool_pending() -> int:
	"""Số tin còn trong hàng dự phòng webhook; payload thô chưa gắn Lead nên không xóa theo Lead được (xem docs/12 G-V21-04-1)."""
	from omni_crm.ingest import spool

	return spool.spool_count()


@frappe.whitelist(methods=["POST"])
def erase_contact_data(contact: str, reason: str) -> dict[str, Any]:
	"""Xóa dữ liệu cá nhân của MỘT người liên hệ (Contact) theo yêu cầu (chỉ Omni Admin); giữ bản ghi ẩn danh (docs/14 mục 6)."""
	require_omni_admin()
	reason = (reason or "").strip()
	if len(reason) < MIN_REASON_CHARS:
		frappe.throw(_("Phải nhập lý do xóa dữ liệu"))
	if not frappe.db.exists("Contact", contact):
		frappe.throw(_("Không tìm thấy người liên hệ {0}").format(contact), frappe.DoesNotExistError)
	org = frappe.db.get_value("Contact", contact, "company_name")
	erased = erase_contact(contact, reason)
	if org and frappe.db.exists("CRM Organization", org):
		frappe.get_doc("CRM Organization", org).add_comment(
			"Comment",
			_("Omni: {0} đã xóa dữ liệu cá nhân của một người liên hệ. Lý do: {1}").format(
				frappe.session.user, escape_html(reason)
			),
		)
	frappe.logger("omni").warning("omni: xóa dữ liệu người liên hệ bởi=%s", frappe.session.user)
	return {"erased": erased}


def _erase_contacts(lead: str, reason: str) -> int:
	"""Xóa dữ liệu cá nhân của các Contact là chính người của Lead (V2-02, docs/14 mục 6); trả số Contact đã xóa."""
	return sum(1 for c in contacts_of_lead(lead) if erase_contact(c, reason))


def _erase_calls(lead: str) -> tuple[int, int]:
	"""Xóa số điện thoại, sự kiện thô, tham chiếu ghi âm của Omni Call và CRM Call Log của Lead (V2-01, 07.08).

	CRM Call Log tìm qua `Omni Call.crm_call_log` và qua tham chiếu tới Lead hoặc Deal của Lead (`from`/`to` bắt
	buộc nên đặt "[đã xóa]"). Giữ bản ghi kỹ thuật (trạng thái, thời lượng, thời điểm). Trả (số Omni Call, số CRM Call Log).
	"""
	calls = frappe.get_all("Omni Call", filters={"lead": lead}, fields=["name", "crm_call_log"])
	deals = frappe.get_all("CRM Deal", filters={"lead": lead}, pluck="name")
	logs = {c.crm_call_log for c in calls if c.crm_call_log}
	logs.update(
		frappe.get_all("CRM Call Log", filters={"reference_docname": ["in", [lead, *deals]]}, pluck="name")
	)
	if calls:
		frappe.db.sql(
			"""update `tabOmni Call` set from_number = %s, to_number = %s, raw_events = null, recording_ref = null
			where name in %s""",
			(ERASED, ERASED, tuple(c.name for c in calls)),
		)
	if logs:
		frappe.db.sql(
			"update `tabCRM Call Log` set `from` = %s, `to` = %s, recording_url = null where name in %s",
			(ERASED, ERASED, tuple(logs)),
		)
	return len(calls), len(logs)
