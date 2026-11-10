"""Dựng kênh email cho hộp thư chỉ đọc qua miyano-intake (Gmail API), KHÔNG sửa code CRM.

Tạo (nếu chưa có) một Email Account "vỏ" và một Omni Channel Email trỏ vào nó. Vỏ không kéo thư và không gửi thư:
`enable_incoming = 0`, `create_lead_from_incoming_email = 0`, không đặt `append_to`; chỉ để Communication nạp từ file
intake có chỗ bám và hook `on_communication_insert` tìm được kênh. Thư đi vào đường email có sẵn của CRM.

Mặc định chỉ IN kế hoạch. Muốn ghi thật: `--kwargs '{"mailbox": "...", "apply": 1}'`.
  bench --site <site> execute omni_crm.intake_files.setup.ensure_email_channel --kwargs '{"mailbox": "info@miyano.com.vn"}'
Nếu CRM đã tự kéo hộp thư này (Email Account có enable_incoming = 1) thì không làm gì: file Gmail sẽ bị bỏ qua đúng thiết kế.
"""

from __future__ import annotations

import frappe
from frappe import _

SHELL_PASSWORD_NOTE = "vo-khong-dung"  # trường bắt buộc của Email Account; vỏ không đăng nhập đâu cả


def ensure_email_channel(mailbox: str, apply: int = 0, channel_name: str | None = None) -> dict:
	"""Đảm bảo có Email Account + Omni Channel Email cho `mailbox`; trả dict mô tả việc đã/sẽ làm."""
	mailbox = (mailbox or "").strip().lower()
	if "@" not in mailbox:
		frappe.throw(_("Cần địa chỉ hộp thư hợp lệ"))
	name = channel_name or "email-" + mailbox.split("@", 1)[0]
	plan: dict = {"mailbox": mailbox, "channel": name, "apply": bool(int(apply)), "steps": []}

	account = frappe.db.get_value(
		"Email Account",
		{"email_id": mailbox},
		["name", "enable_incoming", "create_lead_from_incoming_email"],
		as_dict=True,
	)
	if account and account.enable_incoming:
		plan["steps"].append(
			"CRM đã tự kéo hộp thư này (enable_incoming = 1): không cần kênh cho file intake"
		)
		_show(plan)
		return plan
	if account and account.create_lead_from_incoming_email:
		plan["steps"].append(
			f"CẢNH BÁO: Email Account {account.name} đang bật tạo Lead của CRM: tắt đi để không trùng Lead"
		)

	if not account:
		plan["steps"].append(f"tạo Email Account vỏ '{mailbox}' (không nhận, không gửi)")
	if not frappe.db.exists("Omni Channel", name):
		plan["steps"].append(f"tạo Omni Channel Email '{name}' trỏ vào Email Account")
	if not plan["steps"]:
		plan["steps"].append("đã đủ, không có gì phải làm")

	if plan["apply"]:
		if not account:
			doc = frappe.get_doc(
				{
					"doctype": "Email Account",
					"email_account_name": mailbox,
					"email_id": mailbox,
					"enable_incoming": 0,
					"enable_outgoing": 0,
					"default_incoming": 0,
					"default_outgoing": 0,
					"create_lead_from_incoming_email": 0,
					"password": SHELL_PASSWORD_NOTE,
				}
			)
			doc.flags.ignore_validate = True  # vỏ không có máy chủ thư để kiểm tra kết nối
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_permissions=True)
			account_name = doc.name
		else:
			account_name = account.name
		if not frappe.db.exists("Omni Channel", name):
			frappe.get_doc(
				{
					"doctype": "Omni Channel",
					"channel_name": name,
					"channel_type": "Email",
					"email_account": account_name,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
	_show(plan)
	return plan


def _show(plan: dict) -> None:
	print(
		("ĐÃ LÀM" if plan["apply"] else 'KẾ HOẠCH (chưa ghi, thêm "apply": 1 để ghi)')
		+ f" - hộp thư {plan['mailbox']}"
	)
	for step in plan["steps"]:
		print("  -", step)
