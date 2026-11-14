"""V2-02 mục D + dòng 14.10: kiểm quyền các API trả dữ liệu khách khi có Organization/Contact; không có API gửi hàng loạt."""

import ast
import pathlib
import re

import frappe

import omni_crm
from omni_crm.api import bot_control, inbox
from omni_crm.api import organization as org_api
from omni_crm.tests.test_prospect_model import (
	SALE1,
	SALE2,
	ProspectCase,
	make_award,
	make_lead,
	make_org,
	make_user,
)

ROOT = pathlib.Path(omni_crm.__file__).parent

# Danh sách hàm whitelisted đã duyệt (V2-02). Thêm hàm mới = test đỏ để người duyệt xem có phải đường gửi hàng loạt /
# trả dữ liệu khách không kiểm quyền không.
EXPECTED_WHITELISTED = {
	"api/admin.py": {
		"reprocess",
		"monitor_snapshot",
		"list_manual",
		"sweep_now",
		"zalo_signature_probes",  # V21-05: chỉ đọc, chỉ Omni Admin, không gửi gì
		"refresh_zalo_token",
		"erase_customer_data",
		"erase_contact_data",
	},
	"api/auto.py": {"get_auto_status", "enable_auto", "disable_auto"},
	"api/bot_control.py": {"pause_bot", "resume_bot"},
	"api/drafts.py": {"get_draft_kb_item", "send_draft", "discard_draft", "rate_draft"},
	"api/inbox.py": {"list_conversations", "get_thread", "send_reply", "request_phone", "system_status"},
	"api/prospect.py": {
		"run_import",
		"download_template",
	},  # V2-03: nhập danh sách (một nhật ký/lần), chỉ Omni Admin
	"api/dashboard.py": {"snapshot"},  # V2-04: Tổng quan (chỉ đọc; phạm vi theo quyền xem Lead)
	"api/customer.py": {  # V2-04: Chi tiết khách/nhà thầu; mỗi hàm kiểm quyền theo MỘT Lead, không có đường gửi tin
		"get_header",
		"get_profile",
		"update_field",
		"get_contacts",
		"get_fit",
		"get_side",
		"set_task_done",
		"add_task",
		"get_awards",
		"get_care_logs",
		"save_care_log",
		"get_activity",
		"get_mini_profile",
	},
	"api/organization.py": {
		"get_organization",
		"get_contacts",
		"get_awards",
		"list_similar_hospitals",
		"merge_organizations",
	},
	"api/webhooks.py": {
		"zalo",
		"zalo_oauth_start",
		"zalo_oauth_callback",
		"telephony_event",
		"telephony_lookup",
		"meta_leads",  # V2-05
		"google_leads",
		"web_form",
	},
	"api/zalo_tab.py": {
		"tab_state",
		"get_conversation",
		"send",
		"request_phone",  # V3-07: thẻ xin số cho 1 Lead/lần, chỉ trong 48 giờ, chưa có số
		"send_attachment",  # V3-09: 1 ảnh/tệp của chính Lead tới 1 khách/lần, qua mọi guard của send_zalo
	},  # V3-01: tab Zalo, 1 Lead/1 khách mỗi lần gửi
	"api/messenger_tab.py": {
		"tab_state",
		"get_conversation",
		"send",
		"send_attachment",  # ADR 0023: 1 ảnh/tệp của chính Lead tới 1 khách/lần, qua mọi guard của send_messenger
		"get_templates",  # ADR 0026: chỉ đọc, điền mẫu tin nhắn cho 1 Lead, không gửi
		"send_sticker",  # ADR 0027: 1 sticker tới 1 khách/lần, qua mọi guard của send_messenger
		"react",  # ADR 0027: cảm xúc lên 1 tin của 1 Lead (sender_action, không tạo tin)
		"hide_message",  # ADR 0027: ẩn 1 tin trong CRM (thu hồi 1 phía), không gửi gì
		"sticker_packs",  # ADR 0027: chỉ đọc kho sticker của Meta
		"sticker_pack",
		"sticker_search",
		"send_voice",  # ADR 0027 đợt 2: 1 bản ghi âm (đổi MP3) tới 1 khách/lần, qua mọi guard của send_messenger
		"forward_targets",  # ADR 0027 đợt 2: chỉ đọc, Lead Messenger người dùng được sửa
		"forward",  # ADR 0027 đợt 2: 1 tin Page đã gửi tới MỘT Lead khác/lần, qua mọi guard của Lead nhận
	},  # ADR 0023: tab Messenger, 1 Lead/1 khách mỗi lần gửi
	# ADR 0028: nút Connect Facebook Page, chỉ Omni Admin; không gửi tin, không trả dữ liệu khách hay token
	"api/meta_connect.py": {"start", "callback", "pages", "subscribe"},
	"identity/duplicates.py": {"dismiss", "merge"},
	"api/lead_contact.py": {
		"state",
		"sync_from_lead",
	},  # V3-06: tạo/cập nhật Liên hệ từ 1 Lead, không gửi tin
	# V3-05: trợ lý nội bộ, chỉ thao tác CRM theo quyền người dùng, không gửi tin cho khách
	"assistant/api.py": {"tab_state", "start_session", "chat", "approve", "reject", "close_session"},
}
BULK_NAME = re.compile(r"bulk|broadcast|mass|blast|newsletter|campaign_send|send_all|send_many", re.I)
# Tệp được phép TẠO Omni Outbound Message (mỗi tin gắn với đúng một Lead/khách).
OUTBOUND_CREATORS = {
	"api/inbox.py",
	"channels/zalo/worker_hooks.py",
	"channels/meta_messenger/worker_hooks.py",  # ADR 0022: ghi echo (tin nhân viên đã gửi), một tin một Lead
	"pipeline/outbound.py",
	"channels/zalo/phone_request.py",  # V3-07: thẻ xin số, mỗi lần MỘT định danh (hẹn theo lô tin vừa Done)
}


