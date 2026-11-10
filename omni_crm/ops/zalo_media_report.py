"""Chẩn đoán CHỈ ĐỌC: tin Zalo gần đây có ảnh/GIF/tệp hay không, vì sao tab Lead không hiện ảnh (V3-05, ADR 0018).

    bench --site <site> execute omni_crm.ops.zalo_media_report.run
    bench --site <site> execute omni_crm.ops.zalo_media_report.run --kwargs "{'limit': 50}"

Mỗi tin: tên sự kiện Zalo (lấy từ payload gốc), event_type đã lưu, trạng thái, có đính kèm không, từng đính kèm (loại, CHỈ host của
URL, có bị `_safe_attachments` loại không, có được vẽ thành ảnh nội tuyến không). Nội dung tin cắt 40 ký tự; không in URL đầy đủ,
không in payload gốc, không ghi gì vào DB. Kèm bảng đếm tên sự kiện bị bỏ qua (`ignored`) trong 7 ngày gần nhất: nếu thấy
`user_send_*` lạ ở đó là app Zalo đang gửi sự kiện mà adapter chưa hỗ trợ; nếu KHÔNG có tin ảnh/GIF nào trong danh sách thì
nhiều khả năng app Zalo chưa đăng ký các sự kiện đó (tin chưa hề tới hệ thống).
"""

from __future__ import annotations

import json
from collections import Counter

import frappe
from frappe.utils import add_days, cint, now_datetime

from omni_crm.api import inbox
from omni_crm.crm_bridge.timeline import parse_attachments
from omni_crm.utils import media

TEXT_LEN = 40
MAX_LIMIT = 200


def _event_name(raw_payload: str | None) -> str:
	"""Tên sự kiện Zalo trong payload gốc ("" nếu không đọc được); không trả thêm gì khác của payload."""
	try:
		data = json.loads(raw_payload or "{}")
	except ValueError:
		return ""
	name = data.get("event_name") if isinstance(data, dict) else ""
	return "".join(c for c in str(name or "") if c.isalnum() or c == "_")[:48]


def describe_attachments(raw) -> list[dict]:
	"""Từng đính kèm: {type, host, filtered, inline, has_thumb}; không chứa URL đầy đủ."""
	kept = {a["url"] for a in inbox._safe_attachments(raw)}
	out = []
	for att in parse_attachments(raw):
		url = str(att.get("url") or "")
		kind = str(att.get("type") or "").strip().lower()
		out.append(
			{
				"type": kind or "?",
				"host": media.url_host(url) or ("(nội bộ)" if url.startswith(media.LOCAL_PREFIXES) else ""),
				"filtered": url not in kept,
				"inline": kind in media.INLINE_IMAGE_TYPES and media.is_inline_image_url(url),
				"has_thumb": bool(att.get("thumb")),
			}
		)
	return out


def run(limit: int | str = 20) -> dict:
	"""Báo cáo `limit` (tối đa 200) tin Zalo OA mới nhất và đếm sự kiện bị bỏ qua 7 ngày qua; chỉ đọc."""
	limit = max(1, min(cint(limit) or 20, MAX_LIMIT))
	rows = frappe.get_all(
		"Omni Inbound Message",
		filters={"channel_type": "Zalo OA"},
		fields=[
			"name",
			"event_type",
			"status",
			"received_at",
			"text",
			"attachments",
			"raw_payload",
			"lead",
		],
		order_by="received_at desc",
		limit_page_length=limit,
	)
	messages = []
	for r in rows:
		atts = describe_attachments(r.attachments)
		text = " ".join((r.text or "").split())
		messages.append(
			{
				"name": r.name,
				"received_at": str(r.received_at),
				"event_name": _event_name(r.raw_payload),
				"event_type": r.event_type,
				"status": r.status,
				"has_lead": bool(r.lead),
				"text": text if len(text) <= TEXT_LEN else text[:TEXT_LEN] + "…",
				"has_attachments": bool(atts),
				"attachments": atts,
			}
		)
	ignored = Counter()
	for r in frappe.get_all(
		"Omni Inbound Message",
		filters={
			"channel_type": "Zalo OA",
			"event_type": "ignored",
			"received_at": [">=", add_days(now_datetime(), -7)],
		},
		fields=["raw_payload"],
		limit_page_length=2000,
	):
		ignored[_event_name(r.raw_payload) or "(không tên)"] += 1
	unparsed = frappe.db.count(
		"Omni Inbound Message",
		{
			"channel_type": "Zalo OA",
			"event_type": "unparsed",
			"received_at": [">=", add_days(now_datetime(), -7)],
		},
	)
	with_media = [m for m in messages if m["has_attachments"]]
	return {
		"scanned": len(messages),
		"with_attachments": len(with_media),
		"inline_images": sum(1 for m in messages for a in m["attachments"] if a["inline"]),
		"filtered_attachments": sum(1 for m in messages for a in m["attachments"] if a["filtered"]),
		"event_names": dict(Counter(m["event_name"] or "(không rõ)" for m in messages)),
		"ignored_event_names_7d": dict(ignored),
		"unparsed_7d": unparsed,  # payload không parse được (trạng thái Manual): xem Error Log "Omni webhook: parse_that_bai"
		"messages": messages,
	}
