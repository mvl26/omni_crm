"""Tiến trình theo dõi spool của miyano-intake: có file mới thì nạp ngay, không chờ cron mỗi phút.

Chạy dưới supervisor như một chương trình riêng (docs/16, mục "Nạp ngay"):

	bench --site <site> execute omni_crm.intake_files.watcher.run

Mỗi giây chỉ `stat` vài thư mục, không đọc file, không đụng DB: thư mục của hai ngày mới nhất trong `inbox/` và thư mục
kênh bên trong (chỉ các kênh được phép nạp). Intake ghi file bằng `os.replace` nên mtime thư mục kênh đổi ngay khi có
file. Thấy đổi thì `runner.kick()` đẩy một lượt nạp vào hàng đợi; việc nạp chạy trong worker như mọi job khác.

Thư có ngày gửi cũ (intake xếp theo `occurred_at` nên nằm ở thư mục ngày cũ) và mọi trường hợp sót khác vẫn được cron
mỗi phút nạp như trước. Lỗi trong vòng lặp chỉ in ra log của supervisor (không ghi DB: kết nối DB của tiến trình sống
lâu có thể đã bị đóng), rồi thử lại sau.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from omni_crm.intake_files import runner

INTERVAL = 1.0  # giây giữa hai lần xem
DAYS_WATCHED = 2
RETRY_SECONDS = 30  # chưa cấu hình spool hoặc vừa gặp lỗi: chờ rồi xem lại


def _digit_dirs(p: Path) -> list[Path]:
	"""Thư mục con tên toàn chữ số (năm/tháng/ngày), mới nhất trước."""
	try:
		return sorted(
			(c for c in p.iterdir() if c.name.isdigit() and c.is_dir()), key=lambda c: c.name, reverse=True
		)
	except OSError:
		return []


def latest_days(inbox: Path, n: int = DAYS_WATCHED) -> list[Path]:
	"""`n` thư mục ngày (inbox/YYYY/MM/DD) mới nhất, mới nhất trước."""
	days: list[Path] = []
	for y in _digit_dirs(inbox):
		for m in _digit_dirs(y):
			for d in _digit_dirs(m):
				days.append(d)
				if len(days) >= n:
					return days
	return days


def _mtime(p: Path) -> int | None:
	try:
		return p.stat().st_mtime_ns
	except OSError:
		return None


def signature(root: Path, channel_dirs: set[str]) -> tuple:
	"""Dấu vết rẻ của những chỗ intake ghi file mới; đổi khi có file mới hoặc sang ngày mới."""
	sig: list[tuple] = []
	for day in latest_days(root / "inbox"):
		sig.append((str(day), _mtime(day)))
		sig.extend((str(day / ch), _mtime(day / ch)) for ch in sorted(channel_dirs))
	return tuple(sig)


def _log(msg: str) -> None:
	print(f"[omni-intake-watch] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def run(interval: float = INTERVAL, max_loops: int | None = None) -> None:
	"""Vòng lặp chính. `max_loops` chỉ dùng trong test; tiến trình thật chạy tới khi supervisor dừng."""
	last: tuple | None = None
	loops = 0
	_log("bắt đầu theo dõi")
	while max_loops is None or loops < max_loops:
		loops += 1
		try:
			root = runner.spool_root()
			if root is None:
				time.sleep(RETRY_SECONDS)
				continue
			sig = signature(root, runner.enabled_dirs())
			if sig != last:
				runner.kick()  # lần đầu cũng đẩy một lượt: nạp các file đến trong lúc tiến trình tắt
				last = sig
		except Exception as e:
			_log(f"lỗi {type(e).__name__}: {str(e)[:200]}; thử lại sau {RETRY_SECONDS} giây")
			last = None  # xem lại từ đầu, chắc chắn đẩy một lượt khi hồi phục
			time.sleep(RETRY_SECONDS)
			continue
		time.sleep(interval)
