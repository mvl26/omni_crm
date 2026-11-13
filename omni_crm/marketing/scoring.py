"""Chấm điểm bài đăng (V3-10, Prompt 4 mục 4): mỗi bài một lần gọi AI theo thang 100, cộng các luật cứng.

Luật cứng chạy sau AI và chỉ được hạ điểm: số tiền / phần trăm không có trong kho tri thức hay yêu cầu -> tiêu chí
thông tin tối đa 10; tiêu đề Zalo quá 150 ký tự -> độ dài 0. Điểm chỉ ghi vào bài qua `apply_to_item`
(cờ `score_via_ai`), người dùng không sửa tay được.
"""

from __future__ import annotations

import re
from typing import Any

import frappe
from frappe import _

from omni_crm.marketing import ai, kb, prompts, rules
from omni_crm.marketing import settings as mk_settings

NUMBER_CLAIM = re.compile(
	r"\d[\d.,]*\s*(?:%|phần trăm|đ\b|đồng|vnđ|vnd|triệu|tỷ|tỉ|nghìn|ngàn|k\b|usd|\$)", re.IGNORECASE
)
CRITERIA_MAX = {"thong_tin": 40, "keu_goi": 20, "giong_van": 20, "do_dai": 20}
LABELS = {"thong_tin": "Thông tin", "keu_goi": "Kêu gọi", "giong_van": "Giọng văn", "do_dai": "Độ dài"}


def _digits(text: str) -> str:
	return re.sub(r"\D", "", text or "")


def unsourced_claims(caption: str, context: str) -> list[str]:
	"""Các cụm số tiền / phần trăm trong caption mà dãy số không xuất hiện trong ngữ cảnh (kho tri thức + yêu cầu)."""
	ctx_digits = {_digits(m.group(0)) for m in re.finditer(r"\d[\d.,]*", context or "")}
	out = []
	for m in NUMBER_CLAIM.finditer(caption or ""):
		if _digits(m.group(0)) not in ctx_digits:
			out.append(m.group(0).strip())
	return out


def hard_rules(post: dict, criteria: dict, context: str) -> tuple[dict, list[str]]:
	"""Áp luật cứng lên điểm AI; trả (tiêu chí sau điều chỉnh, vấn đề thêm)."""
	crit = {k: max(0, min(int(criteria.get(k) or 0), mx)) for k, mx in CRITERIA_MAX.items()}
	issues: list[str] = []
	claims = unsourced_claims(post.get("caption") or "", context)
	if claims:
		crit["thong_tin"] = min(crit["thong_tin"], 10)
		issues.append(_("Số liệu chưa có nguồn trong Kho tri thức: {0}").format(", ".join(claims[:5])))
	if post.get("platform") == rules.ZALO and len(post.get("title") or "") > 150:
		crit["do_dai"] = 0
		issues.append(_("Tiêu đề Zalo quá 150 ký tự"))
	if prompts.PLACEHOLDER in (post.get("caption") or "") + (post.get("title") or ""):
		issues.append(_("Còn chỗ [cần bổ sung]: điền thông tin trước khi duyệt"))
	if re.search(r"https?://", post.get("caption") or ""):
		issues.append(_("Caption có đường link: hệ thống tự gắn link có mã theo dõi"))
	return crit, issues


def _note(crit: dict, reason: str, issues: list[str]) -> str:
	parts = ", ".join(f"{LABELS[k]} {crit[k]}/{mx}" for k, mx in CRITERIA_MAX.items())
	lines = [parts, reason.strip()] + [f"- {i}" for i in issues]
	return "\n".join(x for x in lines if x)[:1500]


def score_post(
	post: dict, *, brief: str = "", batch_id: str | None = None, extra: list[kb.Passage] | None = None
) -> dict[str, Any]:
	"""Chấm một bài (dict có platform, post_format, title, caption, funnel_stage); trả {score, note, criteria, issues,
	status, cost_usd}. AI lỗi / hết ngân sách: score None, note là lý do.
	"""
	passages = kb.search(f"{post.get('title', '')} {post.get('caption', '')}", limit=6)
	seen = {p.id for p in passages}
	passages += [p for p in (extra or []) if p.id not in seen]
	context = brief + "\n" + "\n".join(p.text for p in passages)
	body = (
		f'<bai_dang nen_tang="{kb.esc(post.get("platform"))}" dinh_dang="{kb.esc(post.get("post_format"))}" '
		f'giai_doan="{kb.esc(post.get("funnel_stage"))}">\n<tieu_de>{kb.esc(post.get("title"))}</tieu_de>\n'
		f"<caption>{kb.esc(post.get('caption'))}</caption>\n</bai_dang>"
	)
	messages = [
		{
			"role": "user",
			"content": [
				{
					"type": "text",
					"text": f"<yeu_cau>{kb.esc(brief)}</yeu_cau>" if brief else "<yeu_cau></yeu_cau>",
				},
				{"type": "text", "text": kb.render_passages(passages)},
				{"type": "text", "text": body},
				{"type": "text", "text": "Chấm bài đăng trên."},
			],
		}
	]
	result = ai.call(
		"score",
		prompts.system_scorer(mk_settings.brand_voice()),
		messages,
		prompts.scorer_tool(),
		max_tokens=3000,
		timeout=120,
		batch_id=batch_id,
	)
	if result.status != "Success":
		return {
			"score": None,
			"note": ai.user_message(result),
			"criteria": {},
			"issues": [],
			"status": result.status,
			"cost_usd": result.cost_usd,
		}
	out = result.output
	crit, more = hard_rules(post, out.get("criteria") or {}, context)
	issues = list(out.get("issues") or []) + more
	score = sum(crit.values())
	return {
		"score": score,
		"note": _note(crit, out.get("reason") or "", issues),
		"criteria": crit,
		"issues": issues,
		"status": result.status,
		"cost_usd": result.cost_usd,
	}


def apply_to_item(item: str, result: dict) -> None:
	"""Ghi điểm vào bài (chỉ đường này đổi được quality_score)."""
	doc = frappe.get_doc("Omni Content Item", item)
	doc.quality_score = int(result.get("score") or 0)
	doc.quality_note = result.get("note") or ""
	doc.flags.score_via_ai = True
	doc.flags.ignore_permissions = True
	doc.save()


def score_item(item: str) -> dict:
	"""Job nền: chấm lại một bài đã lưu (lấy nền tảng đầu tiên của bài) và ghi điểm; trả kết quả chấm."""
	doc = frappe.get_doc("Omni Content Item", item)
	target = frappe.get_all(
		"Omni Content Target",
		filters={"content_item": item, "status": ["!=", rules.T_CANCELLED]},
		fields=["platform", "post_format"],
		limit=1,
	)
	campaign = (
		frappe.db.get_value(
			"Omni Campaign", doc.campaign, ["objective", "audience", "key_message"], as_dict=True
		)
		or {}
	)
	brief = "\n".join(
		x for x in (campaign.get("objective"), campaign.get("audience"), campaign.get("key_message")) if x
	)
	post = {
		"platform": target[0].platform if target else "",
		"post_format": target[0].post_format if target else "",
		"title": doc.title,
		"caption": doc.caption,
		"funnel_stage": doc.funnel_stage,
	}
	result = score_post(post, brief=brief, batch_id=f"item-{item}")
	if result["score"] is not None:
		apply_to_item(item, result)
		frappe.db.commit()
	frappe.publish_realtime(
		"oc_publish_update", {"item": item, "quality_score": result["score"]}, after_commit=False
	)
	return result
