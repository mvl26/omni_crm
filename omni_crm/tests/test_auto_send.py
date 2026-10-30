# ruff: noqa: RUF001
"""D3-05: bot tự gửi theo nhóm câu hỏi. Mọi lời gọi AI và gửi đều giả; sandbox_mode luôn bật trong test, không HTTP.

Khóa hạ tầng `omni_auto_send_allowed` KHÔNG có trên site test; test chỉ bật nó trong phạm vi một test bằng
`mock.patch.dict(frappe.conf, …)` (tự khôi phục), riêng test đa luồng đặt thẳng vào `frappe.local.conf` của luồng đó.
"""

import threading
import time
from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from omni_crm import flags
from omni_crm.api import auto, drafts, inbox
from omni_crm.bot import analyze, auto_send, handoff, reply
from omni_crm.bot.fake_client import Rule
from omni_crm.channels.zalo.adapter import ZaloAdapter
from omni_crm.ingest.events import OutboundRequest
from omni_crm.omni_crm.doctype.omni_question_group.omni_question_group import enabling
from omni_crm.omni_crm.report.bot_tự_gửi import bot_tự_gửi as report
from omni_crm.pipeline import outbound
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_analyze import signals
from omni_crm.tests.test_bot_reply import DRAFT, G_HOURS, G_PRICE, GROUP, reply_ok
from omni_crm.tests.test_draft import DraftTestCase
from omni_crm.tests.test_inbox import SALE1, SALE2

OUT = "Omni Outbound Message"
TEXT = "Dạ Miyano làm việc từ 8 giờ đến 17 giờ 30 ạ."
INFRA = {flags.AUTO_SEND_CONF: 1}
ADMIN_USER = "td305-admin@example.com"
MANAGER_USER = "td305-manager@example.com"
KE_USER = "td305-editor@example.com"


class AutoTestCase(DraftTestCase):
	def setUp(self):
		super().setUp()
		self._orig_bot_hours = frappe.db.get_single_value("Omni Settings", "bot_active_hours")
		self.addCleanup(self._clean_d305)
		for p in (
			mock.patch.dict(frappe.conf, INFRA),
			mock.patch.object(handoff, "bot_active_now", return_value=True),
			mock.patch("frappe.enqueue"),
		):
			p.start()
			self.addCleanup(p.stop)
		self.make_staff()

	def _clean_d305(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		groups = frappe.get_all(GROUP, filters={"name": ["like", "T07 %"]}, pluck="name")
		for dt, field in (("Comment", "reference_name"), ("Notification Log", "document_name")):
			frappe.db.delete(dt, {field: ["in", groups or [""]]})
		frappe.db.delete("Version", {"ref_doctype": GROUP, "docname": ["in", groups or [""]]})
		frappe.db.delete(OUT, {"lead": ["in", [self.lead1, self.lead2]]})
		for u in (ADMIN_USER, MANAGER_USER, KE_USER):
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def make_staff(self):
		for email, role in (
			(ADMIN_USER, "Omni Admin"),
			(MANAGER_USER, "Sales Manager"),
			(KE_USER, "Omni Knowledge Editor"),
		):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": "TD305",
						"send_welcome_email": 0,
						"roles": [{"role": role}],
					}
				).insert(ignore_permissions=True)
		frappe.db.commit()

	# --- dựng dữ liệu ---------------------------------------------------------------------------------------

	def enable_raw(self, group=G_HOURS):
		"""Bật nhóm như `enable_group` đã làm xong (bỏ qua điều kiện), để thử đường gửi."""
		doc = frappe.get_doc(GROUP, group)
		with enabling():
			doc.auto_send_enabled = 1
			doc.auto_send_enabled_at = now_datetime()
			doc.auto_send_enabled_by = "Administrator"
			doc.save(ignore_permissions=True)
		frappe.db.commit()

	def group_on(self, group=G_HOURS):
		return bool(frappe.db.get_value(GROUP, group, "auto_send_enabled"))

	def bot_outs(self, lead=None):
		return frappe.get_all(
			OUT, filters={"lead": lead or self.lead1, "source": "Bot"}, fields=["*"], order_by="creation asc"
		)

	def run_batch(self, text="Cho hỏi giờ làm việc", result=None, analysis=None, minutes_ago=2):
		"""Tin khách -> phân tích (giả) -> bot soạn. Trả (batch, trạng thái reply_batch)."""
		batch = self.say(text, minutes_ago=minutes_ago)
		if analysis is not False:
			self.analyze_as(batch, **(analysis or {}))
		self.fake.rules.insert(
			0, Rule(result or reply_ok(reply_text=TEXT), kind="Reply")
		)  # luật mới thắng luật cũ
		return batch, reply.reply_batch(batch)

	def analyze_as(self, batch, **sig):
		"""Phân tích giả của lượt `batch` (luật mới đặt lên đầu để thắng luật của lượt trước)."""
		self.fake.rules.insert(0, Rule(signals(**sig), kind="Analyze"))
		analyze.analyze_batch(batch)

	def draft_of(self, batch):
		return self.drafts(batch_id=batch)[0]

	def pending_draft(self, **kw):
		"""Nháp Draft/Reply đang chờ duyệt (nhóm CHƯA bật lúc tạo)."""
		batch, status = self.run_batch(**kw)
		self.assertEqual(status, "drafted")
		d = self.draft_of(batch)
		self.assertEqual((d.mode, d.decision), ("Draft", "Pending"))
		return d.name

	def mk(self, group=G_HOURS, decision="Sent As Is", ratio=0.0, days_ago=1.0, created=None, **kw):
		"""Chèn một nháp đã quyết định (cho eligibility)."""
		decided = add_to_date(now_datetime(), days=-days_ago)
		values = {
			"doctype": DRAFT,
			"lead": self.lead1,
			"identity": self.ident1,
			"channel": self.channel.name,
			"batch_id": f"T05-mk{frappe.generate_hash(length=8)}",
			"mode": "Draft",
			"action": "Reply",
			"decision": decision,
			"question_group": group,
			"draft_text": "x",
			"edit_distance_ratio": ratio,
			"decided_at": decided if decision in auto_send.DECIDED else None,
			**kw,
		}
		doc = frappe.get_doc(values).insert(ignore_permissions=True)
		frappe.db.set_value(DRAFT, doc.name, "creation", created or decided, update_modified=False)
		return doc.name

	def mk_many(self, n, **kw):
		for _i in range(n):
			self.mk(**kw)
		frappe.db.commit()


