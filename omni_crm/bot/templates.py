"""Mẫu tin cho sale điền rồi gửi khi bot chuyển người vì khách hỏi giá hoặc đặt lịch demo (ADR 0031).

Bot không tự trả lời giá/lịch. Nháp vẫn là `Handoff` (chuyển người, tạm dừng bot như cũ) nhưng có sẵn `draft_text` là
một mẫu tin với các chỗ trống `[...]`; sale điền rồi bấm gửi trong Hộp thư. Máy chủ chặn gửi khi còn `[...]`
(`api/drafts.send_draft` và `pipeline/outbound._check_bot_draft`), nên mẫu điền dở không tới được khách.

Không dùng mẫu khi khách khiếu nại, hỏi y khoa, pháp lý hay điều khoản hợp đồng: những nháp đó giữ nguyên chuyển người
không nội dung. Nội dung mẫu lấy theo tài liệu "Demo, tài liệu và báo giá" và "Giọng văn" của Kho tri thức Miyano;
site_config `omni_handoff_templates` ({"price": "...", "demo": "..."}) thay được nội dung, "" hoặc thiếu khóa = mặc định,
`{"price": null}` = tắt mẫu đó. Công tắc chung: site_config `omni_handoff_templates_enabled` = 0 tắt hẳn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import frappe

from omni_crm.bot import guards, intents, knowledge

CONF = "omni_handoff_templates"
CONF_ENABLED = "omni_handoff_templates_enabled"
GROUP_PRICE = "Hỏi giá"
GROUP_DEMO = "Đặt lịch demo"
BLOCK_GROUPS = frozenset({"Khiếu nại", "Tư vấn y khoa"})
BLOCK_TOPICS = frozenset({"khieu_nai", "y_khoa", "phap_ly", "dieu_khoan_hop_dong"})
DEMO_WORDS = ("demo", "dung thu", "trai nghiem thu", "xem thu phan mem")

DEFAULTS = {
	"price": (
		"Dạ Miyano cảm ơn anh/chị đã quan tâm ạ. [Nội dung báo giá cho anh/chị]. "
		"Báo giá có hiệu lực đến [ngày hết hiệu lực], [đã gồm / chưa gồm] VAT và phí vận chuyển. "
		"Anh/chị cần điều chỉnh mặt hàng hay số lượng thì cứ nhắn Miyano nhé ạ."
	),
	"demo": (
		"Dạ Miyano rất vui được demo [SupplyCore / hệ thống SPD] cho anh/chị ạ. "
		"Buổi demo miễn phí, khoảng 30 phút, online qua Microsoft Teams hoặc gặp trực tiếp. "
		"Miyano đề xuất các khung giờ: [khung giờ 1], [khung giờ 2], [khung giờ 3]. "
		"Anh/chị chọn giúp Miyano một khung giờ và cho Miyano xin email để gửi lời mời ạ."
	),
}
TARGET_GROUP = {"price": GROUP_PRICE, "demo": GROUP_DEMO}


@dataclass(frozen=True)
class Template:
	key: str
	text: str
	group: str


def enabled() -> bool:
	"""Có dùng mẫu tin cho nháp chuyển người không (site_config, mặc định bật)."""
	return str(frappe.conf.get(CONF_ENABLED, 1)).strip() not in ("0", "false", "False", "")


def _texts() -> dict[str, str | None]:
	raw = frappe.conf.get(CONF)
	try:
		custom = json.loads(raw) if isinstance(raw, str) else (raw or {})
	except ValueError:
		custom = {}
	out: dict[str, str | None] = dict(DEFAULTS)
	if isinstance(custom, dict):
		for key in DEFAULTS:
			if key in custom and custom[key] is None:
				out[key] = None
			elif isinstance(custom.get(key), str) and custom[key].strip():
				out[key] = custom[key].strip()
	return out


def _wants_demo(customer_text: str | None) -> bool:
	forms = guards._topic_forms(customer_text)
	return any(word in f for f in forms for word in DEMO_WORDS)


def pick(group: str | None, intent: str | None, customer_text: str | None) -> Template | None:
	"""Mẫu tin cho nháp chuyển người, hoặc None (khiếu nại/y khoa/pháp lý/điều khoản, không phải giá hay demo)."""
	if not enabled():
		return None
	if intents.normalize(intent) == intents.COMPLAINT or group in BLOCK_GROUPS:
		return None
	topics = set(guards.sensitive_topics(customer_text))
	if topics & BLOCK_TOPICS:
		return None
	if group == GROUP_DEMO or _wants_demo(customer_text):
		key = "demo"
	elif (
		group == GROUP_PRICE
		or "hoi_gia" in topics
		or (intents.normalize(intent) == intents.FEE and guards.intent_forces_handoff(intent, customer_text))
	):
		key = "price"
	else:
		return None
	text = _texts().get(key)
	if not text:
		return None
	return Template(key=key, text=text, group=TARGET_GROUP[key])


def is_template_draft(row: Any) -> bool:
	"""Nháp chuyển người có mẫu tin để sale điền và gửi."""
	return row.action == "Handoff" and bool((row.draft_text or "").strip())


def unfilled(text: str | None) -> bool:
	"""Còn chỗ trống `[...]` chưa điền."""
	return knowledge.has_placeholder(text or "")
