"""Bot trích xuất thông tin vào Lead (docs/08 mục 3).

Kết quả của AI chỉ là DỮ LIỆU đề xuất: mọi trường phải có căn cứ trong lời khách (kiểm bằng code) rồi mới được
ghi, và không bao giờ đè lên trường sale đã sửa tay. Lỗi AI không làm chặn hay mất lượt xử lý tin.
"""

from __future__ import annotations

import copy
import json
import re
from difflib import SequenceMatcher
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm import flags
from omni_crm.bot import ai_client, intents
from omni_crm.bot.context import _line, build_conversation, escape_tags, mask_pii, normalize_text
from omni_crm.bot.prompts.loader import load_prompt, render
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.identity import duplicates, resolver
from omni_crm.identity.extract import extract_contacts
from omni_crm.identity.normalize import is_placeholder_name, normalize_email, normalize_phone, strip_accents
from omni_crm.pipeline.retry import redact
from omni_crm.prospect.normalize import InvalidTaxId, normalize_tax_id
from omni_crm.queue import queue_for

LEAD = "CRM Lead"
INBOUND = "Omni Inbound Message"
BOT_RUN = "Omni Bot Run"
QUEUE = queue_for()  # V21-01: việc chat/bot/tổng đài/nhập danh sách luôn ở hàng đợi mặc định
JOB = "omni_crm.bot.analyze.analyze_batch"
JOB_TIMEOUT = 600  # 1 lần gọi AI có thể tới 3 x 60 giây + chờ thử lại
EVIDENCE_THRESHOLD = 0.85  # docs/08 mục 3 bước 1
MIN_EVIDENCE_CHARS = 6
MAX_CONTACTS = 3  # SĐT/email chấp nhận tối đa mỗi loại trong một lượt
MAX_CANDIDATES = 3  # Duplicate Candidate tạo tối đa trong một lượt
VALUE_MATCH_RATIO = 0.8  # tỉ lệ từ khóa của giá trị phải có trong trích dẫn
MAX_CHARS = 6000  # như build_conversation
MAX_TURNS = 20
UNMASK_CONF = "omni_ai_unmask_pii"  # site_config: chỉ bật sau khi chủ dự án chốt Q11 cho phép gửi SĐT/email
ANALYZE_EVENTS = ("message", "user_info")  # cuộc gọi có transcript: Đợt 3 (D3-03), chưa có dữ liệu
SCORE_MEDIUM_ONE = 60  # như resolver: một Lead trùng SĐT/email trích từ nội dung
SCORE_MEDIUM_MANY = 50
URGENCIES = ("Thấp", "Trung bình", "Cao")
INTENTS = ("Tư vấn", "Hỏi giá", "Đặt hàng", "Khiếu nại", "Khác")
FIELD_LIMITS = {"need": 300, "products": 500, "quantity": 140, "timeframe": 140, "summary": 400}
# Trường tín hiệu -> trường Lead (docs/02 mục 15)
LEAD_FIELDS = {
	"need": "omni_need",
	"products": "omni_products",
	"quantity": "omni_quantity",
	"timeframe": "omni_timeframe",
	"urgency": "omni_urgency",
	"intent": "omni_intent",
	# V2-06 (docs/14 mục 7): trường B2B, chỉ có khi câu lệnh/schema v2 đòi bot trích
	"tax_id": "omni_tax_id_stated",
	"hospitals_mentioned": "omni_hospitals_mentioned",
	"current_pain": "omni_current_pain",
	"decision_role": "omni_decision_role",
}
DECISION_ROLES = ("Người quyết định", "Phụ trách đấu thầu", "Kế toán – công nợ", "Mua hàng", "Khác")  # noqa: RUF001
SCHEMA_V1, SCHEMA_V3 = "analyze_schema_v1", "analyze_schema_v3"  # v3 = V21-06 (ý định B2B, bỏ trường bán lẻ)
SCHEMA_VERSION = (
	SCHEMA_V3  # phiên bản schema mới nhất; ghi vào kết quả phân tích (output_json) và báo cáo bộ đề
)