class TestInfraAndFields(AutoTestCase):
	def test_site_has_no_infra_flag_and_default_is_closed(self):
		with mock.patch.dict(frappe.conf, {}, clear=False):
			frappe.conf.pop(flags.AUTO_SEND_CONF, None)
			self.assertFalse(flags.auto_send_infra_allowed())
			self.assertFalse(flags.group_auto_send(G_HOURS))
			for bad in (0, "0", "", None, "false"):
				frappe.conf[flags.AUTO_SEND_CONF] = bad
				self.assertFalse(flags.auto_send_infra_allowed(), bad)
		site_conf = frappe.get_site_config()
		self.assertNotIn(flags.AUTO_SEND_CONF, site_conf)  # không đặt trên omni.test ngoài test

	def test_group_defaults_off_and_cannot_be_ticked_by_hand(self):
		self.assertFalse(self.group_on())
		doc = frappe.get_doc(GROUP, G_HOURS)
		doc.auto_send_enabled = 1
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": GROUP, "group_name": "T07 Moi", "auto_send_enabled": 1}).insert(
				ignore_permissions=True
			)
		self.assertFalse(self.group_on())

	def test_always_handoff_group_cannot_hold_the_flag(self):
		doc = frappe.get_doc(GROUP, G_PRICE)
		with enabling():
			doc.auto_send_enabled = 1
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)
		# bật nhóm thường rồi chuyển nó thành luôn chuyển người: tự tắt
		self.enable_raw()
		doc = frappe.get_doc(GROUP, G_HOURS)
		doc.always_handoff = 1
		doc.save(ignore_permissions=True)
		self.assertFalse(self.group_on())
		self.assertFalse(flags.group_auto_send(G_HOURS))

	def test_anyone_with_write_can_turn_off_and_trail_cleared(self):
		self.enable_raw()
		doc = frappe.get_doc(GROUP, G_HOURS)
		doc.auto_send_enabled = 0
		doc.save(ignore_permissions=True)
		row = frappe.db.get_value(GROUP, G_HOURS, ["auto_send_enabled", "auto_send_enabled_at"], as_dict=True)
		self.assertEqual((row.auto_send_enabled, row.auto_send_enabled_at), (0, None))


class TestEligibility(AutoTestCase):
	def test_under_min_sample_not_eligible(self):
		self.mk_many(29)
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["decided"], el["good"], el["eligible"]), (29, 29, False))
		self.assertIn("29", " ".join(el["reasons"]))
		self.mk_many(1)
		self.assertTrue(auto_send.eligibility(G_HOURS)["eligible"])

	def test_rate_threshold_95_percent_exact(self):
		self.mk_many(29)
		self.mk(decision="Discarded")  # 29/30 = 96.7%
		frappe.db.commit()
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["decided"], el["good"], el["rate"], el["eligible"]), (30, 29, 96.7, True))
		self.mk(decision="Discarded")
		frappe.db.commit()
		el = auto_send.eligibility(G_HOURS)  # 29/31 = 93.5%
		self.assertFalse(el["eligible"])
		self.assertIn("93.5", " ".join(el["reasons"]))

	def test_edit_ratio_boundary_and_discard_not_numerator(self):
		self.mk_many(28)
		self.mk(decision="Sent Edited", ratio=0.1)  # đúng ngưỡng: tính
		self.mk(decision="Sent Edited", ratio=0.2)  # sửa nhiều: vào mẫu số, không vào tử số
		frappe.db.commit()
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["decided"], el["good"]), (30, 29))
		# nháp bỏ có edit_distance_ratio = 0 (mặc định cột) không được tính vào tử số
		self.mk(decision="Discarded", ratio=0.0)
		frappe.db.commit()
		self.assertEqual(auto_send.eligibility(G_HOURS)["good"], 29)

	def test_pending_expired_and_auto_excluded_from_sample(self):
		self.mk_many(30)
		self.mk_many(10, decision="Expired", decided_at=None)
		self.mk_many(5, decision="Pending", decided_at=None)
		self.mk_many(20, mode="Auto")  # Auto không tự nâng tỉ lệ
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["decided"], el["good"], el["pending"], el["expired"]), (30, 30, 5, 10))
		self.assertTrue(el["eligible"])

	def test_handoff_drafts_that_were_decided_count_in_denominator(self):
		self.mk_many(30)
		self.mk(decision="Discarded", action="Handoff")
		frappe.db.commit()
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["decided"], el["good"]), (31, 30))

	def test_critical_and_serious_wrong_block_even_with_good_rate(self):
		self.mk_many(60)
		self.assertTrue(auto_send.eligibility(G_HOURS)["eligible"])
		cname = self.mk(critical=1)
		frappe.db.commit()
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["critical"], el["eligible"]), (1, False))
		frappe.db.set_value(DRAFT, cname, "critical", 0)
		self.mk(rating="Sai")  # Sai không ghi chú: không "nghiêm trọng"
		frappe.db.commit()
		self.assertTrue(auto_send.eligibility(G_HOURS)["eligible"])
		self.mk(rating="Sai", rating_note="Báo sai giờ, khách phàn nàn")
		frappe.db.commit()
		el = auto_send.eligibility(G_HOURS)
		self.assertEqual((el["serious_wrong"], el["eligible"]), (1, False))

	def test_critical_on_auto_draft_also_blocks(self):
		self.mk_many(30)
		self.mk(mode="Auto", critical=1)
		frappe.db.commit()
		self.assertFalse(auto_send.eligibility(G_HOURS)["eligible"])

	def test_window_is_14_days_with_injected_time(self):
		self.mk_many(30, days_ago=13.9)
		now = now_datetime()
		self.assertTrue(auto_send.eligibility(G_HOURS, now)["eligible"])
		self.assertEqual(
			auto_send.eligibility(G_HOURS, now + timedelta(hours=3))["decided"], 0
		)  # 13,9 ngày + 3 giờ > 14
		self.assertEqual(auto_send.eligibility(G_HOURS, now + timedelta(days=2))["decided"], 0)
		# cửa sổ chỉ tính tới as_of: lùi về trước các quyết định thì chưa có quyết định nào
		self.assertEqual(auto_send.eligibility(G_HOURS, now - timedelta(days=14))["decided"], 0)

	def test_always_handoff_never_eligible(self):
		self.mk_many(40, group=G_PRICE)
		el = auto_send.eligibility(G_PRICE)
		self.assertFalse(el["eligible"])
		self.assertTrue(any("luôn chuyển người" in r for r in el["reasons"]))


