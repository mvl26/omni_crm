"""Bộ đo "bot hiểu ý khách theo ngữ cảnh" (chạy tay, gọi AI thật): mỗi ca gồm vài lượt trước đó, câu bot vừa hỏi,
tin khách và đáp án đúng. Dùng để biết tỷ lệ hiểu đúng của câu lệnh `script_classify` trước khi đưa lên máy chủ.

Chạy: `bench --site <site> execute omni_crm.script.context_eval.run`. Các câu trong bộ là câu mẫu do đội viết lại từ
những lần thử thật (không có email / số điện thoại của khách), nên gọi AI với `synthetic=True`.
"""

from __future__ import annotations

import frappe

from omni_crm.bot import ai_client
from omni_crm.bot.prompts.loader import load_prompt, render
from omni_crm.script import classify

Q_ITEMS = "Anh/chị đang cần cung cấp những mặt hàng nào ạ? Anh/chị có thể gõ tên hàng, quy cách, hãng, hoặc gửi file danh mục (Excel, PDF, ảnh) đều được ạ."
Q_QTY = "Anh/chị cần số lượng bao nhiêu cho mỗi mặt hàng ạ?"
Q_WHEN = "Anh/chị cần hàng vào khoảng thời gian nào ạ?\n\n1. Trong 1 tuần\n2. Trong 1 tháng\n3. 1–3 tháng\n4. Theo lịch gói thầu"
Q_SUB = "Nếu không có đúng hãng anh/chị yêu cầu, Miyano đề xuất hãng khác tương đương để thay thế được không ạ?\n\n1. Được\n2. Chỉ đúng hãng\n3. Tuỳ mặt hàng"
Q_MODE = "Anh/chị muốn xem demo theo hình thức nào ạ?\n\n1. Online qua Teams\n2. Gặp trực tiếp"
Q_SLOT = "Miyano đề xuất các khung giờ sau, anh/chị chọn giúp Miyano một khung phù hợp ạ:\n\n1. Thứ Sáu 20/11, 8:00\n2. Thứ Sáu 20/11, 14:00\n3. Thứ Hai 23/11, 8:00\n4. Giờ khác"
Q_ATT = "Bên anh/chị dự kiến có bao nhiêu người tham dự ạ? Miyano hỏi để chuẩn bị nội dung cho phù hợp."
Q_FILE = "Dạ, anh/chị gửi giúp Miyano danh mục cần báo giá (file Excel, PDF hoặc ảnh chụp đều được ạ)."
Q_MAIN = "Anh/chị quan tâm nội dung nào ạ? Anh/chị có thể chọn bên dưới hoặc nhắn trực tiếp câu hỏi.\n\n1. Cung ứng vật tư\n2. Phần mềm quản lý\n3. Hệ thống SPD\n4. Khác\n5. Gặp nhân viên"
Q_SPD = "Dạ, Miyano cảm ơn anh/chị đã quan tâm đến hệ thống SPD. Anh/chị muốn Miyano hỗ trợ gì ạ?\n\n1. Tìm hiểu nhanh\n2. Nhận tài liệu\n3. Xin báo giá\n4. Đặt lịch demo\n5. Gặp nhân viên"
Q_DOC = "Dạ, anh/chị đang cần tài liệu về nội dung nào ạ?\n\n1. Giới thiệu SPD\n2. Quy trình triển khai\n3. Hồ sơ năng lực\n4. Khác"
Q_SITES = "Anh/chị đang cung ứng vật tư cho bao nhiêu cơ sở y tế ạ?\n\n1. 1\n2. 2–5\n3. Trên 5"
Q_SW = "Dạ, anh/chị cần báo giá phần mềm quản lý chuỗi cung ứng SupplyCore đúng không ạ?\n\n1. Đúng\n2. Sản phẩm khác"

