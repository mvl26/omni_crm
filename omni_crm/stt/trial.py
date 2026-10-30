"""Tính toán thuần của thử nghiệm chuyển giọng nói D3-03: WER, thời lượng, chi phí, đọc thư mục ghi âm, bảng đánh
dấu của người duyệt và quyết định theo ngưỡng. Không gọi mạng, không gọi AI, không ghi DB.

Ghi âm, bản chuẩn và văn bản thu được là dữ liệu khách: chỉ đọc/ghi ở thư mục NGOÀI repo (`assert_outside_repo`).
"""

from __future__ import annotations

import csv
import json
import mimetypes
import re
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".webm")
MIME_BY_SUFFIX = {
	".wav": "audio/wav",
	".mp3": "audio/mpeg",
	".m4a": "audio/mp4",
	".ogg": "audio/ogg",
	".opus": "audio/ogg",
	".flac": "audio/flac",
	".aac": "audio/aac",
	".webm": "audio/webm",
}
REVIEW_COLUMNS = ("recording", "provider", "field", "extracted_value", "correct")
RIGHT = frozenset({"đúng", "dung", "đ", "d", "1", "true", "yes", "x"})
WRONG = frozenset({"sai", "s", "0", "false", "no"})


@dataclass(frozen=True)
class WordErrors:
	"""Số lỗi từ: thay, xóa (bỏ sót), chèn (thừa) và tổng số từ của bản chuẩn."""

	substitutions: int
	deletions: int
	insertions: int
	ref_words: int

	@property
	def errors(self) -> int:
		"""Tổng số lỗi."""
		return self.substitutions + self.deletions + self.insertions


@dataclass(frozen=True)
class Thresholds:
	"""Ngưỡng quyết định D3-03 (mặc định theo prompt; người duyệt có thể đổi, ghi vào docs/12)."""

	wer_max: float = 0.25
	accuracy_min: float = 0.80
	min_references: int = 5  # số ghi âm có bản chuẩn tối thiểu để tin con số WER


@dataclass(frozen=True)
class Recording:
	"""Một ghi âm trong thư mục thử: đường dẫn, tên (không đuôi), mime, bản chuẩn (nếu có), cờ stereo/tổng hợp, meta."""

	path: Path
	name: str
	mime: str
	reference: str | None
	stereo: bool
	synthetic: bool
	meta: dict


# --- WER ---------------------------------------------------------------------------------------------------


def normalize_text(text: str | None) -> str:
	"""NFC, chữ thường, bỏ dấu câu/ký hiệu, gộp khoảng trắng. KHÔNG bỏ dấu thanh: sai dấu là sai từ."""
	if not text:
		return ""
	text = unicodedata.normalize("NFC", text).lower()
	text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text)
	return re.sub(r"\s+", " ", text).strip()


def word_errors(reference: str | None, hypothesis: str | None) -> WordErrors:
	"""Khoảng cách Levenshtein theo từ giữa bản chuẩn và bản máy, tách thành thay/xóa/chèn."""
	ref, hyp = normalize_text(reference).split(), normalize_text(hypothesis).split()
	# dp[i][j] = (tổng, thay, xóa, chèn) tối ưu cho ref[:i], hyp[:j]
	prev = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
	for i in range(1, len(ref) + 1):
		cur = [(i, 0, i, 0)]
		for j in range(1, len(hyp) + 1):
			if ref[i - 1] == hyp[j - 1]:
				cur.append(prev[j - 1])
				continue
			s, d, n = prev[j - 1], prev[j], cur[j - 1]
			best = min(
				(s[0] + 1, s[1] + 1, s[2], s[3]),
				(d[0] + 1, d[1], d[2] + 1, d[3]),
				(n[0] + 1, n[1], n[2], n[3] + 1),
			)
			cur.append(best)
		prev = cur
	_, sub, dele, ins = prev[len(hyp)]
	return WordErrors(sub, dele, ins, len(ref))


