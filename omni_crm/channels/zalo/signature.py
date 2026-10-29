"""Xác thực chữ ký webhook Zalo.

Công thức theo docs/06 mục 3 (CHƯA đối chiếu ví dụ chính thức, xem docs/12 G7):
header `X-ZEvent-Signature: mac=<hex>`, hex = sha256(app_id + raw_body + timestamp + oa_secret_key),
`timestamp` lấy từ payload (chuỗi nguyên văn).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from omni_crm.channels.zalo import config

OK = "ok"
MISSING = "thieu_chu_ky"
BAD_BODY = "body_khong_doc_duoc"
STALE = "timestamp_lech"
MISMATCH = "chu_ky_sai"


def _header(headers: dict, name: str) -> str | None:
	lower = name.lower()
	for key, value in (headers or {}).items():
		if str(key).lower() == lower:
			return value
	return None


def compute_mac(app_id: str, raw_body: bytes, timestamp: str, oa_secret_key: str) -> str:
	"""Chữ ký hex theo công thức Zalo."""
	data = app_id.encode() + raw_body + str(timestamp).encode() + oa_secret_key.encode()
	return hashlib.sha256(data).hexdigest()


def _timestamp_seconds(value: str) -> float | None:
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	return number / 1000 if number > 1e11 else number  # Zalo gửi mili giây


def check(
	raw_body: bytes, headers: dict, app_id: str, oa_secret_key: str, now: float | None = None
) -> tuple[bool, str]:
	"""(hợp lệ, lý do) - lý do là một trong OK/MISSING/BAD_BODY/STALE/MISMATCH để thống kê từ chối."""
	header = _header(headers, config.get("signature_header"))
	if not header or not app_id or not oa_secret_key:
		return False, MISSING
	received = header.strip()
	if received.lower().startswith("mac="):
		received = received[4:]
	try:
		timestamp = str(json.loads(raw_body)["timestamp"])
	except (ValueError, KeyError, TypeError):
		return False, BAD_BODY
	expected = compute_mac(app_id, raw_body, timestamp, oa_secret_key)
	if not hmac.compare_digest(expected.encode(), received.lower().encode()):
		return False, MISMATCH
	seconds = _timestamp_seconds(timestamp)
	current = time.time() if now is None else now
	if seconds is None or abs(current - seconds) > config.get("max_timestamp_skew_seconds"):
		return False, STALE
	return True, OK


def verify(raw_body: bytes, headers: dict, app_id: str, oa_secret_key: str, now: float | None = None) -> bool:
	"""Chữ ký đúng và timestamp lệch không quá 10 phút so với `now` (giây epoch, mặc định giờ máy chủ)."""
	return check(raw_body, headers, app_id, oa_secret_key, now)[0]
