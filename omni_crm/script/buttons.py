"""Nút bấm trên Zalo: khách thấy CHỮ TRÊN NÚT, bot vẫn làm việc bằng mã `#CODE`.

Zalo (`oa.query.show`) hiện nguyên `payload` của nút thành tin của khách. Trước đây payload là `#MÃ` nên khách thấy
"#DANHMUC_VATTU" trong khung chat. Nay payload là nhãn nút ("Gửi danh mục"); khi tin đó về, `code_for` tra lại các
tin kèm nút bot đã gửi cho đúng khách này để biết đó là mã nào. Tin lưu trong hệ thống vẫn là chữ trên nút, nên
nhân viên xem hội thoại trong CRM cũng thấy chữ, không thấy mã.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_to_date, cint, now_datetime

OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
CODE_PAYLOAD_CONF = "omni_zalo_button_payload_code"  # site_config = 1: quay về payload `#MÃ` như cũ
RECENT_MESSAGES = 6  # số tin kèm nút gần nhất được tra
RECENT_HOURS = 48
OLD_MIN_WORDS = 2  # nút của tin CŨ hơn tin kèm nút mới nhất: chỉ nhận nhãn từ 2 từ, tránh bắt nhầm câu khách gõ
MAX_LABEL = 60
OLD_MIN_CHARS = 8  # ("Không", "Được", "1"...) thành một lần bấm nút cũ


def show_label() -> bool:
	return not cint(frappe.conf.get(CODE_PAYLOAD_CONF) or 0)


def payload(button: dict) -> str:
	"""Payload gửi lên Zalo cho một nút."""
	label = " ".join(str(button.get("label") or "").split())
	return label if show_label() and label else "#" + str(button.get("target") or "")


def _norm(text: object) -> str:
	return " ".join(str(text or "").split()).casefold()


def match(text: str | None, messages: list[list[dict]]) -> str | None:
	"""Mã nút có nhãn trùng trọn với `text` (thuần). `messages`: các bộ nút, MỚI NHẤT trước."""
	typed = _norm(text)
	if not typed or typed.startswith("#"):
		return None
	for index, buttons in enumerate(messages):
		for b in buttons or []:
			label = _norm(b.get("label"))
			if label != typed or not b.get("target"):
				continue
			if index and (len(label.split()) < OLD_MIN_WORDS or len(label) < OLD_MIN_CHARS):
				continue
			return str(b["target"])
	return None


def code_for(identity_key: str, text: str | None) -> str | None:
	"""Như `code_for_identity` nhưng theo khoá định danh của tin vào (`identity_key`)."""
	try:
		raw = (text or "").strip()
		if not raw or raw.startswith("#") or len(raw) > MAX_LABEL:
			return None
		identity = frappe.db.get_value(IDENTITY, {"identity_key": identity_key}, "name")
	except Exception:
		return None
	return code_for_identity(identity, text) if identity else None


def is_tap(text: str | None, identity_key: str | None = None) -> bool:
	"""Tin là một lần bấm nút: `#MÃ` (nút gửi trước đây) hoặc đúng chữ trên một nút bot vừa gửi cho khách này."""
	raw = (text or "").strip()
	if raw.startswith("#") and " " not in raw:
		return True
	return bool(identity_key and code_for(identity_key, raw))


def code_for_identity(identity: str | None, text: str | None) -> str | None:
	"""`#MÃ` nếu `text` là nhãn một nút bot đã gửi gần đây cho khách này; không thì None. Không bao giờ ném lỗi."""
	try:
		raw = (text or "").strip()
		if not identity or not raw or raw.startswith("#") or len(raw) > MAX_LABEL:
			return None
		rows = frappe.get_all(
			OUTBOUND,
			filters={"identity": identity, "buttons": ["is", "set"], "creation": [">=", add_to_date(now_datetime(), hours=-RECENT_HOURS)]},
			fields=["buttons"],
			order_by="creation desc",
			limit_page_length=RECENT_MESSAGES,
		)
		messages = []
		for r in rows:
			try:
				messages.append(json.loads(r.buttons) or [])
			except Exception:
				messages.append([])
		target = match(raw, messages)
		return f"#{target}" if target else None
	except Exception as e:
		frappe.logger("omni").info("omni script: không tra được nhãn nút (%s)", type(e).__name__)
		return None
