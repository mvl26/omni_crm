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
	"api/media.py": {"check_storage"},  # ADR 0033: nút Kiểm kho ảnh S3 (Omni Admin)
	"api/script.py": {  # ADR 0032 (bot kịch bản v3): MỘT Lead mỗi lần gọi; bot_status kiểm quyền đọc, còn lại POST + quyền ghi Lead
		"bot_status",
		"return_to_bot",
		"mark_docs_sent",
		"mark_quote_sent",
		"set_demo_result",
		"set_no_bot_followup",
		"demo_calendar",  # trang Lịch demo: chỉ đọc, Lead lọc theo quyền đọc; khung đã đặt chỉ có giờ, không lộ khách
	},
	"script/brain.py": {  # ADR 0034 (bộ não cải tiến): chỉ Omni Admin / System Manager; sửa câu kịch bản, không gửi tin
		"approve",
		"reject",
		"approve_phrases",
	},
	"marketing/api.py": {  # V3-10: Marketing Hub, mỗi lời gọi MỘT bài / MỘT kênh
		"hub_state",
		"get_social_channels",
		"check_channel",
		"list_approvers",
		"submit_item",
		"approve_item",
		"cancel_item",
		"ai_budget",  # Prompt 4: AI chỉ soạn / chấm, không đăng; mỗi lời gọi MỘT phiên / MỘT bài / MỘT câu hỏi
		"planner_send",
		"planner_state",
		"planner_create",
		"score_item",
		"kb_overview",
		"kb_reindex",
		"kb_ask",
		"kb_feedback",
		"marketing_report",  # Prompt 6: chỉ đọc
		"export_report",
		"list_campaigns",  # Prompt 3: chỉ đọc, trừ approve_campaign đi qua approval từng bài
		"campaign_detail",
		"item_detail",
		"approve_campaign",
		"get_calendar",
	},
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
