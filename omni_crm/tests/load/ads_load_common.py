"""Dữ liệu tổng hợp dùng chung cho tải kênh quảng cáo (V2-08). Không có dữ liệu khách thật.

Mọi tên/SĐT/email sinh theo chỉ số `i` để máy khách gửi, máy chủ Graph giả và bước đối chiếu cùng tính ra một kết quả.
Tiền tố `LOADADS` được dùng để dọn sạch.
"""

from __future__ import annotations

PREFIX = "LOADADS"
CHANNELS = {
	"meta": f"{PREFIX} meta",
	"google": f"{PREFIX} google",
	"web": f"{PREFIX} web",
	"zalo": f"{PREFIX} zalo",
}
SALES = [f"loadads_sale{i}@example.com" for i in range(1, 6)]
META_PAGE = f"{PREFIX}PAGE"
META_FORMS = [f"{PREFIX}MF{i}" for i in (1, 2)]
GOOGLE_FORMS = [f"{PREFIX}GF{i}" for i in (1, 2)]
WEB_FORMS = [f"{PREFIX}WF1"]
APP_SECRET = "loadads-meta-app-secret-khong-that"
VERIFY_TOKEN = "loadads-verify-token-khong-that"
PAGE_TOKEN = "loadads-page-token-khong-that-" + "x" * 20
GOOGLE_KEY = "loadads-google-key-khong-that"
ZALO_APP_ID = "loadadsapp"
ZALO_SECRET = "loadads-zalo-secret-khong-that"
DUP_EVERY = 5  # người có chỉ số chia hết cho 5 (20%) đã có Contact trong CRM


def person(i: int) -> dict:
	"""Người thứ i: tên, SĐT, email, công ty."""
	return {
		"name": f"{PREFIX} Khach {i:04d}",
		"phone": f"0971{i:06d}",
		"phone_e164": f"+84971{i:06d}",
		"email": f"loadads{i:04d}@loadads-congty.example.com",
		"company": f"Benh vien {PREFIX} {i:04d}",
	}


def has_contact(i: int) -> bool:
	"""Người này đã có Contact từ trước (kịch bản 20% trùng Contact)."""
	return i % DUP_EVERY == 0


def platform_of(i: int) -> str:
	"""Trộn nền tảng: 40% Meta, 30% Google, 30% web."""
	r = i % 10
	return "meta" if r < 4 else ("google" if r < 7 else "web")


def meta_lead(leadgen_id: str, i: int) -> dict:
	"""Phản hồi Graph giả cho một lead Meta."""
	p = person(i)
	return {
		"id": leadgen_id,
		"created_time": "2026-11-02T03:00:00+0000",
		"ad_id": f"{PREFIX}AD{i % 4}",
		"form_id": META_FORMS[i % 2],
		"field_data": [
			{"name": "full_name", "values": [p["name"]]},
			{"name": "email", "values": [p["email"]]},
			{"name": "phone_number", "values": [p["phone_e164"]]},
			{"name": "company_name", "values": [p["company"]]},
		],
	}
