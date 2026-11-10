"""Nạp file JSON của miyano-intake vào cổng tiếp nhận omni_crm (một chiều: file -> Omni Inbound Message).

Cấu hình qua site_config (hạ tầng, không thêm trường vào DocType):
- `omni_intake_dir`: thư mục spool của miyano-intake (chứa `inbox/`). Vắng = tắt, không làm gì.
- `omni_intake_channels` (tùy chọn): {"Telephony": "<Omni Channel>", "Web Form": "..."}. Vắng thì dùng kênh DUY NHẤT
  đang bật của loại đó; không có hoặc có nhiều hơn một thì file giữ nguyên trong inbox (không đoán).
- `omni_intake_kinds` (tùy chọn): danh sách loại cần nạp, mặc định `["email"]`; các giá trị: `email` (thư mục `gmail`),
  `call` (`voice`), `web_form` (`web_form`), `messenger` (`messenger`). Thư mục không nằm trong danh sách thì không đọc, không đụng.
- Email không cần khai: thư vào Email Account có `email_id` trùng hộp thư của file (`source.account`).

Sau khi nạp, file chuyển sang `archive/` (cùng cấu trúc ngày), giống quy ước trong README của miyano-intake;
file hỏng sang `dead_crm/` kèm tệp `.loi.txt`; loại tin CRM chưa hỗ trợ (Messenger) sang `chua_nap/` để nạp sau.
Chạy lại an toàn: chống trùng bằng `dedupe_key` của cổng (cùng khóa với đường trực tiếp).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import frappe

from omni_crm.channels.email import adapter as email_adapter
from omni_crm.channels.registry import get_adapter
from omni_crm.ingest import gateway
from omni_crm.intake_files import mapper
from omni_crm.pipeline.retry import redact

KIND_DIRS = {"email": "gmail", "call": "voice", "web_form": "web_form", "messenger": "messenger"}
DEFAULT_KINDS = ("email",)
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PER_RUN = 200
RESULTS = ("moi", "trung", "bo_qua", "chua_ho_tro", "thieu_kenh", "loi")


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
	if mapped.channel_type == "Web Form":
		channel_doc = frappe.get_cached_doc("Omni Channel", channel)
		raw = json.dumps(mapped.body, ensure_ascii=False).encode()
		events = get_adapter("Web Form").parse(raw, {}, channel_doc)
		if not events:
			return "bo_qua"  # honeypot / mã biểu mẫu chưa cấu hình / không có dữ liệu
		names = [gateway.save_inbound(e) for e in events]
	else:
		mapped = mapper.with_channel(env, mapped, channel)
		if mapped.skip:
			return "bo_qua"
		names = [gateway.save_inbound(mapped.event)]
	return "moi" if any(names) else "trung"


def run(limit: int = MAX_PER_RUN) -> dict[str, int]:
	"""Quét `inbox/` và nạp tối đa `limit` file theo thứ tự đường dẫn (cũ trước). Gọi từ scheduler, lỗi từng file không dừng cả lô."""
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
	for path in files[:limit]:
		try:
			if path.stat().st_size > MAX_FILE_BYTES:
				raise ValueError("file lớn quá 2 MB")
			env = json.loads(path.read_text(encoding="utf-8"))
			result = ingest_envelope(env)
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			stats["loi"] += 1
			_move(root, path, "dead_crm", redact(f"{type(e).__name__}: {e}")[:500])
			frappe.log_error(
				title="Omni: lỗi nạp file intake",
				message=redact(f"{path.name}\n{frappe.get_traceback(with_context=False)[-1200:]}"),
			)
			frappe.db.commit()
			continue
		stats[result] += 1
		if result in ("moi", "trung", "bo_qua"):
			_move(root, path, "archive")
		elif result == "chua_ho_tro":
			_move(root, path, "chua_nap")
		# thieu_kenh: giữ nguyên trong inbox, nạp lại khi đã bật kênh
	return stats