TOOL_V1: dict[str, Any] = {
	"name": "record_lead_signals",
	"description": "Ghi lại thông tin về khách rút ra từ hội thoại, mỗi trường kèm trích dẫn nguyên văn làm căn cứ",
	"input_schema": {
		"type": "object",
		"properties": {
			"customer_name": {"type": ["string", "null"]},
			"organization": {"type": ["string", "null"]},
			"phones": {"type": "array", "items": {"type": "string"}},
			"emails": {"type": "array", "items": {"type": "string"}},
			"need": {"type": ["string", "null"], "maxLength": 300},
			"products": {"type": "array", "items": {"type": "string"}},
			"quantity": {"type": ["string", "null"]},
			"timeframe": {"type": ["string", "null"]},
			"timeframe_within_30d": {"type": ["boolean", "null"]},
			"urgency": {"enum": ["Thấp", "Trung bình", "Cao", None]},
			"intent": {"enum": list(INTENTS)},
			"wants_human": {"type": "boolean"},
			"summary": {"type": "string", "maxLength": 400},
			"evidence": {
				"type": "object",
				"description": "Với mỗi trường đã điền (trừ intent, summary): trích nguyên văn ngắn từ lời khách",
				"additionalProperties": {"type": "string", "maxLength": 200},
			},
		},
		"required": ["intent", "wants_human", "summary", "evidence", "phones", "emails", "products"],
	},
}

# Schema v3 (câu lệnh v2 trở lên, V21-06/F6) = v1 đổi sang ý định B2B, BỎ trường bán lẻ (products, quantity, timeframe,
# timeframe_within_30d) và thêm trường B2B tùy chọn. Cột cũ trên Lead giữ nguyên, chỉ là bot không còn điền.
# Bản ghim v1 giữ TOOL_V1 nguyên vẹn (enum ý định cũ).
TOOL_V3: dict[str, Any] = copy.deepcopy(TOOL_V1)
_props_v3 = TOOL_V3["input_schema"]["properties"]
for _retail in ("products", "quantity", "timeframe", "timeframe_within_30d"):
	del _props_v3[_retail]
_props_v3["intent"] = {"enum": list(intents.INTENTS_B2B)}
TOOL_V3["input_schema"]["required"] = ["intent", "wants_human", "summary", "evidence", "phones", "emails"]
_props_v3.update(
	{
		"tax_id": {
			"type": ["string", "null"],
			"description": "Mã số thuế doanh nghiệp khách nêu rõ (10 hoặc 13 số)",
		},
		"hospitals_mentioned": {"type": "array", "items": {"type": "string"}},
		"current_pain": {"type": ["string", "null"], "maxLength": 300},
		"decision_role": {"enum": [*DECISION_ROLES, None]},
		"asks_proposal": {"type": ["boolean", "null"]},
	}
)
TOOL = TOOL_V3


def tool_for(prompt_version: str | None) -> dict[str, Any]:
	"""Schema của tool theo phiên bản câu lệnh: v1 giữ schema v1 (đường chạy ghim không đổi), còn lại schema v2."""
	return TOOL_V1 if (prompt_version or "v1") == "v1" else TOOL_V3


# --- Kiểm căn cứ --------------------------------------------------------------------------------------------


def _fold(text: str | None) -> str:
	"""Chuẩn hóa để so khớp: NFKC, bỏ dấu, chữ thường, bỏ dấu câu, gộp khoảng trắng."""
	folded = strip_accents(normalize_text(text or ""))
	return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", folded)).strip()


def evidence_supported(evidence: str | None, customer_text: str | None) -> bool:
	"""Trích dẫn `evidence` có nằm trong lời khách không: bỏ dấu, chữ thường, so mờ >= 0.85 theo cửa sổ trượt.

	Chặt hơn đặc tả ở một điểm: mọi số trong trích dẫn phải xuất hiện nguyên vẹn trong lời khách (so mờ không
	được biến "10" thành "100"). Chỉ so với `customer_text`; caller không đưa lời Miyano vào đây.
	"""
	ev, text = _fold(evidence), _fold(customer_text)
	if len(ev) < MIN_EVIDENCE_CHARS or not text:
		return False
	numbers = re.findall(r"\d+", ev)
	if numbers:
		text_numbers = set(re.findall(r"\d+", text))
		if any(n not in text_numbers for n in numbers):
			return False
	if ev in text:
		return True
	size = len(ev)
	if len(text) < size * EVIDENCE_THRESHOLD:
		return False
	matcher = SequenceMatcher(None, "", ev, autojunk=False)
	for start in range(max(len(text) - size, 0) + 1):
		matcher.set_seq1(text[start : start + size])
		if (
			matcher.real_quick_ratio() >= EVIDENCE_THRESHOLD
			and matcher.quick_ratio() >= EVIDENCE_THRESHOLD
			and matcher.ratio() >= EVIDENCE_THRESHOLD
		):
			return True
	return False


