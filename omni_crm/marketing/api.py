"""API whitelisted của Marketing Hub (V3-10). Mỗi lời gọi xử lý MỘT bài / MỘT kênh."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from omni_crm.marketing import ai, approval, channels, kb, planner, prompts, roles, rules, scoring
from omni_crm.marketing import settings as mk_settings


def _require_hub() -> None:
	if not mk_settings.hub_enabled():
		frappe.throw(_("Marketing Hub đang tắt"), frappe.PermissionError)


@frappe.whitelist()
def hub_state() -> dict[str, Any]:
	"""Giao diện CRM hỏi: hub bật không, người dùng được xem / sửa Thiết lập / duyệt bài không."""
	enabled = mk_settings.hub_enabled()
	return {
		"enabled": enabled,
		"can_view": enabled and roles.can_edit(),
		"can_edit_settings": enabled and roles.is_settings_admin(),
		"is_manager": enabled and roles.is_manager(),
		"dry_run": mk_settings.dry_run(),
	}


@frappe.whitelist()
def get_social_channels() -> list[dict[str, Any]]:
	"""Thiết lập > Mạng xã hội: một dòng mỗi Page Facebook / OA Zalo, kèm TikTok. Không trả token (chỉ 4 ký tự cuối)."""
	_require_hub()
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền xem kênh mạng xã hội"), frappe.PermissionError)
	return channels.accounts()


@frappe.whitelist(methods=["POST"])
def check_channel(name: str) -> dict[str, Any]:
	"""Nút "Kiểm tra quyền": gọi đọc Facebook (debug_token, thông tin Trang) / Zalo (thông tin OA), lưu kết quả."""
	_require_hub()
	if not roles.is_settings_admin():
		frappe.throw(_("Chỉ Omni Admin được kiểm tra quyền kênh"), frappe.PermissionError)
	if not name or not frappe.db.exists("Omni Channel", name):
		frappe.throw(_("Không tìm thấy kênh"), frappe.DoesNotExistError)
	return channels.check(name)


@frappe.whitelist()
def list_approvers() -> list[dict[str, str]]:
	"""Người có vai trò Marketing Manager (để chọn người duyệt mặc định)."""
	_require_hub()
	users = frappe.get_all(
		"Has Role",
		filters={"role": roles.MANAGER, "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", users or [""]], "enabled": 1, "user_type": "System User"},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)
	return [{"value": r.name, "label": r.full_name or r.name} for r in rows]


@frappe.whitelist(methods=["POST"])
def submit_item(name: str) -> str:
	"""Gửi duyệt một bài (Nháp -> Chờ duyệt)."""
	_require_hub()
	return approval.submit(name)


@frappe.whitelist(methods=["POST"])
def approve_item(name: str) -> str:
	"""Duyệt và lên lịch một bài (chỉ Marketing Manager, điểm >= ngưỡng, có nền tảng)."""
	_require_hub()
	return approval.approve(name)


@frappe.whitelist(methods=["POST"])
def cancel_item(name: str) -> str:
	"""Hủy lịch một bài: nền tảng chưa đăng -> Đã hủy."""
	_require_hub()
	return approval.cancel(name)


# --- AI (Prompt 4) --------------------------------------------------------------------------------------------


def _require_editor() -> None:
	_require_hub()
	if not roles.can_edit():
		frappe.throw(_("Bạn không có quyền dùng AI marketing"), frappe.PermissionError)


@frappe.whitelist()
def ai_budget() -> dict[str, Any]:
	"""Ngân sách AI marketing tháng này (USD): {budget, spent, remaining}."""
	_require_editor()
	return ai.budget_state()


@frappe.whitelist(methods=["POST"])
def planner_send(text: str = "", files: Any = None, session_id: str | None = None) -> dict[str, Any]:
	"""Khung chat tạo chiến dịch: gửi một lượt (mô tả / trả lời câu hỏi, kèm tệp); AI chạy nền, kết quả báo realtime."""
	_require_editor()
	files = frappe.parse_json(files) if isinstance(files, str) else files
	return planner.start_turn(text, [str(f) for f in (files or [])], session_id or None)


@frappe.whitelist()
def planner_state(session_id: str) -> dict[str, Any]:
	"""Trạng thái phiên tạo chiến dịch (câu hỏi, kế hoạch, điểm từng bài)."""
	_require_editor()
	return planner.public_state(planner.load(session_id))


@frappe.whitelist(methods=["POST"])
def planner_create(session_id: str) -> dict[str, Any]:
	"""Nút "Duyệt và lên lịch" trên kế hoạch: tạo chiến dịch, bài, nền tảng rồi gửi duyệt / duyệt theo quy tắc."""
	_require_editor()
	return planner.create_from_plan(session_id)


@frappe.whitelist(methods=["POST"])
def score_item(name: str) -> dict[str, Any]:
	"""Chấm điểm lại một bài (job nền); kết quả báo qua realtime oc_publish_update."""
	_require_editor()
	doc = frappe.get_doc("Omni Content Item", name)
	doc.check_permission("write")
	if doc.status not in (rules.I_DRAFT, rules.I_REVIEW):
		frappe.throw(_("Chỉ chấm lại được bài Nháp hoặc Chờ duyệt"))
	frappe.enqueue(
		"omni_crm.marketing.scoring.score_item",
		queue="long",
		job_id=f"oc-score-{name}",
		deduplicate=True,
		item=name,
	)
	return {"queued": True}


@frappe.whitelist()
def kb_overview() -> dict[str, Any]:
	"""Trang Kho tri thức: danh mục kèm số tài liệu, danh sách tài liệu."""
	_require_editor()
	cats = frappe.get_all(
		kb.CATEGORY, fields=["name", "category_name", "is_active", "description"], order_by="category_name"
	)
	counts = dict(
		frappe.db.sql(f"select coalesce(category, ''), count(*) from `tab{kb.DOCUMENT}` group by category")
	)
	docs = frappe.get_all(
		kb.DOCUMENT,
		fields=[
			"name",
			"title",
			"category",
			"status",
			"is_active",
			"source_file",
			"chunk_count",
			"indexed_at",
			"index_error",
			"modified",
			"owner",
		],
		order_by="modified desc",
		limit=500,
	)
	return {
		"categories": [{**c, "document_count": counts.get(c.name, 0)} for c in cats],
		"uncategorized": counts.get("", 0),
		"documents": docs,
	}


@frappe.whitelist(methods=["POST"])
def kb_reindex(name: str) -> dict[str, Any]:
	"""Lập chỉ mục lại một tài liệu (job nền)."""
	_require_editor()
	frappe.get_doc(kb.DOCUMENT, name).check_permission("write")
	kb.queue_index(name)
	return {"queued": True}


@frappe.whitelist(methods=["POST"])
def kb_ask(question: str) -> dict[str, Any]:
	"""Ô "Hỏi thử Kho tri thức": trả lời chỉ dựa trên kho, kèm nguồn trích; `run` dùng để gửi đánh giá."""
	_require_editor()
	question = (question or "").strip()[:2000]
	if not question:
		frappe.throw(_("Nhập câu hỏi"))
	passages = kb.search(question, limit=6)
	messages = [
		{
			"role": "user",
			"content": [
				{"type": "text", "text": kb.render_passages(passages)},
				{"type": "text", "text": f"<cau_hoi>{kb.esc(question)}</cau_hoi>"},
			],
		}
	]
	ids = {p.id for p in passages}
	result = ai.call(
		"kb-ask",
		prompts.system_kb_answer(),
		messages,
		prompts.kb_answer_tool(),
		max_tokens=3000,
		timeout=90,
		check=lambda out: None
		if set(out.get("sources") or []) <= ids
		else "sources chỉ được chứa id của các thẻ <doan> đã cho",
	)
	if result.status != "Success":
		return {"ok": False, "message": ai.user_message(result), "run": result.run}
	by_id = {p.id: p for p in passages}
	sources = [
		{"id": s, "source": by_id[s].source, "document": by_id[s].document, "excerpt": by_id[s].text[:400]}
		for s in result.output.get("sources") or []
		if s in by_id
	]
	return {
		"ok": True,
		"answer": result.output.get("answer") or "",
		"insufficient": bool(result.output.get("insufficient")),
		"sources": sources,
		"run": result.run,
		"cost_usd": result.cost_usd,
	}


@frappe.whitelist(methods=["POST"])
def kb_feedback(run: str, verdict: str, note: str = "") -> dict[str, Any]:
	"""Đánh giá câu trả lời Hỏi thử: Đúng / Cần sửa / Sai (lưu vào Omni Bot Run của câu trả lời)."""
	_require_editor()
	if verdict not in ("Đúng", "Cần sửa", "Sai"):
		frappe.throw(_("Đánh giá không hợp lệ"))
	row = frappe.db.get_value("Omni Bot Run", run, ["kind", "prompt_version"], as_dict=True)
	if not row or row.kind != ai.KIND or row.prompt_version != f"{ai.PROMPT_VERSION}-kb-ask":
		frappe.throw(_("Không tìm thấy câu trả lời"), frappe.DoesNotExistError)
	frappe.db.set_value(
		"Omni Bot Run",
		run,
		{"feedback": verdict, "feedback_note": (note or "")[:1000], "feedback_by": frappe.session.user},
		update_modified=False,
	)
	return {"ok": True}


# --- Báo cáo (Prompt 6) ---------------------------------------------------------------------------------------


@frappe.whitelist()
def marketing_report(filters: Any = None) -> dict[str, Any]:
	"""Trang Báo cáo marketing: thẻ số, theo trụ cột, theo phễu, từng bài, lỗi đăng tải."""
	_require_editor()
	from omni_crm.marketing import report

	return report.build(filters)


@frappe.whitelist()
def export_report(filters: Any = None) -> None:
	"""Nút "Xuất Excel": tải tệp .xlsx cùng bộ lọc với trang báo cáo."""
	_require_editor()
	from omni_crm.marketing import report

	data = report.build(filters)
	frappe.local.response.filename = f"bao-cao-marketing-{frappe.utils.nowdate()}.xlsx"
	frappe.local.response.filecontent = report.to_xlsx(data)
	frappe.local.response.type = "binary"


# --- Chiến dịch, bài, lịch (Prompt 3) -------------------------------------------------------------------------


@frappe.whitelist()
def list_campaigns(status: str | None = None) -> list[dict[str, Any]]:
	"""Trang Chiến dịch: chiến dịch có bài đăng (hoặc tạo từ Marketing Hub), kèm đếm bài theo trạng thái."""
	_require_editor()
	from omni_crm.marketing import views

	return views.campaigns(status)


@frappe.whitelist()
def campaign_detail(name: str) -> dict[str, Any]:
	"""Chiến dịch và các bài (điểm, nền tảng, trạng thái)."""
	_require_editor()
	from omni_crm.marketing import views

	return views.campaign_detail(name)


@frappe.whitelist()
def item_detail(name: str) -> dict[str, Any]:
	"""Trang bài đăng: nội dung, nền tảng (trạng thái, link theo dõi, link bài), nhật ký đăng."""
	_require_editor()
	from omni_crm.marketing import views

	return views.item_detail(name)


@frappe.whitelist(methods=["POST"])
def approve_campaign(name: str) -> list[dict[str, Any]]:
	"""Nút "Duyệt và lên lịch" của cả chiến dịch: gửi duyệt / duyệt từng bài đủ điều kiện, trả kết quả từng bài."""
	_require_editor()
	from omni_crm.marketing import views

	return views.approve_campaign(name)


@frappe.whitelist()
def get_calendar(month: int, year: int, campaign: str | None = None, platform: str | None = None) -> list:
	"""Lịch đăng content theo tháng: một mục mỗi nền tảng đăng (giờ, nền tảng, trạng thái, bài, chiến dịch)."""
	_require_editor()
	from omni_crm.marketing import views

	return views.calendar(int(month), int(year), campaign, platform)
