"""Đối soát hằng giờ lead Meta (docs/15 mục 2): bù lead mà webhook bị sót.

Với mỗi Omni Lead Form Meta đang bật: lấy lead có `time_created` sau mốc đối soát cuối và lưu sự kiện cho lead chưa có (chống
trùng theo `leadgen_id`, cùng khóa với webhook nên hai đường không tạo đôi). Gọi Graph API SAU khi đã commit (không giữ khóa DB).
Lỗi một biểu mẫu chỉ ghi log loại lỗi và đi tiếp biểu mẫu khác. CHƯA THỬ VỚI NỀN TẢNG THẬT.

V21-03 (F3): bước kéo lại. Tin Meta ở `Manual` với `error_kind = tam_thoi` (Graph chết/giới hạn tần suất lâu hơn số lần thử) nhận trong
`meta_requeue_hours` (mặc định 72) được đưa về `New` và vào lại hàng đợi qua `enqueue_identity` (-> `queue_for`), tối đa `meta_requeue_batch`
(mặc định 200) mỗi lần; mỗi tin tối đa MAX_REQUEUE lần, quá thì dừng và cảnh báo. Không tạo bản ghi mới nên không thể trùng Lead/Inbound.
Lỗi `vinh_vien` (token, quyền, lead đã xóa, lỗi dữ liệu) không kéo lại: MỘT cảnh báo gộp mỗi kênh mỗi lượt, mỗi tin chỉ được báo một lần.
Bước kéo lại bị bỏ qua lượt này nếu Graph vẫn lỗi tạm thời khi liệt kê lead (tránh đốt lượt kéo lại vô ích).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.channels import form_common
from omni_crm.channels.meta_leads.client import MetaClient
from omni_crm.channels.meta_leads.errors import TAM_THOI, TRANSIENT, VINH_VIEN, MetaError
from omni_crm.crm_bridge import form_lead
from omni_crm.ingest import gateway
from omni_crm.monitoring.alerts import Alert, deliver_now

FORM = "Omni Lead Form"
INBOUND = "Omni Inbound Message"
FIRST_LOOKBACK_HOURS = 24
MAX_REQUEUE = 3  # quá số lần kéo lại này thì dừng và cảnh báo (V21-03)
RESULT_CACHE_KEY = "omni:meta:reconcile:last"
RESULT_TTL = 7 * 86400
CHANNEL_TYPE = "Meta Lead Ads"
OVERLAP_MINUTES = 5  # lùi mốc một chút để không sót lead sát ranh giới; trùng đã được chống bằng dedupe_key


def run() -> dict[str, int]:
	"""Tác vụ theo giờ: đối soát mọi kênh Meta đang bật có Page access token. Trả {"channels", "created"}."""
	total = {"channels": 0, "created": 0, "requeued": 0, "permanent": 0, "exhausted": 0}
	for ch in frappe.get_all(
		"Omni Channel", filters={"channel_type": CHANNEL_TYPE, "enabled": 1}, pluck="name"
	):
		total["channels"] += 1
		try:
			stats = reconcile_channel(ch)
			for key in ("created", "requeued", "permanent", "exhausted"):
				total[key] += stats.get(key, 0)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: đối soát Meta lỗi", message=f"kenh={ch}")
			frappe.db.commit()
	try:  # kết quả lượt gần nhất cho màn giám sát; lỗi Redis không làm hỏng đối soát
		frappe.cache().set_value(
			RESULT_CACHE_KEY, {**total, "at": str(now_datetime())}, expires_in_sec=RESULT_TTL
		)
	except Exception:
		pass
	return total


def _ts(value: datetime) -> int:
	return int(value.timestamp())


def reconcile_channel(channel: str, client: Any = None, now: datetime | None = None) -> dict[str, int]:
	"""Đối soát một kênh; trả {"forms", "seen", "created", "errors", "requeued", "permanent", "exhausted"}."""
	now = now or now_datetime()
	channel_doc = frappe.get_doc("Omni Channel", channel)
	if not channel_doc.get_password("meta_page_access_token", raise_exception=False):
		return {
			"forms": 0,
			"seen": 0,
			"created": 0,
			"errors": 0,
			"requeued": 0,
			"permanent": 0,
			"exhausted": 0,
		}
	client = client or MetaClient(channel)
	stats = {"forms": 0, "seen": 0, "created": 0, "errors": 0, "requeued": 0, "permanent": 0, "exhausted": 0}
	graph_down = False
	forms = frappe.get_all(FORM, filters={"platform": "Meta", "enabled": 1}, pluck="name")
	for name in forms:
		form_doc = frappe.get_doc(FORM, name)
		since = (
			get_datetime(form_doc.last_reconciled_at)
			if form_doc.last_reconciled_at
			else now - timedelta(hours=FIRST_LOOKBACK_HOURS)
		) - timedelta(minutes=OVERLAP_MINUTES)
		frappe.db.commit()  # không giữ giao dịch/khóa nào mở khi gọi ra ngoài
		stats["forms"] += 1
		try:
			leads = client.list_form_leads(form_doc.external_form_id, _ts(since))
		except MetaError as e:
			stats["errors"] += 1
			graph_down = graph_down or e.kind == TRANSIENT
			frappe.logger("omni").warning("omni: doi soat meta loi (%s)", e.kind)
			continue
		for lead in leads:
			stats["seen"] += 1
			lead_id = form_common.clean(lead.get("id"), 140)
			if not lead_id or frappe.db.exists(INBOUND, {"dedupe_key": f"{channel}:{lead_id}"}):
				continue
			try:
				ad = client.get_ad_info(str(lead.get("ad_id") or ""))
			except MetaError:
				ad = {}
			hook = {
				"lead_id": lead_id,
				"form_id": form_doc.external_form_id,
				"page_id": channel_doc.meta_page_id,
			}
			form = form_lead.meta_form_from_graph(lead, ad, hook, form_doc)
			event = form_common.to_event(
				channel_doc, form, received_at=form_common.parse_time(lead.get("created_time")) or now
			)
			if gateway.save_inbound(event):
				stats["created"] += 1
			frappe.db.commit()
		frappe.db.set_value(FORM, name, "last_reconciled_at", now, update_modified=False)
		frappe.db.commit()
	if flags.meta_requeue_enabled():
		stats.update(requeue_manual(channel, now, skip_requeue=graph_down))
	return stats


def _manual_rows(channel: str, kind: str, since: datetime, extra: dict | None = None, limit: int = 0) -> list:
	filters = {
		"channel": channel,
		"channel_type": CHANNEL_TYPE,
		"status": "Manual",
		"error_kind": kind,
		"creation": [">=", since],
		**(extra or {}),
	}
	return frappe.get_all(
		INBOUND,
		filters=filters,
		fields=["name", "identity_key", "requeue_count"],
		order_by="creation asc",
		limit_page_length=limit or 0,
	)


def requeue_manual(channel: str, now: datetime | None = None, skip_requeue: bool = False) -> dict[str, int]:
	"""Kéo lại tin Meta Manual (lỗi tạm thời) của một kênh và cảnh báo gộp; trả {"requeued", "permanent", "exhausted"}.

	Idempotent: chỉ đổi trạng thái bản ghi sẵn có (không tạo mới), enqueue qua `enqueue_identity` (job_id/deduplicate như mọi đường khác).
	"""
	now = now or now_datetime()
	since = add_to_date(now, hours=-flags.ads_setting("meta_requeue_hours"))
	batch = flags.ads_setting("meta_requeue_batch")
	requeued = 0
	if not skip_requeue:
		rows = _manual_rows(channel, TAM_THOI, since, {"requeue_count": ["<", MAX_REQUEUE]}, limit=batch)
		for row in rows:
			frappe.db.set_value(
				INBOUND,
				row.name,
				{
					"status": "New",
					"attempts": 0,
					"next_attempt_at": None,
					"processing_started_at": None,
					"requeue_count": (row.requeue_count or 0) + 1,
				},
				update_modified=False,
			)
			frappe.db.commit()  # một tin = một đơn vị việc
		requeued = len(rows)
		for key in dict.fromkeys(r.identity_key for r in rows if r.identity_key):
			gateway.enqueue_identity(key, after_commit=False)  # hàng đợi do queue_for_identity quyết định
	exhausted = _manual_rows(
		channel, TAM_THOI, since, {"requeue_count": [">=", MAX_REQUEUE], "error_alerted": 0}
	)
	permanent = _manual_rows(channel, VINH_VIEN, since, {"error_alerted": 0})
	if permanent or exhausted:
		_alert_permanent(channel, len(permanent), len(exhausted))
		names = [r.name for r in permanent + exhausted]
		frappe.db.set_value(INBOUND, names, "error_alerted", 1, update_modified=False)
		frappe.db.commit()
	return {"requeued": requeued, "permanent": len(permanent), "exhausted": len(exhausted)}


def _alert_permanent(channel: str, permanent: int, exhausted: int) -> None:
	"""MỘT cảnh báo gộp cho kênh trong lượt đối soát (chỉ số lượng và tên kênh: không token, không nội dung khách)."""
	detail = _(
		"Kênh {0}: {1} tin Meta ở Manual do lỗi vĩnh viễn (token sai/hết hạn, thiếu quyền leads_retrieval, lead đã bị xóa hoặc lỗi dữ liệu) "
		"và {2} tin đã kéo lại quá {3} lần mà vẫn lỗi. Hệ thống KHÔNG tự kéo lại các tin này. Kiểm token Meta rồi xử lý lại trong trang Giám sát."
	).format(channel, permanent, exhausted, MAX_REQUEUE)
	deliver_now(Alert("meta_loi_vinh_vien", channel, _("Có lead Meta không tự kéo lại được"), detail))