def wer(reference: str | None, hypothesis: str | None) -> float | None:
	"""Tỉ lệ lỗi từ (có thể > 1); None khi bản chuẩn không có từ nào (không đo được)."""
	e = word_errors(reference, hypothesis)
	return e.errors / e.ref_words if e.ref_words else None


def pool_wer(items: list[WordErrors]) -> float | None:
	"""WER gộp: tổng lỗi / tổng số từ chuẩn của nhiều ghi âm (ghi âm dài có trọng số lớn hơn); None nếu rỗng."""
	words = sum(i.ref_words for i in items)
	return sum(i.errors for i in items) / words if words else None


# --- Thời lượng, chi phí ------------------------------------------------------------------------------------


def audio_seconds(path: Path, meta: dict) -> float | None:
	"""Thời lượng (giây): từ tệp WAV, hoặc `duration_seconds` trong tệp meta bên cạnh; None nếu không biết."""
	if path.suffix.lower() == ".wav":
		try:
			with wave.open(str(path), "rb") as w:
				return w.getnframes() / float(w.getframerate())
		except (wave.Error, EOFError, OSError, ZeroDivisionError):
			pass
	value = meta.get("duration_seconds")
	return float(value) if isinstance(value, int | float) and value > 0 else None


def load_pricing(path: str | None) -> dict[str, dict]:
	"""Đọc bảng giá do người dùng cấp: {"Nhà cung cấp": {"per_minute": số, "currency": "USD"}}; sai định dạng -> ValueError."""
	if not path:
		return {}
	data = json.loads(Path(path).read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise ValueError("Bảng giá phải là một đối tượng JSON {nhà cung cấp: {per_minute, currency}}")
	out: dict[str, dict] = {}
	for name, row in data.items():
		per_minute = row.get("per_minute") if isinstance(row, dict) else None
		if isinstance(per_minute, bool) or not isinstance(per_minute, int | float) or per_minute < 0:
			raise ValueError(f"Bảng giá của {name}: per_minute phải là số không âm")
		out[name] = {"per_minute": float(per_minute), "currency": str(row.get("currency") or "")}
	return out


def estimate_cost(pricing: dict, provider: str, seconds: float | None) -> dict | None:
	"""{amount, currency} = phút x giá/phút; None nếu thiếu giá hoặc thời lượng (không bịa số)."""
	row = pricing.get(provider)
	if not row or seconds is None:
		return None
	return {"amount": seconds / 60.0 * row["per_minute"], "currency": row["currency"]}


# --- Thư mục ghi âm -----------------------------------------------------------------------------------------


def assert_outside_repo(path: str | Path) -> Path:
	"""Trả đường dẫn đã resolve; ValueError nếu nằm trong repo omni_crm (ghi âm/văn bản khách không được vào repo)."""
	p = Path(path).expanduser().resolve()
	if p == REPO_ROOT or REPO_ROOT in p.parents:
		raise ValueError("Thư mục ghi âm và kết quả phải nằm NGOÀI repo (dữ liệu khách không được commit)")
	return p


def discover_recordings(directory: str | Path) -> list[Recording]:
	"""Ghi âm trong thư mục (sắp theo tên). Bản chuẩn: `<tên>.txt`; meta: `<tên>.json` {stereo, synthetic, duration_seconds}."""
	root = Path(directory)
	out: list[Recording] = []
	for path in sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES):
		ref_file, meta_file = path.with_suffix(".txt"), path.with_suffix(".json")
		meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
		reference = ref_file.read_text(encoding="utf-8").strip() if ref_file.exists() else None
		mime = MIME_BY_SUFFIX.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "audio/wav"
		out.append(
			Recording(
				path=path,
				name=path.stem,
				mime=mime,
				reference=reference or None,
				stereo=bool(meta.get("stereo")),
				synthetic=bool(meta.get("synthetic")),
				meta=meta,
			)
		)
	return out