def customer_text(
	lead: str, max_turns: int = MAX_TURNS, max_chars: int = MAX_CHARS, mask: bool = True
) -> str:
	"""Gộp lời KHÁCH (không có lời Miyano/sale) trong đúng cửa sổ lượt mà bot nhìn thấy, chưa che SĐT/email.

	Cửa sổ cắt như `build_conversation`: `max_turns` lượt gần nhất rồi bỏ lượt cũ nhất khi vượt `max_chars`.
	"""
	turns = [t for t in timeline.conversation_turns(lead) if (t.get("text") or "").strip()]
	turns = turns[-max(max_turns, 0) :]
	sizes = []
	for t in turns:
		text = escape_tags(normalize_text(t["text"]).strip())
		sizes.append(len(_line(t, mask_pii(text) if mask else text)) + 1)
	while len(turns) > 1 and sum(sizes) > max_chars:
		turns.pop(0)
		sizes.pop(0)
	return "\n".join(t["text"] for t in turns if t.get("actor") == "customer")


def value_supported(value: str, evidence: str | None) -> bool:
	"""Giá trị bot điền có khớp trích dẫn không: mọi số của giá trị có trong trích dẫn, và >= 80% từ của giá trị
	(sau bỏ dấu, chữ thường) nằm trong các từ của trích dẫn. Chặn ca "trích dẫn có thật nhưng giá trị bịa".
	"""
	words = _fold(value).split()
	if not words:
		return False
	have = set(_fold(evidence).split())
	if any(w.isdigit() and w not in have for w in words):
		return False
	return sum(w in have for w in words) / len(words) >= VALUE_MATCH_RATIO


# --- Hậu xử lý bằng code (docs/08 mục 3, bước 1-6) ---------------------------------------------------------


def _clean(value: Any, limit: int) -> str:
	"""Văn bản thuần từ đầu ra AI: bỏ ký tự điều khiển và < >, gộp khoảng trắng, cắt theo giới hạn."""
	text = re.sub(r"[\x00-\x1f\x7f<>]+", " ", str(value or ""))
	return re.sub(r"\s+", " ", text).strip()[:limit]


_PHONE_SHAPED = re.compile(r"0[35789]\d{8}")
_TAX_CUE = re.compile(r"\b(?:ma so thue|mst|tax)\b")


def tax_id_supported(tax: str, evidence: str | None) -> bool:
	"""Mã số thuế đã chuẩn hóa có nằm (theo chữ số) trong trích dẫn không; chặn ca trích dẫn thật nhưng MST bịa.

	Chuỗi 10 số có dạng SĐT di động VN (0[35789]xxxxxxxx) chỉ được coi là MST khi trích dẫn nêu rõ "mã số thuế"/"MST"
	(V2-08): tránh lấy SĐT của khách làm MST.
	"""
	have = re.sub(r"\D", "", evidence or "")
	want = re.sub(r"\D", "", tax)
	if not tax or want not in have:
		return False
	if _PHONE_SHAPED.fullmatch(want[:10]) and len(want) in (10, 13):
		from omni_crm.bot.guards import _folded

		return bool(_TAX_CUE.search(_folded(evidence)))
	return True


