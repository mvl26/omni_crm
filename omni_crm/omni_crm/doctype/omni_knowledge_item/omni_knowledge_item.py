from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document

ITEM_ID_RE = re.compile(r"^KB-\d{5,}$")
PREFIX = "KB-"


def next_item_id() -> str:
	"""Mã mục kế tiếp (KB-00001 …) theo số lớn nhất hiện có."""
	rows = frappe.get_all("Omni Knowledge Item", pluck="name")
	top = max((int(r[3:]) for r in rows if ITEM_ID_RE.match(r)), default=0)
	return f"{PREFIX}{top + 1:05d}"


class OmniKnowledgeItem(Document):
	def before_naming(self):
		"""Chưa có `item_id` thì cấp số kế tiếp (tên bản ghi = item_id)."""
		if not (self.item_id or "").strip():
			self.item_id = next_item_id()

	def validate(self):
		self.item_id = (self.item_id or "").strip()
		if not ITEM_ID_RE.match(self.item_id):
			frappe.throw(_("Mã mục phải có dạng KB-00001"))

	def on_update(self):
		bump_kb_version()

	def on_trash(self):
		bump_kb_version()


def bump_kb_version(doc=None, method=None) -> None:
	"""Tăng Omni Settings.kb_version một đơn vị (nguyên tử trong DB) khi kho tri thức đổi."""
	settings = "Omni Settings"
	frappe.db.sql(
		"update `tabSingles` set `value` = cast(`value` as unsigned) + 1 where doctype=%s and field='kb_version'",
		settings,
	)
	if not frappe.db.sql("select 1 from `tabSingles` where doctype=%s and field='kb_version'", settings):
		frappe.db.set_single_value(settings, "kb_version", 1)
	frappe.clear_document_cache(settings, settings)
	frappe.db.value_cache.get(settings, {}).pop("kb_version", None)
