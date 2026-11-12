"""Ghi dòng thời gian của Lead/Deal (ADR 0004: mỗi tin Zalo là một Communication, medium "Chat").

CRM lấy hoạt động của Lead/Deal từ `frappe.desk.form.load.get_docinfo`, trong đó Communication có `communication_type`
IN ('Communication', 'Feedback', 'Automated Message') và trỏ tới Lead/Deal đều được trả về (không lọc theo medium).
Từ V3-02 (ADR 0017) tin Zalo OA ghi với `communication_type = "Chat"` nên KHÔNG hiện ở tab Hoạt động/Email (đã có tab
Zalo); vẫn là Communication thật (hook CRM cập nhật `communication_status` như cũ). Khóa chống ghi trùng là Custom Field
`omni_inbound_message` / `omni_outbound_message` trên Communication.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html, get_datetime, now_datetime, strip_html_tags

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge

LEAD = "CRM Lead"
DEAL = "CRM Deal"
INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
COMMUNICATION = "Communication"
EMAIL = "Email"
ZALO = "Zalo OA"
# Sự kiện có nội dung hội thoại để hiện trên dòng thời gian (follow/unfollow/user_info... không hiện).
TIMELINE_EVENTS = {"message", "oa_echo", "form_lead"}
SUBJECT_LEN = 80
REALTIME_EVENT = "omni_inbox_update"
# Bộ nghe có sẵn của giao diện CRM (crm/frontend/src/socket.js): nhận khóa đệm rồi tự tải lại tài nguyên đó.
# Khối hoạt động/email của trang Lead/Deal có khóa ["activity", tên] (components/Activities/Activities.vue).
REFETCH_EVENT = "refetch_resource"


def resolve_target(target: str | None) -> tuple[str, str] | None:
	"""(doctype, tên) của Lead hoặc Deal theo tên; None nếu rỗng hoặc không tồn tại."""
	if not target:
		return None
	if frappe.db.exists(DEAL, target):
		return DEAL, target
	if frappe.db.exists(LEAD, target):
		return LEAD, target
	return None


def short_external_id(channel_type: str | None, external_id: str | None) -> str:
	"""Mã rút gọn để làm nhãn dự phòng, ví dụ "Zalo OA …8383"; không bao giờ trả mã dài nguyên vẹn."""
	tail = "".join(ch for ch in str(external_id or "") if ch.isalnum())[-4:]
	label = channel_type or _("Khách")
	return f"{label} …{tail}" if tail else str(label)


def sender_label(
	display_name: str | None,
	fallback_name: str | None,
	channel_type: str | None,
	external_id: str | None,
) -> str:
	"""Tên người gửi tin vào: `Omni Identity.display_name`, rồi tên hiển thị của Lead/Deal, cuối cùng mã rút gọn."""
	for candidate in (display_name, fallback_name):
		if candidate and str(candidate).strip():
			return str(candidate).strip()
	return short_external_id(channel_type, external_id)


def target_display_name(target: tuple[str, str] | None) -> str | None:
	"""Tên hiển thị của Lead/Deal (trường `lead_name`); None nếu không có."""
	if not target:
		return None
	return frappe.db.get_value(target[0], target[1], "lead_name") or None


def _identity_display_name(row: Any) -> str | None:
	"""`display_name` của định danh gửi tin. Worker chỉ ghi `identity` lên tin SAU khi ghi dòng thời gian, nên lần đầu
	phải tra theo `identity_key`."""
	if row.identity:
		return frappe.db.get_value("Omni Identity", row.identity, "display_name")
	if row.identity_key:
		return frappe.db.get_value("Omni Identity", {"identity_key": row.identity_key}, "display_name")
	return None


def render_text(text: str | None, attachments: list[dict] | None = None) -> str:
	"""HTML an toàn cho nội dung tin: thoát ký tự, xuống dòng thành <br>, đính kèm là liên kết http(s)."""
	html = escape_html(text or "").replace("\r\n", "\n").replace("\n", "<br>")
	links = []
	for att in attachments or []:
		url = str(att.get("url") or "")
		if url.startswith(("http://", "https://")):
			label = escape_html(str(att.get("name") or url))
			links.append(f'<a href="{escape_html(url)}" target="_blank" rel="noopener">{label}</a>')
	if links:
		html += "<br>" + "<br>".join(links)
	return f"<p>{html}</p>"


def parse_attachments(raw: Any) -> list[dict]:
	"""Danh sách đính kèm từ trường JSON (chuỗi hoặc list); dữ liệu hỏng thì trả danh sách rỗng."""
	if not raw:
		return []
	try:
		value = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return []
	return [a for a in value if isinstance(a, dict)] if isinstance(value, list) else []


def _subject(channel_type: str, text: str | None) -> str:
	snippet = " ".join((text or "").split())[:SUBJECT_LEN]
	return f"[{channel_type}] {snippet}".strip()


def communication_type_for(channel_type: str | None) -> str:
	"""`Communication.communication_type` cho tin của kênh: "Chat" (ẩn khỏi Hoạt động/Email) với Zalo OA, trừ khi bật
	`Omni Settings.zalo_in_activity_enabled`; kênh khác (form quảng cáo, web form) giữ "Communication"."""
	if channel_type == ZALO and not flags.zalo_in_activity_enabled():
		return "Chat"
	return "Communication"


def _insert_communication(values: dict, channel_type: str | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": COMMUNICATION,
			"communication_type": communication_type_for(channel_type),
			"communication_medium": "Chat",
			**values,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def _can_repoint(current: tuple[str | None, str | None], target: tuple[str, str]) -> bool:
	"""Có được trỏ Communication về `target` không: chưa có tham chiếu, đã đúng đích, hoặc đang trỏ Lead/Deal
	"cũ" của cùng khách (Lead đã gộp, hoặc Lead đã chuyển thành đúng Deal đích). Tham chiếu sang tài liệu
	khác (Issue, Lead khác chưa gộp...) thì không ghi đè."""
	ref_dt, ref_name = current
	if not ref_name:
		return True
	if (ref_dt, ref_name) == target:
		return True
	if ref_dt == LEAD:
		primary = lead_bridge.follow_merged(ref_name)
		if primary and primary != ref_name:
			return True  # Lead phụ đã gộp
		if primary:
			deal = lead_bridge.get_deal_for_lead(primary)
			return (DEAL, deal) == target if deal else False
	return False


def _crm_communication_hook(name: str) -> None:
	"""Chạy hook cập nhật của CRM (communication_status, modified của Lead); db.set_value không tự chạy nó."""
	try:
		from crm.utils import on_communication_update

		on_communication_update(frappe.get_doc(COMMUNICATION, name))
	except Exception:
		frappe.log_error(
			title="Omni: không cập nhật được trạng thái liên lạc của Lead",
			message=f"communication={name}",
		)


def _link_email(target: tuple[str, str], row: Any) -> bool:
	"""Email đã là Communication: trỏ tham chiếu về Lead/Deal (nếu được phép) và ghi khóa, không tạo bản sao."""
	if not row.communication:
		return False
	cur = frappe.db.get_value(
		COMMUNICATION, row.communication, ["reference_doctype", "reference_name"], as_dict=True
	)
	if not cur or not _can_repoint((cur.reference_doctype, cur.reference_name), target):
		return False
	changed = (cur.reference_doctype, cur.reference_name) != target
	frappe.db.set_value(
		COMMUNICATION,
		row.communication,
		{
			"reference_doctype": target[0],
			"reference_name": target[1],
			"omni_inbound_message": row.name,
		},
		update_modified=False,
	)
	if changed:
		_crm_communication_hook(row.communication)
	return True


def append(target: str | None, msgs: list[Any]) -> int:
	"""Ghi các tin vào dòng thời gian của Lead hoặc Deal; trả số bản ghi mới tạo/gắn.

	`target` là tên Lead hoặc Deal (Q10: Lead đã chuyển thì truyền tên Deal); None thì không làm gì. Chạy lại
	cùng tin không tạo bản ghi thứ hai (khóa `omni_inbound_message`). Không commit.
	"""
	tgt = resolve_target(target)
	names = [m.name for m in msgs if getattr(m, "name", None)]
	if not tgt or not names:
		return 0
	rows = frappe.get_all(
		INBOUND,
		filters={"name": ["in", names]},
		fields=[
			"name",
			"channel_type",
			"event_type",
			"text",
			"attachments",
			"communication",
			"received_at",
			"identity",
			"identity_key",
			"external_sender_id",
		],
		order_by="received_at asc, name asc",
	)
	done = 0
	target_name = target_display_name(tgt)
	for row in rows:
		if row.channel_type == EMAIL:
			done += int(_link_email(tgt, row))
			continue
		if row.event_type not in TIMELINE_EVENTS:
			continue
		if frappe.db.exists(COMMUNICATION, {"omni_inbound_message": row.name}):
			continue  # đã ghi ở lượt trước
		echo = row.event_type == "oa_echo"
		who = sender_label(
			_identity_display_name(row),
			target_name,
			row.channel_type,
			row.external_sender_id,
		)
		_insert_communication(
			{
				"sent_or_received": "Sent" if echo else "Received",
				"subject": _subject(row.channel_type, row.text),
				"content": render_text(row.text, parse_attachments(row.attachments)),
				"sender_full_name": _("{0} (gửi ngoài hệ thống)").format(row.channel_type) if echo else who,
				"communication_date": row.received_at or now_datetime(),
				"reference_doctype": tgt[0],
				"reference_name": tgt[1],
				"omni_inbound_message": row.name,
			},
			row.channel_type,
		)
		done += 1
	return done


def append_outbound(target: str | None, outbound: str | Any) -> str | None:
	"""Ghi một tin ra (Omni Outbound Message) vào dòng thời gian; trả tên Communication hoặc None.

	Kênh Email không tạo bản sao (thư gửi đi đã là Communication, xử lý ở D1-07/D1-09). Chạy lại không
	tạo bản ghi thứ hai.
	"""
	tgt = resolve_target(target)
	name = outbound if isinstance(outbound, str) else getattr(outbound, "name", None)
	if not tgt or not name:
		return None
	row = frappe.db.get_value(
		OUTBOUND,
		name,
		[
			"name",
			"channel",
			"text",
			"source",
			"sent_by",
			"sent_at",
			"creation",
			"is_sandbox",
			"attachment_kind",
			"attachment_name",
		],
		as_dict=True,
	)
	if not row:
		return None
	if row.attachment_kind:  # V3-09: tin ảnh/tệp hiện nhãn "[Ảnh] tên tệp" thay vì trống
		from omni_crm.channels.zalo.media_out import display_text

		row.text = display_text(row.text, row.attachment_kind, row.attachment_name)
	channel_type = frappe.db.get_value("Omni Channel", row.channel, "channel_type") or ""
	if channel_type == EMAIL:
		return None
	existing = frappe.db.get_value(COMMUNICATION, {"omni_outbound_message": row.name}, "name")
	if existing:
		return existing
	if row.sent_by:
		full_name = frappe.db.get_value("User", row.sent_by, "full_name") or row.sent_by
	else:
		full_name = _("Bot") if row.source == "Bot" else _("Hệ thống")
	subject, content = _subject(channel_type, row.text), render_text(row.text)
	if row.is_sandbox:  # tin thử nghiệm không được trông như đã gửi thật
		subject = f"[{_('Thử nghiệm')}] {subject}"
		content += f"<p><i>{_('Tin thử nghiệm (sandbox), chưa gửi tới khách')}</i></p>"
	return _insert_communication(
		{
			"sent_or_received": "Sent",
			"subject": subject,
			"content": content,
			"sender": row.sent_by,
			"sender_full_name": full_name,
			"communication_date": row.sent_at or row.creation or now_datetime(),
			"reference_doctype": tgt[0],
			"reference_name": tgt[1],
			"omni_outbound_message": row.name,
		},
		channel_type,
	)


def notify(lead: str | None, channel_types: list[str] | None = None) -> None:
	"""Báo realtime (sau commit) rằng hội thoại của Lead có tin mới; KHÔNG kèm nội dung tin.

	Chỉ gửi tới phòng của chính Lead (Frappe kiểm quyền đọc khi người dùng đăng ký phòng) và tới người phụ
	trách/được giao. Client nhận rồi gọi API (có kiểm quyền) để tải lại.
	"""
	if not lead:
		return
	payload = {"lead": lead, "channels": sorted(set(channel_types or []))}
	frappe.publish_realtime(REALTIME_EVENT, payload, doctype=LEAD, docname=lead, after_commit=True)
	row = frappe.db.get_value(LEAD, lead, ["lead_owner", "_assign"], as_dict=True) or {}
	users = {row.get("lead_owner")}
	try:
		users.update(json.loads(row.get("_assign") or "[]"))
	except ValueError:
		pass
	for user in filter(None, users):
		frappe.publish_realtime(REALTIME_EVENT, payload, user=user, after_commit=True)
	refresh_activity(lead)


def refresh_activity(lead: str) -> None:
	"""Trang Lead (và Deal của nó) đang mở tự tải lại khối hoạt động/email, không phải F5.

	Gửi vào phòng riêng của tài liệu: chỉ trình duyệt đang mở đúng tài liệu đó (Frappe kiểm quyền đọc khi đăng ký
	phòng) mới nhận. Tin chỉ có khóa đệm; trình duyệt tự gọi API có kiểm quyền để tải lại.
	"""
	targets = [(LEAD, lead)]
	deal = lead_bridge.get_deal_for_lead(lead)
	if deal:
		targets.append((DEAL, deal))
	for doctype, name in targets:
		frappe.publish_realtime(
			REFETCH_EVENT, {"cache_key": ["activity", name]}, doctype=doctype, docname=name, after_commit=True
		)


def conversation_turns(lead: str, until: Any = None) -> list[dict]:
	"""Mọi lượt hội thoại của Lead (và Deal của nó) trên mọi kênh, cũ nhất trước (đầu vào cho bot ở Đợt 2).

	Mỗi phần tử: {at, direction "in"|"out", channel_type, actor "customer"|"sale"|"bot"|"system"|"external",
	text, kind "message"|"oa_echo"|"outbound"|"email_out", ref}. Gồm tin vào của khách, tin Sale trả lời ngoài
	hệ thống (oa_echo), tin ra đã gửi (Sent, kể cả sandbox) và thư gửi đi từ CRM (Communication Sent). Tin ra
	trùng mã tin với oa_echo chỉ tính một lần. `until`: chỉ lấy lượt có thời điểm <= until.
	"""
	refs = [lead]
	deal = lead_bridge.get_deal_for_lead(lead)
	if deal:
		refs.append(deal)
	turns: list[dict] = []
	inbound = frappe.get_all(
		INBOUND,
		filters={"lead": lead, "event_type": ["in", sorted(TIMELINE_EVENTS)], "status": ["!=", "Ignored"]},
		fields=["name", "channel_type", "event_type", "text", "received_at", "external_message_id"],
	)
	echo_ids = {m.external_message_id for m in inbound if m.event_type == "oa_echo" and m.external_message_id}
	for m in inbound:
		echo = m.event_type == "oa_echo"
		turns.append(
			{
				"at": m.received_at,
				"direction": "out" if echo else "in",
				"channel_type": m.channel_type,
				"actor": "external" if echo else "customer",
				"text": m.text or "",
				"kind": m.event_type,
				"ref": m.name,
			}
		)
	outbound = frappe.get_all(
		OUTBOUND,
		filters={"lead": lead, "status": "Sent"},
		fields=["name", "channel", "text", "source", "sent_at", "creation", "external_message_id"],
	)
	types = {r.name: r.channel_type for r in frappe.get_all("Omni Channel", fields=["name", "channel_type"])}
	for o in outbound:
		if o.external_message_id and o.external_message_id in echo_ids:
			continue
		turns.append(
			{
				"at": o.sent_at or o.creation,
				"direction": "out",
				"channel_type": types.get(o.channel),
				"actor": o.source.lower() if o.source in ("Sale", "Bot", "System") else "system",
				"text": o.text or "",
				"kind": "outbound",
				"ref": o.name,
			}
		)
	mails = frappe.get_all(
		COMMUNICATION,
		filters={
			"reference_doctype": ["in", [LEAD, DEAL]],
			"reference_name": ["in", refs],
			"communication_medium": "Email",
			"sent_or_received": "Sent",
		},
		fields=["name", "content", "communication_date", "creation"],
	)
	for c in mails:
		turns.append(
			{
				"at": c.communication_date or c.creation,
				"direction": "out",
				"channel_type": EMAIL,
				"actor": "sale",
				"text": strip_html_tags(c.content or "").strip(),
				"kind": "email_out",
				"ref": c.name,
			}
		)
	if until is not None:
		limit = get_datetime(until)
		turns = [t for t in turns if get_datetime(t["at"]) <= limit]
	turns.sort(key=lambda t: (get_datetime(t["at"]), t["ref"]))
	return turns