# (tên, câu bot vừa hỏi, tin khách, quan hệ đúng (None = không xét), các mã chấp nhận (rỗng = không xét), lựa chọn đúng (0 = không xét))
CASES = [
	# --- khách trả lời câu đang hỏi bằng lời tự nhiên ---
	("mode_online", Q_MODE, "Online", "TRA_LOI", (), 1),
	("mode_online_sentence", Q_MODE, "Anh muốn demo online", "TRA_LOI", (), 1),
	("mode_teams", Q_MODE, "qua teams nhé em", "TRA_LOI", (), 1),
	("mode_direct_long", Q_MODE, "gặp trực tiếp tại bệnh viện", "TRA_LOI", (), 2),
	("mode_direct_come", Q_MODE, "em qua chỗ anh đi", "TRA_LOI", (), 2),
	("when_week", Q_WHEN, "tuần sau nhé", "TRA_LOI", (), 0),
	("when_with_question", Q_WHEN, "Thứ 3 tuần sau được k em", "TRA_LOI", (), 0),
	("when_month", Q_WHEN, "trong tháng này", "TRA_LOI", (), 2),
	("sub_yes", Q_SUB, "ok em", "TRA_LOI", (), 1),
	("sub_brand", Q_SUB, "Hãng moba em nhé", "TRA_LOI", (), 0),
	("sub_only", Q_SUB, "anh chỉ lấy đúng hãng thôi", "TRA_LOI", (), 2),
	("items_plain", Q_ITEMS, "găng tay nitrile không bột size M", "TRA_LOI", (), 0),
	("items_noaccent", Q_ITEMS, "bom tiem 5ml voi kim g23", "TRA_LOI", (), 0),
	("qty_words", Q_QTY, "khoảng vài chục hộp thôi", "TRA_LOI", (), 0),
	("att_words", Q_ATT, "chắc tầm hai chục người", "TRA_LOI", (), 0),
	("slot_second", Q_SLOT, "cái thứ hai đi em", "TRA_LOI", (), 2),
	("slot_friday_pm", Q_SLOT, "chiều thứ sáu nhé", "TRA_LOI", (), 2),
	("slot_other", Q_SLOT, "3 giờ chiều ngày thứ 5 tuần sau", "TRA_LOI", (), 0),
	("sw_yes", Q_SW, "Đúng rồi em", "TRA_LOI", (), 1),
	("sites_two", Q_SITES, "bên anh có 2 bệnh viện", "TRA_LOI", (), 2),
	("doc_process", Q_DOC, "cho anh quy trình triển khai", "TRA_LOI", (), 2),
	("main_supplies", Q_MAIN, "Em hỗ trợ anh tư vấn vật tư với", None, ("MENU_VATTU",), 0),
	("spd_demo", Q_SPD, "cho anh đặt lịch demo", None, ("DEMO",), 0),
	# --- khách nói sang việc khác trong lúc bot đang hỏi ---
	("items_demo", Q_ITEMS, "Anh muốn demo spd", "Y_KHAC", ("DEMO",), 0),
	("file_demo", Q_FILE, "Demo cơ mà không phải mua hàng", "Y_KHAC", ("DEMO",), 0),
	("items_software", Q_ITEMS, "thế còn phần mềm thì sao em", "Y_KHAC", ("MENU_PHANMEM",), 0),
	("items_human", Q_ITEMS, "cho anh gặp nhân viên đi", "Y_KHAC", ("GAP_NHANVIEN",), 0),
	("qty_weather", Q_QTY, "hôm nay trời đẹp nhỉ em", "Y_KHAC", ("KHONG_LIENQUAN",), 0),
	("items_medical", Q_ITEMS, "bé nhà anh sốt 39 độ thì uống thuốc gì", "Y_KHAC", ("Y_KHOA",), 0),
	("items_bot", Q_ITEMS, "em là bot hay người vậy", "Y_KHAC", ("BOT_HOI",), 0),
	("mode_kb", Q_MODE, "demo mất bao lâu em?", "Y_KHAC", ("KB_HOI",), 0),
	("att_cancel", Q_ATT, "thôi anh không muốn demo nữa", "Y_KHAC", (), 0),
	("when_docs", Q_WHEN, "à mà gửi anh tài liệu trước đã", "Y_KHAC", ("TAILIEU",), 0),
	("slot_tax", Q_SLOT, "Anh muốn biết mã số thuế của công ty", "Y_KHAC", ("KB_HOI", "CAN_NGUOI"), 0),
	("qty_fix", Q_QTY, "à anh nhầm, đổi găng tay thành khẩu trang nhé", "TRA_LOI", (), 0),
	# --- bot không hỏi gì ---
	("idle_demo", "", "Anh thấy bảo bên mình có phần mềm spd anh muốn đặt lich demo", "", ("DEMO",), 0),
	("idle_buy", "", "Anh muốn mua 1000 cái ống thở bên của em", "", ("BAOGIA",), 0),
	("idle_demo_full", "", "anh muốn xem demo phần mềm online thứ sáu này 14h", "", ("DEMO",), 0),
	("idle_quote_sites", "", "báo giá SPD cho 2 bệnh viện", "", ("BAOGIA",), 0),
	("idle_where", "", "Em đang ở đâu thế", "", ("KB_HOI", "BOT_HOI", "KHONG_LIENQUAN"), 0),
	("idle_tax", "", "Anh muốn biết mã số thuế của công ty", "", ("KB_HOI", "CAN_NGUOI"), 0),
	("idle_demo_plain", "", "Anh muốn đặt lịch demo", "", ("DEMO",), 0),
]


def run(verbose: int = 1) -> dict:
	"""Chạy cả bộ với câu lệnh đang ghim; trả {total, relation_ok, code_ok, choice_ok, all_ok, failed: [...]}."""
	prompt = load_prompt(classify.PROMPT_NAME, classify.PROMPT_VERSION)
	model = frappe.get_cached_doc("Omni Settings").get("analyze_model") or classify.DEFAULT_MODEL
	out = {"version": prompt.version, "total": len(CASES), "relation_ok": 0, "code_ok": 0, "choice_ok": 0, "all_ok": 0, "failed": []}
	for name, asked, text, relation, codes, choice in CASES:
		state = {classify.ASKED_KEY: asked, classify.HISTORY_KEY: [["BOT", asked]] if asked else []}
		raw = ai_client.call_tool(
			classify.AI_KIND,
			model,
			render(prompt),
			[{"role": "user", "content": classify.build_user_message(text, state)}],
			classify.TOOL,
			prompt_version=f"{classify.PROMPT_NAME}:{prompt.version}:eval",
			max_tokens=classify.MAX_TOKENS,
			synthetic=True,
		)
		raw = raw if isinstance(raw, dict) else {}
		got = classify.context_fields(raw, text, bool(asked))
		r_ok = relation is None or got["relation"] == relation  # None: ở menu, coi là chọn mục hay ý mới đều dẫn tới cùng chỗ
		c_ok = (not codes) or raw.get("code") in codes
		ch_ok = (not choice) or got["choice"] == choice
		out["relation_ok"] += r_ok
		out["code_ok"] += c_ok
		out["choice_ok"] += ch_ok
		out["all_ok"] += r_ok and c_ok and ch_ok
		if not (r_ok and c_ok and ch_ok):
			out["failed"].append(
				{"case": name, "text": text, "want": [relation, list(codes), choice], "got": [got["relation"], raw.get("code"), got["choice"]], "empty": not raw}
			)
	if verbose:
		print(frappe.as_json(out))
	return out