class TestEnableApi(AutoTestCase):
	def test_not_eligible_cannot_enable(self):
		with self.assertRaises(frappe.ValidationError):
			auto.enable_auto(G_HOURS, confirm=1)
		self.assertFalse(self.group_on())

	def test_only_omni_admin_can_enable_or_disable(self):
		self.mk_many(30)
		for user in (SALE1, KE_USER, MANAGER_USER):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				auto.enable_auto(G_HOURS, confirm=1)
			with self.assertRaises(frappe.PermissionError):
				auto.disable_auto(G_HOURS)
			with self.assertRaises(frappe.PermissionError):
				auto.get_auto_status(G_HOURS)
		frappe.set_user("Administrator")
		self.assertFalse(self.group_on())

	def test_eligible_needs_confirm_then_enables_and_records_trail(self):
		self.mk_many(30)
		frappe.set_user(ADMIN_USER)
		res = auto.enable_auto(G_HOURS)
		self.assertEqual(res["status"], "confirm_required")
		self.assertEqual((res["eligibility"]["decided"], res["eligibility"]["good"]), (30, 30))
		self.assertFalse(self.group_on())
		res = auto.enable_auto(G_HOURS, confirm=1)
		frappe.db.commit()
		frappe.set_user("Administrator")
		self.assertEqual(res["status"], "enabled")
		row = frappe.db.get_value(
			GROUP,
			G_HOURS,
			["auto_send_enabled", "auto_send_enabled_by", "auto_send_enabled_at"],
			as_dict=True,
		)
		self.assertEqual((row.auto_send_enabled, row.auto_send_enabled_by), (1, ADMIN_USER))
		self.assertTrue(row.auto_send_enabled_at)
		note = frappe.get_all(
			"Comment", filters={"reference_name": G_HOURS, "content": ["like", f"%{auto_send.ENABLE_MARK}%"]}
		)
		self.assertEqual(len(note), 1)
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": GROUP, "docname": G_HOURS}))
		self.assertEqual(auto.enable_auto(G_HOURS, confirm=1)["status"], "already")

	def test_infra_lock_blocks_enable_even_with_force_and_eligible(self):
		self.mk_many(30)
		frappe.conf.pop(flags.AUTO_SEND_CONF, None)
		for kw in ({}, {"force": 1, "reason": "chủ dự án đã đồng ý bằng văn bản"}):
			with self.assertRaises(frappe.ValidationError):
				auto.enable_auto(G_HOURS, confirm=1, **kw)
		self.assertFalse(self.group_on())

	def test_force_needs_reason_confirm_and_comments_it(self):
		with self.assertRaises(frappe.ValidationError):
			auto.enable_auto(G_HOURS, confirm=1, force=1)
		with self.assertRaises(frappe.ValidationError):
			auto.enable_auto(G_HOURS, confirm=1, force=1, reason="ngắn")
		self.assertEqual(
			auto.enable_auto(G_HOURS, force=1, reason="Chủ dự án duyệt ngoại lệ")["status"],
			"confirm_required",
		)
		self.assertFalse(self.group_on())
		res = auto.enable_auto(G_HOURS, confirm=1, force=1, reason="Chủ dự án duyệt ngoại lệ")
		self.assertTrue(res["forced"])
		self.assertTrue(self.group_on())
		(c,) = frappe.get_all(
			"Comment",
			filters={"reference_name": G_HOURS, "content": ["like", f"%{auto_send.ENABLE_MARK}%"]},
			pluck="content",
		)
		self.assertIn("Chủ dự án duyệt ngoại lệ", c)
		self.assertIn("CHƯA ĐỦ ĐIỀU KIỆN", c)

	def test_always_handoff_never_enabled_even_with_force(self):
		self.mk_many(60, group=G_PRICE)
		for kw in ({}, {"force": 1, "reason": "ép bật nhóm hỏi giá thử xem"}):
			with self.assertRaises(frappe.ValidationError):
				auto.enable_auto(G_PRICE, confirm=1, **kw)
		self.assertFalse(self.group_on(G_PRICE))

	def test_disable_by_admin(self):
		self.enable_raw()
		frappe.set_user(ADMIN_USER)
		self.assertEqual(auto.disable_auto(G_HOURS, "kiểm tra")["status"], "disabled")
		self.assertEqual(auto.disable_auto(G_HOURS)["status"], "already_off")
		frappe.set_user("Administrator")
		self.assertFalse(self.group_on())


