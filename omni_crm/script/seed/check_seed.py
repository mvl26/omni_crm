"""Kiểm dữ liệu khởi tạo kho kịch bản (chạy: python3 -I omni_crm/script/seed/check_seed.py)."""

import json
import re
import sys
import unicodedata
from pathlib import Path

SEED = Path(__file__).resolve().parent
ROOT = SEED.parents[2]
SCRIPT = ROOT / "docs" / "kich_ban_bot_miyano_v3.md"

STEPS = {"1", "2", "3", "4", "5", "C"}
SERVICES = {"", "VATTU", "SUPPLYCORE", "SPD"}
CHANNELS = {"Tất cả", "Zalo OA", "Messenger"}
STATUSES = {"Đang dùng", "Cần duyệt", "Tắt"}
KEYS = {"code", "title", "step", "service", "channel", "status", "text", "buttons", "action", "source_ref", "notes"}
CODE_RE = re.compile(r"^[A-Z0-9_]+$")
VAR_RE = re.compile(r"\{([a-z0-9_]+)\}")
# Biến đã đổi tên so với kịch bản (tên có "/" không dùng được) và URL thay bằng biến.
REVERSE = {
	"{hom_nay_hom_qua}": "{hom_nay/hom_qua}",
	"{tai_lieu_bao_gia}": "{tai_lieu/bao_gia}",
	"{chinh_sach_bao_mat}": "miyano.com.vn/chinh-sach-bao-mat",
}
# Node "Đang dùng" có câu suy ra theo quy tắc trong kịch bản (không nguyên văn từng chữ).
DERIVED = {"NL_TAILIEU_VUAROI": "4.3: thay 'Hôm qua' bằng 'Vừa rồi'"}
# Tham chiếu đặc biệt trong action (router tự giải).
SPECIAL_REFS = {"BAOGIA_<dich_vu>"}


def nfc(s: str) -> str:
	"""Chuẩn hoá NFC và gộp khoảng trắng."""
	return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip()


def corpus() -> str:
	"""Nội dung kịch bản đã bỏ dấu trích dẫn '>' và gộp khoảng trắng."""
	lines = [re.sub(r"^\s*>\s?", "", ln) for ln in SCRIPT.read_text(encoding="utf-8").splitlines()]
	return nfc(" \n ".join(lines))


