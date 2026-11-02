import frappe
from frappe import _
from frappe.model.document import Document

from omni_crm.prospect import fit, hospital
from omni_crm.prospect.normalize import hospital_key


class OmniHospitalAlias(Document):
	def autoname(self) -> None:
		self.alias_key = hospital_key(self.alias_key)
		self.name = self.alias_key

	def validate(self) -> None:
		self.alias_key = hospital_key(self.alias_key)
		self.canonical_name = (self.canonical_name or "").strip()
		if not self.alias_key or not self.canonical_name:
			frappe.throw(_("Phải nhập tên viết khác và tên chuẩn"))
		if hospital_key(self.canonical_name) == self.alias_key:
			frappe.throw(_("Tên chuẩn trùng với tên viết khác sau khi chuẩn hóa, không cần ánh xạ"))

	def on_update(self) -> None:
		self._refresh()

	def after_delete(self) -> None:
		self._refresh()

	def _refresh(self) -> None:
		"""Đổi bảng alias: cập nhật khóa bệnh viện của các gói liên quan rồi tính lại điểm các nhà thầu đó."""
		for org in hospital.refresh_awards_for_alias(self.alias_key):
			fit.apply_fit(org)