class TestAutoSend(AutoTestCase):
	def test_group_off_stays_draft(self):
		name = self.pending_draft()
		self.assertEqual(self.bot_outs(), [])
		self.assertTrue(drafts.thread_drafts(self.lead1))
		self.assertEqual(self.d(name).decision, "Pending")

	def test_eligible_draft_is_sent_as_bot_in_sandbox_without_http(self):
		self.enable_raw()
		batch, status = self.run_batch()
		self.assertEqual(status, "drafted")
		d = self.draft_of(batch)
		self.assertEqual((d.mode, d.decision, d.action, d.final_text), ("Auto", "Sent As Is", "Reply", TEXT))
		(o,) = self.bot_outs()
		self.assertEqual(
			(o.source, o.status, o.is_sandbox, o.bot_draft, o.text), ("Bot", "Queued", 1, d.name, TEXT)
		)
		self.assertEqual(drafts.thread_drafts(self.lead1), [])  # không còn chờ sale duyệt
		with (
			mock.patch("omni_crm.channels.zalo.client.requests.request") as http,
			mock.patch.object(ZaloAdapter, "send") as send,
		):
			self.assertEqual(outbound.send_outbound(o.name), "Sent")
		http.assert_not_called()
		send.assert_not_called()
		self.assertEqual(frappe.db.get_value(OUT, o.name, "is_sandbox"), 1)

	def test_no_notification_for_auto_sent_draft(self):
		self.enable_raw()
		before = frappe.db.count("Notification Log", {"document_name": self.lead1})
		self.run_batch()
		self.assertEqual(frappe.db.count("Notification Log", {"document_name": self.lead1}), before)

	def test_infra_closed_falls_back_to_draft(self):
		self.enable_raw()
		frappe.conf.pop(flags.AUTO_SEND_CONF, None)
		batch, _s = self.run_batch()
		d = self.draft_of(batch)
		self.assertEqual((d.mode, d.decision), ("Draft", "Pending"))
		self.assertEqual(self.bot_outs(), [])

	def test_outside_free_window_falls_back_to_draft(self):
		self.enable_raw()
		self.set_last_inbound(hours_ago=72)
		batch, _s = self.run_batch()
		d = self.draft_of(batch)
		self.assertEqual((d.mode, d.decision), ("Draft", "Pending"))
		self.assertIn("auto_khong_gui:khung_gui", d.guard_flags)
		self.assertEqual(self.bot_outs(), [])

	def test_outside_bot_hours_falls_back_to_draft(self):
		self.enable_raw()
		with mock.patch.object(handoff, "bot_active_now", return_value=False):
			batch, _s = self.run_batch()
		d = self.draft_of(batch)
		self.assertEqual((d.mode, d.decision), ("Draft", "Pending"))
		self.assertIn("auto_khong_gui:ngoai_gio_bot", d.guard_flags)

	def test_global_mode_must_be_draft(self):
		name = self.pending_draft()
		self.enable_raw()
		set_settings(bot_reply_mode="Shadow")
		self.assertFalse(auto_send.try_auto_send(name))
		self.assertEqual(self.bot_outs(), [])

	def test_no_analysis_or_complaint_or_wants_human_falls_back(self):
		self.enable_raw()
		for analysis, text in (
			(False, "hỏi giờ 1"),  # không có phân tích
			({"intent": "Khiếu nại"}, "hỏi giờ 2"),
			({"wants_human": True}, "hỏi giờ 3"),
		):
			frappe.db.set_value(
				"Omni Identity", self.ident1, "bot_paused_until", None
			)  # Handoff trước đó đã tạm dừng bot
			batch, _s = self.run_batch(text=text, analysis=analysis)
			self.assertNotEqual(self.draft_of(batch).mode, "Auto", analysis)
		self.assertEqual(self.bot_outs(), [])

	def test_analyze_switch_off_falls_back(self):
		name = self.pending_draft()
		self.enable_raw()
		set_settings(bot_analyze_enabled=0)
		self.assertFalse(auto_send.try_auto_send(name))
		self.assertEqual(self.bot_outs(), [])

	def test_paused_identity_sale_reply_and_identity_mismatch_block(self):
		name = self.pending_draft()
		self.enable_raw()
		frappe.db.set_value(
			"Omni Identity", self.ident1, "bot_paused_until", add_to_date(now_datetime(), hours=2)
		)
		self.assertFalse(auto_send.try_auto_send(name))
		frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
		# sale đã trả lời sau tin khách
		self.make_outbound(self.lead1, "Em chào anh, em xử lý ạ", 0)
		self.assertFalse(auto_send.try_auto_send(name))
		frappe.db.delete(OUT, {"lead": self.lead1})
		# định danh của nháp khác định danh của tin khách cuối
		frappe.db.set_value(DRAFT, name, "identity", self.ident2)
		self.assertFalse(auto_send.try_auto_send(name))
		self.assertEqual(self.bot_outs(), [])
		self.assertEqual(self.d(name).decision, "Pending")

	def test_outbound_disabled_channel_and_frequency_cap(self):
		name = self.pending_draft()
		self.enable_raw()
		frappe.db.set_value("Omni Channel", self.channel.name, "outbound_enabled", 0)
		self.assertFalse(auto_send.try_auto_send(name))
		frappe.db.set_value("Omni Channel", self.channel.name, "outbound_enabled", 1)
		for i in range(auto_send.MAX_AUTO_PER_IDENTITY_DAY):
			frappe.get_doc(
				{
					"doctype": OUT,
					"channel": self.channel.name,
					"identity": self.ident1,
					"lead": self.lead1,
					"text": f"cũ {i}",
					"source": "Bot",
					"status": "Sent",
				}
			).insert(ignore_permissions=True)
		self.assertFalse(auto_send.try_auto_send(name))
		self.assertEqual(self.d(name).decision, "Pending")

	def test_price_in_answer_guard_blocks_and_gives_handoff_draft(self):
		self.enable_raw()
		batch, _s = self.run_batch(result=reply_ok(reply_text="Dạ giá 500.000đ một hộp ạ"))
		d = self.draft_of(batch)
		self.assertEqual((d.action, d.mode), ("Handoff", "Draft"))
		self.assertEqual(self.bot_outs(), [])

	def test_handoff_in_enabled_group_sends_one_holding_message_as_system(self):
		self.enable_raw()
		batch, _s = self.run_batch(
			result=reply_ok(action="handoff", kb_item_ids=[], handoff_reason="Khách cần người")
		)
		d = self.draft_of(batch)
		self.assertEqual((d.action, d.mode, d.decision), ("Handoff", "Draft", "Pending"))
		outs = frappe.get_all(
			OUT, filters={"lead": self.lead1}, fields=["source", "text", "is_sandbox", "bot_draft"]
		)
		self.assertEqual(len(outs), 1)
		self.assertEqual((outs[0].source, outs[0].bot_draft, outs[0].is_sandbox), ("System", d.name, 1))
		self.assertEqual(outs[0].text, handoff.holding_message())
		self.assertIn(auto_send.HOLDING_MARK, d.guard_flags)
		self.assertTrue(handoff.is_bot_paused(self.ident1))  # vẫn chuyển người và tạm dừng bot
		self.assertFalse(auto_send.try_holding(d.name))  # chỉ một lần cho mỗi nháp
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1}), 1)

	def test_handoff_without_enabled_group_or_for_always_handoff_sends_nothing(self):
		batch, _s = self.run_batch(result=reply_ok(action="handoff", kb_item_ids=[], handoff_reason="x"))
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1}), 0)  # nhóm chưa bật
		self.enable_raw()
		frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
		batch, _s = self.run_batch(
			text="Cho hỏi giá",
			result=reply_ok(action="handoff", question_group=G_PRICE, kb_item_ids=[], handoff_reason="giá"),
		)
		self.assertEqual(frappe.db.count(OUT, {"lead": self.lead1}), 0)  # nhóm luôn chuyển người

	def test_failure_inside_send_rolls_back_to_plain_draft(self):
		self.enable_raw()
		with mock.patch.object(outbound, "queue_outbound", side_effect=RuntimeError("boom")):
			batch, _s = self.run_batch()
		d = self.draft_of(batch)
		self.assertEqual((d.mode, d.decision), ("Draft", "Pending"))
		self.assertEqual(self.bot_outs(), [])
		self.assertTrue(drafts.thread_drafts(self.lead1))  # vẫn hiện cho sale duyệt


