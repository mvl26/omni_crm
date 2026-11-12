"""Bộ mẫu thư dựng sẵn cho sale: Email Template của Frappe, chọn ở nút mẫu thư khi soạn thư trên Lead/Deal trong CRM.

Cài (chạy lại không sao):  bench --site <site> execute omni_crm.crm_bridge.email_templates.install
Cho Sales Manager tạo/sửa mẫu: bench --site <site> execute omni_crm.crm_bridge.email_templates.grant_manager_access

- Chỉ tạo mẫu chưa có (theo tên). Mẫu đã có, kể cả mẫu người quản lý đã sửa, giữ nguyên.
- Lead thiếu dữ liệu không làm thư có chỗ trống hay chữ "None": không có tên -> "Kính gửi anh/chị,", không có công ty -> bỏ cả cụm,
  sale chưa khai số điện thoại -> bỏ dòng điện thoại.
- Nội dung theo quy tắc trả lời khách của bot (bot/prompts/reply_v2.md): không nêu phí, tỷ lệ, chiết khấu, điều khoản hay
  cam kết; xưng "Miyano", gọi khách là "anh/chị"; không dùng trường Salutation (danh mục tiếng Anh: Mr, Ms...).
- Soạn dạng Rich Text (use_html = 0): giao diện CRM chỉ điền nội dung mẫu từ trường `response`
  (crm/frontend/src/components/EmailEditor.vue, applyEmailTemplate).
"""

from __future__ import annotations

import frappe

LEAD = "CRM Lead"
DEAL = "CRM Deal"
MANAGER_ROLE = "Sales Manager"

# Biến dùng chung, đặt ở đầu thân thư (Jinja của Frappe: Lead/Deal là ngữ cảnh, `frappe` có get_fullname, db.get_value).
# Không dùng `lead_name`: CRM ghép cả danh xưng vào đó, và khi không có tên người thì lấy tên công ty, phần trước @ của email
# hoặc "Unnamed Lead". Tên tạm omni_crm đặt khi kênh không cho biết tên ("Khách Email 1234", crm_bridge.lead.default_lead_name)
# và tên là địa chỉ email đều coi như không có tên.
_VARS = (
	'{% set ten = ([first_name, middle_name, last_name] | select | join(" ")) | trim %}'
	'{% if ten.startswith("Khách ") or "@" in ten %}{% set ten = "" %}{% endif %}'
	"{% set cty = organization_name or organization %}"
	'{% set dt = frappe.db.get_value("User", frappe.session.user, "mobile_no") %}'
)
_GREETING = '<p>Kính gửi anh/chị{{ " " ~ ten if ten else "" }},</p>'
_SIGNATURE = (
	"<p>Trân trọng,<br>{{ frappe.get_fullname(frappe.session.user) }}<br>Công ty TNHH Miyano Việt Nam"
	"{% if dt %}<br>Điện thoại: {{ dt }}{% endif %}</p>"
)
_COMPANY_SUBJECT = '{{ organization_name or organization or "anh/chị" }}'


def _body(*paragraphs: str) -> str:
	return _VARS + _GREETING + "".join(f"<p>{p}</p>" for p in paragraphs) + _SIGNATURE


