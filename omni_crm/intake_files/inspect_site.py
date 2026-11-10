"""Chỉ ĐỌC: liệt kê kênh và cấu hình liên quan tới nạp file intake của site (không in bí mật).

Chạy: bench --site <site> execute omni_crm.intake_files.inspect_site.report
"""

from __future__ import annotations

import frappe


def report() -> None:
	"""In Omni Channel, Email Account (không mật khẩu), Omni Lead Form Website và khóa site_config omni_intake_*."""
	print("== Omni Channel")
	for r in frappe.get_all(
		"Omni Channel",
		fields=["name", "channel_type", "enabled", "email_account", "telephony_provider"],
		order_by="channel_type, name",
	):
		print(
			f"  {r.channel_type:<18} {r.name:<30} bật={r.enabled} email_account={r.email_account or '-'} nhà cung cấp={r.telephony_provider or '-'}"
		)
	print("== Email Account (enable_incoming = CRM tự kéo hộp thư này)")
	for r in frappe.get_all(
		"Email Account",
		fields=[
			"name",
			"email_id",
			"enable_incoming",
			"enable_outgoing",
			"create_lead_from_incoming_email",
			"append_to",
		],
	):
		print(
			f"  {r.name:<30} {r.email_id:<30} nhận={r.enable_incoming} gửi={r.enable_outgoing} tạo_lead_CRM={r.create_lead_from_incoming_email} append_to={r.append_to or '-'}"
		)
	print("== Omni Lead Form (Website)")
	for r in frappe.get_all(
		"Omni Lead Form", filters={"platform": "Website"}, fields=["name", "external_form_id", "enabled"]
	):
		print(f"  {r.external_form_id:<20} bật={r.enabled}")
	print("== site_config")
	for k, v in sorted(frappe.conf.items()):
		if k.startswith("omni_intake"):
			print(f"  {k} = {v}")
	print(
		"== Số liệu: Communication",
		frappe.db.count("Communication"),
		"| Omni Inbound Message",
		frappe.db.count("Omni Inbound Message"),
	)