class TestBotSourceGate(AutoTestCase):
	def req(self, name, source="Bot", text=TEXT):
		return OutboundRequest(self.channel.name, self.ident1, text, None, source, bot_draft=name)

	def test_bot_source_rejected_without_draft_or_outside_auto_path(self):
		name = self.pending_draft()
		self.enable_raw()
		with self.assertRaises(frappe.ValidationError):
			outbound.queue_outbound(OutboundRequest(self.channel.name, self.ident1, TEXT, None, "Bot"))
		# nháp hoàn toàn hợp lệ nhưng không đi qua auto_send (không có dấu đang gửi) vẫn bị từ chối
		with self.assertRaises(frappe.ValidationError):
			outbound.queue_outbound(self.req(name))
		# dấu đang gửi của nháp khác không dùng được cho nháp này
		with auto_send._sending("NHAP-KHAC"), self.assertRaises(frappe.ValidationError):
			outbound.queue_outbound(self.req(name))
		self.assertEqual(self.bot_outs(), [])

	def test_bot_source_rejected_for_wrong_draft_states(self):
		name = self.pending_draft()
		self.enable_raw()
		with auto_send._sending(name):
			for field, value in (("decision", "Discarded"), ("action", "Handoff"), ("mode", "Shadow")):
				old = frappe.db.get_value(DRAFT, name, field)
				frappe.db.set_value(DRAFT, name, field, value)
				with self.assertRaises(frappe.ValidationError):
					outbound.queue_outbound(self.req(name))
				frappe.db.set_value(DRAFT, name, field, old)
			with self.assertRaises(frappe.ValidationError):  # văn bản khác nháp
				outbound.queue_outbound(self.req(name, text="Dạ giá 1 đồng ạ"))
			frappe.conf.pop(flags.AUTO_SEND_CONF, None)
			with self.assertRaises(frappe.ValidationError):  # khóa hạ tầng đóng
				outbound.queue_outbound(self.req(name))
		self.assertEqual(self.bot_outs(), [])

	def test_system_and_sale_sources_unchanged(self):
		name = self.pending_draft()
		out = outbound.queue_outbound(
			OutboundRequest(self.channel.name, self.ident1, "Dạ chào ạ", None, "System")
		)
		self.assertEqual(frappe.db.get_value(OUT, out, "source"), "System")
		with self.assertRaises(frappe.ValidationError):  # System không được kèm nháp
			outbound.queue_outbound(self.req(name, source="System"))


class TestSendTimeRecheck(AutoTestCase):
	def queued(self):
		self.enable_raw()
		self.run_batch()
		(o,) = self.bot_outs()
		return o.name

	def send_blocked(self, name):
		with mock.patch.object(ZaloAdapter, "send") as send:
			self.assertEqual(outbound.send_outbound(name), "Blocked")
		send.assert_not_called()

	def test_group_disabled_before_send_blocks(self):
		name = self.queued()
		frappe.set_user(ADMIN_USER)
		auto.disable_auto(G_HOURS)
		frappe.db.commit()
		frappe.set_user("Administrator")
		self.send_blocked(name)
		self.assertIn("tắt tự động", frappe.db.get_value(OUT, name, "last_error"))

	def test_infra_closed_paused_or_outside_hours_before_send_blocks(self):
		for how in ("infra", "paused", "hours"):
			frappe.db.delete(OUT, {"lead": self.lead1})
			frappe.db.delete(DRAFT, {"lead": self.lead1})
			frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
			frappe.db.set_value(GROUP, G_HOURS, "auto_send_enabled", 0)
			name = self.queued()
			if how == "infra":
				frappe.conf.pop(flags.AUTO_SEND_CONF, None)
			elif how == "paused":
				frappe.db.set_value(
					"Omni Identity", self.ident1, "bot_paused_until", add_to_date(now_datetime(), hours=1)
				)
			with mock.patch.object(handoff, "bot_active_now", return_value=how != "hours"):
				self.send_blocked(name)
			frappe.conf[flags.AUTO_SEND_CONF] = 1

	def test_bot_message_blocked_at_send_time_returns_draft_to_sale(self):
		"""Tin Bot bị chặn lúc gửi không được làm khách bị bỏ rơi trong im lặng: nháp về Draft/Pending và có báo."""
		name = self.queued()
		before = frappe.db.count("Notification Log", {"document_name": self.lead1})
		frappe.db.set_value(GROUP, G_HOURS, "auto_send_enabled", 0)  # nhóm vừa bị tắt (vd ngắt mạch)
		frappe.db.commit()
		self.send_blocked(name)
		(d,) = self.drafts()
		self.assertEqual((d.mode, d.decision, d.final_text, d.decided_at), ("Draft", "Pending", None, None))
		self.assertEqual(len(drafts.thread_drafts(self.lead1)), 1)  # sale thấy nháp để tự gửi
		self.assertGreater(frappe.db.count("Notification Log", {"document_name": self.lead1}), before)
		# nháp đã về Draft không thể bị tự gửi lại khi nhóm tắt, và Outbound cũ không bị gửi lần nữa
		self.assertFalse(auto_send.try_auto_send(d.name))
		self.assertEqual(frappe.db.get_value(OUT, name, "status"), "Blocked")

	def test_bot_message_blocked_by_paid_window_returns_draft(self):
		name = self.queued()
		self.set_last_inbound(hours_ago=72)  # trong lúc chờ khung chuyển sang có phí
		self.send_blocked(name)
		(d,) = self.drafts()
		self.assertEqual((d.mode, d.decision), ("Draft", "Pending"))


