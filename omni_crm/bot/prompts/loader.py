"""Nạp câu lệnh hệ thống theo phiên bản: tệp `bot/prompts/<tên>_v<N>.md`, dòng đầu `version: v<N>`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frappe

PROMPT_DIR = Path(__file__).parent
VERSION_FILE_RE = re.compile(r"^(?P<name>[a-z_]+)_v(?P<n>\d+)\.md$")
VERSION_LINE_RE = re.compile(r"^version:\s*(?P<v>\S+)\s*$")
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


def active_version(name: str) -> str | None:
	"""Phiên bản mới nhất có tệp của câu lệnh `name` (vd `v1`); không có tệp thì None."""
	found = _versions(name)
	return _parse(name, found[max(found)]).version if found else None


def load_prompt(name: str, version: str | None = None) -> Prompt:
	"""Nạp câu lệnh `name` ở phiên bản `version` (mặc định mới nhất); không có thì FileNotFoundError."""
	found = _versions(name)
	if version is None:
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
	"""Ghi phiên bản mới nhất của từng câu lệnh vào Omni Settings (trường chỉ đọc); trả về {tên: phiên bản}."""
	written: dict[str, str] = {}
	for name, fieldname in SETTINGS_FIELD.items():
		version = active_version(name)
		if version and frappe.db.get_single_value("Omni Settings", fieldname) != version:
			frappe.db.set_single_value("Omni Settings", fieldname, version)
			written[name] = version
	return written
