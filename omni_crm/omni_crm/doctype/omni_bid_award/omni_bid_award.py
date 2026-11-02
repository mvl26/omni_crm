import frappe
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from omni_crm.prospect import hospital
from omni_crm.prospect.normalize import InvalidTaxId, normalize_tax_id


def build_import_key(tax_id: str, notice_no: str, raw_key: str, award_date, award_value) -> str:
	"""Khóa nhập duy nhất (docs/14 mục 1.2); dùng tên bệnh viện TRƯỚC khi gộp alias để sửa alias không làm nhập lại tạo trùng."""
	if notice_no:
		return f"{tax_id}|{notice_no}|{raw_key}"
	return f"{tax_id}|{raw_key}|{award_date or ''}|{int(flt(award_value))}"


class OmniBidAward(Document):
	def validate(self) -> None:
		org_tax = frappe.db.get_value("CRM Organization", self.organization, "omni_tax_id")
		try:
			given = normalize_tax_id(self.tax_id)
		except InvalidTaxId:
			frappe.throw(_("Mã số thuế «{0}» không hợp lệ").format(self.tax_id))
		if given and org_tax and given != org_tax:
			frappe.throw(
				_("Mã số thuế của gói thầu ({0}) khác mã số thuế của nhà thầu ({1})").format(given, org_tax)
			)
		self.tax_id = given or org_tax or ""
		self.notice_no = (self.notice_no or "").strip()
		self.hospital = (self.hospital or "").strip()
		self.hospital_raw_key, self.hospital_key = hospital.resolve_keys(self.hospital)
		if not self.hospital_raw_key:
			frappe.throw(_("Phải nhập tên bệnh viện / bên mời thầu"))
		self.end_date = (
			getdate(self.award_date) + relativedelta(months=cint(self.duration_months))
			if self.award_date and cint(self.duration_months) > 0
			else None
		)
		self.import_key = build_import_key(
			self.tax_id or f"org:{self.organization}",
			self.notice_no,
			self.hospital_raw_key,
			self.award_date,
			self.award_value,
		)
		other = frappe.db.get_value(
			"Omni Bid Award", {"import_key": self.import_key, "name": ["!=", self.name or ""]}, "name"
		)
		if other:
			frappe.throw(
				_("Gói thầu này đã có trong hệ thống ({0})").format(other), frappe.DuplicateEntryError
			)