class TestHoldingMessageHardening(AutoTestCase):
	def holding(self):
		self.enable_raw()
		batch, _s = self.run_batch(result=reply_ok(action="handoff", kb_item_ids=[], handoff_reason="x"))
		(o,) = frappe.get_all(OUT, filters={"lead": self.lead1}, fields=["name", "source", "bot_draft"])
		return self.draft_of(batch), o

	def test_holding_blocked_at_send_when_infra_or_group_closed(self):
		for how in ("infra", "group"):
			frappe.db.delete(OUT, {"lead": self.lead1})
			frappe.db.delete(DRAFT, {"lead": self.lead1})
			frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
			frappe.db.set_value(GROUP, G_HOURS, "auto_send_enabled", 0)
			_d, o = self.holding()
			if how == "infra":
				frappe.conf.pop(flags.AUTO_SEND_CONF, None)
			else:
				frappe.db.set_value(GROUP, G_HOURS, "auto_send_enabled", 0)
			with mock.patch.object(ZaloAdapter, "send") as send:
				self.assertEqual(outbound.send_outbound(o.name), "Blocked")
			send.assert_not_called()
			frappe.conf[flags.AUTO_SEND_CONF] = 1

	def test_holding_link_cannot_be_forged_from_outside(self):
		d, _o = self.holding()
		req = OutboundRequest(
			self.channel.name, self.ident1, handoff.holding_message(), None, "System", bot_draft=d.name
		)
		with self.assertRaises(frappe.ValidationError):
			outbound.queue_outbound(req)

	def test_holding_message_rate_limited_per_identity(self):
		d, _o = self.holding()
		frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)
		batch, _s = self.run_batch(
			text="lại hỏi", result=reply_ok(action="handoff", kb_item_ids=[], handoff_reason="y")
		)
		self.assertEqual(
			frappe.db.count(OUT, {"lead": self.lead1}), 1
		)  # nháp Handoff thứ hai không gửi thêm câu giữ chân
		# quá N giờ thì được gửi lại
		frappe.db.set_value(
			OUT, _o["name"], "creation", add_to_date(now_datetime(), hours=-auto_send.HOLDING_MIN_HOURS - 1)
		)
		frappe.db.set_value("Omni Identity", self.ident1, "bot_paused_until", None)  # draft 2 đã tạm dừng bot
		self.assertTrue(auto_send.try_holding(self.draft_of(batch).name))


class TestEmergencySwitchFallback(AutoTestCase):
	def test_trip_still_disables_when_save_fails(self):
		from omni_crm.omni_crm.doctype.omni_question_group.omni_question_group import OmniQuestionGroup

		self.enable_raw()
		with (
			mock.patch.object(OmniQuestionGroup, "save", side_effect=RuntimeError("hỏng")),
			mock.patch("frappe.log_error") as log,
		):
			self.assertTrue(auto_send.trip(G_HOURS, "complaint"))
		self.assertFalse(self.group_on())
		self.assertTrue(log.called)

	def test_rate_wrong_still_disables_when_save_fails(self):
		from omni_crm.omni_crm.doctype.omni_question_group.omni_question_group import OmniQuestionGroup

		self.enable_raw()
		batch, _s = self.run_batch()
		name = self.draft_of(batch).name
		with mock.patch.object(OmniQuestionGroup, "save", side_effect=RuntimeError("hỏng")):
			self.call(drafts.rate_draft, name, "Sai")
		self.assertFalse(self.group_on())


class TestBotFailureNotice(AutoTestCase):
	def test_failed_bot_message_notifies_owner(self):
		self.enable_raw()
		self.run_batch()
		(o,) = self.bot_outs()
		before = frappe.db.count("Notification Log", {"document_name": self.lead1})
		with (
			mock.patch.object(ZaloAdapter, "send", side_effect=ValueError("loi")),
			mock.patch("omni_crm.flags.sandbox", return_value=False),
		):
			self.assertEqual(outbound.send_outbound(o.name), "Failed")
		self.assertGreater(frappe.db.count("Notification Log", {"document_name": self.lead1}), before)


