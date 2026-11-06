"""API của tab "Trợ lý" trên trang Lead (V3-05). Mọi hàm là whitelist thường (không allow_guest), chạy dưới phiên user.

- `tab_state`, `start_session`, `chat`, `approve`, `reject`, `close_session`.
- Công tắc tổng `assistant_enabled` tắt: mọi hàm (trừ `tab_state`) từ chối ngay bằng thông báo gọn (không 500).
- Chủ phiên mới được chat/duyệt/bỏ/đóng trong phiên đó. Lead đang mở phải đọc được thì mới vào tập Lead của phiên.
- Thiếu khóa AI, hết hạn mức token, quá 20 lượt/giờ: trả `{"error": ...}` để giao diện hiện rõ, không ném lỗi 500.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from omni_crm import flags
from omni_crm.assistant import loop, tools
from omni_crm.bot import ai_client
from omni_crm.crm_bridge import lead as lead_bridge

RATE_PER_HOUR = 20
TRANSCRIPT_MAX_TURNS = 400  # số dòng lịch sử hiển thị giữ lại mỗi phiên (cũ nhất bị bỏ trước)
MAX_COMMAND_CHARS = 2000
SETTINGS_URL = "/app/omni-settings"


def _require_enabled() -> None:
	if frappe.session.user == "Guest":
		frappe.throw(_("Cần đăng nhập"), frappe.PermissionError)
	if not flags.assistant_enabled():
		frappe.throw(_("Trợ lý AI đang tắt. Quản trị bật ở Omni Settings."), title=_("Trợ lý AI"))


def _has_key() -> bool:
	if ai_client._client_override is not None:
		return True
	try:
		return bool(frappe.get_cached_doc("Omni Settings").get_password("ai_api_key", raise_exception=False))
	except Exception:
		return False


def _own_session(session_id: str) -> Any:
	if not session_id or not frappe.db.exists(tools.SESSION, session_id):
		frappe.throw(_("Không tìm thấy phiên trợ lý"), frappe.DoesNotExistError)
	doc = frappe.get_doc(tools.SESSION, session_id)
	if doc.user != frappe.session.user:
		frappe.throw(_("Phiên này không phải của bạn"), frappe.PermissionError)
	return doc


def _rate_ok(user: str) -> bool:
	"""Tối đa 20 lượt chat mỗi người mỗi giờ (bộ đếm Redis theo giờ). Redis lỗi thì cho qua."""
	try:
		cache = frappe.cache()
		key = cache.make_key(f"omni:assistant:rate:{user}:{now_datetime().strftime('%Y%m%d%H')}")
		count = cache.incr(key)
		if count == 1:
			cache.expire(key, 3700)
		return int(count) <= RATE_PER_HOUR
	except Exception:
		return True


def _new_session(lead: str | None) -> str:
	allowed = []
	if lead:
		if not frappe.db.exists("CRM Lead", lead):
			frappe.throw(_("Không tìm thấy Lead"), frappe.DoesNotExistError)
		lead = lead_bridge.follow_merged(lead) or lead
		if not frappe.has_permission("CRM Lead", "read", doc=lead):
			frappe.throw(_("Bạn không có quyền xem Lead này"), frappe.PermissionError)
		allowed = [lead]
	name = tools._sys_insert(
		{
			"doctype": tools.SESSION,
			"user": frappe.session.user,
			"lead": lead or None,
			"status": "Open",
			"allowed_leads": json.dumps(allowed),
			"messages": "[]",
		}
	)
	frappe.db.commit()
	return name


@frappe.whitelist()
def tab_state() -> dict:
	"""Có hiện tab Trợ lý không (công tắc `assistant_enabled`, người dùng đã đăng nhập)."""
	return {"enabled": bool(flags.assistant_enabled() and frappe.session.user != "Guest")}


def _latest_session(lead: str | None) -> str | None:
	"""Phiên gần nhất của CHÍNH người dùng trên Lead này (Lead đã gộp thì theo Lead chính)."""
	if not lead or not frappe.db.exists("CRM Lead", lead):
		return None
	lead = lead_bridge.follow_merged(lead) or lead
	return frappe.db.get_value(
		tools.SESSION, {"user": frappe.session.user, "lead": lead}, "name", order_by="modified desc"
	)


def history(session: str) -> list[dict]:
	"""Lịch sử hiển thị của phiên; trạng thái thẻ duyệt đọc lại từ Omni Assistant Action (đã duyệt/bỏ ở lần trước)."""
	turns = json.loads(frappe.db.get_value(tools.SESSION, session, "transcript") or "[]")
	names = [a["action"] for t in turns for a in t.get("actions") or [] if a.get("action")]
	status = dict(
		frappe.get_all(
			tools.ACTION, filters={"name": ["in", names or [""]]}, fields=["name", "status"], as_list=True
		)
	)
	for t in turns:
		for a in t.get("actions") or []:
			if a.get("action") in status:
				a["status"] = status[a["action"]]
	return turns


def _append_history(session: str, *turns: dict) -> None:
	rows = json.loads(frappe.db.get_value(tools.SESSION, session, "transcript") or "[]")
	rows.extend(turns)
	tools._sys_update(
		tools.SESSION,
		session,
		{"transcript": json.dumps(rows[-TRANSCRIPT_MAX_TURNS:], ensure_ascii=False, default=str)},
	)
	frappe.db.commit()


@frappe.whitelist(methods=["POST"])
def start_session(lead: str | None = None, new: int | str = 0) -> dict:
	"""Mở lại phiên gần nhất của người dùng trên Lead này (kèm lịch sử), hoặc phiên mới khi `new=1` / chưa có phiên.

	Báo kèm có khóa AI chưa (thiếu thì giao diện hiện link tới Omni Settings).
	"""
	_require_enabled()
	session = None if cint(new) else _latest_session(lead)
	if session:
		if lead and not frappe.has_permission(
			"CRM Lead", "read", doc=frappe.db.get_value(tools.SESSION, session, "lead")
		):
			frappe.throw(_("Bạn không có quyền xem Lead này"), frappe.PermissionError)
		if frappe.db.get_value(tools.SESSION, session, "status") != "Open":
			tools._sys_update(tools.SESSION, session, {"status": "Open"})
			frappe.db.commit()
	else:
		session = _new_session(lead)
	return {
		"session": session,
		"history": history(session),
		"has_key": _has_key(),
		"settings_url": SETTINGS_URL,
	}


def _card(action: dict) -> dict:
	"""Phần giao diện cần của một thao tác: dòng có link (đã ghi) hoặc thẻ duyệt (chờ duyệt)."""
	out = {"tool": action["tool"], "status": action["status"], "action": action.get("action")}
	if action["status"] == "Pending":
		out.update({"lead": action["lead"], "changes": action["changes"]})
	elif action["status"] == tools.DONE:
		res = action.get("result") or {}
		links = []
		if res.get("care_log"):
			links.append({"doctype": tools.LOG, "name": res["care_log"], "label": _("Nhật ký chăm sóc")})
		if res.get("crm_task"):
			links.append({"doctype": tools.TASK, "name": res["crm_task"], "label": _("Việc")})
		out["links"] = links
	else:
		out["error"] = action.get("error") or ""
	return out


@frappe.whitelist(methods=["POST"])
def chat(message: str, session_id: str | None = None, lead: str | None = None) -> dict:
	"""Gửi một câu lệnh cho trợ lý; trả {session, reply, actions, error?}. Tạo phiên mới nếu chưa có `session_id`."""
	_require_enabled()
	session = _own_session(session_id).name if session_id else _new_session(lead)
	if frappe.db.get_value(tools.SESSION, session, "status") != "Open":
		return {"session": session, "error": _("Phiên đã đóng, tải lại trang để mở phiên mới.")}
	text = (message or "").strip()
	if not text:
		return {"session": session, "error": _("Hãy nhập câu lệnh")}
	if len(text) > MAX_COMMAND_CHARS:
		return {"session": session, "error": _("Câu lệnh dài quá {0} ký tự").format(MAX_COMMAND_CHARS)}
	if not _has_key():
		return {
			"session": session,
			"error": _("Chưa cấu hình khoá AI. Quản trị dán khoá ở Omni Settings."),
			"settings_url": SETTINGS_URL,
		}
	if not _rate_ok(frappe.session.user):
		return {
			"session": session,
			"error": _("Bạn đã dùng {0} lượt trong giờ này, thử lại sau.").format(RATE_PER_HOUR),
		}
	if not loop.budget_left(loop.settings()["token_limit"]):
		return {
			"session": session,
			"error": _("Trợ lý đã dùng hết hạn mức token hôm nay, mai dùng tiếp được."),
		}
	result = loop.run_turn(session, text)
	cards = [_card(a) for a in result["actions"]]
	now = str(now_datetime())
	_append_history(
		session,
		{"role": "user", "text": text, "at": now},
		{"role": "assistant", "text": result["reply"], "actions": cards, "at": now},
	)
	return {"session": session, "reply": result["reply"], "stopped": result["stopped"], "actions": cards}


def _pending_action(action_name: str) -> Any:
	if not action_name or not frappe.db.exists(tools.ACTION, action_name):
		frappe.throw(_("Không tìm thấy đề xuất"), frappe.DoesNotExistError)
	action = frappe.get_doc(tools.ACTION, action_name)
	_own_session(action.session)
	if not action.requires_approval or action.status != "Pending":
		frappe.throw(_("Đề xuất này đã được xử lý hoặc không cần duyệt"))
	return action


@frappe.whitelist(methods=["POST"])
def approve(action_name: str) -> dict:
	"""Duyệt đề xuất sửa Lead: kiểm lại quyền, trường, giá trị rồi mới ghi (bằng quyền của người duyệt)."""
	_require_enabled()
	action = _pending_action(action_name)
	user = frappe.session.user
	args = json.loads(action.arguments or "{}")
	lead = lead_bridge.follow_merged(action.target_name) or action.target_name
	try:
		changes = tools.apply_lead_update(user, lead, args.get("fields") or {})
	except tools.ToolError as e:
		frappe.db.rollback()
		frappe.throw(str(e))
	tools._sys_update(
		tools.ACTION,
		action.name,
		{
			"status": tools.DONE,
			"approved_by": user,
			"approved_at": now_datetime(),
			"result": json.dumps({"lead": lead, "applied": changes}, ensure_ascii=False),
		},
	)
	frappe.db.commit()
	return {"action": action.name, "status": tools.DONE, "lead": lead, "applied": changes}


@frappe.whitelist(methods=["POST"])
def reject(action_name: str) -> dict:
	"""Bỏ đề xuất sửa Lead: không ghi gì vào Lead."""
	_require_enabled()
	action = _pending_action(action_name)
	tools._sys_update(
		tools.ACTION,
		action.name,
		{"status": "Rejected", "approved_by": frappe.session.user, "approved_at": now_datetime()},
	)
	frappe.db.commit()
	return {"action": action.name, "status": "Rejected"}


@frappe.whitelist(methods=["POST"])
def close_session(session_id: str) -> dict:
	"""Đóng phiên khi người dùng rời trang Lead (gọi lại nhiều lần vẫn được)."""
	doc = _own_session(session_id)
	if doc.status != "Closed":
		tools._sys_update(tools.SESSION, doc.name, {"status": "Closed"})
		frappe.db.commit()
	return {"session": doc.name, "status": "Closed"}
