"""Hỗ trợ test định danh/gộp: dữ liệu được commit thật nên phải dọn thủ công."""

from datetime import timedelta
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.identity import resolver
from omni_crm.identity.normalize import normalize_sender

PHONE_LIKE = "+849031%"  # dải số 0903 1xx xxx dùng riêng cho test
EMAIL_LIKE = "%@td105.example"
KEY_LIKE = "%TD105%"


def phone(n: int) -> str:
	"""Số di động test dạng 0903105nnn (viết thô)."""
	return f"0903105{n:03d}"


def e164(n: int) -> str:
	return f"+8490310{5}{n:03d}"


def make_msg(
	channel_type: str,
	sender: str,
	text: str | None = None,
	profile: dict | None = None,
	event_type: str = "message",
	received_at=None,
	name: str | None = None,
):
	"""Tạo tin giả có các trường resolver cần (không ghi DB)."""
	import json

	return SimpleNamespace(
		name=name,
		channel="TD105 kenh",
		channel_type=channel_type,
		event_type=event_type,
		external_sender_id=sender,
		identity_key=f"{channel_type}:{normalize_sender(channel_type, sender)}",
		received_at=received_at or now_datetime(),
		text=text,
		sender_profile=json.dumps(profile) if profile else None,
	)


def cleanup() -> None:
	"""Xóa mọi dữ liệu test D1-05 và commit."""
	frappe.db.rollback()
	leads = set(
		frappe.get_all(
			"CRM Lead",
			or_filters=[
				["omni_phone_e164", "like", PHONE_LIKE],
				["omni_email_norm", "like", EMAIL_LIKE],
				["first_name", "like", "TD105%"],
			],
			pluck="name",
		)
	)
	idents = frappe.get_all(
		"Omni Identity",
		or_filters=[["identity_key", "like", KEY_LIKE], ["phone_e164", "like", PHONE_LIKE]],
		pluck="name",
	)
	idents += frappe.get_all("Omni Identity", filters={"lead": ["in", list(leads) or [""]]}, pluck="name")
	leads.update(
		x
		for x in frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead")
		if x
	)
	# Lead tạo bởi resolver chỉ có tên mặc định/tên hiển thị: tìm qua định danh
	leads = {x for x in leads if x}
	if leads:  # Lead đã gộp vào Lead test cũng là dữ liệu test
		leads.update(
			frappe.get_all("CRM Lead", filters={"omni_merged_into": ["in", list(leads)]}, pluck="name")
		)
	if leads:
		frappe.db.delete("Omni Duplicate Candidate", {"lead_a": ["in", list(leads)]})
		frappe.db.delete("Omni Duplicate Candidate", {"lead_b": ["in", list(leads)]})
		for dt, f in (
			("Comment", "reference_name"),
			("Communication", "reference_name"),
			("CRM Task", "reference_docname"),
			("FCRM Note", "reference_docname"),
			("CRM Call Log", "reference_docname"),
		):
			frappe.db.delete(dt, {f: ["in", list(leads)]})
		frappe.db.delete("Dynamic Link", {"link_doctype": "CRM Lead", "link_name": ["in", list(leads)]})
		frappe.db.delete("Communication Link", {"link_doctype": "CRM Lead", "link_name": ["in", list(leads)]})
		frappe.db.delete("Omni Inbound Message", {"lead": ["in", list(leads)]})
		frappe.db.delete("Omni Outbound Message", {"lead": ["in", list(leads)]})
		frappe.db.delete("CRM Deal", {"lead": ["in", list(leads)]})
	if idents:
		frappe.db.delete("Omni Identity", {"name": ["in", idents]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Omni Inbound Message", {"channel": ["like", "TD105%"]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", "TD105%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", "TD105%"]})
	frappe.db.delete("CRM Call Log", {"id": ["like", "TD105%"]})
	frappe.db.commit()


class IdentityTestCase(FrappeTestCase):
	"""Dọn trước/sau mỗi test; tiện ích tạo Lead, gọi resolve và đếm."""

	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		frappe.cache().delete_keys("omni:")
		self.tracked: list[str] = []

	def make_lead(self, first_name: str, mobile: str | None = None, email: str | None = None, **kw) -> str:
		doc = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": first_name, "mobile_no": mobile, "email": email, **kw}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name

	def resolve(self, *msgs):
		res = resolver.resolve_full(list(msgs))
		self.tracked.append(res.identity)
		return res

	def lead_count(self) -> int:
		return frappe.db.count("CRM Lead")

	def candidates(self, lead: str) -> list[dict]:
		return frappe.get_all(
			"Omni Duplicate Candidate",
			or_filters=[["lead_a", "=", lead], ["lead_b", "=", lead]],
			fields=["name", "lead_a", "lead_b", "status", "score", "reason"],
		)

	def ago(self, **kw):
		return now_datetime() - timedelta(**kw)