class TestBreakers(AutoTestCase):
	def auto_sent(self, text="Cho hỏi giờ làm việc"):
		self.enable_raw()
		batch, _s = self.run_batch(text=text)
		d = self.draft_of(batch)
		self.assertEqual(d.mode, "Auto")
		return d.name

	def assert_tripped(self, code, notified=True):
		self.assertFalse(self.group_on())
		self.assertEqual(auto_send.auto_off_count(G_HOURS), 1)
		(c,) = frappe.get_all(
			"Comment",
			filters={"reference_name": G_HOURS, "content": ["like", f"%{auto_send.BREAKER_MARK}:{code}%"]},
			pluck="content",
		)
		self.assertIn("TỰ TẮT", c)
		if notified:
			users = frappe.get_all("Notification Log", filters={"document_name": G_HOURS}, pluck="for_user")
			self.assertIn(ADMIN_USER, users)
			self.assertIn(MANAGER_USER, users)

	def test_rating_wrong_on_bot_message_turns_group_off_and_alerts(self):
		name = self.auto_sent()
		res = self.call(drafts.rate_draft, name, "Sai", "Báo giờ nhầm")
		self.assertTrue(res["auto_disabled"])
		self.assert_tripped("rated_wrong")

	def test_rating_ok_missing_or_wrong_on_plain_draft_does_not_trip(self):
		plain = self.pending_draft(text="hỏi giờ khác")  # nhóm chưa bật: nháp Draft thường, sale gửi
		self.call(drafts.send_draft, plain)
		name = self.auto_sent("hỏi giờ nữa")
		for rating in ("Đúng", "Thiếu"):
			self.assertFalse(self.call(drafts.rate_draft, name, rating)["auto_disabled"])
		self.assertTrue(self.group_on())
		# nháp Draft thường (sale gửi) chấm Sai: không phải tin bot đã gửi, không ngắt mạch
		self.assertFalse(self.call(drafts.rate_draft, plain, "Sai")["auto_disabled"])
		self.assertTrue(self.group_on())

	def test_critical_flag_on_bot_message_trips(self):
		name = self.auto_sent()
		self.call(drafts.rate_draft, name, "Thiếu", None, 1)
		self.assert_tripped("rated_wrong")

	def test_complaint_within_24h_trips_the_groups_of_earlier_bot_message(self):
		name = self.auto_sent()
		(o,) = self.bot_outs()
		sent = add_to_date(now_datetime(), hours=-23, minutes=-30)
		frappe.db.set_value(OUT, o.name, {"sent_at": sent, "creation": sent})
		frappe.db.commit()
		batch = self.say("Hàng bị lỗi, tôi rất bực", minutes_ago=1)
		self.analyze_as(batch, intent="Khiếu nại")
		self.assert_tripped("complaint")
		self.assertEqual(self.d(name).mode, "Auto")

	def test_complaint_after_24h_or_before_bot_message_does_not_trip(self):
		self.auto_sent()
		(o,) = self.bot_outs()
		old = add_to_date(now_datetime(), hours=-24, minutes=-30)
		frappe.db.set_value(OUT, o.name, {"sent_at": old, "creation": old})
		frappe.db.commit()
		batch = self.say("Hàng bị lỗi", minutes_ago=1)
		self.analyze_as(batch, intent="Khiếu nại")
		self.assertTrue(self.group_on())
		# tin khiếu nại TRƯỚC tin bot
		future = add_to_date(now_datetime(), hours=1)
		frappe.db.set_value(OUT, o.name, {"sent_at": future, "creation": future})
		frappe.db.commit()
		batch = self.say("Lại lỗi nữa", minutes_ago=1)
		self.analyze_as(batch, intent="Khiếu nại")
		self.assertTrue(self.group_on())
		# khách nhắn bình thường trong 24 giờ: không ngắt mạch
		recent = add_to_date(now_datetime(), hours=-1)
		frappe.db.set_value(OUT, o.name, {"sent_at": recent, "creation": recent})
		frappe.db.commit()
		batch = self.say("Cảm ơn nhé", minutes_ago=1)
		self.analyze_as(batch, intent="Tư vấn")
		self.assertTrue(self.group_on())

	def test_complaint_window_boundary_with_injected_time(self):
		self.auto_sent()
		(o,) = self.bot_outs()
		sent = get_datetime(o.creation).replace(microsecond=0)
		frappe.db.set_value(OUT, o.name, {"sent_at": sent, "creation": sent})
		frappe.db.commit()
		at = lambda **kw: auto_send.complaint_groups(self.lead1, sent + timedelta(**kw))  # noqa: E731
		self.assertEqual(at(hours=24), [G_HOURS])
		self.assertEqual(at(hours=23, minutes=59, seconds=59), [G_HOURS])
		self.assertEqual(at(hours=24, seconds=1), [])
		self.assertEqual(at(seconds=0), [])
		self.assertEqual(at(seconds=-5), [])

	def test_handoff_ratio_doubling_trips_with_simulated_time(self):
		self.enable_raw()
		now = now_datetime()

		def put(n, handoffs, when):
			for i in range(n):
				self.mk(
					decision="Pending",
					action="Handoff" if i < handoffs else "Reply",
					decided_at=None,
					created=when,
				)

		put(40, 4, add_to_date(now, days=-5))  # nền: 10%
		put(12, 3, add_to_date(now, hours=-2))  # gần: 25% < 2 lần nền? 25 >= 20 nên gấp đôi
		frappe.db.commit()
		r = auto_send.handoff_ratio(G_HOURS, now)
		self.assertEqual((r["recent_drafts"], r["recent_handoffs"], r["baseline_drafts"]), (12, 3, 40))
		self.assertTrue(r["doubled"])
		# giả lập thời gian: 3 ngày sau, cửa sổ gần đã trống -> không còn gấp đôi, không ngắt mạch
		later = now + timedelta(days=3)
		self.assertFalse(auto_send.handoff_ratio(G_HOURS, later)["doubled"])
		self.assertEqual(auto_send.run_breaker_checks(later)["tripped"], 0)
		self.assertTrue(self.group_on())
		self.assertEqual(auto_send.run_breaker_checks(now)["tripped"], 1)
		self.assert_tripped("handoff_doubled")
		self.assertEqual(auto_send.run_breaker_checks(now)["tripped"], 0)  # đã tắt, không tắt lại

	def test_handoff_ratio_small_samples_or_not_doubled_do_not_trip(self):
		now = now_datetime()
		old, recent = add_to_date(now, days=-5), add_to_date(now, hours=-2)
		for i in range(40):
			self.mk(decision="Pending", action="Handoff" if i < 6 else "Reply", decided_at=None, created=old)
		for i in range(12):  # 25% < 2 x 15%
			self.mk(
				decision="Pending", action="Handoff" if i < 3 else "Reply", decided_at=None, created=recent
			)
		frappe.db.commit()
		self.assertFalse(auto_send.handoff_ratio(G_HOURS, now)["doubled"])

	def test_trip_with_no_recipients_logs_error(self):
		self.enable_raw()
		with (
			mock.patch.object(auto_send, "alert_recipients", return_value=[]),
			mock.patch("frappe.log_error") as log,
		):
			self.assertTrue(auto_send.trip(G_HOURS, "complaint"))
		self.assertTrue(any("không có người nhận" in str(c) for c in log.call_args_list))
		self.assertFalse(self.group_on())


class TestInboxAndReport(AutoTestCase):
	def test_thread_labels_bot_message_and_rights(self):
		self.enable_raw()
		batch, _s = self.run_batch()
		d = self.draft_of(batch)
		frappe.set_user(SALE1)
		item = next(m for m in inbox.get_thread(self.lead1)["messages"] if m.get("bot_sent"))
		self.assertEqual(item["sender"], "Bot")
		self.assertEqual(item["bot"]["draft"], d.name)
		self.assertEqual(
			(item["bot"]["can_rate"], item["bot"]["can_disable"], item["bot"]["rating"]), (True, False, None)
		)
		frappe.set_user("Administrator")
		item = next(m for m in inbox.get_thread(self.lead1)["messages"] if m.get("bot_sent"))
		self.assertTrue(item["bot"]["can_disable"])
		frappe.set_user(ADMIN_USER)  # Omni Admin nhưng không ghi được Lead: thấy nút tắt, không chấm được
		meta = drafts.bot_sent_meta(self.lead1, [d.name])[d.name]
		self.assertEqual((meta["can_disable"], meta["can_rate"]), (True, False))
		frappe.set_user("Administrator")
		# tin của sale không có nhãn bot
		self.make_outbound(self.lead1, "Em chào anh", 0)
		sale_items = [m for m in inbox.get_thread(self.lead1)["messages"] if m["name"] != item["name"]]
		self.assertFalse(any(m.get("bot_sent") for m in sale_items if m["direction"] == "out"))

	def test_rating_needs_lead_write_permission(self):
		self.enable_raw()
		batch, _s = self.run_batch()
		d = self.draft_of(batch)
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			drafts.rate_draft(d.name, "Sai")
		frappe.set_user("Administrator")
		self.assertTrue(self.group_on())

	def test_report_counts_only_and_roles(self):
		self.enable_raw()
		self.run_batch()
		frappe.set_user(KE_USER)
		cols, rows = report.execute({})
		frappe.set_user("Administrator")
		self.assertTrue(cols)
		mine = next(r for r in rows if r["question_group"] == G_HOURS)
		self.assertEqual(
			(mine["auto_enabled"], mine["bot_sent"], mine["auto_off"], mine["eligible"]), (1, 0, 0, 0)
		)
		allowed = {c["fieldname"] for c in cols}
		for r in rows:
			self.assertEqual(set(r), allowed)  # chỉ cột số đếm, không nội dung
		frappe.db.set_value(OUT, self.bot_outs()[0].name, "status", "Sent")
		_cols, rows = report.execute({})
		self.assertEqual(next(r for r in rows if r["question_group"] == G_HOURS)["bot_sent"], 1)
		for user in (SALE1, MANAGER_USER):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				report.execute({})
		frappe.set_user("Administrator")
		auto_send.trip(G_HOURS, "complaint")
		_cols, rows = report.execute({})
		mine = next(r for r in rows if r["question_group"] == G_HOURS)
		self.assertEqual((mine["auto_enabled"], mine["auto_off"]), (0, 1))


