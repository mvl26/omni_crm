"""Mẫu tin nhắn chat cho sale (ADR 0026): DocType `Omni Chat Template`, chọn ở nút "Mẫu tin" của tab Messenger.

Chọn mẫu chỉ ĐIỀN chữ vào ô soạn; sale sửa rồi tự bấm gửi, mọi guard gửi tin (24 giờ, từ chối liên hệ, sandbox) giữ nguyên.
Không bao giờ tự gửi.

Nội dung là chữ thuần, dựng bằng Jinja sandbox của Frappe với các biến điền sẵn (`VARIABLES`). Khác mẫu thư email
(`crm_bridge.email_templates`): không HTML, không tiêu đề, tên khách đã được làm sạch sẵn thành biến.

Cài bộ mẫu khởi đầu (chỉ tạo mẫu chưa có theo tên, mẫu đã có kể cả đã sửa giữ nguyên; chạy lại không sao):
    bench --site <site> execute omni_crm.crm_bridge.chat_templates.install
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint

DOCTYPE = "Omni Chat Template"
LEAD = "CRM Lead"
VARIABLES = ("ten_goi", "ten_khach", "cong_ty", "ten_sale", "sdt_sale", "lead")
MAX_CONTENT = 2000  # một tin Messenger tối đa 2000 ký tự; mẫu dài hơn thì nên tách
MAX_TEMPLATES = 100

# Ngữ cảnh mẫu để kiểm mẫu lúc lưu (không phải dữ liệu thật).
_SAMPLE_LEAD = {"first_name": "Nguyễn Văn An", "organization": "Bệnh viện mẫu", "email": "an@example.com"}


class TemplateError(frappe.ValidationError):
	"""Mẫu tin nhắn không dựng được (sai cú pháp, biến lạ, lỗi khi chạy)."""


def customer_name(lead: dict) -> str:
	"""Họ tên khách; rỗng nếu là tên tạm omni_crm đặt ("Khách Meta Messenger 1234") hoặc là địa chỉ email."""
	parts = (lead.get("first_name"), lead.get("middle_name"), lead.get("last_name"))
	name = " ".join(" ".join(str(p) for p in parts if p).split())
	if name.startswith("Khách ") or "@" in name:
		return ""
	return name


def context_for(lead: dict, user: str | None = None) -> dict:
	"""Biến điền sẵn cho Lead `lead` (dict các trường) và người đang soạn."""
	user = user if user is not None else frappe.session.user
	full = customer_name(lead)
	real_user = bool(user) and user != "Guest"
	return {
		"ten_khach": full,
		"ten_goi": full.split()[-1]
		if full
		else "",  # tên gọi: chữ cuối của họ tên kiểu Việt ("Tổng Bị Minh" -> "Minh")
		"cong_ty": str(lead.get("organization") or "").strip(),
		"ten_sale": frappe.utils.get_fullname(user) if real_user else "",
		"sdt_sale": str(frappe.db.get_value("User", user, "mobile_no") or "").strip() if real_user else "",
		# dict thường, giá trị trống thành "" (không in chữ "None"); trường không có thì Jinja báo rõ trong chữ
		"lead": {k: ("" if v is None else v) for k, v in dict(lead).items()},
	}


def _env():
	from frappe.utils.jinja import get_jenv

	return get_jenv()  # môi trường sandbox của Frappe (FrappeSandboxedEnvironment)


def _short(err: Exception) -> str:
	return (str(err) or type(err).__name__).splitlines()[0][:200]


def unknown_variables(content: str) -> list[str]:
	"""Tên biến mẫu dùng mà không có trong `VARIABLES` (hay viết sai); ném TemplateError nếu sai cú pháp."""
	from jinja2 import TemplateSyntaxError, meta

	env = _env()
	try:
		names = meta.find_undeclared_variables(env.parse(content))
	except TemplateSyntaxError as e:
		raise TemplateError(_("Mẫu sai cú pháp (dòng {0}): {1}").format(e.lineno, e.message)) from e
	return sorted(n for n in names if n not in VARIABLES and n not in env.globals)


def _tidy(text: str) -> str:
	"""Dọn khoảng trắng do biến trống để lại: bỏ khoảng trắng thừa và trước dấu câu, gộp dòng trống liên tiếp."""
	lines = []
	for line in text.replace("\r\n", "\n").split("\n"):
		line = re.sub(r"[ \t]+", " ", line).strip()
		line = re.sub(r" +([,.!?;:])", r"\1", line)
		lines.append(line)
	return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def render(content: str, ctx: dict) -> str:
	"""Dựng chữ của mẫu; ném TemplateError (KHÔNG frappe.throw, để không bật hộp thông báo khi chỉ liệt kê mẫu)."""
	content = content or ""
	if ".__" in content:
		raise TemplateError(_("Mẫu chứa chuỗi không được phép"))
	unknown = unknown_variables(content)
	if unknown:
		raise TemplateError(_("Mẫu dùng biến không có sẵn: {0}").format(", ".join(unknown)))
	try:
		out = _env().from_string(content).render(ctx)
	except Exception as e:
		raise TemplateError(_("Mẫu lỗi khi điền: {0}").format(_short(e))) from e
	return _tidy(out)


def check(content: str) -> None:
	"""Kiểm mẫu lúc lưu (controller): rỗng, quá dài, sai cú pháp, biến lạ, lỗi khi điền với dữ liệu mẫu."""
	content = (content or "").strip()
	if not content:
		frappe.throw(_("Nội dung mẫu không được trống"), TemplateError)
	if len(content) > MAX_CONTENT:
		frappe.throw(
			_("Mẫu dài {0} ký tự, tối đa {1} (một tin Messenger)").format(len(content), MAX_CONTENT),
			TemplateError,
		)
	try:
		for lead in (_SAMPLE_LEAD, {}):  # có đủ dữ liệu và không có gì: cả hai phải dựng được
			render(content, context_for(lead, user=""))
	except TemplateError as e:
		frappe.throw(str(e), TemplateError)


def for_lead(lead: str, channel_type: str) -> list[dict]:
	"""Mẫu đang dùng cho kênh `channel_type` (và mẫu để trống kênh), đã điền cho Lead `lead` và người đang soạn.

	Người gọi đã kiểm quyền đọc Lead. Mẫu lỗi vẫn có trong danh sách với `error` (chữ rỗng) để sale báo quản lý.
	"""
	rows = frappe.get_all(
		DOCTYPE,
		filters={"enabled": 1},
		fields=["name", "template_name", "usage_note", "channel_type", "sort_order", "content"],
		limit_page_length=MAX_TEMPLATES * 2,
	)
	rows = [r for r in rows if (r.channel_type or "") in ("", channel_type)]
	rows.sort(key=lambda r: (cint(r.sort_order), (r.template_name or "").lower()))
	lead_row = frappe.db.get_value(LEAD, lead, "*", as_dict=True) or {}
	ctx = context_for(lead_row)
	out = []
	for r in rows[:MAX_TEMPLATES]:
		item = {
			"name": r.name,
			"title": r.template_name,
			"note": r.usage_note or "",
			"text": "",
			"error": None,
		}
		try:
			item["text"] = render(r.content, ctx)
		except TemplateError as e:
			item["error"] = str(e)
		out.append(item)
	return out


# ---- Bộ mẫu khởi đầu (người phụ trách duyệt chữ trước khi cài trên máy thật) ----
# Cùng quy tắc với mẫu thư email và bot (bot/prompts/reply_v2.md): không nêu giá, chiết khấu, điều khoản, cam kết thời
# hạn; gọi khách "anh/chị"; không biểu tượng cảm xúc. Sale xưng "em" vì tin đi dưới tên người đang soạn.
_G = "{{ ' ' ~ ten_goi if ten_goi else '' }}"

TEMPLATES: list[tuple[str, str, int, str]] = [
	(
		"Chào hỏi",
		"Tin đầu tiên khi khách mới nhắn",
		10,
		"Dạ Miyano Việt Nam xin chào anh/chị" + _G + ". Em là {{ ten_sale }}, nhân viên tư vấn của Miyano. "
		"Anh/chị cần em hỗ trợ thông tin gì ạ?",
	),
	(
		"Đã nhận yêu cầu báo giá",
		"Khách gửi danh mục cần báo giá",
		20,
		"Dạ em đã nhận được yêu cầu báo giá của anh/chị" + _G + ". Em đang tổng hợp và sẽ gửi báo giá cho "
		"anh/chị sớm nhất có thể. Anh/chị cho em xin email hoặc số điện thoại để tiện gửi tài liệu ạ.",
	),
	(
		"Xin thông tin để báo giá",
		"Khách hỏi giá nhưng chưa đủ thông tin",
		30,
		"Dạ để báo giá đúng nhu cầu, anh/chị vui lòng cho em xin thêm:\n"
		"- Tên sản phẩm hoặc quy cách\n- Số lượng dự kiến\n- Địa chỉ giao hàng\n- Thời hạn cần báo giá\n"
		"Anh/chị gửi luôn trong tin nhắn này giúp em ạ.",
	),
	(
		"Hẹn gọi lại",
		"Cần trao đổi qua điện thoại",
		40,
		"Dạ để trao đổi kỹ hơn, em xin phép gọi điện cho anh/chị" + _G + ". Anh/chị cho em xin số điện thoại "
		"và thời gian thuận tiện ạ.{% if sdt_sale %} Hoặc anh/chị gọi trực tiếp cho em theo số {{ sdt_sale }}.{% endif %}",
	),
	(
		"Cảm ơn",
		"Khép lại cuộc trò chuyện",
		50,
		"Dạ em cảm ơn anh/chị" + _G + " đã liên hệ Miyano Việt Nam. Khi cần thêm thông tin, anh/chị cứ nhắn "
		"vào đây, em luôn sẵn sàng hỗ trợ ạ.",
	),
]


def install() -> dict[str, list[str]]:
	"""Tạo các mẫu khởi đầu chưa có (theo tên); mẫu đã có giữ nguyên. Kênh để trống = mọi kênh chat."""
	created, kept = [], []
	for name, note, order, content in TEMPLATES:
		if frappe.db.exists(DOCTYPE, name):
			kept.append(name)
			continue
		frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"template_name": name,
				"usage_note": note,
				"sort_order": order,
				"enabled": 1,
				"content": content,
			}
		).insert(ignore_permissions=True)
		created.append(name)
	frappe.db.commit()
	for n in created:
		print(f"tạo: {n}")
	for n in kept:
		print(f"đã có, giữ nguyên: {n}")
	return {"created": created, "kept": kept}