def whitelisted_functions() -> dict[str, dict[str, list[str]]]:
	found: dict[str, dict[str, list[str]]] = {}
	for path in sorted(ROOT.rglob("*.py")):
		rel = path.relative_to(ROOT).as_posix()
		if rel.startswith("tests/") or "/tests/" in rel:
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef) and any(
				"whitelist" in ast.dump(d) for d in node.decorator_list
			):
				found.setdefault(rel, {})[node.name] = [a.arg for a in node.args.args]
	return found


class TestNoBulkSend(ProspectCase):
	def test_whitelisted_api_matches_reviewed_list(self):
		got = {rel: set(fns) for rel, fns in whitelisted_functions().items()}
		self.assertEqual(got, EXPECTED_WHITELISTED)

	def test_no_bulk_named_function_and_no_list_recipient_param(self):
		for rel, fns in whitelisted_functions().items():
			for name, args in fns.items():
				with self.subTest(f"{rel}:{name}"):
					self.assertIsNone(BULK_NAME.search(name))
					# chiều ra chỉ nhận MỘT lead/draft mỗi lần gọi; không tham số kiểu danh sách người nhận
					for a in args:
						self.assertNotIn(
							a, ("leads", "recipients", "identities", "contacts", "organizations")
						)

	def test_outbound_messages_are_created_only_in_reviewed_files(self):
		creators = set()
		for path in ROOT.rglob("*.py"):
			rel = path.relative_to(ROOT).as_posix()
			if rel.startswith("tests/"):
				continue
			if re.search(
				r'"doctype":\s*(OUTBOUND|"Omni Outbound Message")', path.read_text(encoding="utf-8")
			):
				creators.add(rel)
		self.assertEqual(creators, OUTBOUND_CREATORS)

	def test_send_reply_takes_a_single_lead(self):
		self.assertEqual(
			whitelisted_functions()["api/inbox.py"]["send_reply"][:3], ["lead", "channel", "text"]
		)


class TestLeadScopedApisDenyOthers(ProspectCase):
	"""Mọi API có tham số `lead` phải từ chối người không đọc được Lead, kể cả khi Lead đã gắn Organization/Contact."""

	def setUp(self):
		super().setUp()
		make_user(SALE1)
		make_user(SALE2)
		self.org = make_org("TD213 D1", "0100109140")
		self.lead = make_lead("TD213 DLead", self.org, SALE1)
		make_award(self.org, "BV D")
		frappe.db.set_value("CRM Lead", self.lead, "omni_last_inbound_at", frappe.utils.now_datetime())

	def calls(self):
		return (
			(inbox.get_thread, (self.lead,)),
			(inbox.send_reply, (self.lead, "x", "xin chào")),
			(inbox.request_phone, (self.lead,)),
			(bot_control.pause_bot, (self.lead,)),
			(bot_control.resume_bot, (self.lead,)),
			(org_api.get_organization, (self.lead,)),
			(org_api.get_contacts, (self.lead,)),
			(org_api.get_awards, (self.lead,)),
		)

	def test_other_sales_user_and_guest_are_denied(self):
		for user in (SALE2, "Guest"):
			frappe.set_user(user)
			for fn, args in self.calls():
				with self.subTest(user=user, fn=fn.__name__), self.assertRaises(frappe.PermissionError):
					fn(*args)

	def test_inbox_list_hides_lead_from_other_sales_user(self):
		frappe.set_user(SALE1)
		own = [r["lead"] for r in inbox.list_conversations({}, 1, 50)["rows"]]
		self.assertIn(self.lead, own)  # đối chứng: chủ Lead thấy, nên kiểm tra bên dưới không vô nghĩa
		frappe.set_user(SALE2)
		names = [r["lead"] for r in inbox.list_conversations({}, 1, 50)["rows"]]
		self.assertNotIn(self.lead, names)

	def test_organization_data_does_not_leak_through_inbox_thread_for_owner(self):
		frappe.set_user(SALE1)
		thread = inbox.get_thread(self.lead)
		blob = repr(thread)
		self.assertNotIn(
			"0100109140", blob
		)  # mã số thuế chỉ ra qua API nhà thầu (đã kiểm quyền), không ngầm qua hộp thư