class TestConcurrency(AutoTestCase):
	"""Hai kết nối thật. `frappe.conf` của luồng khác không thấy patch của luồng chính: đặt lại trong từng luồng."""

	def committed_pending(self):
		name = self.pending_draft()
		self.enable_raw()
		return name

	def thread_job(self, label, fn, results):
		site, sites_path = frappe.local.site, frappe.local.sites_path

		def run():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.local.conf[flags.AUTO_SEND_CONF] = 1
			try:
				results[label] = fn()
				frappe.db.commit()
			except BaseException as e:
				results[label] = e
				frappe.db.rollback()
			finally:
				frappe.destroy()

		return threading.Thread(target=run, name=label)

	def test_two_auto_senders_send_exactly_once(self):
		name = self.committed_pending()
		results: dict = {}
		a_in, release = threading.Event(), threading.Event()
		real = outbound.queue_outbound

		def hooked(*a, **kw):
			if threading.current_thread().name == "A":
				a_in.set()
				release.wait(timeout=20)
			return real(*a, **kw)

		with mock.patch.object(outbound, "queue_outbound", side_effect=hooked):
			a = self.thread_job("A", lambda: auto_send.try_auto_send(name), results)
			b = self.thread_job("B", lambda: auto_send.try_auto_send(name), results)
			a.start()
			self.assertTrue(a_in.wait(timeout=20))
			b.start()
			time.sleep(1.5)
			release.set()
			a.join(timeout=60)
			b.join(timeout=60)
		self.assertIs(results["A"], True, results)
		self.assertIs(results["B"], False, results)
		frappe.db.rollback()
		self.assertEqual(len(self.bot_outs()), 1)
		self.assertEqual(self.d(name).mode, "Auto")

	def test_auto_vs_sale_send_draft_sends_once(self):
		name = self.committed_pending()
		results: dict = {}
		a_in, release = threading.Event(), threading.Event()
		real = outbound.queue_outbound

		def hooked(*a, **kw):
			if threading.current_thread().name == "A":
				a_in.set()
				release.wait(timeout=20)
			return real(*a, **kw)

		def sale():
			frappe.set_user(SALE1)
			return drafts.send_draft(name)

		with mock.patch.object(outbound, "queue_outbound", side_effect=hooked):
			a = self.thread_job("A", lambda: auto_send.try_auto_send(name), results)
			b = self.thread_job("B", sale, results)
			a.start()
			self.assertTrue(a_in.wait(timeout=20))
			b.start()
			time.sleep(1.5)
			release.set()
			a.join(timeout=60)
			b.join(timeout=60)
		self.assertIs(results["A"], True, results)
		self.assertIsInstance(results["B"], frappe.ValidationError, results)
		frappe.db.rollback()
		rows = frappe.get_all(OUT, filters={"lead": self.lead1}, fields=["source"])
		self.assertEqual([r.source for r in rows], ["Bot"])

	def test_sale_wins_then_auto_does_not_send(self):
		name = self.committed_pending()
		results: dict = {}
		s_in, release = threading.Event(), threading.Event()
		real = inbox.queue_zalo_reply

		def hooked(*a, **kw):
			if threading.current_thread().name == "S":
				s_in.set()
				release.wait(timeout=20)
			return real(*a, **kw)

		def sale():
			frappe.set_user(SALE1)
			return drafts.send_draft(name)

		with mock.patch.object(inbox, "queue_zalo_reply", side_effect=hooked):
			s = self.thread_job("S", sale, results)
			t = self.thread_job("T", lambda: auto_send.try_auto_send(name), results)
			s.start()
			self.assertTrue(s_in.wait(timeout=20))
			t.start()
			time.sleep(1.5)
			release.set()
			s.join(timeout=60)
			t.join(timeout=60)
		self.assertEqual(results["S"]["status"], "Queued", results)
		self.assertIs(results["T"], False, results)
		frappe.db.rollback()
		rows = frappe.get_all(OUT, filters={"lead": self.lead1}, fields=["source"])
		self.assertEqual([r.source for r in rows], ["Sale"])

	def test_disable_committed_while_auto_waits_means_zero_sends(self):
		name = self.committed_pending()
		results: dict = {}
		d_in, release = threading.Event(), threading.Event()
		seen: dict = {}
		real_note = auto_send._note_on_group
		real_check = auto_send.check_reply

		def hooked_note(*a, **kw):
			if threading.current_thread().name == "D":
				d_in.set()  # D giữ khóa dòng nhóm (đã lưu tắt, chưa commit)
				release.wait(timeout=20)
			return real_note(*a, **kw)

		def spy_check(row, now=None, group_row=None):
			seen.setdefault(
				"group_enabled_seen_by_auto", group_row.auto_send_enabled if group_row else None
			)  # lần kiểm ĐẦU TIÊN (trước queue_outbound)
			return real_check(row, now, group_row)

		def disable():
			frappe.set_user("Administrator")
			return auto_send.disable_group(G_HOURS, "ngắt")

		with (
			mock.patch.object(auto_send, "_note_on_group", side_effect=hooked_note),
			mock.patch.object(auto_send, "check_reply", side_effect=spy_check),
		):
			d = self.thread_job("D", disable, results)
			t = self.thread_job("T", lambda: auto_send.try_auto_send(name), results)
			d.start()
			self.assertTrue(d_in.wait(timeout=20))
			t.start()
			time.sleep(1.5)  # T đang chờ khóa dòng nhóm
			release.set()
			d.join(timeout=60)
			t.join(timeout=60)
		self.assertEqual(results["D"]["status"], "disabled", results)
		self.assertIs(results["T"], False, results)
		self.assertEqual(seen.get("group_enabled_seen_by_auto"), 0, seen)  # T thấy trạng thái ĐÃ TẮT
		frappe.db.rollback()
		self.assertEqual(self.bot_outs(), [])
		self.assertEqual(self.d(name).decision, "Pending")


class TestFlagsRestored(AutoTestCase):
	def test_bot_active_hours_setting_unchanged_by_these_tests(self):
		self.assertEqual(
			frappe.db.get_single_value("Omni Settings", "bot_active_hours"), self._orig_bot_hours
		)