def _postprocess_b2b(
	signals: dict, evidence: dict, text_of_customer: str, accepted: dict, dropped: list
) -> None:
	"""Trường B2B (docs/14 mục 7): MST chuẩn hóa, bệnh viện nhắc tới, vướng mắc, vai trò quyết định, yêu cầu đề xuất.

	Mỗi trường cần `evidence` nằm trong lời khách như trường cũ; thiếu hoặc sai thì bỏ. Không ghi gì vào Lead ở đây.
	"""

	def supported(key: str) -> bool:
		return evidence_supported(evidence.get(key), text_of_customer)

	raw_tax = signals.get("tax_id")
	if raw_tax not in (None, ""):
		try:
			tax = normalize_tax_id(_clean(raw_tax, 40))
		except InvalidTaxId:
			tax = None
		if tax and supported("tax_id") and tax_id_supported(tax, evidence.get("tax_id")):
			accepted["tax_id"] = tax
		else:
			dropped.append("tax_id")
	hospitals = [h for h in (_clean(x, 140) for x in signals.get("hospitals_mentioned") or []) if h]
	if hospitals:
		if supported("hospitals_mentioned"):
			ev = evidence.get("hospitals_mentioned")
			kept = [h for h in hospitals if value_supported(h, ev)][:10]
			if kept:
				accepted["hospitals_mentioned"] = ", ".join(kept)[: FIELD_LIMITS["products"]]
			if len(kept) < len(hospitals):
				dropped.append(
					"hospitals_mentioned" if not kept else f"hospitals_mentioned:{len(hospitals) - len(kept)}"
				)
		else:
			dropped.append("hospitals_mentioned")
	pain = _clean(signals.get("current_pain"), FIELD_LIMITS["need"])
	if pain:
		if supported("current_pain"):
			accepted["current_pain"] = pain
		else:
			dropped.append("current_pain")
	if signals.get("decision_role") not in (None, ""):
		if signals["decision_role"] in DECISION_ROLES and supported("decision_role"):
			accepted["decision_role"] = signals["decision_role"]
		else:
			dropped.append("decision_role")
	if signals.get("asks_proposal") is True:
		if supported("asks_proposal"):
			accepted["asks_proposal"] = True
		else:
			dropped.append("asks_proposal")


def postprocess(signals: dict, text_of_customer: str) -> dict:
	"""Giữ lại trường có căn cứ trong lời khách; trả {"accepted": {...}, "dropped": [tên trường]}.

	Trường thiếu `evidence` hoặc có trích dẫn không nằm trong lời khách thì bị bỏ. SĐT/email còn phải được bộ
	trích xuất của hệ thống tìm thấy trong lời khách. `intent`, `wants_human`, `summary` không cần căn cứ.
	"""
	evidence = signals.get("evidence") if isinstance(signals.get("evidence"), dict) else {}
	accepted: dict[str, Any] = {}
	dropped: list[str] = []

	def decide(key: str, value: Any, check_value: bool = True) -> bool:
		"""Nhận khi trích dẫn có trong lời khách và (nếu check_value) giá trị khớp trích dẫn."""
		ev = evidence.get(key)
		ok = evidence_supported(ev, text_of_customer) and (not check_value or value_supported(value, ev))
		if ok:
			accepted[key] = value
		else:
			dropped.append(key)
		return ok

	for key in ("customer_name", "organization", "quantity", "timeframe"):
		value = _clean(signals.get(key), FIELD_LIMITS.get(key, 140))
		if value:
			decide(key, value)
	need = _clean(signals.get("need"), FIELD_LIMITS["need"])
	if need:
		decide("need", need, check_value=False)  # nhu cầu là diễn đạt lại, chỉ cần có căn cứ
	products = [p for p in (_clean(x, 140) for x in signals.get("products") or []) if p]
	if products:
		ev = evidence.get("products")
		if evidence_supported(ev, text_of_customer):
			kept = [p for p in products if value_supported(p, ev)]
			if kept:
				accepted["products"] = ", ".join(kept)[: FIELD_LIMITS["products"]]
			if len(kept) < len(products):
				dropped.append("products" if not kept else f"products:{len(products) - len(kept)}")
		else:
			dropped.append("products")
	if signals.get("urgency") in URGENCIES:
		decide("urgency", signals["urgency"], check_value=False)  # giá trị là nhãn chuẩn, chỉ cần có căn cứ
	if "timeframe" in accepted and isinstance(signals.get("timeframe_within_30d"), bool):
		accepted["timeframe_within_30d"] = signals["timeframe_within_30d"]

	_postprocess_b2b(signals, evidence, text_of_customer, accepted, dropped)

	found = extract_contacts(text_of_customer)
	for key, normalizer, known in (
		("phones", normalize_phone, found["phones"]),
		("emails", normalize_email, found["emails"]),
	):
		wanted = [n for n in (normalizer(x) for x in signals.get(key) or []) if n]
		if not wanted:
			continue
		ok = [n for n in dict.fromkeys(wanted) if n in known]
		if ok and evidence_supported(evidence.get(key), text_of_customer):
			accepted[key] = ok[:MAX_CONTACTS]
			if len(ok) > MAX_CONTACTS:
				dropped.append(f"{key}_truncated:{len(ok) - MAX_CONTACTS}")
		else:
			dropped.append(key)

	if (
		signals.get("intent") in intents.ALL_INTENTS
	):  # câu lệnh v1 trả giá trị cũ, câu lệnh v2 trả giá trị mới
		accepted["intent"] = signals["intent"]
	accepted["wants_human"] = bool(signals.get("wants_human"))
	summary = _clean(signals.get("summary"), FIELD_LIMITS["summary"])
	if summary:
		accepted["summary"] = summary
	return {"accepted": accepted, "dropped": dropped}


