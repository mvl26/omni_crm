"""Gom tin (bước 3 của yêu cầu "hiểu ngữ cảnh" 20/11): đợi khách NHẮN XONG rồi mới xử lý cả lượt.

- Tin gõ: đợi tới khi khách im `debounce_seconds` kể từ tin CUỐI (không phải từ tin đầu). Tin cuối có dấu hiệu dở dang
  (kết thúc bằng "là", "và", "với", dấu phẩy, dấu ba chấm) thì đợi thêm một nhịp. Tổng thời gian đợi có trần.
- Bấm nút (`#MÃ`): không đợi.
- Đợi quá `NOTICE_AFTER` giây kể từ tin đầu của lượt thì gửi MỘT câu báo đang xem (ô `DANG_SUYNGHI`).
- Khách nhắn thêm khi lượt đang chạy: lượt đó bị huỷ (`router.Superseded`), chữ của nó được mang sang lượt sau.

Job `run` thay `pipeline.worker.process_identity` cho định danh đang chạy bot kịch bản và có tin gõ (xem
`ingest.gateway.enqueue_identity`); phần xử lý vẫn là `process_identity` với `debounce_seconds=0`.
"""

from __future__ import annotations

import re
import time

import frappe
from frappe.utils import cint, get_datetime, now_datetime

INBOUND = "Omni Inbound Message"
IDENTITY = "Omni Identity"
METHOD = "omni_crm.script.gather.run"
MAX_WAIT = 60  # trần tổng thời gian đợi gom tin của một lượt (giây)
NOTICE_AFTER = 10  # đợi quá bấy nhiêu giây kể từ tin đầu của lượt thì báo khách đang xem
POLL = 2
UNFINISHED = re.compile(r"(?:(?<![\wÀ-ỹ])(?:là|và|với|thì|mà|nhưng|hoặc|hay)|[,…]|\.\.\.)\s*$", re.I)


def pending(identity_key: str) -> list:
	"""Các tin của khách đang chờ xử lý (New / Retry), cũ trước."""
	return frappe.get_all(
		INBOUND,
		filters={"identity_key": identity_key, "status": ["in", ["New", "Retry"]], "event_type": "message"},
		fields=["name", "text", "received_at", "creation"],
		order_by="received_at asc, creation asc",
		limit_page_length=50,
	)


def is_tap(text: str | None) -> bool:
	raw = (text or "").strip()
	return raw.startswith("#") and " " not in raw


def quiet_needed(rows: list, base: int) -> float:
	"""Số giây khách phải im kể từ tin cuối (thuần): 0 nếu cả lượt là bấm nút; `base`, cộng thêm nửa nhịp nếu tin cuối dở dang."""
	if not rows or all(is_tap(r.text) for r in rows):
		return 0
	last = (rows[-1].text or "").strip()
	return base * 1.5 if UNFINISHED.search(last) else base


def wait_quiet(identity_key: str, base: int | None = None, sleep=time.sleep, now=now_datetime) -> float:
	"""Đợi tới khi khách im đủ lâu (xem `quiet_needed`) hoặc chạm trần; trả số giây đã đợi. Gửi câu báo chờ một lần."""
	if base is None:
		base = cint(frappe.db.get_single_value("Omni Settings", "debounce_seconds") or 0)
	waited, noticed = 0.0, False
	while waited < MAX_WAIT:
		frappe.db.rollback()  # đọc lại tin mới của khách (mức cô lập mặc định giữ ảnh chụp cũ)
		rows = pending(identity_key)
		need = quiet_needed(rows, base)
		if not need:
			break
		at = now()
		quiet = (at - get_datetime(rows[-1].received_at or rows[-1].creation)).total_seconds()
		if quiet >= need:
			break
		since_first = (at - get_datetime(rows[0].received_at or rows[0].creation)).total_seconds()
		if not noticed and since_first >= NOTICE_AFTER:
			noticed = True
			_notice(identity_key)
		step = max(0.5, min(POLL, need - quiet))
		sleep(step)
		waited += step
	return waited


def _notice(identity_key: str) -> None:
	try:
		from omni_crm.script import send

		name = frappe.db.get_value(IDENTITY, {"identity_key": identity_key}, "name")
		if name:
			send.send_thinking(name)
	except Exception as e:
		frappe.db.rollback()
		frappe.logger("omni").info("omni script: không gửi được câu báo chờ khi gom tin (%s)", type(e).__name__)


def run(identity_key: str) -> str:
	"""Job nền: đợi khách nhắn xong rồi xử lý cả lượt bằng `process_identity` (không chờ thêm)."""
	from omni_crm.pipeline import worker

	try:
		wait_quiet(identity_key)
	except Exception as e:
		frappe.db.rollback()
		frappe.logger("omni").info("omni script: gom tin lỗi (%s), xử lý ngay", type(e).__name__)
	return worker.process_identity(identity_key, debounce_seconds=0)
