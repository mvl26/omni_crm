"""Chấm điểm Lead theo bảng điểm (docs/08 mục 4).

Hoàn toàn bằng code, không gọi AI: tín hiệu lấy từ trường Lead, kết quả phân tích gần nhất của bot (Omni Bot
Run) và số lần khách liên hệ trong 30 ngày. Mỗi tín hiệu cộng một lần; bảng điểm là `Omni Settings.score_rules`.
Lỗi chấm điểm không bao giờ làm hỏng pipeline (các hàm điểm vào tự bắt lỗi).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity.normalize import normalize_email, normalize_phone
from omni_crm.pipeline.retry import redact

LEAD = "CRM Lead"
SETTINGS = "Omni Settings"
BOT_RUN = "Omni Bot Run"
INBOUND = "Omni Inbound Message"
CALL = "Omni Call"
SIGNALS = (
	"has_phone",
	"has_email",
	"has_org",
	"intent_price",
	"intent_purchase",
	"quantity_given",
	"timeframe_within_30d",
	"urgency_high",
	"repeat_contact",
	"complaint",
)
DEFAULT_THRESHOLD = 60  # docs/02 mục 2
REPEAT_DAYS = 30
REPEAT_MIN_CONTACTS = 2
BOT_RUN_SCAN = 20  # số lần phân tích gần nhất xét khi tìm timeframe_within_30d
SAMPLE_FILE = Path(__file__).resolve().parents[2] / "mau" / "bang-diem-lead-mau.csv"
HOT_REALTIME_EVENT = "omni_hot_lead"
MAX_REASON_ROWS = 10


# --- Bảng điểm ---------------------------------------------------------------------------------------------


def load_rules() -> dict[str, dict]:
	"""Bảng điểm đang dùng: {tín hiệu: {points, note}}; mỗi tín hiệu lấy dòng đầu. Rỗng nếu chưa nhập."""
	try:
		rows = frappe.get_cached_doc(SETTINGS).get("score_rules") or []
	except Exception:
		return {}
	rules: dict[str, dict] = {}
	for r in rows:
		if r.signal in SIGNALS and r.signal not in rules:
			rules[r.signal] = {"points": cint(r.points), "note": (r.note or "").strip()}
	return rules


def hot_threshold() -> int:
	"""Ngưỡng Lead nóng; chưa đặt hoặc <= 0 thì dùng mặc định 60."""
	try:
		value = cint(frappe.get_cached_doc(SETTINGS).get("hot_lead_threshold"))
	except Exception:
		value = 0
	return value if value > 0 else DEFAULT_THRESHOLD


def scoring_enabled() -> bool:
	"""Công tắc chấm điểm; lỗi hoặc chưa cấu hình thì coi là tắt (an toàn)."""
	return flags.lead_scoring_enabled()


# --- Tín hiệu ----------------------------------------------------------------------------------------------


def recent_contacts(lead: str, days: int = REPEAT_DAYS) -> int:
	"""Số lần khách liên hệ trong `days` ngày: mỗi lượt tin khách (batch) và mỗi cuộc gọi vào tính một lần."""
	since = add_to_date(now_datetime(), days=-days)
	msgs = frappe.get_all(
		INBOUND,
		filters={
			"lead": lead,
			"event_type": "message",
			"status": ["!=", "Ignored"],
			"received_at": [">=", since],
		},
		fields=["name", "batch_id"],
	)
	contacts = {m.batch_id or m.name for m in msgs}
	try:
		calls = frappe.get_all(
			CALL,
			filters={"lead": lead, "direction": "Inbound", "status": ["!=", ""], "started_at": [">=", since]},
			pluck="name",
		)
	except Exception:
		calls = []  # Omni Call chưa có / lỗi truy vấn: bỏ qua nguồn này
	return len(contacts) + len(calls)


def bot_timeframe_within_30d(lead: str) -> bool:
	"""Lượt phân tích thành công gần nhất có khóa `timeframe_within_30d` (khóa vắng = không có căn cứ) nói gì."""
	runs = frappe.get_all(
		BOT_RUN,
		filters={"lead": lead, "kind": "Analyze", "status": "Success"},
		fields=["output_json"],
		order_by="creation desc, name desc",  # name tăng dần theo thời gian: phân định khi cùng giây
		limit_page_length=BOT_RUN_SCAN,
	)
	for run in runs:
		try:
			accepted = (json.loads(run.output_json or "null") or {}).get("accepted") or {}
		except (ValueError, AttributeError):
			continue
		if isinstance(accepted.get("timeframe_within_30d"), bool):
			return accepted["timeframe_within_30d"]
	return False


def collect_signals(lead: str) -> dict[str, bool]:
	"""Tín hiệu hiện có của Lead (True = đang thỏa); không ghi gì."""
	row = frappe.db.get_value(
		LEAD,
		lead,
		["mobile_no", "phone", "email", "organization", "omni_quantity", "omni_urgency", "omni_intent"],
		as_dict=True,
	)
	if not row:
		return dict.fromkeys(SIGNALS, False)
	intent = row.omni_intent
	return {
		"has_phone": bool(normalize_phone(row.mobile_no) or normalize_phone(row.phone)),
		"has_email": bool(normalize_email(row.email)),
		"has_org": bool((row.organization or "").strip()),
		"intent_price": intent == "Hỏi giá",
		"intent_purchase": intent == "Đặt hàng",
		"quantity_given": bool((row.omni_quantity or "").strip()),
		"timeframe_within_30d": bot_timeframe_within_30d(lead),
		"urgency_high": row.omni_urgency == "Cao",
		"repeat_contact": recent_contacts(lead) >= REPEAT_MIN_CONTACTS,
		"complaint": intent == "Khiếu nại",
	}


def compute(signals: dict[str, bool], rules: dict[str, dict]) -> tuple[int, list[dict]]:
	"""Thuần: cộng điểm các tín hiệu đang thỏa có trong bảng điểm (mỗi tín hiệu một lần); trả (điểm, các dòng)."""
	items = [
		{"signal": s, "points": rules[s]["points"], "note": rules[s]["note"]}
		for s in SIGNALS
		if signals.get(s) and s in rules
	]
	return sum(i["points"] for i in items), items


def _parse(raw: Any) -> dict:
	try:
		value = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return {}
	return value if isinstance(value, dict) else {}


def score_lead(lead: str) -> tuple[int, dict]:
	"""Tính điểm Lead theo bảng điểm rồi ghi `omni_score` + `omni_score_breakdown`; trả (điểm, breakdown).

	Giữ nguyên dấu `hot_notified_at` đã có. Không commit, không báo Lead nóng (xem `run_scoring`).
	"""
	score, items = compute(collect_signals(lead), load_rules())
	old = _parse(frappe.db.get_value(LEAD, lead, "omni_score_breakdown"))
	breakdown: dict[str, Any] = {"total": score, "signals": items, "scored_at": str(now_datetime())}
	if old.get("hot_notified_at"):
		breakdown["hot_notified_at"] = old["hot_notified_at"]
	with flags.omni_bot():
		frappe.db.set_value(
			LEAD,
			lead,
			{"omni_score": score, "omni_score_breakdown": json.dumps(breakdown, ensure_ascii=False)},
			update_modified=False,
		)
	return score, breakdown


# --- Báo Lead nóng -----------------------------------------------------------------------------------------


def _recipients(lead: str) -> list[str]:
	"""Người phụ trách (chủ Lead + người được giao) còn hoạt động; không có thì người nhận dự phòng của Settings."""
	row = frappe.db.get_value(LEAD, lead, ["lead_owner", "_assign"], as_dict=True) or {}
	users = [row.get("lead_owner")]
	try:
		users += json.loads(row.get("_assign") or "[]")
	except ValueError:
		pass
	if not any(users):
		fallback = frappe.db.get_single_value(SETTINGS, "assignment_fallback_users") or ""
		users = [u.strip() for u in fallback.replace("\n", ",").split(",")]
	users = [u for u in dict.fromkeys(users) if u]
	return (
		frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name") if users else []
	)


def hot_message(lead_name: str, score: int, items: Iterable[dict]) -> tuple[str, str]:
	"""(tiêu đề, nội dung) báo Lead nóng: chỉ tên Lead, điểm và lý do từ bảng điểm; không SĐT/email/nội dung tin."""
	reasons = [
		f"{i.get('note') or i['signal']} ({int(i['points']):+d})"
		for i in sorted(items, key=lambda i: -abs(i["points"]))[:MAX_REASON_ROWS]
	]
	subject = _("Lead nóng: {0}, điểm {1}").format(lead_name, score)
	return subject, _("{0}. Lý do: {1}").format(subject, "; ".join(reasons) or "-")


def notify_hot(lead: str, score: int, items: list[dict]) -> int:
	"""Tạo Notification Log + realtime cho người phụ trách; trả số người đã báo. Không commit."""
	users = _recipients(lead)
	lead_name = frappe.db.get_value(LEAD, lead, "lead_name") or lead
	subject, content = hot_message(lead_name, score, items)
	done = 0
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": content,
					"document_type": LEAD,
					"document_name": lead,
				}
			).insert(ignore_permissions=True)
			frappe.publish_realtime(
				HOT_REALTIME_EVENT, {"lead": lead, "score": score}, user=user, after_commit=True
			)
			done += 1
		except Exception:
			frappe.log_error(title="Omni: không báo được Lead nóng", message=f"lead={lead}")
	return done


def check_hot(lead: str) -> bool:
	"""Lần đầu điểm >= ngưỡng thì báo người phụ trách một lần rồi ghi dấu `hot_notified_at`; trả True nếu vừa báo.

	Khóa dòng Lead khi kiểm dấu để hai worker không báo đôi. Dấu không bị xóa khi điểm tụt dưới ngưỡng, nên
	điểm dao động quanh ngưỡng không báo lại. Không có người nhận thì chưa ghi dấu (báo khi có người phụ trách).
	Lead đã chuyển thành Deal không báo. Caller commit.
	"""
	row = frappe.db.get_value(
		LEAD, lead, ["omni_score", "omni_score_breakdown", "converted"], as_dict=True, for_update=True
	)
	if not row or cint(row.converted) or cint(row.omni_score) < hot_threshold():
		return False
	breakdown = _parse(row.omni_score_breakdown)
	if breakdown.get("hot_notified_at"):
		return False
	if not notify_hot(lead, cint(row.omni_score), breakdown.get("signals") or []):
		return False
	breakdown["hot_notified_at"] = str(now_datetime())
	frappe.db.set_value(
		LEAD,
		lead,
		"omni_score_breakdown",
		json.dumps(breakdown, ensure_ascii=False),
		update_modified=False,
	)
	return True


# --- Điểm vào ----------------------------------------------------------------------------------------------


def run_scoring(lead: str | None) -> int | None:
	"""Chấm điểm Lead (nếu công tắc bật và đã có bảng điểm) rồi kiểm Lead nóng; trả điểm hoặc None.

	Tự commit; mọi lỗi chỉ ghi log (không ném ra) để không làm hỏng pipeline.
	"""
	if not lead or not scoring_enabled() or not load_rules():
		return None
	score = None
	for step, title in ((_score_step, "Omni: lỗi chấm điểm Lead"), (_hot_step, "Omni: lỗi báo Lead nóng")):
		try:
			lead = lead_bridge.follow_merged(lead) or lead
			result = step(lead)
			score = result if step is _score_step else score
		except Exception as e:
			_log_failure(title, lead, e)
			if step is _score_step:
				return None
	return score


def _score_step(lead: str) -> int:
	score, _breakdown = score_lead(lead)
	frappe.db.commit()
	return score


def _hot_step(lead: str) -> bool:
	hot = check_hot(lead)
	frappe.db.commit()
	return hot


def _log_failure(title: str, lead: str, error: Exception) -> None:
	"""Ghi Error Log không chứa dữ liệu khách (chỉ mã Lead và loại lỗi)."""
	try:
		frappe.db.rollback()
		frappe.log_error(title=title, message=redact(f"lead={lead}\nloai_loi={type(error).__name__}"))
		frappe.db.commit()
	except Exception:
		pass


def on_batch_done(batch_id: str, lead: str | None) -> None:
	"""Từ `post_batch_hooks` khi lượt KHÔNG đi qua bot phân tích (cuộc gọi, bot tắt, tin không có chữ): chấm điểm luôn.

	Lượt có phân tích thì `analyze.after_analyze` chấm sau khi bot ghi Lead. Đây là điểm nối cuộc gọi của D2-01.
	"""
	run_scoring(lead)


# --- Bảo vệ trường chỉ đọc ---------------------------------------------------------------------------------


def score_view(lead_row: Any) -> dict | None:
	"""Điểm và lý do cho giao diện (từ hàng Lead có `omni_score`, `omni_score_breakdown`); None nếu chưa chấm.

	Chỉ gồm điểm, tổng và các dòng "ghi chú (+điểm)" lấy từ bảng điểm; không có dữ liệu khách.
	"""
	raw = lead_row.get("omni_score_breakdown")
	if not raw:
		return None
	data = _parse(raw)
	reasons = [
		f"{i.get('note') or i.get('signal')} ({int(i.get('points') or 0):+d})"
		for i in data.get("signals") or []
		if isinstance(i, dict)
	]
	return {
		"score": cint(lead_row.get("omni_score")),
		"reasons": reasons,
		"hot": bool(data.get("hot_notified_at")),
	}


def protect_score_fields(doc: Any, method: str | None = None) -> None:
	"""Hook `CRM Lead.validate`: người dùng (không phải code hệ thống) không sửa được `omni_score` và chi tiết điểm."""
	if flags.is_system_write():
		return
	previous = None if doc.is_new() else doc.get_doc_before_save()
	for fieldname in ("omni_score", "omni_score_breakdown"):
		doc.set(fieldname, previous.get(fieldname) if previous else None)


# --- Nhập bảng điểm từ CSV -------------------------------------------------------------------------------------


def read_rules_csv(path: str) -> list[dict]:
	"""Đọc và kiểm CSV (cột signal, points, note); ném ValueError gom mọi lỗi nếu có dòng sai. Không ghi DB."""
	file = Path(path)
	if not file.is_file():
		raise ValueError(f"Không thấy tệp: {path}")
	with file.open(encoding="utf-8-sig", newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames or not {"signal", "points"} <= {c.strip() for c in reader.fieldnames}:
			raise ValueError("Tệp phải có cột signal và points (và note)")
		rows, errors, seen = [], [], set()
		for n, raw in enumerate(reader, start=2):
			row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
			if not any(row.values()):
				continue
			signal = row.get("signal", "")
			if signal not in SIGNALS:
				errors.append(f"Dòng {n}: tín hiệu không hợp lệ '{signal}'")
				continue
			if signal in seen:
				errors.append(f"Dòng {n}: tín hiệu '{signal}' bị trùng")
				continue
			try:
				points = int(row.get("points", ""))
			except ValueError:
				errors.append(f"Dòng {n}: điểm không phải số nguyên '{row.get('points', '')}'")
				continue
			seen.add(signal)
			rows.append({"signal": signal, "points": points, "note": row.get("note", "")[:140]})
	if errors:
		raise ValueError("Bảng điểm sai, KHÔNG thay đổi gì:\n" + "\n".join(errors))
	if not rows:
		raise ValueError("Tệp không có dòng điểm nào")
	return rows


def is_sample(rows: list[dict], path: str) -> bool:
	"""Bảng nhập còn là giá trị mẫu chưa duyệt: trùng bản mẫu của repo (tín hiệu + điểm) hoặc tên tệp có 'mau'."""
	if "mau" in Path(path).name.lower():
		return True
	try:
		sample = {(r["signal"], r["points"]) for r in read_rules_csv(str(SAMPLE_FILE))}
	except ValueError:
		return False
	return {(r["signal"], r["points"]) for r in rows} == sample


def import_rules(path: str) -> dict:
	"""Nhập bảng điểm từ CSV, thay toàn bộ `score_rules`; chạy bằng `bench execute omni_crm.bot.scoring.import_rules`.

	Sai tín hiệu/điểm: ném ValueError và giữ nguyên bảng cũ. Giá trị mẫu chưa duyệt vẫn nhập, kèm cảnh báo.
	"""
	rows = read_rules_csv(path)
	warnings = []
	if is_sample(rows, path):
		warnings.append("CẢNH BÁO: đây là bảng điểm MẪU, chưa được Trưởng sale duyệt (docs/12 Q5).")
	settings = frappe.get_doc(SETTINGS)
	settings.set("score_rules", [])
	for r in rows:
		settings.append("score_rules", r)
	settings.flags.ignore_permissions = True
	settings.save()
	frappe.db.commit()
	for w in warnings:
		print(w)
	print(f"Đã nhập {len(rows)} dòng vào bảng điểm.")
	return {"imported": len(rows), "warnings": warnings}
