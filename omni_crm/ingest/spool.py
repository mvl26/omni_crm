"""Hàng dự phòng của webhook (V21-04, ADR 0014): tệp JSON-lines nối thêm trong thư mục private của site.

Khi webhook ĐÃ qua xác thực mà không ghi được DB (hết hạn chờ khóa, bế tắc, mất kết nối), payload thô được nối vào đây
rồi webhook vẫn trả thành công. Tác vụ quét mỗi phút (`drain`) kiểm lại chữ ký và chèn vào `Omni Inbound Message`
(chống trùng bằng `dedupe_key`), xóa dòng đã chèn.

Mỗi dòng: {"v","id","ch","ct","recv","hdr","raw"(base64),"seal"}. KHÔNG có bí mật: `hdr` chỉ gồm các header chữ ký
(giá trị là chữ ký, không phải khóa), `seal` là HMAC bằng khóa mã hóa của site (không lưu) chứng minh dòng do app ghi
sau khi xác thực. Payload thô chứa dữ liệu cá nhân của khách nên: thư mục 0700, tệp 0600, giới hạn dung lượng/số dòng,
dòng bị loại (hỏng, sai chữ ký) bị xóa ngay, dòng không chèn được bị xóa sau `omni_spool_expire_days` (mặc định 14).
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import fcntl
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Iterator
from datetime import datetime

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

VERSION = 1
SPOOL_DIR_NAME = "omni_spool"
ACTIVE = "spool.jsonl"
WORK_SUFFIX = ".work"
LOCK_FILE = "spool.lock"
DRAIN_LOCK_FILE = "drain.lock"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_LINES = 5000
DEFAULT_EXPIRE_DAYS = 14
MAX_LINE_BYTES = 2 * 1024 * 1024
STALE_ALERT_SECONDS = 10 * 60
# Chỉ các header chữ ký cần để kiểm lại (giá trị là chữ ký). Tuyệt đối không Authorization/Cookie/token.
HEADER_ALLOWLIST = ("x-zevent-signature", "x-hub-signature-256", "x-stringee-signature")


class SpoolError(Exception):
	"""Không ghi được vào hàng dự phòng (hết chỗ, lỗi đĩa, quyền)."""


class SpoolFull(SpoolError):
	"""Hàng dự phòng đã chạm giới hạn dung lượng hoặc số dòng."""


# ------------------------------------------------------------------ cấu hình


def _conf_int(key: str, default: int) -> int:
	try:
		value = int(frappe.conf.get(key) or 0)
	except (TypeError, ValueError):
		value = 0
	return value if value > 0 else default


def max_bytes() -> int:
	"""Dung lượng tối đa của hàng dự phòng (site_config `omni_spool_max_bytes`)."""
	return _conf_int("omni_spool_max_bytes", DEFAULT_MAX_BYTES)


def max_lines() -> int:
	"""Số dòng tối đa của hàng dự phòng (site_config `omni_spool_max_lines`)."""
	return _conf_int("omni_spool_max_lines", DEFAULT_MAX_LINES)


def expire_days() -> int:
	"""Số ngày giữ dòng không chèn được trước khi xóa (site_config `omni_spool_expire_days`)."""
	return _conf_int("omni_spool_expire_days", DEFAULT_EXPIRE_DAYS)


def spool_dir() -> str:
	"""Thư mục hàng dự phòng: <site>/private/omni_spool (không nằm trong public/ và không phải private/files)."""
	return frappe.get_site_path("private", SPOOL_DIR_NAME)


def _ensure_dir() -> str:
	path = spool_dir()
	os.makedirs(path, mode=0o700, exist_ok=True)
	os.chmod(path, 0o700)
	return path


def _open_private(path: str, flags: int) -> int:
	return os.open(path, flags | getattr(os, "O_CLOEXEC", 0), 0o600)


@contextlib.contextmanager
def _flock(name: str, *, blocking: bool = True) -> Iterator[bool]:
	"""Khóa tệp (flock) giữa các tiến trình; non-blocking mà không lấy được thì trả False."""
	fd = _open_private(os.path.join(_ensure_dir(), name), os.O_RDWR | os.O_CREAT)
	got = False
	try:
		try:
			fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
			got = True
		except BlockingIOError:
			got = False
		yield got
	finally:
		if got:
			fcntl.flock(fd, fcntl.LOCK_UN)
		os.close(fd)


# ------------------------------------------------------------------ niêm phong (seal)


def _seal_key() -> bytes:
	from frappe.utils.password import get_encryption_key

	return hashlib.sha256(b"omni-spool-v1|" + str(get_encryption_key()).encode()).digest()


def compute_seal(entry: dict) -> str:
	"""HMAC-SHA256 trên (v, id, kênh, thời điểm nhận, SHA-256 của payload thô, header chữ ký)."""
	raw = base64.b64decode(entry.get("raw") or "")
	hdr = json.dumps(entry.get("hdr") or {}, sort_keys=True, separators=(",", ":"))
	msg = "|".join(
		[
			str(entry.get("v")),
			str(entry.get("id")),
			str(entry.get("ch")),
			str(entry.get("recv")),
			hashlib.sha256(raw).hexdigest(),
			hashlib.sha256(hdr.encode()).hexdigest(),
		]
	)
	return hmac.new(_seal_key(), msg.encode(), hashlib.sha256).hexdigest()


def _signature_headers(headers: dict) -> dict[str, str]:
	out = {}
	for k, v in (headers or {}).items():
		if str(k).lower() in HEADER_ALLOWLIST:
			out[str(k).lower()] = str(v)[:512]
	return out


def build_entry(channel_name: str, channel_type: str, raw: bytes, headers: dict, received_at=None) -> dict:
	"""Dựng một dòng hàng dự phòng đã niêm phong."""
	entry = {
		"v": VERSION,
		"id": uuid.uuid4().hex,
		"ch": channel_name,
		"ct": channel_type,
		"recv": str(received_at or now_datetime()),
		"hdr": _signature_headers(headers),
		"raw": base64.b64encode(raw).decode(),
	}
	entry["seal"] = compute_seal(entry)
	return entry


# ------------------------------------------------------------------ đọc/đếm


def _files() -> list[str]:
	path = spool_dir()
	if not os.path.isdir(path):
		return []
	names = sorted(n for n in os.listdir(path) if n == ACTIVE or n.endswith(WORK_SUFFIX))
	return [os.path.join(path, n) for n in names]


def _count_lines(path: str) -> int:
	n = 0
	try:
		with open(path, "rb") as f:
			while chunk := f.read(1 << 20):
				n += chunk.count(b"\n")
	except FileNotFoundError:
		return 0
	return n


def _iter_entries(path: str) -> Iterator[dict | None]:
	"""Từng dòng đã parse JSON; dòng hỏng trả None."""
	try:
		with open(path, "rb") as f:
			for line in f:
				line = line.strip()
				if not line:
					continue
				try:
					data = json.loads(line)
					yield data if isinstance(data, dict) else None
				except ValueError:
					yield None
	except FileNotFoundError:
		return


def spool_count() -> int:
	"""Số tin đang nằm trong hàng dự phòng (V21-08 đọc hàm này). Không chạm DB; lỗi đọc thì trả 0."""
	try:
		return sum(_count_lines(p) for p in _files())
	except OSError:
		return 0


def oldest_age_seconds() -> int | None:
	"""Tuổi (giây) của tin cũ nhất trong hàng dự phòng; None nếu rỗng. Dòng hỏng tính theo thời gian sửa tệp."""
	oldest: datetime | None = None
	try:
		for path in _files():
			for entry in _iter_entries(path):
				try:
					t = (
						get_datetime(entry["recv"])
						if entry
						else datetime.fromtimestamp(os.path.getmtime(path))
					)
				except Exception:
					t = datetime.fromtimestamp(os.path.getmtime(path))
				oldest = t if oldest is None or t < oldest else oldest
	except OSError:
		return None
	return None if oldest is None else max(0, int((now_datetime() - oldest).total_seconds()))


def stats() -> dict:
	"""Số liệu cho màn giám sát/preflight: số tin, tuổi cũ nhất, byte, ghi được không."""
	try:
		size = sum(os.path.getsize(p) for p in _files())
	except OSError:
		size = 0
	return {
		"count": spool_count(),
		"oldest_age_seconds": oldest_age_seconds(),
		"bytes": size,
		"writable": is_writable(),
		"stale": (oldest_age_seconds() or 0) > STALE_ALERT_SECONDS,
	}


def is_writable() -> bool:
	"""Thư mục hàng dự phòng ghi được không (tạo nếu chưa có)."""
	try:
		return os.access(_ensure_dir(), os.W_OK)
	except OSError:
		return False


# ------------------------------------------------------------------ ghi


def enabled() -> bool:
	"""Công tắc ghi vào hàng dự phòng. Cực tính NGƯỢC (`webhook_spool_disabled`): chưa lưu hoặc 0 = bật. Lỗi đọc = bật."""
	try:
		row = frappe.db.sql(
			"select value from `tabSingles` where doctype = 'Omni Settings' and field = 'webhook_spool_disabled'"
		)
		return not (row and str(row[0][0] or "0") not in ("0", ""))
	except Exception:
		return True  # DB hỏng thì không đọc được công tắc: ưu tiên không mất tin


def append(entry: dict) -> None:
	"""Nối một dòng vào hàng dự phòng (khóa ghi, fsync). Hết chỗ: SpoolFull; lỗi đĩa/quyền: SpoolError."""
	line = (json.dumps(entry, separators=(",", ":")) + "\n").encode()
	if len(line) > MAX_LINE_BYTES:
		raise SpoolFull("dòng vượt giới hạn kích thước")
	try:
		with _flock(LOCK_FILE) as got:
			if not got:
				raise SpoolError("không lấy được khóa ghi")
			files = _files()
			if sum(os.path.getsize(p) for p in files) + len(line) > max_bytes():
				raise SpoolFull("vượt dung lượng")
			if sum(_count_lines(p) for p in files) + 1 > max_lines():
				raise SpoolFull("vượt số dòng")
			fd = _open_private(os.path.join(spool_dir(), ACTIVE), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
			try:
				os.write(fd, line)
				os.fsync(fd)
			finally:
				os.close(fd)
	except SpoolError:
		raise
	except OSError as e:
		raise SpoolError(type(e).__name__) from e


# ------------------------------------------------------------------ chèn lại


def _rotate() -> None:
	"""Đổi tên tệp đang ghi thành tệp xử lý (dưới khóa ghi) để webhook tiếp tục ghi tệp mới."""
	with _flock(LOCK_FILE) as got:
		active = os.path.join(spool_dir(), ACTIVE)
		if got and os.path.exists(active) and os.path.getsize(active):
			os.rename(
				active,
				os.path.join(
					spool_dir(), f"spool.{int(time.time() * 1000)}.{uuid.uuid4().hex[:6]}{WORK_SUFFIX}"
				),
			)


def _rewrite(path: str, lines: list[bytes]) -> None:
	if not lines:
		with contextlib.suppress(FileNotFoundError):
			os.remove(path)
		return
	tmp = path + ".tmp"
	fd = _open_private(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
	try:
		os.write(fd, b"".join(lines))
		os.fsync(fd)
	finally:
		os.close(fd)
	os.replace(tmp, path)


def _log(msg: str, *args) -> None:
	# Chỉ id/kênh/lý do; không payload, không PII.
	frappe.logger("omni").warning("omni spool: " + msg, *args)


def reverify(entry: dict, channel_doc) -> tuple[bool, str]:
	"""Kiểm lại một dòng: seal (bắt buộc) rồi chữ ký của nhà cung cấp nếu kiểm lại được. Trả (hợp lệ, lý do)."""
	if entry.get("v") != VERSION:
		return False, "phien_ban_la"
	try:
		raw = base64.b64decode(entry.get("raw") or "", validate=True)
		if not hmac.compare_digest(compute_seal(entry), str(entry.get("seal") or "")):
			return False, "seal_sai"
	except Exception:
		return False, "dong_hong"
	headers = {k: v for k, v in (entry.get("hdr") or {}).items()}
	ctype = channel_doc.channel_type
	try:
		if ctype == "Zalo OA":
			if channel_doc.zalo_skip_signature:
				return True, "ok"
			from omni_crm.channels.zalo import config, signature

			header = (headers.get(config.get("signature_header").lower()) or "").strip()
			secret = channel_doc.get_password("zalo_oa_secret_key", raise_exception=False) or ""
			if not header or not secret or not channel_doc.zalo_app_id:
				return False, "thieu_chu_ky"
			received = header[4:] if header.lower().startswith("mac=") else header
			ts = str(json.loads(raw)["timestamp"])  # bỏ kiểm lệch giờ: đã kiểm lúc nhận
			expected = signature.compute_mac(channel_doc.zalo_app_id, raw, ts, secret)
			ok = hmac.compare_digest(expected.encode(), received.lower().encode())
		elif ctype == "Meta Lead Ads":
			from omni_crm.channels.registry import get_adapter

			ok = bool(get_adapter(ctype).verify_request(raw, headers, channel_doc))
		elif ctype == "Google Lead Form":
			from omni_crm.channels.registry import get_adapter

			ok = bool(get_adapter(ctype).verify_request(raw, headers, channel_doc))
		elif ctype == "Telephony":
			from omni_crm.channels.telephony.registry import provider_for_channel

			ok = bool(provider_for_channel(channel_doc).verify_request(raw, headers, {}, channel_doc))
		else:
			ok = True  # Web Form/Email: không có chữ ký kiểm lại được (captcha dùng một lần); seal đã đủ
	except Exception:
		return False, "kiem_chu_ky_loi"
	return (True, "ok") if ok else (False, "chu_ky_sai")


def _is_db_down(e: BaseException) -> bool:
	from omni_crm.api.webhooks import is_db_unavailable

	return is_db_unavailable(e)


def _insert_entry(entry: dict) -> str:
	"""Chèn một dòng: 'saved'/'dup' khi xong, 'drop:<lý do>' khi phải loại; ném lỗi khi DB lỗi (giữ lại)."""
	from omni_crm.api.webhooks import _unparsed_event
	from omni_crm.channels.registry import get_adapter
	from omni_crm.ingest import gateway

	channel = entry.get("ch")
	if not channel or not frappe.db.exists("Omni Channel", channel):
		return "drop:kenh_khong_con"
	channel_doc = frappe.get_cached_doc("Omni Channel", channel)
	ok, reason = reverify(entry, channel_doc)
	if not ok:
		return f"drop:{reason}"
	raw = base64.b64decode(entry["raw"])
	recv = get_datetime(entry["recv"])
	adapter = get_adapter(channel_doc.channel_type)
	try:
		events = adapter.parse(raw, entry.get("hdr") or {}, channel_doc)
	except Exception as e:
		if _is_db_down(e):
			raise
		events = [_unparsed_event(channel_doc, raw)]
	saved = 0
	for event in events:
		if (event.received_at - recv).total_seconds() > 30:  # adapter lấy giờ hiện tại: trả lại giờ nhận thật
			event = dataclasses.replace(event, received_at=recv)
		if gateway.save_inbound(event):
			saved += 1
	frappe.db.commit()
	return "saved" if saved else "dup"


def drain(budget_seconds: float = 50.0) -> dict:
	"""Tác vụ quét mỗi phút: chèn lại các dòng trong hàng dự phòng, xóa dòng đã chèn; lỗi DB thì giữ lại thử lần sau."""
	result = {"saved": 0, "dup": 0, "dropped": 0, "kept": 0, "expired": 0}
	if not _files():
		return result
	with _flock(DRAIN_LOCK_FILE, blocking=False) as got:
		if not got:
			return result
		_rotate()
		deadline = time.monotonic() + budget_seconds
		cutoff = add_to_date(now_datetime(), days=-expire_days())
		for path in [p for p in _files() if p.endswith(WORK_SUFFIX)]:
			keep: list[bytes] = []
			stop = False
			with open(path, "rb") as f:
				lines = [ln for ln in f.read().split(b"\n") if ln.strip()]
			for line in lines:
				if stop or time.monotonic() > deadline:
					keep.append(line)
					result["kept"] += 1
					continue
				try:
					entry = json.loads(line)
				except ValueError:
					entry = None
				if not isinstance(entry, dict):
					result["dropped"] += 1
					_log("loai dong hong (khong doc duoc JSON)")
					continue
				try:
					outcome = _insert_entry(entry)
				except Exception as e:
					frappe.db.rollback()
					keep.append(line)
					result["kept"] += 1
					if _is_db_down(e):
						stop = True  # DB còn lỗi: dừng, thử lại phút sau
					else:
						_log(
							"chen lai loi id=%s kenh=%s loai=%s",
							entry.get("id"),
							entry.get("ch"),
							type(e).__name__,
						)
					continue
				if outcome.startswith("drop:"):
					result["dropped"] += 1
					_log("loai dong id=%s kenh=%s ly_do=%s", entry.get("id"), entry.get("ch"), outcome[5:])
				else:
					result[outcome] += 1
			# dòng giữ lại mà quá hạn: xóa (PII không giữ vô hạn), ghi log nghiêm trọng, không kèm nội dung
			survivors = []
			for line in keep:
				try:
					recv = get_datetime(json.loads(line)["recv"])
				except Exception:
					recv = None
				if recv is not None and recv < cutoff:
					result["expired"] += 1
					frappe.logger("omni").critical(
						"omni spool: xoa dong qua han %s ngay chua chen duoc", expire_days()
					)
				else:
					survivors.append(line + b"\n")
			_rewrite(path, survivors)
	if result["expired"]:
		_alert_expired(result["expired"])
	return result


def _alert_expired(n: int) -> None:
	try:
		from omni_crm.monitoring.alerts import Alert, deliver_now

		deliver_now(
			Alert(
				"webhook_spool_het_han",
				"",
				frappe._("Tin trong hàng dự phòng bị xóa vì quá hạn"),
				frappe._(
					"{0} tin nằm trong hàng dự phòng quá {1} ngày không chèn được vào DB nên đã bị xóa (không giữ dữ liệu cá nhân vô hạn). Kiểm tra DB và nhật ký."
				).format(n, expire_days()),
			)
		)
	except Exception:
		pass
