"""Gửi tin kịch bản qua đường gửi chung (`pipeline.outbound.queue_outbound`, nguồn `Bot`).

Quy tắc an toàn số 1: `switch.enabled_for(identity)` sai thì KHÔNG tạo tin nào (trả None). Công tắc được kiểm lại ngay
trước khi gửi (`send_time_check`, gọi từ `bot.auto_send.send_time_check`).

Khóa hạ tầng: khách NGOÀI danh sách thử (tức là đang chạy nhờ `script_bot_enabled`=1) chỉ được nhận tin khi site_config
có `omni_script_bot_allowed` đúng (ngoài DB, như `omni_auto_send_allowed`). Khách trong danh sách thử không cần khóa này.

Mỗi tin ghi `script_batch` = lượt đang xử lý (router đặt `frappe.flags.omni_script_batch`) để chống trả lời hai lần.
Tin kèm nút dài quá `BUTTON_TEXT_MAX` bị từ chối ngay lúc xếp (trả None) để nơi gọi còn kịp đi đường khác. Tin kịch bản
bị chặn lúc gửi vì lý do khác công tắc / không liên hệ / rác thì báo người phụ trách (`on_blocked`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm.bot import handoff
from omni_crm.channels.zalo.client import BUTTON_TEXT_MAX
from omni_crm.ingest.events import OutboundRequest
from omni_crm.omni_crm.doctype.omni_script_node.omni_script_node import validate_buttons
from omni_crm.pipeline.retry import redact
from omni_crm.script import store, switch

OUTBOUND = "Omni Outbound Message"
IDENTITY = "Omni Identity"
INBOUND = "Omni Inbound Message"
SENDING_FLAG = "omni_script_sending"  # frappe.flags: định danh đang được send.py xếp tin (auto_send.assert_can_queue đọc)
SAVEPOINT = "omni_script_queue"
TEXT_CODE = "_TEXT"  # script_node của tin gửi bằng send_text không kèm mã (câu Kho tri thức)
JUNK_STATUS = "Junk"  # CRM Lead Status coi là spam (không có trường spam riêng)
BATCH_FLAG = (
	"omni_script_batch"  # frappe.flags: lượt tin router đang xử lý, ghi vào `script_batch` của tin ra
)
INFRA_CONF = "omni_script_bot_allowed"  # site_config: khóa hạ tầng cho khách ngoài danh sách thử
# Mã lý do chặn: nhóm "im lặng có chủ ý" (không báo nhân viên khi tin bị chặn lúc gửi)
SWITCH, INFRA, PAUSED, DNC, JUNK = "switch", "infra", "paused", "dnc", "junk"
QUIET_BLOCKS = frozenset({SWITCH, INFRA, DNC, JUNK})


@contextmanager
def _sending(identity: str) -> Iterator[None]:
	frappe.flags[SENDING_FLAG] = identity
	try:
		yield
	finally:
		frappe.flags.pop(SENDING_FLAG, None)


def is_script_sending(identity: str | None) -> bool:
	"""Lời gọi `queue_outbound` hiện tại đến từ send.py cho đúng định danh này."""
	return bool(identity) and frappe.flags.get(SENDING_FLAG) == identity


def channel_of(identity: str) -> str | None:
	"""Kênh (Omni Channel) của định danh: kênh của tin vào gần nhất."""
	return frappe.db.get_value(INBOUND, {"identity": identity}, "channel", order_by="received_at desc")


def infra_allowed() -> bool:
	"""Khóa hạ tầng NGOÀI DB cho bot kịch bản (site_config `omni_script_bot_allowed`); vắng, sai kiểu hay lỗi thì chặn."""
	try:
		return bool(cint(frappe.conf.get(INFRA_CONF)))
	except Exception:
		return False


def infra_ok(identity: str | None) -> bool:
	"""Định danh trong danh sách thử, hoặc khóa hạ tầng đang mở (khách chạy nhờ `script_bot_enabled`)."""
	return switch.is_test(identity) or infra_allowed()


def block_code(identity: str | None) -> str | None:
	"""Mã lý do chặn tin kịch bản lúc này: switch / infra / paused / dnc / junk; None nếu được gửi."""
	if not switch.enabled_for(identity):
		return SWITCH
	if not infra_ok(identity):
		return INFRA
	if handoff.is_bot_paused(identity):
		return PAUSED
	lead = frappe.db.get_value(IDENTITY, identity, "lead")
	if lead:
		row = frappe.db.get_value("CRM Lead", lead, ["status", "omni_do_not_contact"], as_dict=True) or {}
		if cint(row.get("omni_do_not_contact")):
			return DNC
		if row.get("status") == JUNK_STATUS:
			return JUNK
	return None


def blocked_reason(identity: str | None) -> str | None:
	"""Lý do không được gửi tin kịch bản cho định danh lúc này (công tắc, tạm dừng, không liên hệ, spam); None nếu được."""
	labels = {
		SWITCH: _("Bot kịch bản đang tắt với khách này"),
		INFRA: _("Khóa hạ tầng bot kịch bản đang đóng"),
		PAUSED: _("Bot đang tạm dừng với khách này"),
		DNC: _("Lead đánh dấu không liên hệ"),
		JUNK: _("Lead bị đánh dấu rác"),
	}
	return labels.get(block_code(identity))


def send_node(identity: str, code: str, ctx: dict | None = None, *, purpose: str = "reply") -> str | None:
	"""Xếp tin của ô kịch bản `code` cho định danh; trả tên Omni Outbound Message hoặc None nếu không gửi."""
	if not switch.enabled_for(identity):
		return None
	node = store.get_node(code)
	rendered = store.render(node, {**(ctx or {}), "identity": identity})
	if not rendered:
		return None
	text, buttons = rendered
	return _queue(identity, text, buttons, node["code"], purpose, node_channel=node.get("channel"))


def send_text(
	identity: str, text: str, buttons: list[dict] | None = None, code: str | None = None
) -> str | None:
	"""Xếp một câu có sẵn (vd câu Kho tri thức) kèm nút tùy chọn; trả tên Omni Outbound Message hoặc None."""
	if not switch.enabled_for(identity) or not (text or "").strip():
		return None
	btns = [
		{"label": (b.get("label") or "").strip(), "target": store.normalize_code(b.get("target"))}
		for b in buttons or []
	]
	return _queue(identity, text.strip(), btns, store.normalize_code(code) or TEXT_CODE, "reply")


def _queue(
	identity: str,
	text: str,
	buttons: list[dict],
	code: str,
	purpose: str,
	node_channel: str | None = None,
) -> str | None:
	from omni_crm.pipeline import outbound  # import trễ: outbound -> auto_send -> (lazy) script.send

	try:
		validate_buttons([(b["label"], b["target"]) for b in buttons])
	except frappe.ValidationError:
		_log("nut_khong_hop_le", identity, code)
		return None
	if buttons and len(text) > BUTTON_TEXT_MAX:  # Zalo từ chối vĩnh viễn: không xếp, để nơi gọi đi đường khác
		_log("tin_kem_nut_qua_dai", identity, code)
		return None
	text, choices = with_choice_list(text, buttons)
	if blocked_reason(identity):
		return None
	channel = channel_of(identity)
	if not channel:
		_log("khong_co_kenh", identity, code)
		return None
	channel_type = frappe.db.get_value("Omni Channel", channel, "channel_type")
	if node_channel and node_channel not in ("Tất cả", channel_type):
		return None
	req = OutboundRequest(channel, identity, text, None, "Bot", purpose=purpose)
	frappe.db.savepoint(SAVEPOINT)
	try:
		with _sending(identity):
			name = outbound.queue_outbound(req)
	except frappe.ValidationError as e:  # gồm RoutingError (ngoài khung gửi miễn phí...)
		frappe.db.rollback(save_point=SAVEPOINT)  # giữ phần handler đã ghi (state...) chưa commit
		_log(f"khong_xep_duoc:{type(e).__name__}", identity, code)
		return None
	frappe.db.set_value(
		OUTBOUND,
		name,
		{
			"script_node": code,
			"buttons": json.dumps(buttons, ensure_ascii=False) if buttons else None,
			"script_batch": frappe.flags.get(BATCH_FLAG) or None,
		},
		update_modified=False,
	)
	frappe.db.set_value(IDENTITY, identity, "script_last_bot_at", now_datetime(), update_modified=False)
	_remember_choices(identity, choices)
	frappe.db.commit()  # một tin = một đơn vị việc; job gửi chạy sau commit
	return name


CHOICE_MIN = 2  # từ 2 nút trở lên mới kèm danh sách lựa chọn trong phần chữ
CHOICE_HINT_NUMBERED = "(Anh/chị bấm nút, hoặc gõ số tương ứng để chọn.)"
CHOICE_HINT_PLAIN = "(Anh/chị bấm nút, hoặc gõ lại đúng lựa chọn.)"


def with_choice_list(text: str, buttons: list[dict]) -> tuple[str, dict | None]:
	"""(phần chữ kèm danh sách lựa chọn, dữ liệu lựa chọn để hiểu câu khách gõ) — thuần.

	Zalo trên máy tính không hiện nút: lặp các nút thành danh sách trong phần chữ. Đánh số 1..n để khách gõ số;
	nhãn nút đã chứa chữ số (vd "2–5", "Dưới 500") thì KHÔNG đánh số (gõ "3" sẽ không rõ là thứ tự hay số lượng),
	chỉ liệt kê để khách gõ lại nhãn. Quá giới hạn độ dài của tin kèm nút thì giữ nguyên phần chữ.
	"""
	if len(buttons or []) < CHOICE_MIN:
		return text, None
	labels = [b["label"] for b in buttons]
	numbered = not any(ch.isdigit() for label in labels for ch in label)
	lines = [f"{i}. {label}" if numbered else f"• {label}" for i, label in enumerate(labels, 1)]
	hint = CHOICE_HINT_NUMBERED if numbered else CHOICE_HINT_PLAIN
	listed = f"{text}\n\n" + "\n".join(lines) + f"\n{hint}"
	if len(listed) > BUTTON_TEXT_MAX:
		return text, None
	return listed, {"numbered": numbered, "items": [[b["label"], b["target"]] for b in buttons]}


def _remember_choices(identity: str, choices: dict | None) -> None:
	"""Ghi (hoặc xóa) lựa chọn của tin vừa xếp vào `script_state.choices`. Không commit, không ném lỗi."""
	try:
		from omni_crm.script import state

		state.update_state(identity, choices=choices)
	except Exception:
		_log("khong_ghi_duoc_lua_chon", identity, "")


def send_time_check(row: Any) -> tuple[bool, str]:
	"""Ngay trước khi gửi tin kịch bản đang chờ (từ `auto_send.send_time_check`): kiểm lại công tắc và điều kiện dừng."""
	why = blocked_reason(row.get("identity"))
	return (False, why) if why else (True, "")


def batch_has_outbound(batch_id: str | None) -> bool:
	"""Lượt này đã có tin kịch bản được xếp (đã commit) hay chưa: dấu bền để không trả lời hai lần."""
	return bool(batch_id) and bool(frappe.db.exists(OUTBOUND, {"script_batch": batch_id}))


def on_blocked(row: Any) -> bool:
	"""Tin kịch bản bị chặn lúc gửi (gọi từ `auto_send.revert_to_draft`): báo người phụ trách, trừ khi bị chặn vì
	công tắc / khóa hạ tầng / không liên hệ / rác. Trả True nếu đã báo. Không ném lỗi."""
	try:
		if block_code(row.get("identity")) in QUIET_BLOCKS:
			return False
		lead = row.get("lead") or frappe.db.get_value(IDENTITY, row.get("identity"), "lead")
		if not lead:
			return False
		subject = _("Tin bot kịch bản không gửi được, khách đang chờ trả lời: {0}").format(lead)[:140]
		users = handoff.recipients(lead)
		for user in users:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": frappe.utils.escape_html(subject),
					"document_type": "CRM Lead",
					"document_name": lead,
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
		return bool(users)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title="Omni: không báo được tin kịch bản bị chặn", message=f"outbound={row.get('name')}"
		)
		return False


def buttons_of(outbound_name: str | None) -> list[dict]:
	"""Danh sách nút đã ghi trên Omni Outbound Message (trống nếu không có)."""
	if not outbound_name:
		return []
	raw = frappe.db.get_value(OUTBOUND, outbound_name, "buttons")
	try:
		value = json.loads(raw) if raw else []
	except (TypeError, ValueError):
		return []
	return [b for b in value if isinstance(b, dict) and b.get("label") and b.get("target")]


def _log(kind: str, identity: str | None, code: str | None) -> None:
	"""Ghi log nội bộ (không có nội dung tin, không bí mật)."""
	frappe.logger("omni").info(
		"omni script: bỏ qua gửi (%s) identity=%s node=%s", kind, identity, redact(code or "")
	)
