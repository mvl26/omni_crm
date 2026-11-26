"""Các DÒNG HÀNG của một yêu cầu báo giá vật tư (thử nghiệm 25/11, lỗ hổng 4).

Trước đây bot giữ hai chuỗi rời ("mặt hàng", "số lượng"); khách thêm / bỏ / sửa một mặt hàng thì hai chuỗi lệch nhau
("bỏ cồn, bơm tiêm 1 triệu cái" → tên hàng bỏ cồn mà số lượng vẫn ghi cồn 50 chai). Ở đây mỗi mặt hàng là một dòng
{ten, sl, hang, quy_cach}; hai chuỗi cũ được DỰNG LẠI từ các dòng nên luôn khớp. Mọi hàm đều thuần.
"""

from __future__ import annotations

import re
import unicodedata

_SPLIT = re.compile(r"\s*[;\n]\s*|\s*,\s*(?!\d)")
_PAIR = re.compile(r"^(?P<ten>[^:]{1,120}):\s*(?P<sl>.+)$")
MAX_LINES = 20


def _fold(text: object) -> str:
	raw = unicodedata.normalize("NFD", str(text or "").lower().replace("đ", "d"))
	return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in raw if unicodedata.category(c) != "Mn")))


def same(a: object, b: object) -> bool:
	"""Hai tên chỉ cùng một mặt hàng: trùng nhau, hoặc tên này nằm trọn (theo từ) trong tên kia."""
	x, y = _fold(a), _fold(b)
	if not x or not y:
		return False
	short, long = (x, y) if len(x) <= len(y) else (y, x)
	return short == long or f" {short} " in f" {long} "


def qty_of(good: dict) -> str:
	"""Số lượng của một mặt hàng theo bản đọc hiểu: số + đơn vị, không có thì nguyên văn ("1 triệu cái")."""
	qty = good.get("so_luong")
	if qty is not None and not isinstance(qty, bool) and isinstance(qty, int | float):
		return f"{int(qty) if float(qty).is_integer() else qty} {good.get('don_vi') or ''}".strip()
	return " ".join(str(good.get("so_luong_chu") or "").split())


UNITS = "hộp|cái|chiếc|thùng|bộ|gói|lọ|chai|túi|cuộn|đôi|kg|lít|tấm|vỉ|ống|bịch|cây|tuýp|kiện|can|bao|viên|tờ|tập|cuốn|hop|cai|chiec|thung|goi|doi|tui|cuon"
_UNIT_QTY = re.compile(rf"\d[\d.,]*\s*(?:(?:nghìn|ngàn|triệu|tỷ|chục|trăm)\s*)?(?:{UNITS})(?![\wà-ỹ])", re.I)
UNIT_RE = _UNIT_QTY
_BARE_QTY = re.compile(r"(?<![\w.,])\d[\d.,]*(?:\s*(?:nghìn|ngàn|triệu|tỷ|chục|trăm))?(?![\wà-ỹ])", re.I)


def amount_in(piece: str) -> str:
	"""Phần SỐ LƯỢNG trong một mảnh câu ("bơm tiêm 5ml 500 cái" -> "500 cái", "bơm 500" -> "500"); "5ml", "22G" là quy
	cách (số dính liền chữ không phải đơn vị hàng), không phải số lượng. Không có → ""."""
	found = _UNIT_QTY.findall(piece)
	if found:
		return found[-1].strip()
	bare = _BARE_QTY.findall(piece)
	return bare[-1].strip() if bare else ""


