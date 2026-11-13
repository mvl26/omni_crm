"""Khung chat tạo chiến dịch bằng AI (V3-10, Prompt 4 mục 2-5).

Luồng: người dùng gửi mô tả (kèm tệp/ảnh) -> job nền gọi AI -> AI hỏi lại (tối đa 3 câu cả phiên) hoặc trả kế hoạch
JSON -> job chấm điểm từng bài -> người dùng bấm "Duyệt và lên lịch" -> tạo Omni Campaign, Omni Content Item,
Omni Content Target rồi gửi duyệt / duyệt theo quy tắc ở `approval` (Prompt 1).

Phiên lưu ở cache 24 giờ (khóa theo id phiên, chỉ chủ phiên đọc được). Kế hoạch chưa duyệt không ghi vào DB.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime

from omni_crm.marketing import ai, kb, prompts, roles, rules, scoring, views
from omni_crm.marketing import channels as mk_channels
from omni_crm.marketing import settings as mk_settings

SESSION_TTL = 24 * 3600
REALTIME_EVENT = "oc_plan_update"
IMAGE_TYPES = {
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".webp": "image/webp",
	".gif": "image/gif",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4
MAX_ATTACH_CHARS = 20000
MAX_TEXT = 4000
MIN_LEAD_MINUTES = 30  # giờ đăng AI đề xuất phải sau hiện tại ít nhất 30 phút
ST_WAIT, ST_ASK, ST_SCORING, ST_READY, ST_ERROR, ST_CREATED = (
	"dang_xu_ly",
	"hoi_lai",
	"cham_diem",
	"san_sang",
	"loi",
	"da_tao",
)


# --- Phiên ---------------------------------------------------------------------------------------------------


def _key(sid: str) -> str:
	return f"omni:mk:plan:{sid}"


def load(sid: str, check_owner: bool = True) -> dict:
	data = frappe.cache().get_value(_key(sid))
	if not data:
		frappe.throw(_("Phiên tạo chiến dịch đã hết hạn, hãy bắt đầu lại"))
	if check_owner and data.get("owner") != frappe.session.user:
		frappe.throw(_("Không phải phiên của bạn"), frappe.PermissionError)
	return data


def save(sid: str, data: dict) -> None:
	frappe.cache().set_value(_key(sid), data, expires_in_sec=SESSION_TTL)


def public_state(data: dict) -> dict:
	"""Trạng thái phiên cho giao diện (không gồm nội dung tệp đính kèm)."""
	return {
		"session_id": data["id"],
		"status": data["status"],
		"turns": [
			{"role": t["role"], "text": t["text"], "files": [a["name"] for a in t.get("attachments", [])]}
			for t in data["turns"]
		],
		"questions": data.get("questions") or [],
		"asked": data.get("asked", 0),
		"assumptions": data.get("assumptions") or [],
		"plan": data.get("plan"),
		"scores": data.get("scores") or [],
		"error": data.get("error") or "",
		"campaign": data.get("campaign"),
		"results": data.get("results") or [],
		"threshold": mk_settings.quality_threshold(),
		"cost_usd": round(data.get("cost_usd", 0), 4),
	}


def _notify(data: dict) -> None:
	frappe.publish_realtime(
		REALTIME_EVENT, {"session_id": data["id"], "status": data["status"]}, user=data["owner"]
	)


# --- Đính kèm ------------------------------------------------------------------------------------------------


def _attachment(file_url: str) -> dict:
	"""Đọc một tệp người dùng đính kèm (kiểm quyền đọc File): văn bản trích ra hoặc ảnh (giữ đường dẫn)."""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		frappe.throw(_("Không tìm thấy tệp {0}").format(file_url))
	f = frappe.get_doc("File", name)
	if not frappe.has_permission("File", "read", doc=f):
		frappe.throw(_("Bạn không có quyền đọc tệp {0}").format(f.file_name), frappe.PermissionError)
	ext = os.path.splitext(f.file_name or file_url)[1].lower()
	if ext in IMAGE_TYPES:
		if os.path.getsize(f.get_full_path()) > MAX_IMAGE_BYTES:
			frappe.throw(_("Ảnh {0} lớn hơn 5 MB").format(f.file_name))
		return {"name": f.file_name, "kind": "image", "file_url": file_url, "media_type": IMAGE_TYPES[ext]}
	try:
		filename, data = kb.file_bytes(file_url)
		text = kb.extract_text(filename, data)
	except ValueError as e:
		frappe.throw(str(e))
	return {"name": f.file_name, "kind": "text", "text": text[:MAX_ATTACH_CHARS]}


def _image_block(att: dict) -> dict | None:
	try:
		_name, data = kb.file_bytes(att["file_url"])
	except ValueError:
		return None
	return {
		"type": "image",
		"source": {
			"type": "base64",
			"media_type": att["media_type"],
			"data": base64.b64encode(data).decode(),
		},
	}


# --- Lượt hội thoại ------------------------------------------------------------------------------------------


def start_turn(text: str, files: list[str] | None = None, sid: str | None = None) -> dict:
	"""Thêm lượt người dùng vào phiên (tạo phiên mới nếu chưa có) và xếp job gọi AI; trả trạng thái phiên."""
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền tạo chiến dịch"), frappe.PermissionError)
	if not mk_settings.hub_enabled():
		frappe.throw(_("Marketing Hub đang tắt"))
	text = (text or "").strip()[:MAX_TEXT]
	if not text and not files:
		frappe.throw(_("Hãy mô tả chiến dịch cần tạo"))
	if sid:
		data = load(sid)
		if data["status"] in (ST_WAIT, ST_SCORING):
			frappe.throw(_("AI đang xử lý lượt trước, chờ một chút"))
		if data["status"] == ST_CREATED:
			frappe.throw(_("Kế hoạch này đã được tạo thành chiến dịch, hãy mở phiên mới"))
	else:
		sid = frappe.generate_hash(length=16)
		data = {"id": sid, "owner": frappe.session.user, "turns": [], "asked": 0, "cost_usd": 0.0}
	attachments = [_attachment(u) for u in (files or [])[:6]]
	images = sum(1 for t in data["turns"] for a in t.get("attachments", []) if a["kind"] == "image")
	if images + sum(1 for a in attachments if a["kind"] == "image") > MAX_IMAGES:
		frappe.throw(_("Mỗi phiên tối đa {0} ảnh").format(MAX_IMAGES))
	data["turns"].append({"role": "user", "text": text, "attachments": attachments})
	data.update(status=ST_WAIT, error="", questions=[])
	data["turn_no"] = len(data["turns"])
	save(sid, data)
	frappe.enqueue(
		"omni_crm.marketing.planner.run_turn",
		queue="long",
		timeout=1800,
		job_id=f"oc-plan-{sid}-{data['turn_no']}",
		deduplicate=True,
		sid=sid,
		turn_no=data["turn_no"],
	)
	return public_state(data)


def _user_content(turn: dict, extra: str | None = None) -> list[dict]:
	blocks: list[dict] = [{"type": "text", "text": f"<yeu_cau>{kb.esc(turn['text'])}</yeu_cau>"}]
	for a in turn.get("attachments", []):
		if a["kind"] == "text":
			blocks.append(
				{
					"type": "text",
					"text": f'<dinh_kem ten="{kb.esc(a["name"])}">{kb.esc(a["text"])}</dinh_kem>',
				}
			)
		else:
			img = _image_block(a)
			if img:
				blocks.append({"type": "text", "text": f'Ảnh đính kèm: {kb.esc(a["name"])}'})
				blocks.append(img)
	if extra:
		blocks.append({"type": "text", "text": extra})
	return blocks


def _context_note(data: dict) -> str:
	"""Thông tin đổi theo lượt (đặt sau điểm cache): giờ hiện tại, số câu đã hỏi, kênh, kho tri thức liên quan."""
	query = " ".join(t["text"] for t in data["turns"] if t["role"] == "user")
	for t in data["turns"]:
		for a in t.get("attachments", []):
			if a["kind"] == "text":
				query += " " + a["text"][:2000]
	passages = kb.search(query, limit=8)
	data["sources"] = [p.id for p in passages]
	channels = [p for p in prompts.AI_PLATFORMS if mk_channels.default_channel(p)]
	remaining = max(prompts.MAX_QUESTIONS - data.get("asked", 0), 0)
	return "\n".join(
		[
			f"Thời điểm hiện tại (giờ Việt Nam): {now_datetime().strftime('%Y-%m-%d %H:%M')}.",
			f"Số câu hỏi làm rõ còn được hỏi: {remaining}."
			+ (" Không được hỏi thêm, hãy lập kế hoạch." if remaining == 0 else ""),
			"Kênh đang kết nối: " + (", ".join(channels) or "chưa có kênh nào") + ".",
			kb.render_passages(passages),
		]
	)


def build_messages(data: dict) -> list[dict]:
	"""Lịch sử hội thoại dạng Messages API; lượt user cuối kèm ngữ cảnh của lượt."""
	msgs: list[dict] = []
	last = len(data["turns"]) - 1
	for i, t in enumerate(data["turns"]):
		if t["role"] == "user":
			msgs.append(
				{"role": "user", "content": _user_content(t, _context_note(data) if i == last else None)}
			)
		else:
			msgs.append({"role": "assistant", "content": t["text"]})
	return msgs


def _parse_dt(value: str) -> datetime | None:
	for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
		try:
			return datetime.strptime((value or "").strip(), fmt)
		except ValueError:
			continue
	return None


def plan_problems(plan: dict, pillars: list[str], now: datetime | None = None) -> list[str]:
	"""Lỗi nghiệp vụ của kế hoạch (ngoài JSON Schema); rỗng là hợp lệ."""
	now = now or now_datetime()
	problems: list[str] = []
	try:
		start, end = getdate(plan.get("start_date")), getdate(plan.get("end_date"))
	except Exception:
		return ["start_date / end_date phải dạng YYYY-MM-DD"]
	if end < start:
		problems.append("end_date trước start_date")
	try:
		rules.landing_path(plan.get("landing_page") or "/spd/")
	except ValueError:
		problems.append("landing_page phải là đường dẫn dạng /duong-dan/, không gồm tên miền")
	earliest = now + timedelta(minutes=MIN_LEAD_MINUTES)
	for i, p in enumerate(plan.get("posts") or [], 1):
		when = _parse_dt(p.get("scheduled_at"))
		if when is None:
			problems.append(f"bài {i}: scheduled_at phải dạng YYYY-MM-DD HH:MM")
		else:
			if when < earliest:
				problems.append(f"bài {i}: giờ đăng phải sau {earliest.strftime('%Y-%m-%d %H:%M')}")
			if not (start <= when.date() <= end):
				problems.append(f"bài {i}: giờ đăng nằm ngoài start_date..end_date")
		if not rules.check_format(p.get("platform"), p.get("post_format")):
			problems.append(f"bài {i}: định dạng {p.get('post_format')} không dùng cho {p.get('platform')}")
		if p.get("platform") == rules.ZALO and len(p.get("title") or "") > 150:
			problems.append(f"bài {i}: tiêu đề Zalo quá 150 ký tự")
		if p.get("pillar") and p["pillar"] not in pillars:
			problems.append(f"bài {i}: trụ cột '{p['pillar']}' không có trong danh sách")
		if not (p.get("caption") or "").strip():
			problems.append(f"bài {i}: caption rỗng")
	return problems


def make_check(asked: int, pillars: list[str]):
	"""Hàm kiểm đầu ra cho `ai.call`: số câu hỏi còn lại và ràng buộc kế hoạch."""

	def check(out: dict) -> str | None:
		if out.get("action") == "hoi_lai":
			qs = out.get("questions") or []
			if not qs:
				return "action hoi_lai cần ít nhất 1 câu hỏi"
			if asked + len(qs) > prompts.MAX_QUESTIONS:
				left = max(prompts.MAX_QUESTIONS - asked, 0)
				return f"chỉ còn được hỏi {left} câu; hết lượt hỏi thì phải trả action ke_hoach"
			return None
		plan = out.get("plan")
		if not isinstance(plan, dict):
			return "action ke_hoach cần trường plan"
		problems = plan_problems(plan, pillars)
		return "; ".join(problems[:6]) if problems else None

	return check


def _pillars() -> list[str]:
	return frappe.get_all(
		"Omni Content Pillar", filters={"is_active": 1}, pluck="pillar_name", order_by="pillar_name"
	)


def run_turn(sid: str, turn_no: int) -> str:
	"""Job nền: một lượt AI lập kế hoạch, rồi chấm điểm từng bài nếu đã có kế hoạch; trả trạng thái phiên."""
	data = load(sid, check_owner=False)
	if data.get("turn_no") != turn_no or data["status"] != ST_WAIT:
		return data["status"]  # lượt cũ hoặc đã chạy
	frappe.set_user(data["owner"])
	pillars = _pillars()
	result = ai.call(
		"plan",
		prompts.system_planner(mk_settings.brand_voice(), pillars),
		build_messages(data),
		prompts.planner_tool(),
		max_tokens=16000,
		check=make_check(data.get("asked", 0), pillars),
		timeout=300,
		batch_id=f"plan-{sid}",
	)
	data["cost_usd"] = data.get("cost_usd", 0) + result.cost_usd
	if result.status != "Success":
		data.update(status=ST_ERROR, error=ai.user_message(result))
		data["turns"].pop()  # cho phép gửi lại đúng lượt này
		data["turn_no"] = len(data["turns"])
		save(sid, data)
		_notify(data)
		return data["status"]
	out = result.output
	if out["action"] == "hoi_lai":
		qs = out["questions"]
		data["asked"] = data.get("asked", 0) + len(qs)
		text = "\n".join(
			f"{i}. {q['question']}"
			+ (f" (gợi ý: {' / '.join(q['suggestions'])})" if q.get("suggestions") else "")
			for i, q in enumerate(qs, 1)
		)
		data["turns"].append({"role": "assistant", "text": text})
		data.update(status=ST_ASK, questions=qs)
		save(sid, data)
		_notify(data)
		return data["status"]
	plan = out["plan"]
	plan["landing_page"] = rules.landing_path(
		plan.get("landing_page") or mk_settings.get("default_landing_page")
	)
	data["turns"].append(
		{"role": "assistant", "text": "Kế hoạch đã lập:\n" + json.dumps(plan, ensure_ascii=False)}
	)
	data.update(status=ST_SCORING, plan=plan, assumptions=out.get("assumptions") or [], scores=[])
	save(sid, data)
	_notify(data)
	context = kb.search(" ".join(t["text"] for t in data["turns"] if t["role"] == "user"), limit=6)
	brief = "\n".join(t["text"] for t in data["turns"] if t["role"] == "user")
	for i, post in enumerate(plan["posts"]):
		sc = scoring.score_post(post, brief=brief, batch_id=f"plan-{sid}-score-{i + 1}", extra=context)
		data["cost_usd"] += sc.pop("cost_usd", 0)
		data["scores"].append(sc)
		save(sid, data)
		_notify(data)
	data["status"] = ST_READY
	save(sid, data)
	_notify(data)
	return data["status"]


# --- Tạo chiến dịch từ kế hoạch ------------------------------------------------------------------------------


def _campaign_name(title: str) -> str:
	base = (title or _("Chiến dịch")).strip()[:120]
	name, n = base, 1
	while frappe.db.exists("Omni Campaign", name):
		n += 1
		name = f"{base} ({n})"
	return name


def create_from_plan(sid: str) -> dict:
	"""Tạo chiến dịch + bài + nền tảng từ kế hoạch đã chấm, rồi gửi duyệt / duyệt từng bài theo quy tắc Prompt 1."""
	data = load(sid)
	if data.get("campaign"):
		return public_state(data)
	if data["status"] != ST_READY or not data.get("plan"):
		frappe.throw(_("Kế hoạch chưa sẵn sàng (đang chấm điểm hoặc chưa có kế hoạch)"))
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền tạo chiến dịch"), frappe.PermissionError)
	cache = frappe.cache()
	lock = cache.make_key(f"omni:mk:plan_lock:{sid}")
	if not cache.set(lock, 1, nx=True, ex=120):
		frappe.throw(_("Đang tạo chiến dịch từ kế hoạch này"))
	try:
		return _create(sid, data)
	finally:
		cache.delete(lock)


def _create(sid: str, data: dict) -> dict:
	plan = data["plan"]
	platforms = {p["platform"] for p in plan["posts"]}
	campaign = frappe.get_doc(
		{
			"doctype": "Omni Campaign",
			"campaign_name": _campaign_name(plan.get("title")),
			"platform": "Meta" if platforms == {rules.FACEBOOK} else "Đa kênh",
			"status": "Nháp",
			"start_date": plan.get("start_date"),
			"end_date": plan.get("end_date"),
			"objective": plan.get("objective"),
			"audience": plan.get("audience"),
			"key_message": plan.get("key_message"),
			"landing_page": plan.get("landing_page"),
			"owner_team": "Marketing",
		}
	)
	# Marketing User chỉ có quyền đọc Omni Campaign (DocType dùng chung với quảng cáo): tạo qua đường đã kiểm
	# roles.can_edit() ở create_from_plan; bài và nền tảng vẫn kiểm quyền thường.
	campaign.flags.ignore_permissions = True
	campaign.insert()
	scores = data.get("scores") or []
	results: list[dict] = []
	for i, post in enumerate(plan["posts"]):
		sc = scores[i] if i < len(scores) else {}
		item = frappe.get_doc(
			{
				"doctype": "Omni Content Item",
				"title": (post.get("title") or "")[:140] or _("Bài {0}").format(i + 1),
				"campaign": campaign.name,
				"pillar": post.get("pillar") or None,
				"funnel_stage": post.get("funnel_stage") or None,
				"caption": post.get("caption"),
				"quality_score": cint(sc.get("score")),
				"quality_note": sc.get("note") or "",
			}
		)
		item.flags.score_via_ai = True
		item.insert()
		if post.get("image_hint"):
			item.add_comment("Comment", _("Gợi ý ảnh (AI): {0}").format(post["image_hint"]))
		channel = mk_channels.default_channel(post["platform"])
		frappe.get_doc(
			{
				"doctype": "Omni Content Target",
				"content_item": item.name,
				"platform": post["platform"],
				"channel_account": channel,
				"post_format": post["post_format"],
				"scheduled_at": _parse_dt(post["scheduled_at"]),
			}
		).insert()
		results.append(views.route(item.name))
	if any(r["status"] == rules.I_SCHEDULED for r in results):
		frappe.db.set_value("Omni Campaign", campaign.name, "status", "Đang chạy")
	data.update(status=ST_CREATED, campaign=campaign.name, results=results)
	save(sid, data)
	return public_state(data)
