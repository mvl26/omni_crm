"""Phần dùng chung của ba kênh lead quảng cáo/website (docs/15): ánh xạ trường, chuẩn hóa, dựng sự kiện `form_lead`.

Thuần (không chạm DB trừ khi truyền sẵn dữ liệu): dữ liệu từ biểu mẫu là DỮ LIỆU của khách, không phải lệnh.
Mọi giá trị bị cắt độ dài; văn bản dòng thời gian được thoát HTML ở `timeline.render_text`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from frappe import _
from frappe.utils import now_datetime

from omni_crm.identity.normalize import normalize_email, normalize_phone
from omni_crm.ingest.events import InboundEvent
from omni_crm.prospect.normalize import InvalidTaxId, normalize_tax_id

EVENT_TYPE = "form_lead"
MAX_VALUE_LEN = 2000
MAX_FIELDS = 60
MAX_TEXT_LEN = 6000
PLATFORM_OF_CHANNEL = {"Meta Lead Ads": "Meta", "Google Lead Form": "Google", "Web Form": "Website"}
TEXT_TARGETS = (
	"full_name",
	"phone",
	"email",
	"company_name",
	"tax_id",
	"job_title",
	"province",
	"hospital_count_text",
	"message",
)
LABELS = {
	"full_name": "Họ tên",
	"phone": "Điện thoại",
	"email": "Email",
	"company_name": "Đơn vị",
	"tax_id": "Mã số thuế",
	"job_title": "Chức danh",
	"province": "Tỉnh/thành",
	"hospital_count_text": "Số bệnh viện đang cung cấp",
	"message": "Nội dung",
}
# khóa mặc định (chữ thường) -> đích, dùng khi biểu mẫu không có dòng ánh xạ riêng cho khóa đó
DEFAULT_KEYS = {
	"full_name": "full_name",
	"name": "full_name",
	"fullname": "full_name",
	"ho_ten": "full_name",
	"phone": "phone",
	"phone_number": "phone",
	"mobile": "phone",
	"sdt": "phone",
	"dien_thoai": "phone",
	"email": "email",
	"work_email": "email",
	"company": "company_name",
	"company_name": "company_name",
	"don_vi": "company_name",
	"ten_cong_ty": "company_name",
	"tax_id": "tax_id",
	"mst": "tax_id",
	"ma_so_thue": "tax_id",
	"job_title": "job_title",
	"chuc_danh": "job_title",
	"province": "province",
	"city": "province",
	"state": "province",
	"tinh_thanh": "province",
	"hospital_count": "hospital_count_text",
	"so_benh_vien": "hospital_count_text",
	"message": "message",
	"note": "message",
	"noi_dung": "message",
	"consent_contact": "consent_contact",
	"consent_marketing": "consent_marketing",
}
_TRUTHY = {"1", "true", "yes", "on", "y", "co", "có", "checked", "dong y", "đồng ý", "accept", "accepted"}
_TEST_VALUE = re.compile(r"^<?\s*test lead", re.I)
_UNSAFE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(value: Any, limit: int = MAX_VALUE_LEN) -> str:
	"""Giá trị thành chuỗi gọn: bỏ ký tự điều khiển, cắt khoảng trắng và độ dài. Danh sách nối bằng ", "."""
	if value is None:
		return ""
	if isinstance(value, list | tuple):
		value = ", ".join(clean(v, limit) for v in value if v is not None)
	return _UNSAFE.sub("", str(value)).strip()[:limit]


def truthy(value: Any) -> bool:
	"""Giá trị ô đồng ý có phải "đã tích" không."""
	return clean(value, 40).lower() in _TRUTHY


def parse_time(value: Any) -> datetime | None:
	"""Thời điểm từ ISO-8601 hoặc epoch giây; ra giờ hệ thống naive, None nếu không đọc được hoặc ở tương lai."""
	if value in (None, ""):
		return None
	from frappe.utils import convert_utc_to_system_timezone, get_datetime

	try:
		if isinstance(value, int | float) or str(value).isdigit():
			utc = datetime.fromtimestamp(int(value), tz=UTC).replace(tzinfo=None)
			parsed = convert_utc_to_system_timezone(utc).replace(tzinfo=None)
		else:
			dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
			if dt.tzinfo is None:
				parsed = get_datetime(dt)
			else:
				parsed = convert_utc_to_system_timezone(dt.astimezone(UTC).replace(tzinfo=None)).replace(
					tzinfo=None
				)
	except (ValueError, OverflowError, OSError, TypeError):
		return None
	return parsed if parsed <= now_datetime() else None


def target_of(source_key: str, field_map: dict[str, str]) -> str | None:
	"""Đích của một khóa nguồn: dòng ánh xạ của biểu mẫu trước, rồi khóa mặc định; None nếu không ánh xạ được."""
	if source_key in field_map:
		return field_map[source_key]
	return DEFAULT_KEYS.get(re.sub(r"[^a-z0-9_]", "_", source_key.lower()).strip("_"))


def map_answers(answers: dict[str, Any], field_map: dict[str, str] | None = None) -> dict[str, Any]:
	"""Áp ánh xạ lên câu trả lời. Trả {mapped, consent, custom, unmapped, is_test_marker}.

	Khóa Google FIRST_NAME/LAST_NAME được ghép vào họ tên nếu chưa có FULL_NAME. Ô không ánh xạ được giữ nguyên văn
	ở `unmapped` (hiện trên dòng thời gian).
	"""
	field_map = field_map or {}
	mapped: dict[str, str] = {}
	consent: dict[str, bool] = {}
	custom: dict[str, str] = {}
	unmapped: dict[str, str] = {}
	test_marker = False
	parts = {"first": "", "last": ""}
	for key, raw in list(answers.items())[:MAX_FIELDS]:
		key = clean(key, 120)
		value = clean(raw)
		if not key:
			continue
		if _TEST_VALUE.match(value):
			test_marker = True
		upper = key.upper()
		target = target_of(key, field_map)
		if target is None and upper in ("FIRST_NAME", "LAST_NAME"):
			parts["first" if upper == "FIRST_NAME" else "last"] = value
			continue
		if target is None or (not value and not target.startswith("consent_")):
			if value:
				unmapped[key] = value
			continue
		if target.startswith("consent_"):
			consent[target.removeprefix("consent_")] = truthy(value)
		elif target.startswith("custom:"):
			custom[target[7:].strip()[:80]] = value
		elif target == "message":
			mapped["message"] = (mapped.get("message", "") + "\n" + value).strip()[:MAX_VALUE_LEN]
		elif target in TEXT_TARGETS:
			mapped.setdefault(target, value)
	if "full_name" not in mapped and (parts["first"] or parts["last"]):
		mapped["full_name"] = f"{parts['first']} {parts['last']}".strip()
	return {
		"mapped": mapped,
		"consent": consent,
		"custom": custom,
		"unmapped": unmapped,
		"test_marker": test_marker,
	}


def profile_of(form: dict) -> dict:
	"""`sender_profile` cho resolver: SĐT/email/MST hợp lệ là tín hiệu; lead thử nghiệm KHÔNG đưa tín hiệu nào."""
	mapped = form.get("mapped") or {}
	profile: dict[str, Any] = {"form_lead": True}
	name = re.sub(r"\s+", " ", re.sub(r"[<>{}\\]", " ", mapped.get("full_name") or "")).strip()[:140]
	if (
		name
	):  # tên là dữ liệu của khách: bỏ ký tự HTML/mẫu để không vấp kiểm tra tên của Frappe hay hiển thị sai
		profile["display_name"] = name
	if form.get("is_test"):
		return profile  # tránh gộp lead thử vào khách thật qua SĐT/email giả
	phone = normalize_phone(mapped.get("phone"))
	email = normalize_email(mapped.get("email"))
	if phone:
		profile["phone"] = phone
	if email:
		profile["email"] = email
	try:
		tax = normalize_tax_id(mapped.get("tax_id"))
	except InvalidTaxId:
		tax = None
	if tax:
		profile["tax_id"] = tax
	return profile


def render_text(form: dict) -> str:
	"""Nội dung hiển thị trên dòng thời gian: tên biểu mẫu, nền tảng và mọi câu trả lời (văn bản thuần, chưa thoát HTML)."""
	mapped = form.get("mapped") or {}
	head = _("Điền biểu mẫu «{0}» từ {1}").format(
		form.get("form_name") or form.get("form_id") or "?", form.get("platform") or "?"
	)
	lines = [head]
	if form.get("is_test"):
		lines.append(_("[Thử] Lead thử nghiệm: không giao sale, không tính báo cáo"))
	for target in TEXT_TARGETS:
		if mapped.get(target):
			lines.append(f"{_(LABELS[target])}: {mapped[target]}")
	for name, value in (form.get("custom") or {}).items():
		lines.append(f"{name}: {value}")
	if form.get("unmapped"):
		lines.append(_("Câu trả lời khác:"))
		lines += [f"- {k}: {v}" for k, v in form["unmapped"].items()]
	consent = form.get("consent") or {}
	if "contact" in consent:
		lines.append(_("Đồng ý nhận liên hệ: {0}").format(_("Có") if consent["contact"] else _("Không")))
	if "marketing" in consent:
		lines.append(
			_("Đồng ý nhận thông tin tiếp thị: {0}").format(_("Có") if consent["marketing"] else _("Không"))
		)
	return "\n".join(lines)[:MAX_TEXT_LEN]


def build_form(
	platform: str,
	*,
	lead_id: str,
	answers: dict[str, Any],
	form_doc: Any = None,
	form_id: str | None = None,
	extra: dict | None = None,
) -> dict:
	"""Dựng dict `form_lead` chuẩn (lưu trong raw_payload["form_lead"]) từ câu trả lời thô và biểu mẫu đã cấu hình."""
	field_map = {r.source_key: r.target for r in (form_doc.field_map if form_doc else []) or []}
	result = map_answers(answers, field_map)
	extra = dict(extra or {})
	is_test = bool(extra.pop("is_test", False)) or result.pop("test_marker")
	result.pop("test_marker", None)
	return {
		"platform": platform,
		"lead_id": clean(lead_id, 140),
		"form_id": clean(form_id or (form_doc.external_form_id if form_doc else ""), 140),
		"form_name": clean(form_doc.form_name if form_doc else "", 140),
		"is_test": is_test,
		**result,
		**extra,
	}


def to_event(channel_doc, form: dict, *, received_at: datetime | None = None, raw_extra: dict | None = None):
	"""InboundEvent `form_lead`. `external_sender_id` = mã lead của nền tảng (không chứa SĐT/email trong khóa định danh)."""
	lead_id = form["lead_id"]
	return InboundEvent(
		channel=channel_doc.name,
		channel_type=channel_doc.channel_type,
		event_type=EVENT_TYPE,
		external_message_id=lead_id,
		external_sender_id=lead_id,
		received_at=received_at or now_datetime(),
		text=render_text(form),
		sender_profile=profile_of(form),
		raw={"form_lead": form, **(raw_extra or {})},
	)


def values(items: Iterable[dict], key: str = "name") -> dict[str, str]:
	"""[{name, values:[...]}] (Meta field_data) -> {name: "a, b"}."""
	out: dict[str, str] = {}
	for item in items or []:
		if isinstance(item, dict) and item.get(key):
			out[clean(item[key], 120)] = clean(item.get("values"))
	return out
