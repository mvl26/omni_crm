"""Tài liệu Kho tri thức marketing (V3-10, ADR 0029). Lưu xong thì lập chỉ mục trong job nền."""

import frappe
from frappe.model.document import Document

from omni_crm.marketing import kb


class OmniKBDocument(Document):
	def before_insert(self) -> None:
		self.status = kb.S_PROCESSING

	def validate(self) -> None:
		kb.validate_document(self)

	def on_update(self) -> None:
		if self.flags.from_indexer:
			return
		if kb.needs_reindex(self):
			kb.queue_index(self.name)

	def on_trash(self) -> None:
		frappe.db.delete(kb.CHUNK, {"document": self.name})
		kb.bump_version()
