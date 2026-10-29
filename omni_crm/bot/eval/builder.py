"""Dựng ca đề từ hội thoại thật đã có sale trả lời (Đợt 1), CHE thông tin cá nhân (prompt D2-08 bước 3).

Chỉ chạy khi Q11 đã cho phép (site_config `omni_ai_allow_customer_data`). Đầu ra `eval/cases/real-<ngày>.jsonl`
chứa GỢI Ý: `reference_answer` = câu sale (đã che), `expected_action` đoán theo chốt chặn, `reviewed = false`.
NGƯỜI phụ trách phải sửa `expected_*` rồi đổi cờ `reviewed` sang true mới nhập được (importer từ chối ca chưa duyệt).

Quy tắc che (`RULES`, ghi vào tệp `real-<ngày>.quy-tac-che.md` cạnh tệp ca):
- SĐT -> số giả 0900 000 NNN; email -> khachN@example.com; dãy >= 9 chữ số khác -> 000000000; URL -> example.com.
- Tên người (tên Lead, tên danh tính kênh, và cụm sau "em là / tên là ...") -> "Khách A/B/C" nhất quán trong một ca.
- Tên đơn vị (tổ chức của Lead, cụm "Bệnh viện/Công ty/Phòng khám ... <Tên>") -> "Đơn vị A/B/C".
- Mỗi ca dựng lại bảng thay thế riêng; sau khi che quét lại: còn dữ liệu nguồn thì bỏ cả ca.
Giới hạn: tên người tự do trong câu không theo mẫu trên có thể lọt; vì vậy người duyệt PHẢI đọc từng ca.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, nowdate

from omni_crm.bot import ai_client, guards, knowledge
from omni_crm.bot import replay as replay_mod
from omni_crm.bot.context import PHONE_BARE, PHONE_VN
from omni_crm.crm_bridge import timeline
from omni_crm.identity.extract import _EMAIL_RE, _PHONE_RE, extract_contacts
from omni_crm.identity.normalize import normalize_phone

MAX_TURNS = 10
MIN_TOKEN = (
	4  # token của tên nhiều từ ngắn hơn mức này không bị che riêng (tránh che "Văn", "Thị" trong câu thường)
)
FAKE_PHONE = "0900 000 {n:03d}"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
RULES = (
	"SĐT -> 0900 000 NNN (nhất quán trong ca); email -> khachN@example.com; dãy >= 9 chữ số -> 000000000; URL -> example.com",
	"Tên người (Lead, danh tính kênh, cụm sau 'em là/tên là') -> Khách A/B/C; tên đơn vị -> Đơn vị A/B/C",
	"Sau khi che, quét lại dữ liệu nguồn; còn sót thì bỏ cả ca",
	"Giới hạn: tên người tự do ngoài các mẫu trên có thể lọt, nên người duyệt phải đọc từng ca",
)
LONG_DIGITS = re.compile(r"(?<!\d)\d(?:[\s.\-]?\d){8,}(?!\d)")
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
UPPER = "A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ"
CAP = rf"[{UPPER}]\w*"
INTRO = re.compile(
	rf"(?i:\b(?:em|mình|tôi|anh|chị)\s+(?:tên\s+)?là|\btên(?:\s+(?:em|mình|tôi))?\s+là)\s+((?:{CAP}\s?){{1,4}})"
)
OBFUSCATED = re.compile(r"\(\s*at\s*\)|\[\s*at\s*\]|\(\s*dot\s*\)|\[\s*dot\s*\]", re.IGNORECASE)
ORG = re.compile(
	rf"\b(?i:Bệnh viện|BV|Công ty|Cty|Phòng khám|Nhà thuốc|Trung tâm|Viện|Trường|Tập đoàn)\s+(?:{CAP}\s?){{1,5}}"
)


def _fold_with_map(text: str) -> tuple[str, list[int]]:
	"""Bỏ dấu + chữ thường, kèm bảng ánh xạ vị trí ký tự đã bỏ dấu về vị trí trong chuỗi gốc."""
	out, idx = [], []
	for i, ch in enumerate(text):
		base = ch.replace("đ", "d").replace("Đ", "D")
		for c in unicodedata.normalize("NFD", base):
			if unicodedata.category(c) != "Mn":
				out.append(c.lower())
				idx.append(i)
	return "".join(out), idx


def replace_word(text: str, needle: str, fake: str) -> str:
	"""Thay mọi lần `needle` xuất hiện như một từ/cụm từ (không phân biệt hoa thường và dấu) bằng `fake`."""
	folded_needle, _m = _fold_with_map(needle.strip())
	if len(folded_needle) < 2:
		return text
	pattern = re.compile(rf"(?<!\w){re.escape(folded_needle)}(?!\w)")
	while True:
		folded, idx = _fold_with_map(text)
		m = pattern.search(folded)
		if not m:
			return text
		text = text[: idx[m.start()]] + fake + text[idx[m.end() - 1] + 1 :]


class Masker:
	"""Bảng thay thế riêng cho MỘT ca: cùng giá trị gốc luôn ra cùng giá trị giả trong ca."""

	def __init__(self, names: list[str], orgs: list[str], phones: list[str], emails: list[str]):
		self.names, self.orgs = [n for n in names if n and n.strip()], [o for o in orgs if o and o.strip()]
		self.source_phones = {p for p in (normalize_phone(x) for x in phones) if p}
		self.source_emails = {e.lower() for e in emails if e}
		self.phone_map: dict[str, str] = {}
		self.email_map: dict[str, str] = {}
		self.person_map: dict[str, str] = {}
		self.org_map: dict[str, str] = {}

	def _person(self, name: str) -> str:
		key = " ".join(name.lower().split())
		self.person_map.setdefault(key, f"Khách {LETTERS[len(self.person_map) % 26]}")
		return self.person_map[key]

	def _org(self, name: str) -> str:
		key = " ".join(name.lower().split())
		self.org_map.setdefault(key, f"Đơn vị {LETTERS[len(self.org_map) % 26]}")
		return self.org_map[key]

	def _phone(self, m: re.Match) -> str:
		"""Thay bằng dấu tạm (số giả chỉ được đặt vào cuối để các bước che sau không đụng tới)."""
		key = normalize_phone(m.group(0)) or re.sub(r"\D", "", m.group(0))
		self.phone_map.setdefault(key, FAKE_PHONE.format(n=len(self.phone_map) + 1))
		return f"\ue000{list(self.phone_map).index(key)}\ue001"

	def _email(self, m: re.Match) -> str:
		key = m.group(0).lower()
		self.email_map.setdefault(key, f"khach{len(self.email_map) + 1}@example.com")
		return self.email_map[key]

	def mask(self, text: str) -> str:
		"""Che email, SĐT, dãy số dài, URL, tên đơn vị rồi tên người trong một đoạn văn."""
		text = _EMAIL_RE.sub(self._email, text)
		text = URL.sub("https://example.com/lien-ket", text)
		for pattern in (PHONE_VN, PHONE_BARE, _PHONE_RE):
			text = pattern.sub(self._phone, text)
		text = LONG_DIGITS.sub("000000000", text)
		for org in sorted(self.orgs, key=len, reverse=True):
			text = replace_word(text, org, self._org(org))
		text = ORG.sub(lambda m: self._org(m.group(0)), text)
		if self.names:
			fake = self._person(self.names[0])  # mọi tên của Lead/danh tính là cùng một người
			for name in sorted(self.names, key=len, reverse=True):
				text = replace_word(text, name, fake)
				parts = name.split()
				if len(parts) > 1:
					for part in parts:
						if len(part) >= MIN_TOKEN:
							text = replace_word(text, part, fake)
		text = INTRO.sub(self._intro, text)
		fakes = list(self.phone_map.values())
		return re.sub("\ue000(\\d+)\ue001", lambda m: fakes[int(m.group(1))], text)

	def _intro(self, m: re.Match) -> str:
		"""Tên người đứng sau "em là/tên là" (chưa nằm trong danh sách đã biết) -> Khách X."""
		name = m.group(1).strip()
		if name.startswith(("Khách ", "Đơn vị")):  # đã che ở bước trước
			return m.group(0)
		return m.group(0).replace(m.group(1), self._person(name) + " ")

	def leaks(self, text: str) -> list[str]:
		"""Dữ liệu nguồn còn sót trong văn bản đã che (SĐT, email, tên): danh sách loại sót (không chứa giá trị)."""
		found = extract_contacts(text)
		out = []
		if self.source_phones & set(found["phones"]):
			out.append("sdt")
		if self.source_emails & {e.lower() for e in found["emails"]}:
			out.append("email")
		folded, _m = _fold_with_map(text)
		for name in [*self.names, *self.orgs]:
			f, _m2 = _fold_with_map(name)
			if len(f) >= 2 and re.search(rf"(?<!\w){re.escape(f)}(?!\w)", folded):
				out.append("ten")
				break
		return out


def _lead_pii(lead: str) -> dict[str, list[str]]:
	"""Tên, đơn vị, SĐT, email thật gắn với Lead (từ Lead và danh định kênh) để che."""
	row = frappe.db.get_value(
		"CRM Lead",
		lead,
		["lead_name", "first_name", "last_name", "organization", "mobile_no", "phone", "email"],
		as_dict=True,
	)
	names, phones, emails = [], [], []
	if row:
		names += [
			row.lead_name,
			row.first_name,
			row.last_name,
			f"{row.first_name or ''} {row.last_name or ''}",
		]
		phones += [row.mobile_no, row.phone]
		emails += [row.email]
	idents = frappe.get_all(
		"Omni Identity", filters={"lead": lead}, fields=["display_name", "phone_e164", "email_norm"]
	)
	for i in idents:
		names.append(i.display_name)
		phones.append(i.phone_e164)
		emails.append(i.email_norm)
	return {
		"names": [n.strip() for n in names if n and n.strip()],
		"orgs": [row.organization] if row and row.organization else [],
		"phones": [p for p in phones if p],
		"emails": [e for e in emails if e],
	}


def suggest_action(sale_text: str, all_kb_texts: list[str]) -> str:
	"""Gợi ý hành động kỳ vọng: Handoff nếu câu sale có tiền/thời hạn/cam kết/liên hệ ngoài kho hoặc nhắc tới giá."""
	if guards.sale_goes_beyond_kb(sale_text, all_kb_texts):
		return "Handoff"
	if re.search(guards.PRICE_WORD_ACCENTED, (sale_text or "").lower()):
		return "Handoff"
	return "Reply"


def build_case(item: dict, index: int, day: str, all_kb_texts: list[str]) -> tuple[dict | None, str | None]:
	"""Một ca (hoặc None + lý do bỏ) từ một lượt khách -> sale; đã che."""
	pii = _lead_pii(item["lead"])
	masker = Masker(pii["names"], pii["orgs"], pii["phones"], pii["emails"])
	turns = [
		t
		for t in timeline.conversation_turns(item["lead"], until=item["until"])
		if (t.get("text") or "").strip()
	][-MAX_TURNS:]
	conv = [
		{"role": "customer" if t["actor"] == "customer" else "sale", "text": masker.mask(t["text"])}
		for t in turns
	]
	while conv and conv[-1]["role"] != "customer":
		conv.pop()
	if not conv:
		return None, "khong_co_luot_khach"
	reference = masker.mask(item["sale_text"])
	texts = [t["text"] for t in conv] + [reference]
	if any(masker.leaks(t) for t in texts):
		return None, "con_du_lieu_nguon"
	if any(
		OBFUSCATED.search(t) for t in texts
	):  # email viết giấu (a (at) b (dot) com) mà bộ che không nhận ra
		return None, "email_viet_giau"
	case = {
		"case_id": f"REAL-{day.replace('-', '')}-{index:03d}",
		"source": "Real",
		"conversation": conv,
		"expected_fields": {},
		"expected_action": suggest_action(item["sale_text"], all_kb_texts),
		"expected_group": "",
		"reference_answer": reference,
		"critical": False,
		"active": True,
		"reviewed": False,
		"note": "Gợi ý tự động chưa duyệt: sửa expected_* (nhất là fields/group/critical) rồi đổi reviewed thành true",
	}
	return case, None


def build_from_real(
	since: str, until: str, max_cases: int = 200, out_dir: str = "eval/cases"
) -> dict[str, Any]:
	"""Dựng tệp `real-<ngày>.jsonl` (đã che, chờ người duyệt) từ lượt sale trả lời trong [since, until].

	Từ chối chạy khi Q11 chưa cho phép. Trả {"path", "cases", "dropped", "rules"}; ca bị bỏ chỉ ghi lý do.
	"""
	if not cint(frappe.conf.get(ai_client.ALLOW_DATA_CONF)):
		frappe.throw(_("Q11 chưa cho phép dùng dữ liệu khách thật (docs/12); chưa dựng ca từ dữ liệu thật"))
	day = nowdate()
	all_kb = [i["answer"] for i in knowledge.kb_items(active_only=False)]
	rows = replay_mod.collect(since, until, limit=max_cases)
	cases, dropped = [], {}
	for item in rows:
		case, why = build_case(item, len(cases) + 1, day, all_kb)
		if case:
			cases.append(case)
		else:
			dropped[why] = dropped.get(why, 0) + 1
	root = Path(frappe.get_app_path("omni_crm")).parent / out_dir
	root.mkdir(parents=True, exist_ok=True)
	path = root / f"real-{day}.jsonl"
	path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
	rules_path = root / f"real-{day}.quy-tac-che.md"
	rules_path.write_text(
		"# Quy tắc che thông tin cá nhân\n\n"
		+ "\n".join(f"- {r}" for r in RULES)
		+ f"\n\nSố ca: {len(cases)}; bỏ: {dropped}\n",
		encoding="utf-8",
	)
	return {"path": str(path), "cases": len(cases), "dropped": dropped, "rules": list(RULES)}