# --- Ghi vào Lead ---------------------------------------------------------------------------------------------


def apply_to_lead(lead: str, accepted: dict) -> dict:
	"""Ghi trường đã duyệt vào Lead (cờ in_omni_bot); trả {"applied": [...], "skipped_manual": [...]}.

	Không ghi trường nằm trong `omni_manual_fields`. Tên chỉ điền khi Lead đang để tên tạm. SĐT/email khách tự nhắn
	(V3-04, người phụ trách chốt 2026-11-04) chỉ điền vào ô ĐANG TRỐNG và chỉ khi không thuộc Lead khác (thuộc Lead
	khác thì `flag_duplicates` tạo nghi trùng). `organization` chỉ điền khi đang trống.
	"""
	row = frappe.db.get_value(
		LEAD,
		lead,
		[
			"first_name",
			"last_name",
			"lead_name",
			"organization",
			"mobile_no",
			"phone",
			"email",
			"omni_phone_e164",
			"omni_email_norm",
			"omni_manual_fields",
			*LEAD_FIELDS.values(),
		],
		as_dict=True,
		for_update=True,  # khóa dòng tới commit: sale lưu cùng lúc phải chờ, không đọc `omni_manual_fields` cũ
	)
	if not row:
		return {"applied": [], "skipped_manual": []}
	manual = set(lead_bridge._parse_fields(row.omni_manual_fields))
	values: dict[str, Any] = {}
	skipped: list[str] = []

	def put(fieldname: str, value: Any) -> None:
		if value in (None, ""):
			return
		if fieldname in manual:
			skipped.append(fieldname)
		elif value != row.get(fieldname):
			values[fieldname] = value

	for key, fieldname in LEAD_FIELDS.items():
		put(fieldname, accepted.get(key))
	name = accepted.get("customer_name")
	if name and is_placeholder_name(" ".join(filter(None, [row.first_name, row.last_name]))):
		if {"first_name", "last_name", "lead_name"} & manual:
			skipped.append("first_name")
		else:
			values.update(first_name=name[:140], last_name=None, lead_name=name[:140])
	link = {"attached": None, "weak": None, "ambiguous": [], "unverified_name": None}
	if not row.organization:
		link = link_organization(lead, accepted, row, manual)
		if link["attached"]:
			values["organization"] = link["attached"]
	if accepted.get("organization") and not row.organization and "organization" not in values:
		claim = accepted["organization"][:140]
		if organization_exists(
			claim
		):  # tên tự khai trùng nhà thầu thật: tín hiệu yếu, không gắn (bài học R1/Web Form)
			link["unverified_name"] = claim
			_note_weak_org(lead, claim)
		else:
			put("organization", claim)
	values.update(_contact_values(lead, accepted, row, manual, skipped))
	if accepted.get("summary"):
		values["omni_bot_summary"] = accepted["summary"]  # cập nhật mỗi lượt; trường chỉ đọc, sale không sửa
	applied = sorted(values)  # trước set_value (nó thêm modified/modified_by vào dict)
	if values:
		# db.set_value: không chạy validate của CRM (has_gravatar qua mạng), như crm_bridge.lead.fill_blank_contact.
		with flags.omni_bot():
			frappe.db.set_value(LEAD, lead, values)
			_note_update(lead, sorted(set(values) - {"omni_bot_summary", "lead_name", *CONTACT_NORM_FIELDS}))
	return {
		"applied": applied,
		"skipped_manual": sorted(set(skipped)),
		"org_link": {k: v for k, v in link.items() if v},
	}


