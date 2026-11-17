import frappe
from frappe import _
from frappe.model.document import Document

MAX_BUTTONS = 5  # Zalo nhận 5 nút (đã kiểm 17/11, docs/bot_v3_plan.md mục 3)
MAX_LABEL = 20  # giới hạn kịch bản v3 cho nhãn nút


class OmniScriptNode(Document):
	"""Một ô kịch bản bot v3 (nội dung, nút, hành động)."""

	def validate(self) -> None:
		"""Chuẩn hóa mã (bỏ #, viết hoa) và kiểm nút: ≤ 5 nút, nhãn ≤ 20 ký tự."""
		self.code = (self.code or "").strip().lstrip("#").upper()
		validate_buttons([(b.label, b.target) for b in self.buttons or []])
		for b in self.buttons or []:
			b.label = (b.label or "").strip()
			b.target = (b.target or "").strip().lstrip("#").upper()


def validate_buttons(buttons: list[tuple[str, str]]) -> None:
	"""Ném ValidationError nếu quá 5 nút, nhãn trống/quá 20 ký tự hoặc thiếu mã đích."""
	if len(buttons) > MAX_BUTTONS:
		frappe.throw(_("Tối đa {0} nút cho một ô kịch bản").format(MAX_BUTTONS), frappe.ValidationError)
	for label, target in buttons:
		label = (label or "").strip()
		if not label or len(label) > MAX_LABEL:
			frappe.throw(
				_("Nhãn nút phải có từ 1 đến {0} ký tự: {1}").format(MAX_LABEL, label), frappe.ValidationError
			)
		if not (target or "").strip().lstrip("#"):
			frappe.throw(_("Nút {0} chưa có mã ô đích").format(label), frappe.ValidationError)
