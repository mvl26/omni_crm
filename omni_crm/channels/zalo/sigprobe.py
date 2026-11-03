"""Công cụ chẩn đoán chữ ký webhook Zalo (V21-05, ADR 0015): ghi lại webhook bị từ chối để xác nhận Z1 trên OA thật.

Chỉ chạy khi `site_config.json` có `omni_zalo_signature_debug_until` (ISO datetime) CÒN HẠN. Hạn hiệu lực = min(giá trị
đặt, lần đầu hệ thống thấy đúng giá trị đó + 2 giờ): đặt hạn xa cũng không kéo dài quá 2 giờ kể từ lúc bật.

Mỗi bản ghi là một tệp JSON 0600 trong `<site>/private/omni_sigprobe/` (thư mục 0700; không phải DocType, không phải Error
Log nên System Manager không tự thấy; chỉ API `omni_crm.api.admin.zalo_signature_probes` của Omni Admin đọc được). Bản ghi
gồm: lý do từ chối, tên các header nhận được (không giá trị), chữ ký nhận được, chữ ký hệ thống tính ra theo công thức hiện
hành, tên các biến thể công thức khớp với chữ ký nhận được, độ dài thân, 200 ký tự đầu của thân. KHÔNG ghi `oa_secret_key`
(mọi lần xuất hiện trong phần thân bị che). Tệp quá 24 giờ bị xóa (tác vụ mỗi giờ + khi đọc).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_system_timezone, now_datetime

from omni_crm.channels.zalo import config, signature

CONF_KEY = "omni_zalo_signature_debug_until"
DIR_NAME = "omni_sigprobe"
ARMED_FILE = ".armed"
MAX_WINDOW = timedelta(hours=2)
RETENTION = timedelta(hours=24)
BODY_PREVIEW_CHARS = 200
MAX_RECORDS = 200  # chống kẻ lạ gửi hàng loạt webhook sai chữ ký để lấp đầy đĩa


def probe_dir() -> str:
	"""Thư mục bản ghi chẩn đoán: <site>/private/omni_sigprobe."""
	return frappe.get_site_path("private", DIR_NAME)


def _ensure_dir() -> str:
	path = probe_dir()
	os.makedirs(path, mode=0o700, exist_ok=True)
	os.chmod(path, 0o700)
	return path


def _write_private(path: str, text: str) -> None:
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0), 0o600)
	try:
		os.fchmod(fd, 0o600)
		os.write(fd, text.encode("utf-8"))
	finally:
		os.close(fd)


def _parse_until(raw) -> datetime | None:
	"""ISO datetime trong site_config -> datetime không múi giờ theo múi giờ hệ thống; sai định dạng -> None (tắt)."""
	if not raw:
		return None
	try:
		value = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
	except ValueError:
		return None
	if value.tzinfo is not None:
		value = value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return value


def _armed_at(raw, now: datetime) -> datetime:
	"""Lần đầu hệ thống thấy đúng giá trị `raw` (lưu bền trong tệp .armed); giá trị mới -> bắt đầu đếm 2 giờ từ `now`."""
	path = os.path.join(_ensure_dir(), ARMED_FILE)
	with contextlib.suppress(OSError, ValueError, KeyError):
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
		if data["value"] == str(raw):
			return datetime.fromisoformat(data["armed_at"])
	_write_private(path, json.dumps({"value": str(raw), "armed_at": now.isoformat()}))
	return now


def effective_until(now: datetime | None = None) -> datetime | None:
	"""Thời điểm chẩn đoán hết hiệu lực nếu đang bật (chưa quá hạn), ngược lại None."""
	raw = frappe.conf.get(CONF_KEY)
	until = _parse_until(raw)
	if until is None:
		return None
	now = now or now_datetime()
	try:
		until = min(until, _armed_at(raw, now) + MAX_WINDOW)
	except OSError:
		until = min(until, now + MAX_WINDOW)  # không ghi được dấu bật: vẫn cắt về 2 giờ kể từ hiện tại
	return until if now < until else None


def is_active(now: datetime | None = None) -> bool:
	"""Công cụ chẩn đoán đang bật (cờ có hạn còn hiệu lực)."""
	return effective_until(now) is not None


def _mask(text: str, secrets: list[str]) -> str:
	for secret in secrets:
		if secret:
			text = text.replace(secret, "[***]")
	return text


def _variants(app_id: str, raw_body: bytes, timestamp: str, secret: str) -> dict[str, str]:
	"""Các công thức ứng viên (chỉ để đối chiếu với chữ ký nhận được; mã băm không được ghi ra)."""
	a, s, ts = app_id.encode(), secret.encode(), timestamp.encode()
	return {
		"sha256(app_id+body+timestamp+secret)": hashlib.sha256(a + raw_body + ts + s).hexdigest(),
		"sha256(app_id+body+secret)": hashlib.sha256(a + raw_body + s).hexdigest(),
		"sha256(body+timestamp+secret)": hashlib.sha256(raw_body + ts + s).hexdigest(),
		"hmac_sha256(secret,body)": hmac.new(s, raw_body, hashlib.sha256).hexdigest(),
		"hmac_sha256(secret,app_id+body+timestamp)": hmac.new(
			s, a + raw_body + ts, hashlib.sha256
		).hexdigest(),
	}


def build_record(
	raw_body: bytes, headers: dict, app_id: str, oa_secret_key: str, reason: str, channel: str
) -> dict:
	"""Dựng bản ghi chẩn đoán (thuần, dễ test); không chứa `oa_secret_key`."""
	header = signature._header(headers, config.get("signature_header"))
	received = (header or "").strip()
	if received.lower().startswith("mac="):
		received = received[4:]
	try:
		parsed = json.loads(raw_body)
	except ValueError:
		parsed = None
	timestamp = str(parsed.get("timestamp", "")) if isinstance(parsed, dict) else ""
	computed = signature.compute_mac(app_id, raw_body, timestamp, oa_secret_key) if oa_secret_key else ""
	matched = [
		name
		for name, value in (
			_variants(app_id, raw_body, timestamp, oa_secret_key).items() if oa_secret_key else []
		)
		if received and hmac.compare_digest(value.encode(), received.lower().encode())
	]
	secrets = [oa_secret_key]
	preview = _mask(raw_body.decode("utf-8", errors="replace"), secrets)[
		:BODY_PREVIEW_CHARS
	]  # che trước khi cắt
	return {
		"at": now_datetime().isoformat(),
		"channel": channel,
		"reason": reason,
		"signature_header_name": config.get("signature_header"),
		"signature_received": _mask(received, secrets),
		"signature_computed": computed,
		"timestamp_in_body": _mask(timestamp, secrets),
		"matched_variants": matched,
		"body_has_mac_field": isinstance(parsed, dict) and "mac" in parsed,
		"header_names": sorted(str(k).lower() for k in (headers or {})),
		"body_length": len(raw_body),
		"body_preview": preview,
	}


def record_rejection(
	raw_body: bytes, headers: dict, app_id: str, oa_secret_key: str, reason: str, channel: str
) -> bool:
	"""Ghi một bản ghi nếu công cụ đang bật; không bao giờ ném lỗi (webhook không được hỏng vì chẩn đoán). True nếu đã ghi."""
	try:
		if not is_active():
			return False
		folder = _ensure_dir()
		if sum(1 for n in os.listdir(folder) if n.endswith(".json")) >= MAX_RECORDS:
			return False
		record = build_record(raw_body, headers, app_id, oa_secret_key, reason, channel)
		name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
		_write_private(os.path.join(folder, name), json.dumps(record, ensure_ascii=False))
		return True
	except Exception:
		return False


def cleanup(now: datetime | None = None) -> int:
	"""Xóa bản ghi quá 24 giờ (và dấu .armed khi cờ đã bị gỡ khỏi site_config); trả số tệp đã xóa. Tác vụ mỗi giờ."""
	folder = probe_dir()
	if not os.path.isdir(folder):
		return 0
	now_ts = now.timestamp() if now else time.time()
	removed = 0
	for name in os.listdir(folder):
		path = os.path.join(folder, name)
		if name == ARMED_FILE:
			continue
		try:
			if name.endswith(".json") and now_ts - os.path.getmtime(path) > RETENTION.total_seconds():
				os.remove(path)
				removed += 1
		except OSError:
			continue
	if not frappe.conf.get(
		CONF_KEY
	):  # cờ đã gỡ: quên dấu bật. Cờ còn nhưng hết hạn thì GIỮ dấu để không bật lại 2 giờ nữa
		with contextlib.suppress(OSError):
			os.remove(os.path.join(folder, ARMED_FILE))
	return removed


def list_records() -> list[dict]:
	"""Các bản ghi còn hạn 24 giờ, mới nhất trước (dùng cho API của Omni Admin)."""
	cleanup()
	folder = probe_dir()
	if not os.path.isdir(folder):
		return []
	out = []
	for name in sorted((n for n in os.listdir(folder) if n.endswith(".json")), reverse=True):
		with contextlib.suppress(OSError, ValueError):
			with open(os.path.join(folder, name), encoding="utf-8") as f:
				out.append(json.load(f))
	return out