CONTACT_NORM_FIELDS = ("omni_phone_e164", "omni_email_norm")  # trường kỹ thuật, không ghi vào ghi chú


def _unclaimed(lead: str, values: list[str] | None, kind: str) -> str | None:
	"""Giá trị đầu tiên (SĐT hoặc email đã chuẩn hóa) không thuộc Lead nào khác; None nếu không có."""
	for value in values or []:
		phones, emails = ([value], []) if kind == "phone" else ([], [value])
		if not [x for x in resolver.find_leads(phones, emails) if x != lead]:
			return value
	return None


def _contact_values(lead: str, accepted: dict, row: Any, manual: set[str], skipped: list[str]) -> dict:
	"""Điền SĐT/email khách tự nhắn vào ô trống của Lead (không ghi đè, tôn trọng `omni_manual_fields`)."""
	phone = _unclaimed(lead, accepted.get("phones"), "phone")
	email = _unclaimed(lead, accepted.get("emails"), "email")
	if phone and {"mobile_no", "phone"} & manual:
		skipped.append("mobile_no")
		phone = None
	if email and "email" in manual:
		skipped.append("email")
		email = None
	return lead_bridge.blank_contact_values(row, phone, email)


def organization_exists(name: str | None) -> bool:
	"""Có CRM Organization thật tên `name` không (so khớp không phân biệt hoa thường theo collation của DB)."""
	return bool(name) and bool(frappe.db.exists("CRM Organization", name))


def _note_weak_org(lead: str, org: str) -> None:
	"""Comment "khách nêu nhà thầu X nhưng chưa xác minh" (một lần cho mỗi nhà thầu); không chứa MST hay nội dung tin."""
	_note_once(
		lead,
		_("Bot: khách nêu mã số thuế/tên khớp nhà thầu «{0}» nhưng chưa xác minh, chưa gắn Lead").format(org),
	)


def _note_once(lead: str, content: str) -> None:
	if frappe.db.exists(
		"Comment",
		{"reference_doctype": LEAD, "reference_name": lead, "comment_type": "Info", "content": content},
	):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": LEAD,
				"reference_name": lead,
				"content": content,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Omni: không ghi được ghi chú tín hiệu về nhà thầu", message=f"lead={lead}")


def link_organization(lead: str, accepted: dict, row: Any, manual: set[str]) -> dict:
	"""Gắn Lead vào nhà thầu theo MST bot trích (docs/14 mục 7, ADR 0010); trả {attached, weak, ambiguous, unverified_name}.

	Chỉ gắn khi MST khớp ĐÚNG MỘT Organization VÀ nguồn đáng tin: người nhắn đã là liên hệ (Contact) của chính
	Organization đó theo SĐT/email của Lead. MST tự khai trong tin nhắn ở kênh công khai (Zalo, email, cuộc gọi) ai
	cũng gõ được nên là tín hiệu YẾU (như MST trên Web Form, ADR 0013): chỉ ghi `omni_tax_id_stated` + ghi chú cho người
	xử lý. Không bao giờ gắn khi `organization` nằm trong `omni_manual_fields`.
	"""
	out: dict[str, Any] = {"attached": None, "weak": None, "ambiguous": [], "unverified_name": None}
	tax = accepted.get("tax_id")
	if not tax or "organization" in manual:
		return out
	orgs = resolver._orgs_by_tax_id([tax])
	if len(orgs) > 1:
		out["ambiguous"] = orgs
		_note_once(
			lead, _("Bot: mã số thuế khách nêu khớp nhiều nhà thầu, chưa tự gắn: {0}").format(", ".join(orgs))
		)
		return out
	if not orgs:
		return out
	org = orgs[0]
	phones = [p for p in [row.get("omni_phone_e164")] if p]
	emails = [e for e in [row.get("omni_email_norm")] if e]
	if (phones or emails) and org in resolver._orgs_by_contacts(phones, emails):
		out["attached"] = org
	else:
		out["weak"] = org
		_note_weak_org(lead, org)
	return out