def fill(lines: list[dict], quantity: object) -> list[dict]:
	"""Điền câu trả lời số lượng của khách vào các dòng CHƯA có số lượng: theo tên nếu khách nhắc tên ("cồn 50 chai"),
	theo thứ tự nếu số mảnh bằng số dòng còn thiếu ("50 chai, 1000 cái"), còn lại là số lượng chung của các dòng thiếu."""
	out = [dict(line) for line in lines or []]
	loose = []
	for amount in [a.strip() for a in re.split(r"\s*[;\n]\s*", str(quantity or "")) if a.strip()]:
		m = _PAIR.match(amount)
		target = next((line for line in out if m and same(line["ten"], m["ten"])), None)
		if target is not None:
			target["sl"] = m["sl"].strip()
		else:
			loose.append(amount)
	if not loose:
		return out
	pieces = [p.strip() for chunk in loose for p in re.split(r"\s*,\s*(?!\d{3}(?!\d))|\s+và\s+", chunk) if p.strip()]
	rest = []
	for piece in pieces:  # "cồn 50 chai": mảnh có nhắc tên một dòng thì là số lượng của dòng đó
		words = f" {_fold(piece)} "
		target = next((x for x in out if not x.get("sl") and (f" {_fold(x['ten'])} " in words or f" {_fold(x['ten']).split()[0]} " in words)), None)
		amount = amount_in(piece)
		if target is not None and amount:
			target["sl"] = amount
		else:
			rest.append(piece)
	empty = [x for x in out if not x.get("sl")]
	if rest and len(rest) == len(empty) and len(pieces) > 1 and all(amount_in(r) for r in rest):
		for line, piece in zip(empty, rest):  # "50 chai, 1000 cái" cho hai mặt hàng: khách trả lời theo đúng thứ tự đã nêu
			line["sl"] = piece
	elif rest:  # số lượng không gắn tên ("100 hộp", "mỗi loại 100 hộp", "chưa biết"): là của mọi dòng còn thiếu
		for line in empty:
			line["sl"] = ", ".join(rest)
	return out


def seed(items: object, quantity: object) -> list[dict]:
	"""Các dòng dựng từ hai chuỗi đã ghi ("găng tay, bơm tiêm" + "găng tay: 100 hộp; bơm tiêm: 50 cái")."""
	names = [n.strip() for n in _SPLIT.split(str(items or "")) if n.strip()]
	return fill([{"ten": n} for n in names[:MAX_LINES]], quantity)


def missing(lines: list[dict]) -> list[str]:
	"""Tên các dòng chưa có số lượng."""
	return [line["ten"] for line in lines or [] if not line.get("sl")]


def merge(lines: list[dict], goods: list[dict], removed: list) -> tuple[list[dict], bool]:
	"""(các dòng mới, có đổi không): bỏ các tên trong `removed`, rồi thêm / cập nhật theo `goods` của bản đọc hiểu."""
	out = [dict(line) for line in lines or []]
	changed = False
	for name in removed or []:
		kept = [line for line in out if not same(line.get("ten"), name)]
		changed = changed or len(kept) != len(out)
		out = kept
	for good in goods or []:
		name = " ".join(str((good or {}).get("ten") or "").split())
		if not name or any(same(name, gone) for gone in removed or []):
			continue
		new = {"sl": qty_of(good), "hang": good.get("hang") or "", "quy_cach": good.get("quy_cach") or ""}
		line = next((x for x in out if same(x.get("ten"), name)), None)
		if line is None:
			if len(out) >= MAX_LINES:
				continue
			out.append({"ten": name, **{k: v for k, v in new.items() if v}})
			changed = True
			continue
		for key, value in new.items():
			if value and line.get(key) != value:
				line[key] = value
				changed = True
	return out, changed


def items_text(lines: list[dict]) -> str:
	return ", ".join(" ".join(x for x in (line.get("ten"), line.get("quy_cach"), line.get("hang")) if x) for line in lines or [])


def quantity_text(lines: list[dict]) -> str:
	have = [line for line in lines or [] if line.get("sl")]
	if not have:
		return ""
	if len(lines) == 1:
		return have[0]["sl"]
	if len(have) == len(lines) and len({line["sl"] for line in have}) == 1 and ":" not in have[0]["sl"]:
		return have[0]["sl"]  # một số lượng chung cho mọi dòng ("mỗi loại 100 hộp", "100 hộp"): giữ nguyên lời khách
	return "; ".join(f"{line['ten']}: {line['sl']}" for line in have)

