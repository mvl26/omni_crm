"""Kho ảnh S3 (MinIO) cho ảnh bài marketing (Single, ADR 0033). Logic ở `omni_crm.marketing.s3_media`."""

import frappe
from frappe import _
from frappe.model.document import Document


class OmniMediaStorage(Document):
	def validate(self) -> None:
		self.endpoint_url = (self.endpoint_url or "").strip().rstrip("/")
		if self.endpoint_url and not self.endpoint_url.startswith(("https://", "http://")):
			frappe.throw(_("Endpoint S3 phải bắt đầu bằng https:// (http:// chỉ dùng khi thử)"))
		self.bucket = (self.bucket or "").strip()
		self.ca_bundle = (self.ca_bundle or "").strip()
		if self.enabled and not (self.endpoint_url and self.bucket and (self.access_key or "").strip()):
			frappe.throw(_("Điền Endpoint S3, Bucket và Access key trước khi bật"))