def _note_update(lead: str, fieldnames: list[str]) -> None:
	"""Comment ngắn "Bot đã cập nhật: <tên trường>" (set_value không tạo Version); chỉ tên trường, không có giá trị."""
	if not fieldnames:
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": LEAD,
				"reference_name": lead,
				"content": _("Bot đã cập nhật: {0}").format(", ".join(fieldnames)),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Omni: không ghi được ghi chú bot cập nhật Lead", message=f"lead={lead}")


def flag_duplicates(lead: str, accepted: dict) -> tuple[list[str], int]:
	"""SĐT/email bot trích (tín hiệu trung bình) trùng Lead khác thì tạo Omni Duplicate Candidate; không tự gộp.

	Trả (tên các Candidate đã tạo, số Lead bị cắt vì vượt MAX_CANDIDATES).
	"""
	phones, emails = accepted.get("phones") or [], accepted.get("emails") or []
	others = [x for x in resolver.find_leads(phones, emails) if x != lead]
	if not others:
		return [], 0
	cut = max(len(others) - MAX_CANDIDATES, 0)
	reason = _("Bot trích SĐT/email từ lời khách, trùng Lead khác")
	score = SCORE_MEDIUM_ONE if len(others) == 1 else SCORE_MEDIUM_MANY
	made = [duplicates.create_candidate(lead, other, reason, score) for other in others[:MAX_CANDIDATES]]
	return [c for c in made if c], cut


# --- Điểm vào -------------------------------------------------------------------------------------------------


def _lead_of_batch(batch_id: str) -> str | None:
	leads = frappe.get_all(INBOUND, filters={"batch_id": batch_id, "lead": ["is", "set"]}, pluck="lead")
	return lead_bridge.follow_merged(leads[0]) if leads else None


def scrub_raw(batch_id: str, raw: dict | None = None) -> None:
	"""Bỏ `evidence` (lời khách) khỏi output_json thô mà call_tool đã commit; không có `raw` thì xóa hẳn output_json.

	Không ném lỗi: gọi được cả ở nhánh lỗi. Không tìm thấy dòng Bot Run thì không có gì để làm.
	"""
	try:
		kept = None if raw is None else {k: v for k, v in raw.items() if k != "evidence"}
		_store_result(batch_id, kept)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Omni: không scrub được output_json Bot Run", message=f"batch_id={batch_id}")


def _store_result(batch_id: str, result: dict | None) -> None:
	"""Thay output_json của Bot Run bằng `result` (kết quả đã duyệt, không có trích dẫn nguyên văn); D2-06 đọc từ đây."""
	run = frappe.get_all(
		BOT_RUN,
		filters={"batch_id": batch_id, "kind": "Analyze", "status": "Success"},
		order_by="creation desc",
		limit_page_length=1,
		pluck="name",
	)
	if run:
		frappe.db.set_value(
			BOT_RUN, run[0], "output_json", json.dumps(result, ensure_ascii=False) if result else None
		)