def main() -> int:
	"""Chạy mọi kiểm tra, in lỗi, trả mã thoát."""
	errs = []
	nodes = json.loads((SEED / "nodes.json").read_text(encoding="utf-8"))
	codes = [x["code"] for x in nodes]
	codeset = set(codes)
	dup = sorted({c for c in codes if codes.count(c) > 1})
	if dup:
		errs.append(f"mã trùng: {dup}")
	docs = {name: (SEED / name).read_text(encoding="utf-8") for name in ("VARIABLES.md", "CAN_DUYET.md", "EMPTY_SLOTS.md", "CLASSIFY_CODES.md")}
	body = corpus()
	used_vars = set()
	over = []
	for x in nodes:
		c = x["code"]
		if set(x) != KEYS:
			errs.append(f"{c}: khoá sai {sorted(set(x) ^ KEYS)}")
		if not CODE_RE.match(c):
			errs.append(f"{c}: mã không phải A-Z0-9_")
		for f, allowed in (("step", STEPS), ("service", SERVICES), ("channel", CHANNELS), ("status", STATUSES)):
			if x[f] not in allowed:
				errs.append(f"{c}: {f}={x[f]!r} ngoài danh sách")
		if len(x["buttons"]) > 5:
			errs.append(f"{c}: {len(x['buttons'])} nút > 5")
		for b in x["buttons"]:
			lb = unicodedata.normalize("NFC", b["label"])
			if len(lb) > 20:
				over.append((c, lb, len(lb)))
			if b["target"] not in codeset:
				errs.append(f"{c}: nút '{lb}' trỏ tới mã không tồn tại {b['target']}")
			used_vars |= set(VAR_RE.findall(lb))
		for ref in re.findall(r"(?:then|goto):([A-Za-z0-9_<>]+)", x["action"]):
			if ref not in codeset and ref not in SPECIAL_REFS:
				errs.append(f"{c}: action trỏ tới mã không tồn tại {ref}")
		used_vars |= set(VAR_RE.findall(x["text"]))
		used_vars |= set(VAR_RE.findall(x["action"]))
		if not x["text"]:
			if x["status"] != "Tắt" and not x["action"]:
				errs.append(f"{c}: text trống nhưng không Tắt và không có action")
		elif x["status"] == "Đang dùng" and c not in DERIVED:
			t = x["text"]
			for k, v in REVERSE.items():
				t = t.replace(k, v)
			for line in t.split("\n"):
				if line.strip() and nfc(line) not in body:
					errs.append(f"{c}: câu không nguyên văn kịch bản: {line[:80]!r}")
		if x["status"] == "Cần duyệt" and f"`{c}`" not in docs["CAN_DUYET.md"]:
			errs.append(f"{c}: Cần duyệt nhưng thiếu trong CAN_DUYET.md")
		if x["status"] == "Tắt" and f"`{c}`" not in docs["EMPTY_SLOTS.md"]:
			errs.append(f"{c}: Tắt nhưng thiếu trong EMPTY_SLOTS.md")
	byc = {x["code"]: x for x in nodes}
	warns = []
	for x in nodes:
		c = x["code"]
		for f in ("code", "title", "action", "source_ref"):
			if len(x[f]) > 140:
				errs.append(f"{c}: {f} dài {len(x[f])} > 140 (Data)")
		for b in x["buttons"]:
			if len(b["target"]) > 140:
				errs.append(f"{c}: target dài > 140")
		if x["status"] != "Đang dùng":
			continue
		refs = [b["target"] for b in x["buttons"]] + re.findall(r"(?:then|goto):([A-Z0-9_]+)", x["action"])
		for t in refs:
			if t in byc and byc[t]["status"] != "Đang dùng":
				warns.append(f"{c} → {t} ({byc[t]['status']})")
	# Nhãn rút gọn (CAN_DUYET §3): nhãn gốc phải nằm trong notes.
	sec3 = docs["CAN_DUYET.md"].split("## 3.", 1)[1].split("\n## ", 1)[0]
	for row in sec3.splitlines():
		cells = [x.strip() for x in row.strip().strip("|").split("|")]
		if len(cells) != 3 or not cells[0].startswith("`"):
			continue
		orig = re.sub(r"\s*\(\d+.*$", "", cells[1])
		for c in re.findall(r"`([A-Z0-9_]+)`", cells[0]):
			if c not in byc:
				errs.append(f"CAN_DUYET §3: mã {c} không tồn tại")
			elif orig not in byc[c]["notes"]:
				errs.append(f"{c}: notes thiếu nhãn gốc '{orig}'")
	for w in warns:
		print("CẢNH BÁO nút/then từ node Đang dùng tới node chưa gửi được:", w)
	routing = sum(1 for x in nodes if not x["text"] and x["status"] == "Đang dùng")
	print(f"node điều hướng (text trống, Đang dùng): {routing}; node có câu: {sum(1 for x in nodes if x['text'])}")
	for v in sorted(used_vars):
		if f"{{{v}}}" not in docs["VARIABLES.md"]:
			errs.append(f"biến {{{v}}} chưa ghi trong VARIABLES.md")
	for lab in sorted({lb for _, lb, _ in over}):
		print(f"nhãn > 20 ký tự: {lab!r}")
	counts = {}
	for x in nodes:
		key = x["status"] if x["text"] else "điều hướng" if x["status"] == "Đang dùng" else x["status"] + " (trống)"
		counts.setdefault(x["step"], {}).setdefault(key, 0)
		counts[x["step"]][key] += 1
	print(f"{len(nodes)} node; biến dùng: {len(used_vars)}")
	for s in sorted(counts):
		print(f"  bước {s}: {sum(counts[s].values())} ({', '.join(f'{k} {v}' for k, v in sorted(counts[s].items()))})")
	errs += [f"nhãn > 20: {c} {lb!r} ({n})" for c, lb, n in over]
	for e in errs:
		print("LỖI", e)
	print("OK" if not errs else f"{len(errs)} lỗi")
	return 1 if errs else 0


if __name__ == "__main__":
	sys.exit(main())