_CAM_ON = (
	"Miyano Việt Nam cảm ơn " + _COMPANY_SUBJECT + " đã liên hệ",
	_body(
		"Cảm ơn anh/chị{{ ' và ' ~ cty if cty else '' }} đã quan tâm đến dịch vụ vận hành hợp đồng cung ứng "
		"vật tư y tế (SPD) của Miyano.",
		"Để tư vấn đúng nhu cầu, Miyano xin phép được trao đổi ngắn với anh/chị qua điện thoại hoặc gặp trực tiếp. "
		"Anh/chị vui lòng trả lời thư này kèm thời gian thuận tiện và số điện thoại liên hệ.",
	),
)
_XIN_THONG_TIN = (
	"Miyano Việt Nam: thông tin cần để tư vấn vận hành hợp đồng",
	_body(
		"Để Miyano đánh giá và đề xuất phương án vận hành phù hợp, anh/chị vui lòng gửi giúp các thông tin sau "
		"(phần nào có sẵn):",
		"- Tên gói thầu và bệnh viện, cơ sở y tế<br>- Danh mục vật tư y tế trong hợp đồng<br>"
		"- Thời gian thực hiện hợp đồng<br>- Đầu mối liên hệ phía anh/chị",
		"Miyano sẽ liên hệ lại với anh/chị sau khi nhận được thông tin.",
	),
)
_HEN_LICH = (
	"Miyano Việt Nam xin hẹn lịch trao đổi",
	_body(
		"Miyano xin phép được hẹn một buổi trao đổi với anh/chị về nhu cầu vận hành hợp đồng cung ứng vật tư y tế"
		"{{ ' của ' ~ cty if cty else '' }}.",
		"Anh/chị vui lòng cho Miyano biết thời gian và hình thức thuận tiện: gọi điện, họp trực tuyến hoặc gặp "
		"trực tiếp.",
	),
)
_KHONG_LIEN_LAC = (
	"Miyano Việt Nam đã gọi cho anh/chị",
	_body(
		"Miyano đã gọi điện cho anh/chị theo thông tin anh/chị để lại nhưng chưa liên lạc được.",
		"Anh/chị vui lòng trả lời thư này kèm thời gian thuận tiện để Miyano gọi lại"
		"{{ ', hoặc gọi trực tiếp số ' ~ dt if dt else '' }}.",
	),
)
_NHAC_LAI = (
	"Miyano Việt Nam: anh/chị còn quan tâm đến dịch vụ SPD?",
	_body(
		"Miyano đã liên hệ với anh/chị trước đây về dịch vụ vận hành hợp đồng cung ứng vật tư y tế.",
		"Nếu anh/chị vẫn còn nhu cầu, Miyano rất mong được trao đổi thêm. Nếu lúc này chưa phù hợp, anh/chị chỉ "
		"cần trả lời thư này để Miyano không làm phiền.",
	),
)

# (tên mẫu, loại trang, (tiêu đề, nội dung)). Tên Email Template là duy nhất nên mẫu cho Deal có hậu tố riêng.
TEMPLATES: list[tuple[str, str, tuple[str, str]]] = [
	("Miyano - Cảm ơn đã liên hệ", LEAD, _CAM_ON),
	("Miyano - Xin thông tin hợp đồng", LEAD, _XIN_THONG_TIN),
	("Miyano - Hẹn lịch trao đổi", LEAD, _HEN_LICH),
	("Miyano - Gọi không được", LEAD, _KHONG_LIEN_LAC),
	("Miyano - Nhắc lại khi chưa phản hồi", LEAD, _NHAC_LAI),
	("Miyano - Xin thông tin hợp đồng (Deal)", DEAL, _XIN_THONG_TIN),
	("Miyano - Hẹn lịch trao đổi (Deal)", DEAL, _HEN_LICH),
	("Miyano - Nhắc lại khi chưa phản hồi (Deal)", DEAL, _NHAC_LAI),
]


def install() -> dict[str, list[str]]:
	"""Tạo các mẫu chưa có; không đụng mẫu đã có."""
	created, kept = [], []
	for name, doctype, (subject, response) in TEMPLATES:
		if frappe.db.exists("Email Template", name):
			kept.append(name)
			continue
		frappe.get_doc(
			{
				"doctype": "Email Template",
				"name": name,
				"subject": subject,
				"response": response,
				"use_html": 0,
				"enabled": 1,
				"reference_doctype": doctype,
			}
		).insert(ignore_permissions=True)
		created.append(name)
	frappe.db.commit()
	for n in created:
		print(f"tạo: {n}")
	for n in kept:
		print(f"đã có, giữ nguyên: {n}")
	return {"created": created, "kept": kept}


def grant_manager_access(role: str = MANAGER_ROLE) -> None:
	"""Cho vai trò có sẵn (mặc định Sales Manager) đọc/tạo/sửa/xóa Email Template. Không tạo vai trò mới."""
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("Role", role):
		frappe.throw(f"Không có vai trò {role}. Không tạo vai trò mới; kiểm tra lại tên vai trò.")
	add_permission("Email Template", role, 0)  # lần đầu: chép quyền chuẩn sang Custom DocPerm để không mất
	for ptype in ("read", "write", "create", "delete"):
		update_permission_property("Email Template", role, 0, ptype, 1)
	frappe.clear_cache(doctype="Email Template")
	frappe.db.commit()
	print(f"{role}: đọc/tạo/sửa/xóa Email Template")