def run_analysis(batch_id: str, lead: str) -> dict | None:
	"""Gọi AI một lần cho Lead rồi hậu xử lý, ghi Lead; trả kết quả đã duyệt hoặc None khi AI không trả được."""
	settings = frappe.get_cached_doc("Omni Settings")
	prompt = load_prompt("analyze")
	mask = not cint(frappe.conf.get(UNMASK_CONF))
	raw = ai_client.call_tool(
		"Analyze",
		settings.get("analyze_model") or "claude-haiku-5-5",
		render(prompt),
		[{"role": "user", "content": build_conversation(lead, max_turns=MAX_TURNS, mask=mask)}],
		tool_for(prompt.version),
		lead=lead,
		batch_id=batch_id,
		prompt_version=prompt.version,
		max_tokens=1200,
	)  # KHÔNG truyền synthetic: hội thoại khách luôn qua cổng Q11
	if raw is None:
		return None
	scrub_raw(
		batch_id, raw
	)  # call_tool đã commit bản thô (có lời khách trong evidence): bỏ ngay, trước mọi bước dễ lỗi
	checked = postprocess(raw, customer_text(lead, mask=mask))
	accepted = checked["accepted"]
	written = apply_to_lead(lead, accepted)
	candidates, cut = flag_duplicates(lead, accepted)
	result = {
		"schema_version": SCHEMA_V1 if tool_for(prompt.version) is TOOL_V1 else SCHEMA_V3,
		"accepted": accepted,
		"dropped": checked["dropped"] + ([f"candidates_truncated:{cut}"] if cut else []),
		"applied": written["applied"],
		"skipped_manual": written["skipped_manual"],
		"candidates": candidates,
	}
	_store_result(batch_id, result)
	frappe.db.commit()
	return result


def analyze_batch(batch_id: str) -> None:
	"""Job nền: phân tích hội thoại của Lead trong lượt `batch_id`; luôn kết thúc bằng `after_analyze`.

	Mọi lỗi (AI, DB) chỉ ghi log không chứa nội dung khách, không ném ra: lượt xử lý tin đã Done từ trước.
	"""
	lead = None
	try:
		lead = _lead_of_batch(batch_id)
		if lead and flags.bot_analyze_enabled():
			run_analysis(batch_id, lead)
	except Exception as e:
		try:
			frappe.db.rollback()
			frappe.log_error(
				title="Omni: lỗi bot phân tích",
				message=redact(
					f"batch_id={batch_id}\nloai_loi={type(e).__name__}\n"
					f"{frappe.get_traceback(with_context=False)[-1500:]}"
				),
			)
			frappe.db.commit()
		except Exception:
			pass
		scrub_raw(batch_id)  # lỗi giữa chừng: không để lại output_json thô
	try:
		after_analyze(batch_id, lead)
	except Exception:
		frappe.log_error(title="Omni: lỗi sau bot phân tích", message=f"batch_id={batch_id}")
		frappe.db.commit()


def after_analyze(batch_id: str, lead: str | None) -> None:
	"""Điểm nối sau phân tích (kể cả khi AI lỗi): chấm điểm Lead (D2-06, tự bắt lỗi, tắt thì không làm gì).

	Rồi đẩy bot trả lời ngầm (D2-07, docs/08 mục 5.1); chấm điểm chạy trước để bản nháp thấy điểm mới.
	"""
	from omni_crm.bot import auto_send, reply, scoring

	scoring.run_scoring(lead)
	auto_send.on_analysis(batch_id, lead)  # D3-05: ngắt mạch khiếu nại (tự bắt lỗi)
	reply.enqueue_reply(batch_id, lead)


def should_analyze(batch_id: str) -> bool:
	"""Lượt có tin khách đáng phân tích không: tin `message` có chữ hoặc `user_info`."""
	rows = frappe.get_all(
		INBOUND,
		filters={"batch_id": batch_id, "event_type": ["in", list(ANALYZE_EVENTS)]},
		fields=["event_type", "text"],
	)
	return any(r.event_type == "user_info" or (r.text or "").strip() for r in rows)


def enqueue_analyze(batch_id: str, lead: str | None) -> bool:
	"""Từ `post_batch_hooks`: công tắc bật và lượt có tin khách thì đẩy `analyze_batch`; lỗi enqueue không ném ra."""
	if not lead or not flags.bot_analyze_enabled() or not should_analyze(batch_id):
		return False
	try:
		frappe.enqueue(
			JOB,
			queue=QUEUE,
			job_id=f"omni-analyze::{batch_id}",
			deduplicate=True,
			timeout=JOB_TIMEOUT,
			batch_id=batch_id,
		)
	except Exception:
		frappe.log_error(title="Omni: không đẩy được job bot phân tích", message=f"batch_id={batch_id}")
		return False
	return True
