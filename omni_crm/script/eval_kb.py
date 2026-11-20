"""Lớp "hiểu" của bộ kiểm thử nhiều lượt K15 (yêu cầu 20/11): chạy AI THẬT trên đúng đầu vào bot sẽ gửi, ghi lại kết quả.

Quy trình (không tạo khách giả, không gửi tin nào):
1. Trên site thử: `K15_CAPTURE=<inputs.json> bench --site <site thử> run-tests --module omni_crm.tests.test_k15_eval_kb`
   ghi đầu vào (ngữ cảnh + tin khách) của lượt được chấm ở từng ca.
2. Trên máy chủ có khoá AI: `bench --site <site> execute omni_crm.script.eval_kb.record --kwargs
   "{'inputs_path': '<inputs.json>', 'out_path': '<k15_recording.json>'}"`.
3. Chép `k15_recording.json` về `omni_crm/tests/` rồi chạy lại bước 1 với `K15_REPORT=<báo cáo.json>` để chấm hành vi.

Các câu trong bộ là câu mẫu do đội viết lại từ những lần thử (không có email / số điện thoại thật) nên gọi với `synthetic=True`.
"""

from __future__ import annotations

import json

import frappe

from omni_crm.bot import ai_client
from omni_crm.bot.prompts.loader import load_prompt, render
from omni_crm.script import classify


def record(inputs_path: str, out_path: str) -> dict:
	"""Gọi bước hiểu (câu lệnh đang ghim) cho từng đầu vào đã ghi; lưu {mã ca: {tin khách: kết quả thô}}."""
	with open(inputs_path, encoding="utf-8") as f:
		inputs = json.load(f)
	prompt = load_prompt(classify.PROMPT_NAME, classify.PROMPT_VERSION)
	model = frappe.get_cached_doc("Omni Settings").get("analyze_model") or classify.DEFAULT_MODEL
	out: dict = {}
	calls = 0
	for case_id, items in inputs.items():
		for text, content in items.items():
			res = ai_client.call_tool_result(
				classify.AI_KIND,
				model,
				render(prompt),
				[{"role": "user", "content": content}],
				classify.TOOL,
				prompt_version=f"{classify.PROMPT_NAME}:{prompt.version}:k15",
				max_tokens=classify.MAX_TOKENS,
				synthetic=True,
			)
			out.setdefault(case_id, {})[text] = res.output if isinstance(res.output, dict) else {}
			calls += 1
	meta = {"prompt": prompt.version, "model": model, "calls": calls}
	with open(out_path, "w", encoding="utf-8") as f:
		json.dump({"_meta": meta, **out}, f, ensure_ascii=False, indent=1)
	return meta
