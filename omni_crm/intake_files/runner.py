"""Nạp file JSON của miyano-intake vào cổng tiếp nhận omni_crm (một chiều: file -> Omni Inbound Message).

Cấu hình qua site_config (hạ tầng, không thêm trường vào DocType):
- `omni_intake_dir`: thư mục spool của miyano-intake (chứa `inbox/`). Vắng = tắt, không làm gì.
- `omni_intake_channels` (tùy chọn): {"Telephony": "<Omni Channel>", "Web Form": "..."}. Vắng thì dùng kênh DUY NHẤT
  đang bật của loại đó; không có hoặc có nhiều hơn một thì file giữ nguyên trong inbox (không đoán).
- `omni_intake_kinds` (tùy chọn): danh sách loại cần nạp, mặc định `["email"]`; các giá trị: `email` (thư mục `gmail`),
  `call` (`voice`), `web_form` (`web_form`), `messenger` (`messenger`). Thư mục không nằm trong danh sách thì không đọc, không đụng.
- Email không cần khai: thư vào Email Account có `email_id` trùng hộp thư của file (`source.account`).

Nếu người dùng chạy bench KHÔNG có quyền ghi vào spool (chỉ đọc), file không bị di chuyển: tiến trình ghi dấu "đã xử lý"
vào Redis (14 ngày) và chỉ quét các thư mục ngày trong `omni_intake_days` (mặc định 3) ngày gần nhất. Mất dấu thì nạp lại cũng
vô hại vì chống trùng ở cổng và ở Communication.

Có quyền ghi: sau khi nạp, file chuyển sang `archive/` (cùng cấu trúc ngày), giống quy ước trong README của miyano-intake;
file hỏng sang `dead_crm/` kèm tệp `.loi.txt`; loại tin CRM chưa hỗ trợ (ví dụ Instagram) sang `chua_nap/` để nạp sau.

Messenger (ADR 0022): tin của Page khác `meta_page_id` của kênh bị bỏ; echo của chính tin CRM đã gửi (`app_id` = `meta_app_id`)
bị bỏ; đính kèm được chép vào File riêng tư trước khi lưu tin. Tin cũ đã bị cất/ghi dấu trước khi có kênh: nạp bù bằng
`backfill` (bỏ qua dấu Redis và cửa sổ ngày, an toàn nhờ chống trùng).
Chạy lại an toàn: chống trùng bằng `dedupe_key` của cổng (cùng khóa với đường trực tiếp).

Hai nơi gọi `run`: cron mỗi phút (lưới an toàn) và `kick` do tiến trình `watcher` đẩy ngay khi thấy file mới. Mỗi lúc chỉ
một lượt chạy (khóa Redis); lượt đến sau chỉ đánh dấu "chạy lại" để lượt đang chạy quét thêm một vòng, nên không có hai
lượt cùng nạp một file và không file nào phải chờ cron.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import frappe

from omni_crm.channels.email import adapter as email_adapter
from omni_crm.channels.registry import get_adapter
from omni_crm.ingest import gateway
from omni_crm.intake_files import attachments, mapper
from omni_crm.pipeline.retry import is_transient, redact

KIND_DIRS = {"email": "gmail", "call": "voice", "web_form": "web_form", "messenger": "messenger"}
DEFAULT_KINDS = ("email",)
DEFAULT_DAYS = 3
DONE_TTL = 14 * 86400
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PER_RUN = 200
RESULTS = ("moi", "trung", "bo_qua", "chua_ho_tro", "thieu_kenh", "loi", "thu_lai")
RUN_METHOD = "omni_crm.intake_files.runner.run"
KICK_QUEUE = "short"
KICK_JOB_ID = "omni-intake-runner-kick"
LOCK_KEY = "omni:intake:runner:lock"
RERUN_KEY = "omni:intake:runner:rerun"
LOCK_TIMEOUT = 600  # giây; tiến trình chết giữa chừng thì khóa tự hết hạn
MAX_ROUNDS = 5  # số vòng quét tối đa trong một lượt khi liên tục có file mới


def spool_root() -> Path | None:
	"""Thư mục spool đã cấu hình (có `inbox/`), hoặc None."""
	value = frappe.conf.get("omni_intake_dir")
	if not value:
		return None
	root = Path(str(value))
	return root if (root / "inbox").is_dir() else None


def enabled_dirs() -> set[str]:
	"""Tên thư mục kênh trong `inbox/` được phép nạp theo `omni_intake_kinds` (mặc định chỉ email)."""
	kinds = frappe.conf.get("omni_intake_kinds") or DEFAULT_KINDS
	return {KIND_DIRS[k] for k in kinds if k in KIND_DIRS}


def resolve_channel(channel_type: str) -> str | None:
	"""Tên Omni Channel dùng để nạp loại này: cấu hình rõ, hoặc kênh duy nhất đang bật; không thì None."""
	chosen = (frappe.conf.get("omni_intake_channels") or {}).get(channel_type)
	if chosen:
		ok = frappe.db.get_value("Omni Channel", {"name": chosen, "channel_type": channel_type, "enabled": 1})
		return chosen if ok else None
	names = frappe.get_all(
		"Omni Channel", filters={"channel_type": channel_type, "enabled": 1}, pluck="name", limit=2
	)
	return names[0] if len(names) == 1 else None


def ingest_email(env: dict) -> str:
	"""Thư Gmail của intake -> Communication của Email Account có sẵn; hook omni_crm lo phần còn lại.

	- Không nạp thư intake đánh dấu tự động/hàng loạt (newsletter): CRM không cần và Communication không giữ header để lọc lại.
	- Email Account đang bật nhận thư (enable_incoming) nghĩa là CRM tự kéo hộp thư này: bỏ qua để không có hai đường.
	- Không có Email Account khớp địa chỉ hộp thư, hoặc chưa có Omni Channel Email đang bật cho nó: giữ nguyên file.
	"""
	if env.get("ignored") or env.get("ignore_reasons"):
		return "bo_qua"
	mailbox = str((env.get("source") or {}).get("account") or "").strip().lower()
	account = frappe.db.get_value(
		"Email Account",
		{"email_id": mailbox},
		["name", "email_id", "enable_incoming"],
		as_dict=True,
	)
	if not mailbox or not account or not email_adapter.find_channel(account.name):
		return "thieu_kenh"
	if account.enable_incoming:
		return "bo_qua"
	values = mapper.communication_values(env, account.name, account.email_id)
	if frappe.db.exists("Communication", {"message_id": values["message_id"], "email_account": account.name}):
		return "trung"
	frappe.get_doc(values).insert(
		ignore_permissions=True
	)  # after_insert -> on_communication_insert -> cổng tiếp nhận
	return "moi"


def _done_key(path: Path) -> str:
	return "omni:intake:done:" + hashlib.sha1(str(path).encode()).hexdigest()


def _is_done(path: Path) -> bool:
	return bool(frappe.cache().get_value(_done_key(path)))


def _mark_done(path: Path) -> None:
	frappe.cache().set_value(_done_key(path), 1, expires_in_sec=DONE_TTL)


def _day_of(root: Path, path: Path) -> date | None:
	"""Ngày trong đường dẫn `inbox/<năm>/<tháng>/<ngày>/<kênh>/<file>`; None nếu không đọc được."""
	try:
		year, month, day = path.relative_to(root / "inbox").parts[:3]
		return date(int(year), int(month), int(day))
	except (ValueError, TypeError):
		return None


def _in_window(root: Path, path: Path) -> bool:
	days = int(frappe.conf.get("omni_intake_days") or DEFAULT_DAYS)
	day = _day_of(root, path)
	return day is None or day >= date.today() - timedelta(days=days)


def _finish(root: Path, path: Path, bucket: str, note: str | None = None) -> None:
	"""Di chuyển file sang `bucket` nếu được phép ghi; nếu spool chỉ đọc thì ghi dấu đã xử lý."""
	if os.access(path.parent, os.W_OK):
		_move(root, path, bucket, note)
	else:
		_mark_done(path)


def _move(root: Path, src: Path, bucket: str, note: str | None = None) -> None:
	rel = src.relative_to(root / "inbox")
	dst = root / bucket / rel
	dst.parent.mkdir(parents=True, exist_ok=True)
	os.replace(src, dst)
	if note:
		dst.with_name(dst.name + ".loi.txt").write_text(note, encoding="utf-8")


def ingest_envelope(env: Any) -> str:
	"""Nạp một envelope đã đọc; trả một trong RESULTS. Không đụng hệ thống tệp, commit do người gọi."""
	mapped = mapper.map_envelope(env)
	if mapped.skip in (mapper.SKIP_NO_CHANNEL_TYPE,):
		return "chua_ho_tro"
	if mapped.skip:
		return "bo_qua"
	if mapped.channel_type == "Email":
		return ingest_email(env)
	channel = resolve_channel(mapped.channel_type)
	if not channel:
		return "thieu_kenh"
	if mapped.reaction:
		return ingest_reaction(env, channel)
	if mapped.channel_type == "Web Form":
		channel_doc = frappe.get_cached_doc("Omni Channel", channel)
		raw = json.dumps(mapped.body, ensure_ascii=False).encode()
		events = get_adapter("Web Form").parse(raw, {}, channel_doc)
		if not events:
			return "bo_qua"  # honeypot / mã biểu mẫu chưa cấu hình / không có dữ liệu
		names = [gateway.save_inbound(e) for e in events]
	elif mapped.channel_type == mapper.MESSENGER:
		return ingest_messenger(env, mapped, channel)
	else:
		mapped = mapper.with_channel(env, mapped, channel)
		if mapped.skip:
			return "bo_qua"
		names = [gateway.save_inbound(mapped.event)]
	return "moi" if any(names) else "trung"


def ingest_messenger(env: dict, mapped: mapper.Mapped, channel: str) -> str:
	"""Một tin Messenger: lọc Page và echo của chính ta, chép đính kèm vào File, lưu qua cổng; trả một trong RESULTS."""
	channel_doc = frappe.get_cached_doc("Omni Channel", channel)
	page = str((env.get("source") or {}).get("account") or "")
	own_page = str(channel_doc.get("meta_page_id") or "").strip()
	if own_page and page and page != own_page:
		return "bo_qua"  # tin của Page khác: không thuộc kênh này
	mapped = mapper.with_channel(env, mapped, channel)
	if mapped.skip or mapped.event is None:
		return "bo_qua"
	event = mapped.event
	app_id = str(channel_doc.get("meta_app_id") or "").strip()
	if event.event_type == mapper.ECHO and app_id and mapper.echo_app_id(env) == app_id:
		return "bo_qua"  # echo của tin CRM vừa gửi qua Send API: đã có Omni Outbound Message
	if frappe.db.exists("Omni Inbound Message", {"dedupe_key": f"{channel}:{event.external_message_id}"}):
		return "trung"  # kiểm trước khi chép tệp để không sinh File thừa
	stored, files = attachments.store(spool_root(), list(event.attachments))
	name = gateway.save_inbound(dataclasses.replace(event, attachments=stored))
	if not name:
		for f in files:  # trùng do chạy song song: bỏ File vừa tạo
			frappe.delete_doc("File", f, ignore_permissions=True, force=True)
		return "trung"
	attachments.attach(files, name)
	reply_to = mapper.reply_to_of(env)
	if reply_to:  # ADR 0027: khách (hoặc Page) trả lời một tin cụ thể
		frappe.db.set_value("Omni Inbound Message", name, "reply_to_mid", reply_to, update_modified=False)
	return "moi"


def ingest_reaction(env: dict, channel: str) -> str:
	"""Khách thả/bỏ cảm xúc trên một tin Messenger (ADR 0027): ghi lên tin đó, không tạo tin mới."""
	from omni_crm.channels.meta_messenger import reactions

	channel_doc = frappe.get_cached_doc("Omni Channel", channel)
	page = str((env.get("source") or {}).get("account") or "")
	own_page = str(channel_doc.get("meta_page_id") or "").strip()
	if own_page and page and page != own_page:
		return "bo_qua"
	data = mapper.reaction_of(env)
	if not data:
		return "bo_qua"
	return reactions.apply_customer(channel, data)


def backfill(
	kinds: list[str] | str | None = None, since: str | None = None, apply: int | str = 0, limit: int = 5000
) -> dict[str, Any]:
	"""Nạp bù các file trong `inbox/` và `chua_nap/` từ ngày `since` (YYYY-MM-DD), bỏ qua dấu Redis và cửa sổ ngày.

	Dùng sau khi bật một loại kênh mới (Messenger): tin cũ đã bị cất sang `chua_nap/` hoặc ghi dấu "đã xử lý" nên `run`
	không nạp lại. Mặc định chỉ đếm (`apply=0`); `apply=1` mới nạp. Không di chuyển file; chạy lại vô hại nhờ chống trùng.
	"""
	if isinstance(kinds, str):
		kinds = json.loads(kinds) if kinds.strip().startswith("[") else [kinds]
	dirs = {KIND_DIRS[k] for k in (kinds or ["messenger"]) if k in KIND_DIRS}
	root = spool_root()
	stats: dict[str, Any] = {"file": 0, "apply": bool(int(apply)), **dict.fromkeys(RESULTS, 0)}
	if root is None or not dirs:
		return stats
	start = date.fromisoformat(since) if since else None
	files = []
	for bucket in ("inbox", "chua_nap"):
		base = root / bucket
		if not base.is_dir():
			continue
		for p in base.rglob("*.json"):
			if p.parent.name not in dirs or not p.is_file() or p.name.startswith("."):
				continue
			try:
				year, month, day = p.relative_to(base).parts[:3]
				when = date(int(year), int(month), int(day))
			except (ValueError, TypeError):
				when = None
			if start and when and when < start:
				continue
			files.append(p)
	files.sort(key=lambda p: p.relative_to(root).parts[1:])
	stats["file"] = len(files)
	if not int(apply):
		return stats
	for path in files[:limit]:
		try:
			if path.stat().st_size > MAX_FILE_BYTES:
				raise ValueError("file lớn quá 2 MB")
			result = ingest_envelope(json.loads(path.read_text(encoding="utf-8")))
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			stats["loi"] += 1
			frappe.log_error(
				title="Omni: lỗi nạp bù file intake",
				message=redact(f"{path.name}\n{type(e).__name__}: {e}")[:1000],
			)
			frappe.db.commit()
			continue
		stats[result] += 1
		if path.relative_to(root).parts[0] == "inbox" and result in ("moi", "trung", "bo_qua"):
			_mark_done(path)  # để `run` (spool chỉ đọc) không quét lại
	return stats


def run(limit: int = MAX_PER_RUN) -> dict[str, int]:
	"""Một lượt nạp có khóa: mỗi lúc chỉ một lượt quét; lượt đến sau chỉ để lại dấu "chạy lại" cho lượt đang chạy.

	Dấu được đặt TRƯỚC khi thử lấy khóa và lượt giữ khóa xem dấu SAU khi nhả khóa, nên không có khe hở để một file
	phải chờ tới lượt cron sau. Tối đa MAX_ROUNDS vòng mỗi lượt khi file mới đến liên tục.
	"""
	cache = frappe.cache()
	lock_key, rerun_key = cache.make_key(LOCK_KEY), cache.make_key(RERUN_KEY)
	total = dict.fromkeys(RESULTS, 0)
	for _ in range(MAX_ROUNDS):
		cache.set(rerun_key, 1, ex=LOCK_TIMEOUT)
		lock = cache.lock(lock_key, timeout=LOCK_TIMEOUT)
		if not lock.acquire(blocking=False):
			break  # lượt đang giữ khóa sẽ thấy dấu và quét thêm
		try:
			cache.delete(rerun_key)
			for k, v in _scan(limit).items():
				total[k] += v
		finally:
			with contextlib.suppress(Exception):  # khóa đã hết hạn: không còn gì để trả
				lock.release()
		if not cache.get(rerun_key):
			break
	return total


def kick() -> None:
	"""Đẩy ngay một lượt nạp (watcher gọi khi thấy file mới); đã có lượt chờ/đang chạy thì lượt đó quét thêm nhờ dấu."""
	cache = frappe.cache()
	cache.set(cache.make_key(RERUN_KEY), 1, ex=LOCK_TIMEOUT)
	frappe.enqueue(RUN_METHOD, queue=KICK_QUEUE, job_id=KICK_JOB_ID, deduplicate=True)


def _scan(limit: int) -> dict[str, int]:
	"""Quét `inbox/` và nạp tối đa `limit` file theo thứ tự đường dẫn (cũ trước). Lỗi từng file không dừng cả lô."""
	stats = dict.fromkeys(RESULTS, 0)
	root = spool_root()
	if root is None:
		return stats
	allowed = enabled_dirs()
	files = sorted(
		p
		for p in (root / "inbox").rglob("*.json")
		if p.parent.name in allowed and p.is_file() and not p.name.startswith(".")
	)
	files = [p for p in files if os.access(p.parent, os.W_OK) or (_in_window(root, p) and not _is_done(p))]
	for path in files[:limit]:
		try:
			if path.stat().st_size > MAX_FILE_BYTES:
				raise ValueError("file lớn quá 2 MB")
			env = json.loads(path.read_text(encoding="utf-8"))
			result = ingest_envelope(env)
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			if is_transient(e):
				# Deadlock, mất kết nối...: giữ file trong inbox (không ghi dấu đã xử lý) và quét lại vòng sau; nếu vẫn lỗi
				# thì cron thử tiếp. Trước đây file bị coi là hỏng và (ở spool chỉ đọc) không bao giờ được nạp lại.
				stats["thu_lai"] += 1
				cache = frappe.cache()
				cache.set(cache.make_key(RERUN_KEY), 1, ex=LOCK_TIMEOUT)
				frappe.log_error(
					title="Omni: lỗi tạm thời khi nạp file intake, sẽ thử lại",
					message=redact(f"{path.name}\n{type(e).__name__}: {e}")[:1000],
				)
				frappe.db.commit()
				continue
			stats["loi"] += 1
			_finish(root, path, "dead_crm", redact(f"{type(e).__name__}: {e}")[:500])
			frappe.log_error(
				title="Omni: lỗi nạp file intake",
				message=redact(f"{path.name}\n{frappe.get_traceback(with_context=False)[-1200:]}"),
			)
			frappe.db.commit()
			continue
		stats[result] += 1
		if result in ("moi", "trung", "bo_qua"):
			_finish(root, path, "archive")
		elif result == "chua_ho_tro":
			_finish(root, path, "chua_nap")
		# thieu_kenh: giữ nguyên trong inbox, nạp lại khi đã bật kênh
	return stats
