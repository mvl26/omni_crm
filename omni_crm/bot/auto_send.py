"""Bot tự gửi theo nhóm câu hỏi (D3-05, docs/08 mục 5.5 dòng Auto, mục 10): điều kiện mở, bật/tắt, gửi, ngắt mạch.

ĐÂY LÀ NƠI DUY NHẤT trong app được phép tạo `Omni Outbound Message(source="Bot")` (qua `queue_outbound`); test quét mã
giữ điều đó. Mọi thứ mặc định TẮT và đóng theo hướng an toàn (fail-closed):

- Khóa hạ tầng NGOÀI DB: site_config `omni_auto_send_allowed` (vắng = chặn). Chỉ người vận hành đặt, sau khi chủ dự án
  chốt Q1. `enable_group` và mọi đường gửi đều kiểm khóa này, kể cả khi `force`.
- Nhóm phải có `auto_send_enabled` (chỉ Omni Admin bật được, qua `enable_group`) và không phải `always_handoff`.
- Gửi chỉ khi nháp Draft/Reply/Pending qua mọi điều kiện của `check_reply`, dưới khóa dòng nhóm rồi khóa dòng nháp
  (thứ tự cố định nhóm -> nháp; mọi nơi khác chỉ khóa một trong hai), trong cùng giao dịch với việc chuyển nháp sang
  `Auto/Sent As Is` nên một nháp không thể gửi hai lần. `queue_outbound` gọi lại `assert_can_queue` để kiểm lần hai.
- `sandbox_mode` vẫn chặn gửi thật ở `send_outbound`; tại đó còn kiểm lại công tắc ngay trước khi gửi (`send_time_check`).
- Ngắt mạch: tự tắt nhóm khi sale chấm Sai tin bot đã gửi; khi khách có tin Khiếu nại trong 24 giờ sau tin bot; khi tỉ lệ
  Handoff của nhóm tăng gấp đôi so với 14 ngày trước đó. Mỗi lần tắt ghi Comment trên nhóm và báo Omni Admin + Sales Manager.

Câu giữ chân khi Handoff trong nhóm đang Auto đi bằng `source="System"` (không phải Bot), không liên kết nháp; đây là
quyết định riêng, xem docs/12.

Nội dung khách là dữ liệu: module này không đưa nội dung khách vào log, thông báo hay Comment.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.bot import handoff, reply
from omni_crm.channels.registry import AdapterNotFoundError, get_adapter
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.ingest.events import OutboundRequest
from omni_crm.omni_crm.doctype.omni_question_group.omni_question_group import enabling
from omni_crm.pipeline import outbound
from omni_crm.pipeline.retry import redact

GROUP = "Omni Question Group"
DRAFT = "Omni Bot Draft"
OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
CHANNEL = "Omni Channel"
DECIDED = ("Sent As Is", "Sent Edited", "Discarded")
GOOD = ("Sent As Is", "Sent Edited")

WINDOW_DAYS = 14  # docs/08 mục 10
MIN_DECIDED = 30
MIN_RATE_PCT = 95
MAX_EDIT_RATIO = 0.1
COMPLAINT_HOURS = 24
COMPLAINT_INTENT = "Khiếu nại"
HANDOFF_RECENT_HOURS = 24  # cửa sổ "hiện tại" của ngắt mạch Handoff; nền = 14 ngày liền trước cửa sổ này
HANDOFF_BASELINE_DAYS = 14
HANDOFF_FACTOR = 2
HANDOFF_MIN_RECENT = 10  # tối thiểu số nháp trong cửa sổ hiện tại, tránh tắt vì mẫu quá nhỏ
HANDOFF_MIN_RECENT_HANDOFFS = 3
HANDOFF_MIN_BASELINE = 20
MAX_AUTO_PER_IDENTITY_DAY = 3  # mỗi khách tối đa số tin bot tự gửi trong 24 giờ (chống bão tin)
MIN_FORCE_REASON = 10
TEXT_MAX = 300
BREAKER_MARK = "omni:auto_send:breaker"
ENABLE_MARK = "omni:auto_send:enable"
DISABLE_MARK = "omni:auto_send:disable"
HOLDING_MARK = "auto:holding_sent"
SENDING_FLAG = "omni_auto_sending"
SAVEPOINT = "omni_auto_send"
ADMIN_ROLE = "Omni Admin"
LEAD_MANAGER_ROLE = "Sales Manager"  # CRM: vai trò quản lý sale (đã xác nhận ở apps/crm, docs/12 mục C)

BREAKER_LABELS = {
	"rated_wrong": "Sale chấm Sai một tin bot đã gửi",
	"complaint": "Khách có tin Khiếu nại trong 24 giờ sau tin bot",
	"handoff_doubled": "Tỉ lệ chuyển người của nhóm tăng gấp đôi so với trung bình 14 ngày",
}


# --- Điều kiện mở (docs/08 mục 10) ---------------------------------------------------------------------------


def eligibility(group: str, as_of: datetime | str | None = None) -> dict[str, Any]:
	"""Nhóm đã đủ điều kiện mở tự động chưa, tính trên 14 ngày đến `as_of` (mặc định bây giờ); trả số liệu và lý do.

	Mẫu số: MỌI nháp mode=Draft của nhóm đã quyết định trong cửa sổ (theo `decided_at`): Sent As Is + Sent Edited +
	Discarded, kể cả nháp Handoff bị bỏ. Pending/Expired KHÔNG tính (chưa có quyết định), chỉ đếm riêng để người duyệt thấy.
	Nháp mode=Auto không vào mẫu số lẫn tử số (không để tin bot tự gửi tự nâng tỉ lệ). Tử số: Sent As Is/Sent Edited có
	`edit_distance_ratio` <= 0.1. Cần mẫu số >= 30, tỉ lệ >= 95%, không có nháp critical hay Sai kèm ghi chú (mọi nháp
	của nhóm trong cửa sổ, kể cả nháp Auto). Nhóm luôn chuyển người không bao giờ đủ điều kiện.
	"""
	as_of = get_datetime(as_of) if as_of else now_datetime()
	start = add_to_date(as_of, days=-WINDOW_DAYS)
	row = frappe.db.get_value(GROUP, group, ["always_handoff"], as_dict=True)
	decided_rows = frappe.get_all(
		DRAFT,
		filters=[
			[DRAFT, "question_group", "=", group],
			[DRAFT, "mode", "=", "Draft"],
			[DRAFT, "decision", "in", list(DECIDED)],
			[DRAFT, "decided_at", ">", start],
			[DRAFT, "decided_at", "<=", as_of],
		],
		fields=["decision", "edit_distance_ratio"],
	)
	decided = len(decided_rows)
	good = sum(
		1 for r in decided_rows if r.decision in GOOD and (r.edit_distance_ratio or 0) <= MAX_EDIT_RATIO
	)
	touched = frappe.get_all(
		DRAFT,
		filters=[
			[DRAFT, "question_group", "=", group],
			[DRAFT, "mode", "in", ["Draft", "Auto"]],
			[DRAFT, "modified", ">", start],
		],
		fields=["critical", "rating", "rating_note"],
	)
	critical = sum(1 for r in touched if cint(r.critical))
	serious = sum(1 for r in touched if r.rating == "Sai" and (r.rating_note or "").strip())
	span = [
		[DRAFT, "question_group", "=", group],
		[DRAFT, "mode", "=", "Draft"],
		[DRAFT, "creation", ">", start],
		[DRAFT, "creation", "<=", as_of],
	]
	pending = frappe.db.count(DRAFT, [*span, [DRAFT, "decision", "=", "Pending"]])
	expired = frappe.db.count(DRAFT, [*span, [DRAFT, "decision", "=", "Expired"]])
	rate = round(100 * good / decided, 1) if decided else None
	reasons: list[str] = []
	if not row:
		reasons.append(_("Không tìm thấy nhóm câu hỏi"))
	elif cint(row.always_handoff):
		reasons.append(_("Nhóm luôn chuyển người, không bao giờ bật tự động gửi"))
	if decided < MIN_DECIDED:
		reasons.append(
			_("Mới có {0} nháp đã quyết định trong {1} ngày, cần tối thiểu {2}").format(
				decided, WINDOW_DAYS, MIN_DECIDED
			)
		)
	if decided and good * 100 < MIN_RATE_PCT * decided:
		reasons.append(
			_("Tỉ lệ gửi nguyên văn hoặc sửa nhẹ (<= {0}) là {1}%, cần >= {2}%").format(
				MAX_EDIT_RATIO, rate, MIN_RATE_PCT
			)
		)
	if critical:
		reasons.append(_("Có {0} nháp đánh dấu nghiêm trọng trong {1} ngày").format(critical, WINDOW_DAYS))
	if serious:
		reasons.append(_("Có {0} nháp chấm Sai kèm ghi chú trong {1} ngày").format(serious, WINDOW_DAYS))
	return {
		"group": group,
		"as_of": as_of,
		"window_days": WINDOW_DAYS,
		"decided": decided,
		"min_decided": MIN_DECIDED,
		"good": good,
		"rate": rate,
		"min_rate": MIN_RATE_PCT,
		"critical": critical,
		"serious_wrong": serious,
		"pending": pending,
		"expired": expired,
		"eligible": not reasons,
		"reasons": reasons,
	}


# --- Bật / tắt -------------------------------------------------------------------------------------------------


def _require_admin() -> None:
	if ADMIN_ROLE not in frappe.get_roles():
		frappe.throw(_("Chỉ Omni Admin được bật hoặc tắt tự động gửi"), frappe.PermissionError)


def _note_on_group(group: str, mark: str, text: str, user: str | None = None) -> None:
	"""Comment trên nhóm: dòng đầu là dấu máy đọc được (`mark`), rồi mô tả. Không chứa nội dung khách. Không commit."""
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": GROUP,
			"reference_name": group,
			"comment_by": user,
			"content": f"<p>{escape_html(mark)}</p><p>{escape_html(text)}</p>",
		}
	).insert(ignore_permissions=True)


def _lock_group(group: str) -> Any:
	return frappe.db.get_value(
		GROUP, group, ["name", "always_handoff", "auto_send_enabled"], as_dict=True, for_update=True
	)


def enable_group(
	group: str, confirm: bool = False, force: bool = False, reason: str | None = None
) -> dict[str, Any]:
	"""Bật tự động gửi cho nhóm (chỉ Omni Admin). Không đủ điều kiện thì từ chối, trừ `force` kèm lý do (ghi Comment).

	Luôn bị chặn khi khóa hạ tầng đóng hoặc nhóm `always_handoff` (kể cả `force`). Không `confirm` thì chỉ trả số liệu
	để hiện hộp xác nhận. Chạy dưới khóa dòng nhóm. Trả {"status": "confirm_required"|"enabled"|"already", "eligibility": …}.
	"""
	_require_admin()
	if not flags.auto_send_infra_allowed():
		frappe.throw(
			_(
				"Khóa hạ tầng tự động gửi (omni_auto_send_allowed) đang đóng; chỉ người vận hành mở sau khi chủ dự án chốt Q1"
			),
			frappe.ValidationError,
		)
	row = _lock_group(group)
	if not row:
		frappe.throw(_("Không tìm thấy nhóm câu hỏi"), frappe.DoesNotExistError)
	if cint(row.always_handoff):
		frappe.throw(_("Nhóm luôn chuyển người không thể bật tự động gửi"), frappe.ValidationError)
	if cint(row.auto_send_enabled):
		return {"status": "already", "eligibility": eligibility(group)}
	el = eligibility(group)
	forced = False
	if not el["eligible"]:
		if not cint(force):
			frappe.throw(_("Nhóm chưa đủ điều kiện: ") + "; ".join(el["reasons"]), frappe.ValidationError)
		if len((reason or "").strip()) < MIN_FORCE_REASON:
			frappe.throw(
				_("Bật khi chưa đủ điều kiện cần lý do (tối thiểu {0} ký tự)").format(MIN_FORCE_REASON),
				frappe.ValidationError,
			)
		forced = True
	if not cint(confirm):
		return {"status": "confirm_required", "eligibility": el, "forced": forced}
	doc = frappe.get_doc(GROUP, group)
	with enabling():
		doc.auto_send_enabled = 1
		doc.auto_send_enabled_at = now_datetime()
		doc.auto_send_enabled_by = frappe.session.user
		doc.save(ignore_permissions=True, ignore_version=False)
	text = _("{0} bật tự động gửi. Số liệu: {1}/{2} nháp đạt ({3}%), nghiêm trọng {4}.").format(
		frappe.session.user, el["good"], el["decided"], el["rate"], el["critical"] + el["serious_wrong"]
	)
	if forced:
		text += " " + _("BẬT DÙ CHƯA ĐỦ ĐIỀU KIỆN. Lý do: {0}. Chưa đạt: {1}").format(
			(reason or "").strip()[:TEXT_MAX], "; ".join(el["reasons"])
		)
	_note_on_group(group, ENABLE_MARK, text, frappe.session.user)
	return {"status": "enabled", "eligibility": el, "forced": forced}


def disable_group(group: str, reason: str | None = None) -> dict[str, Any]:
	"""Omni Admin tắt tự động gửi của nhóm (nút "Tắt tự động nhóm này"); ghi Comment. Dưới khóa dòng nhóm."""
	_require_admin()
	text = _("{0} tắt tự động gửi. {1}").format(frappe.session.user, (reason or "").strip()[:TEXT_MAX])
	turned = _disable(group, DISABLE_MARK, text, frappe.session.user)
	return {"status": "disabled" if turned else "already_off"}


def _disable(group: str, mark: str, text: str, user: str | None = None) -> bool:
	"""Tắt `auto_send_enabled` dưới khóa dòng nhóm (qua doc.save để có Version); True nếu thực sự vừa tắt. Không commit."""
	row = _lock_group(group)
	if not row or not cint(row.auto_send_enabled):
		return False
	doc = frappe.get_doc(GROUP, group)
	doc.auto_send_enabled = 0
	doc.save(ignore_permissions=True, ignore_version=False)
	_note_on_group(group, mark, text, user)
	return True


# --- Ngắt mạch ---------------------------------------------------------------------------------------------------


def alert_recipients() -> list[str]:
	"""Omni Admin và Sales Manager (Trưởng sale) còn hoạt động."""
	users = frappe.get_all(
		"Has Role",
		filters={"role": ["in", [ADMIN_ROLE, LEAD_MANAGER_ROLE]], "parenttype": "User"},
		pluck="parent",
	)
	users = [u for u in dict.fromkeys(users) if u not in ("Administrator", "Guest")]
	return (
		frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name") if users else []
	)


def trip(group: str, code: str, detail: str = "") -> bool:
	"""Ngắt mạch: tắt tự động gửi của nhóm, ghi lý do (Comment dấu `omni:auto_send:breaker`) và báo Omni Admin + Sales Manager.

	Không có lý do tự do của AI hay lời khách trong thông báo. Trả True nếu nhóm đang bật và vừa bị tắt. Không commit.
	"""
	label = BREAKER_LABELS.get(code, code)
	text = _("Bot tự gửi của nhóm này đã TỰ TẮT: {0}").format(_(label))
	if detail:
		text += f" ({detail[:TEXT_MAX]})"
	if not _disable(group, f"{BREAKER_MARK}:{code}", text):
		return False
	users = alert_recipients()
	if not users:
		frappe.log_error(
			title="Omni: nhóm tự tắt tự gửi nhưng không có người nhận cảnh báo",
			message=f"group={group} code={code}",
		)
	subject = _("Bot tự gửi nhóm {0} đã tự tắt: {1}").format(group, _(label))[:140]
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": escape_html(subject),
					"document_type": GROUP,
					"document_name": group,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Omni: không báo được nhóm tự tắt tự gửi", message=f"group={group}")
	return True


def auto_off_count(group: str) -> int:
	"""Số lần nhóm bị ngắt mạch tự tắt (đếm Comment dấu ngắt mạch)."""
	return frappe.db.count(
		"Comment",
		{
			"reference_doctype": GROUP,
			"reference_name": group,
			"comment_type": "Info",
			"content": ["like", f"%{BREAKER_MARK}%"],
		},
	)


def on_rated(draft: str, rating: str, critical: bool = False) -> bool:
	"""Sale chấm Sai (hoặc đánh dấu nghiêm trọng) một tin bot đã gửi (nháp mode=Auto) thì tắt nhóm của nháp. Không commit."""
	if rating != "Sai" and not critical:
		return False
	try:
		row = frappe.db.get_value(DRAFT, draft, ["mode", "question_group"], as_dict=True)
		if row and row.mode == "Auto" and row.question_group:
			return trip(row.question_group, "rated_wrong")
	except Exception:
		frappe.log_error(title="Omni: lỗi ngắt mạch khi chấm Sai", message=f"draft={draft}")
	return False


def complaint_groups(lead: str, message_at: Any) -> list[str]:
	"""Nhóm có tin bot tự gửi cho Lead trong 24 giờ TRƯỚC tin khách lúc `message_at` (tin bot < tin khách <= tin bot + 24 giờ)."""
	at = get_datetime(message_at)
	rows = frappe.get_all(
		OUTBOUND,
		filters={
			"lead": lead,
			"source": "Bot",
			"status": ["in", ["Queued", "Sent"]],
			"bot_draft": ["is", "set"],
		},
		fields=["bot_draft", "sent_at", "creation"],
	)
	drafts = []
	for r in rows:
		sent = get_datetime(r.sent_at or r.creation)
		if sent < at <= add_to_date(sent, hours=COMPLAINT_HOURS):
			drafts.append(r.bot_draft)
	if not drafts:
		return []
	groups = frappe.get_all(DRAFT, filters={"name": ["in", drafts], "mode": "Auto"}, pluck="question_group")
	return sorted({g for g in groups if g})


def on_analysis(batch_id: str, lead: str | None) -> list[str]:
	"""Sau phân tích (từ `analyze.after_analyze`): tin khách được phân tích là Khiếu nại thì tắt nhóm có tin bot trong 24 giờ trước.

	Không ném lỗi. Commit khi có nhóm bị tắt. Trả danh sách nhóm đã tắt.
	"""
	if not lead:
		return []
	tripped: list[str] = []
	try:
		if reply.analysis_of(batch_id).get("intent") != COMPLAINT_INTENT:
			return []
		times = [m.received_at for m in reply._batch_messages(batch_id) if m.received_at]
		if not times:
			return []
		for group in complaint_groups(lead_bridge.follow_merged(lead) or lead, max(times)):
			if trip(group, "complaint"):
				tripped.append(group)
		if tripped:
			frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Omni: lỗi ngắt mạch khiếu nại", message=f"batch_id={batch_id}")
	return tripped


def handoff_ratio(group: str, now: datetime | str | None = None) -> dict[str, Any]:
	"""Tỉ lệ Handoff của nhóm: 24 giờ gần nhất so với 14 ngày liền trước đó (nháp mode Draft/Auto, theo `creation`).

	`doubled` khi: >= 10 nháp và >= 3 Handoff ở cửa sổ gần, nền có >= 20 nháp, và tỉ lệ gần >= 2 lần tỉ lệ nền
	(nền bằng 0 thì chỉ cần có Handoff). Ngưỡng mẫu nhỏ là lựa chọn của agent, xem docs/12.
	"""
	now = get_datetime(now) if now else now_datetime()
	recent_start = add_to_date(now, hours=-HANDOFF_RECENT_HOURS)
	base_start = add_to_date(recent_start, days=-HANDOFF_BASELINE_DAYS)

	def counts(lo: datetime, hi: datetime) -> tuple[int, int]:
		rows = frappe.get_all(
			DRAFT,
			filters=[
				[DRAFT, "question_group", "=", group],
				[DRAFT, "mode", "in", ["Draft", "Auto"]],
				[DRAFT, "creation", ">", lo],
				[DRAFT, "creation", "<=", hi],
			],
			fields=["action"],
		)
		return len(rows), sum(1 for r in rows if r.action == "Handoff")

	recent_n, recent_h = counts(recent_start, now)
	base_n, base_h = counts(base_start, recent_start)
	doubled = (
		recent_n >= HANDOFF_MIN_RECENT
		and recent_h >= HANDOFF_MIN_RECENT_HANDOFFS
		and base_n >= HANDOFF_MIN_BASELINE
		and recent_h * base_n >= HANDOFF_FACTOR * base_h * recent_n
	)
	return {
		"recent_drafts": recent_n,
		"recent_handoffs": recent_h,
		"baseline_drafts": base_n,
		"baseline_handoffs": base_h,
		"doubled": doubled,
	}


def run_breaker_checks(now: datetime | str | None = None) -> dict[str, int]:
	"""Tác vụ định kỳ (mỗi giờ): nhóm đang Auto có tỉ lệ Handoff tăng gấp đôi thì tắt. Commit sau từng nhóm."""
	out = {"checked": 0, "tripped": 0}
	for group in frappe.get_all(GROUP, filters={"auto_send_enabled": 1}, pluck="name"):
		out["checked"] += 1
		try:
			r = handoff_ratio(group, now)
			if r["doubled"]:
				detail = (
					f"{r['recent_handoffs']}/{r['recent_drafts']} so với "
					f"{r['baseline_handoffs']}/{r['baseline_drafts']}"
				)
				if trip(group, "handoff_doubled", detail):
					out["tripped"] += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Omni: lỗi kiểm ngắt mạch Handoff", message=f"group={group}")
	return out


# --- Điều kiện gửi một nháp ------------------------------------------------------------------------------------


def _get_draft(draft: str, lock: bool = False) -> Any:
	return frappe.db.get_value(
		DRAFT,
		draft,
		[
			"name",
			"lead",
			"identity",
			"channel",
			"batch_id",
			"mode",
			"action",
			"decision",
			"question_group",
			"draft_text",
			"guard_flags",
		],
		as_dict=True,
		for_update=lock,
	)


def _group_on(row: Any, group_row: Any | None) -> bool:
	"""Nhóm của nháp đang tự gửi. Dưới khóa dòng thì PHẢI dùng `group_row` đọc có khóa (đọc thường trong giao dịch
	REPEATABLE READ có thể thấy bản cũ của một lần tắt vừa commit)."""
	if group_row is None:
		return flags.group_auto_send(row.question_group)
	return bool(cint(group_row.auto_send_enabled) and not cint(group_row.always_handoff))


def _common_checks(row: Any, now: datetime, group_row: Any | None = None) -> str | None:
	"""Điều kiện chung của mọi tin tự gửi (câu trả lời và câu giữ chân); trả mã lý do chặn hoặc None."""
	if not flags.auto_send_infra_allowed():
		return "khoa_ha_tang"
	if not _group_on(row, group_row):
		return "nhom_tat"
	if flags.bot_reply_mode() != "Draft":
		return "che_do_bot"
	if not row.identity or not row.channel or not row.lead:
		return "thieu_dinh_danh"
	channel = frappe.db.get_value(CHANNEL, row.channel, ["channel_type", "outbound_enabled"], as_dict=True)
	if not channel or channel.channel_type != "Zalo OA" or not cint(channel.outbound_enabled):
		return "kenh"
	last = (reply._batch_messages(row.batch_id) or [None])[-1]
	if not last or last.event_type != "message" or last.identity != row.identity:
		return "dinh_danh_khong_khop"
	ident = frappe.db.get_value(IDENTITY, row.identity, ["lead"], as_dict=True)
	if not ident or lead_bridge.follow_merged(ident.lead) != lead_bridge.follow_merged(row.lead):
		return "dinh_danh_khong_khop"
	if handoff.is_bot_paused(row.identity):
		return "bot_tam_dung"
	if not handoff.bot_active_now(now):
		return "ngoai_gio_bot"
	if reply.sale_replies_after(lead_bridge.follow_merged(row.lead), last.received_at):
		return "sale_da_tra_loi"
	try:
		channel_doc = frappe.get_doc(CHANNEL, row.channel)
		ok, why = get_adapter(channel_doc.channel_type).can_send_now(
			frappe.get_doc(IDENTITY, row.identity), channel_doc, source="Bot"
		)
	except (AdapterNotFoundError, frappe.DoesNotExistError):
		return "kenh"
	if not ok or why:  # `why` khác rỗng = khung có phí
		return "khung_gui"
	return None


def check_reply(row: Any, now: datetime | None = None, group_row: Any | None = None) -> tuple[bool, str]:
	"""Nháp có được tự gửi không: (True, "") hoặc (False, mã lý do). Chỉ đọc; gọi dưới khóa dòng nhóm rồi nháp,
	`group_row` là dòng nhóm đã đọc CÓ KHÓA."""
	now = now or now_datetime()
	if not row:
		return False, "khong_co_nhap"
	if (row.mode, row.action, row.decision) != ("Draft", "Reply", "Pending"):
		return False, "trang_thai_nhap"
	text = (row.draft_text or "").strip()
	if not text or len(text) > outbound.MAX_TEXT_CHARS:
		return False, "noi_dung"
	if any(f.strip() and not f.strip().startswith("info:") for f in (row.guard_flags or "").splitlines()):
		return False, "chot_chan"
	why = _common_checks(row, now, group_row)
	if why:
		return False, why
	if frappe.db.exists(OUTBOUND, {"bot_draft": row.name}):
		return False, "da_gui"
	# Ngắt mạch khiếu nại chỉ hoạt động khi có phân tích: không có phân tích thì rơi về Draft (an toàn)
	if not flags.bot_analyze_enabled():
		return False, "khong_phan_tich"
	analysis = reply.analysis_of(row.batch_id)
	if not analysis or analysis.get("intent") == COMPLAINT_INTENT or analysis.get("wants_human"):
		return False, "phan_tich"
	recent = frappe.db.count(
		OUTBOUND, {"identity": row.identity, "source": "Bot", "creation": [">", add_to_date(now, hours=-24)]}
	)
	if recent >= MAX_AUTO_PER_IDENTITY_DAY:
		return False, "gioi_han_tan_suat"
	return True, ""


@contextmanager
def _sending(draft: str) -> Iterator[None]:
	"""Đánh dấu duy nhất cho phép `queue_outbound(source="Bot")`: chỉ có hiệu lực cho đúng nháp đang gửi."""
	previous = frappe.flags.get(SENDING_FLAG)
	frappe.flags[SENDING_FLAG] = draft
	try:
		yield
	finally:
		frappe.flags[SENDING_FLAG] = previous


def assert_can_queue(req: OutboundRequest) -> None:
	"""Gọi từ `queue_outbound` cho tin source=Bot: kiểm lần hai MỌI điều kiện; ném ValidationError nếu không đủ."""
	if not req.bot_draft or frappe.flags.get(SENDING_FLAG) != req.bot_draft:
		frappe.throw(_("Không gửi được nháp này"), frappe.ValidationError)
	group = frappe.db.get_value(DRAFT, req.bot_draft, "question_group")
	group_row = _lock_group(group) if group else None  # cùng thứ tự khóa: nhóm rồi nháp
	row = _get_draft(req.bot_draft, lock=True)
	ok, _why = check_reply(row, group_row=group_row)
	if (
		not ok
		or row.identity != req.identity
		or row.channel != req.channel
		or (row.draft_text or "").strip() != (req.text or "").strip()
	):
		frappe.throw(_("Không gửi được nháp này"), frappe.ValidationError)


def send_time_check(row: Any) -> tuple[bool, str]:
	"""Ngay trước khi gửi tin Bot đang chờ (từ `send_outbound`): khóa hạ tầng, nhóm còn bật, bot chưa tạm dừng, trong giờ bot."""
	if not flags.auto_send_infra_allowed():
		return False, _("Khóa hạ tầng tự động gửi đang đóng")
	draft = (
		frappe.db.get_value(
			DRAFT, row.bot_draft, ["mode", "action", "identity", "question_group"], as_dict=True
		)
		if row.get("bot_draft")
		else None
	)
	if not draft or draft.mode != "Auto" or draft.action != "Reply" or draft.identity != row.identity:
		return False, _("Tin bot không có nháp hợp lệ")
	if not flags.group_auto_send(draft.question_group):
		return False, _("Nhóm câu hỏi đã tắt tự động gửi")
	if handoff.is_bot_paused(row.identity):
		return False, _("Bot đang tạm dừng với khách này")
	if not handoff.bot_active_now():
		return False, _("Ngoài giờ bot được tự gửi")
	return True, ""


def try_auto_send(draft: str) -> bool:
	"""Nháp Draft/Reply vừa tạo: nếu đủ mọi điều kiện thì xếp tin bot để gửi và chuyển nháp sang Auto/Sent As Is.

	Trả True nếu đã xếp tin (nháp không còn chờ sale duyệt); False thì nháp giữ nguyên là nháp cho sale. Không commit
	(người gọi commit); không bao giờ ném lỗi. Khóa dòng nhóm rồi dòng nháp; mọi thay đổi nằm trong một savepoint.
	"""
	try:
		group = frappe.db.get_value(DRAFT, draft, "question_group")
		if not group or not flags.group_auto_send(group):  # đường nhanh: tắt thì không khóa gì
			return False
		group_row = _lock_group(group)
		row = _get_draft(draft, lock=True)
		ok, code = check_reply(row, group_row=group_row)
		if not ok:
			_mark_skipped(draft, row, code)
			return False
		frappe.db.savepoint(SAVEPOINT)
		try:
			with _sending(draft):
				outbound.queue_outbound(
					OutboundRequest(
						channel=row.channel,
						identity=row.identity,
						text=row.draft_text.strip(),
						reply_to_external_id=None,
						source="Bot",
						bot_draft=draft,
					)
				)
			frappe.db.set_value(
				DRAFT,
				draft,
				{
					"mode": "Auto",
					"decision": "Sent As Is",
					"final_text": row.draft_text.strip(),
					"edit_distance_ratio": 0.0,
					"decided_at": now_datetime(),
				},
			)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		return True
	except Exception:
		frappe.log_error(
			title="Omni: lỗi bot tự gửi",
			message=redact(f"draft={draft}\n{frappe.get_traceback(with_context=False)[-800:]}"),
		)
		return False


def _mark_skipped(draft: str, row: Any, code: str) -> None:
	"""Nhóm đang bật mà nháp rơi về Draft thì ghi mã lý do vào cờ nháp (để người duyệt hiểu)."""
	if not row or row.decision != "Pending" or code in ("nhom_tat", "khoa_ha_tang", "khong_co_nhap"):
		return
	text = "\n".join(x for x in [row.guard_flags, f"info:auto_khong_gui:{code}"] if x)
	frappe.db.set_value(DRAFT, draft, "guard_flags", text, update_modified=False)


def try_holding(draft: str) -> bool:
	"""Nháp Handoff (Draft) của nhóm đang Auto: gửi MỘT câu giữ chân (`holding_message`) bằng source=System, không liên kết nháp.

	Cùng mọi điều kiện chung của tin tự gửi (khóa hạ tầng, nhóm bật, khung miễn phí, giờ bot, chưa tạm dừng, sale chưa trả
	lời). Phải gọi TRƯỚC `handoff.handoff` (nó tạm dừng bot). Mỗi nháp một lần (dấu trong cờ nháp). Không commit, không ném lỗi.
	"""
	try:
		group = frappe.db.get_value(DRAFT, draft, "question_group")
		if not group or not flags.group_auto_send(group):
			return False
		group_row = _lock_group(group)
		row = _get_draft(draft, lock=True)
		if not row or (row.mode, row.action, row.decision) != ("Draft", "Handoff", "Pending"):
			return False
		if HOLDING_MARK in (row.guard_flags or ""):
			return False
		if _common_checks(row, now_datetime(), group_row):
			return False
		text = handoff.holding_message().strip()
		if not text:
			return False
		frappe.db.set_value(
			DRAFT,
			draft,
			"guard_flags",
			"\n".join(x for x in [row.guard_flags, HOLDING_MARK] if x),
			update_modified=False,
		)
		frappe.db.savepoint(SAVEPOINT)
		try:
			outbound.queue_outbound(
				OutboundRequest(
					channel=row.channel,
					identity=row.identity,
					text=text,
					reply_to_external_id=None,
					source="System",
				)
			)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		return True
	except Exception:
		frappe.log_error(
			title="Omni: lỗi gửi câu giữ chân",
			message=redact(f"draft={draft}\n{frappe.get_traceback(with_context=False)[-800:]}"),
		)
		return False


# --- Số liệu cho báo cáo ---------------------------------------------------------------------------------------


def group_overview(
	from_date: str | None = None, to_date: str | None = None, as_of: datetime | None = None
) -> list[dict]:
	"""Theo từng nhóm: trạng thái Auto, eligibility hiện tại, số tin bot đã gửi (trong khoảng ngày), số lần tự tắt. Chỉ số đếm."""
	groups = frappe.get_all(
		GROUP, fields=["name", "always_handoff", "auto_send_enabled"], order_by="name asc"
	)
	filters: dict[str, Any] = {"source": "Bot", "status": "Sent", "bot_draft": ["is", "set"]}
	if from_date and to_date:
		filters["creation"] = ["between", [from_date, f"{to_date} 23:59:59"]]
	elif from_date:
		filters["creation"] = [">=", from_date]
	elif to_date:
		filters["creation"] = ["<=", f"{to_date} 23:59:59"]
	sent = frappe.get_all(OUTBOUND, filters=filters, pluck="bot_draft")
	by_draft = (
		{
			r.name: r
			for r in frappe.get_all(
				DRAFT, filters={"name": ["in", sent]}, fields=["name", "question_group", "rating"]
			)
		}
		if sent
		else {}
	)
	out = []
	for g in groups:
		el = eligibility(g.name, as_of)
		mine = [by_draft[d] for d in sent if d in by_draft and by_draft[d].question_group == g.name]
		out.append(
			{
				"question_group": g.name,
				"auto_enabled": 1 if cint(g.auto_send_enabled) else 0,
				"always_handoff": 1 if cint(g.always_handoff) else 0,
				"eligible": 1 if el["eligible"] else 0,
				"decided": el["decided"],
				"rate": el["rate"],
				"critical": el["critical"] + el["serious_wrong"],
				"bot_sent": len(mine),
				"rating_ok": sum(1 for d in mine if d.rating == "Đúng"),
				"rating_wrong": sum(1 for d in mine if d.rating == "Sai"),
				"rating_missing": sum(1 for d in mine if d.rating == "Thiếu"),
				"auto_off": auto_off_count(g.name),
			}
		)
	return out