# --- Bảng đánh dấu của người duyệt --------------------------------------------------------------------------


def safe_cell(value: Any) -> Any:
	"""Chống chèn công thức khi mở CSV bằng bảng tính: ô chuỗi bắt đầu bằng = + - @ thêm dấu ' đứng đầu."""
	return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") else value


def review_rows(recording: str, provider: str, accepted: dict) -> list[dict]:
	"""Mỗi trường bot trích xuất được thành một dòng để người duyệt điền cột `correct` (Đúng/Sai)."""
	rows = []
	for field_name, value in accepted.items():
		if isinstance(value, list):
			value = ", ".join(str(v) for v in value)
		rows.append(
			{
				"recording": recording,
				"provider": provider,
				"field": field_name,
				"extracted_value": safe_cell(str(value)),
				"correct": "",
			}
		)
	return rows


def write_review_csv(path: Path, rows: list[dict]) -> None:
	"""Ghi bảng đánh dấu (UTF-8 có BOM để bảng tính đọc đúng tiếng Việt)."""
	with open(path, "w", newline="", encoding="utf-8-sig") as f:
		w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
		w.writeheader()
		w.writerows(rows)


def summarize_review(path: str) -> dict[str, dict]:
	"""Theo nhà cung cấp: số đúng, sai, chưa đánh dấu và tỉ lệ đúng = đúng/(đúng+sai); None nếu chưa có dòng nào đánh dấu."""
	out: dict[str, dict] = {}
	with open(path, newline="", encoding="utf-8-sig") as f:
		for row in csv.DictReader(f):
			s = out.setdefault(row["provider"], {"correct": 0, "wrong": 0, "unmarked": 0})
			mark = unicodedata.normalize("NFC", (row.get("correct") or "").strip().lower())
			if mark in RIGHT:
				s["correct"] += 1
			elif mark in WRONG:
				s["wrong"] += 1
			else:
				s["unmarked"] += 1
	for s in out.values():
		marked = s["correct"] + s["wrong"]
		s["accuracy"] = s["correct"] / marked if marked else None
	return out


# --- Quyết định theo ngưỡng ---------------------------------------------------------------------------------


def decide(
	wer_value: float | None, accuracy: float | None, references: int, thresholds: Thresholds | None = None
) -> dict:
	"""Một nhà cung cấp: ĐẠT / KHÔNG ĐẠT / CHƯA ĐO. Thiếu WER, thiếu đánh dấu hoặc ít bản chuẩn hơn tối thiểu = CHƯA ĐO."""
	th = thresholds or Thresholds()
	missing = []
	if wer_value is None or references < th.min_references:
		missing.append(f"WER (cần >= {th.min_references} ghi âm có bản chuẩn, hiện {references})")
	if accuracy is None:
		missing.append("tỉ lệ trường trích xuất đúng (người duyệt chưa đánh dấu)")
	if missing:
		return {"status": "CHƯA ĐO", "reasons": missing}
	reasons = []
	if wer_value > th.wer_max:
		reasons.append(f"WER {wer_value:.1%} > {th.wer_max:.0%}")
	if accuracy < th.accuracy_min:
		reasons.append(f"trường đúng {accuracy:.1%} < {th.accuracy_min:.0%}")
	return {"status": "KHÔNG ĐẠT" if reasons else "ĐẠT", "reasons": reasons}


def overall(per_provider: dict[str, dict]) -> dict:
	"""Nhánh theo ngưỡng: có nhà cung cấp ĐẠT -> D3-04; mọi nhà cung cấp đều đã đo và không đạt -> HOÃN D3-04; còn lại CHƯA ĐO."""
	statuses = {d["status"] for d in per_provider.values()}
	if "ĐẠT" in statuses:
		branch = "D3-04"
	elif per_provider and statuses == {"KHÔNG ĐẠT"}:
		branch = "HOÃN D3-04"
	else:
		branch = "CHƯA ĐO"
	return {"branch": branch, "per_provider": per_provider}
