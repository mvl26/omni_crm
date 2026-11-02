"""Nạp câu lệnh hệ thống theo phiên bản: tệp `bot/prompts/<tên>_v<N>.md`, dòng đầu `version: v<N>`.

V2-01: đường chạy thật chỉ nạp phiên bản được GHIM (Omni Settings hoặc `PINNED_DEFAULTS`), tệp mới thả vào thư mục không tự có hiệu lực."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frappe

PROMPT_DIR = Path(__file__).parent
VERSION_FILE_RE = re.compile(r"^(?P<name>[a-z_]+)_v(?P<n>\d+)\.md$")
VERSION_LINE_RE = re.compile(r"^version:\s*(?P<v>\S+)\s*$")
# Phiên bản câu lệnh dùng cho đường chạy thật khi Omni Settings chưa ghim. Đổi sang bản mới chỉ qua Omni Settings
# (sau khi bộ đề đạt cổng `doi_cau_lenh`) hoặc sửa hằng này trong một commit có duyệt. Giám khảo không có trường Settings.
PINNED_DEFAULTS = {"analyze": "v1", "reply": "v1", "judge": "v1"}
LATEST = "latest"  # giá trị `version` đặc biệt của load_prompt: bản mới nhất (bộ đề so sánh hai bản)
SETTINGS_FIELD = {
	"analyze": "analyze_prompt_version",
	"reply": "reply_prompt_version",
	"summarize": "summarize_prompt_version",
}


@dataclass(frozen=True)
class Prompt:
	"""Một câu lệnh đã nạp: tên, phiên bản (`v1`), nội dung (không gồm dòng version)."""

	name: str
	version: str
	text: str


def _versions(name: str) -> dict[int, Path]:
	out: dict[int, Path] = {}
	for path in PROMPT_DIR.glob(f"{name}_v*.md"):
		m = VERSION_FILE_RE.match(path.name)
		if m and m["name"] == name:
			out[int(m["n"])] = path
	return out


def _parse(name: str, path: Path) -> Prompt:
	lines = path.read_text(encoding="utf-8").splitlines()
	m = VERSION_LINE_RE.match(lines[0]) if lines else None
	if not m:
		raise ValueError(f"{path.name}: thiếu dòng đầu 'version: …'")
	return Prompt(name, m["v"], "\n".join(lines[1:]).strip())


def latest_version(name: str) -> str | None:
	"""Phiên bản có số lớn nhất trong thư mục câu lệnh (CHỈ để so sánh/tham khảo, không dùng cho đường chạy thật)."""
	found = _versions(name)
	return _parse(name, found[max(found)]).version if found else None


def pinned_version(name: str) -> str | None:
	"""Phiên bản được GHIM của câu lệnh `name` (V2-01, ĐQ-6): Omni Settings nếu có giá trị, không thì hằng `PINNED_DEFAULTS`.

	Tên không có ghim (không nằm trong Settings lẫn hằng) trả None. Không bao giờ tự chọn số cao nhất.
	"""
	value = None
	field = SETTINGS_FIELD.get(name)
	if field:
		try:
			value = (frappe.db.get_single_value("Omni Settings", field) or "").strip()
		except Exception:  # chưa có bảng/trường (cài mới, migrate dở): rơi về hằng
			value = None
	if value == LATEST:  # `latest` chỉ dành cho bộ đề so sánh, không bao giờ là bản ghim
		value = None
	return value or PINNED_DEFAULTS.get(name)


def active_version(name: str) -> str | None:
	"""Phiên bản đang chạy thật = phiên bản ghim; câu lệnh không có ghim thì None."""
	return pinned_version(name)


def load_prompt(name: str, version: str | None = None) -> Prompt:
	"""Nạp câu lệnh `name`: `version` mặc định là bản GHIM; `LATEST` nạp bản mới nhất (so sánh). Thiếu tệp -> FileNotFoundError."""
	found = _versions(name)
	if version is None:
		version = pinned_version(name)
		if version is None:
			raise FileNotFoundError(f"Câu lệnh {name} chưa được ghim phiên bản")
	if version == LATEST:
		if not found:
			raise FileNotFoundError(f"Không có câu lệnh {name}")
		return _parse(name, found[max(found)])
	m = re.fullmatch(r"v(\d+)", version)
	if not m or int(m[1]) not in found:
		raise FileNotFoundError(f"Không có câu lệnh {name} phiên bản {version}")
	return _parse(name, found[int(m[1])])


def render(prompt: Prompt, **variables: str) -> str:
	"""Thay {biến} đã biết trong nội dung câu lệnh; ký tự { } khác giữ nguyên."""
	text = prompt.text
	for key, value in variables.items():
		text = text.replace("{" + key + "}", str(value))
	return text


def sync_settings() -> dict[str, str]:
	"""Điền phiên bản ghim mặc định vào Omni Settings khi trường còn TRỐNG; KHÔNG ghi đè bản đã ghim. Trả {tên: phiên bản}."""
	written: dict[str, str] = {}
	for name, fieldname in SETTINGS_FIELD.items():
		default = PINNED_DEFAULTS.get(name)
		if not default or frappe.db.get_single_value("Omni Settings", fieldname):
			continue
		if int(default[1:]) in _versions(name):
			frappe.db.set_single_value("Omni Settings", fieldname, default)
			written[name] = default
	return written
