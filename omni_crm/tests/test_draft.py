# ruff: noqa: RUF001
"""D3-01: nháp của bot cho sale duyệt (chế độ Draft). Mọi lời gọi AI và gửi đều giả; bot vẫn không tự gửi."""

import re
import threading
import time
from datetime import date
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm import flags
from omni_crm.api import drafts, inbox
from omni_crm.bot import reply
from omni_crm.ingest.events import OutboundRequest
from omni_crm.pipeline import outbound
from omni_crm.tests.test_ai_client import set_settings
from omni_crm.tests.test_bot_reply import DRAFT, G_HOURS, G_PRICE, KB1, ReplyTestCase, reply_ok
from omni_crm.tests.test_inbox import SALE1, SALE2

OUT = "Omni Outbound Message"
TEXT = "Dạ Miyano làm việc từ 8 giờ đến 17 giờ 30 ạ."


class DraftTestCase(ReplyTestCase):
	def setUp(self):
		super().setUp()
		set_settings(bot_reply_mode="Draft")
		frappe.db.set_value("Omni Channel", self.channel.name, "outbound_enabled", 1)
		frappe.db.set_value("Omni Channel", self.channel.name, "zalo_access_token", None)
		self.set_last_inbound(minutes_ago=5)
		p = mock.patch("omni_crm.flags.sandbox", return_value=True)
		p.start()
		self.addCleanup(p.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(self._clean_d301)

	def _clean_d301(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.delete("Notification Log", {"document_name": ["in", [self.lead1, self.lead2]]})
		frappe.db.commit()

	def set_last_inbound(self, minutes_ago=0, hours_ago=0):
		at = add_to_date(now_datetime(), minutes=-minutes_ago, hours=-hours_ago)
		frappe.db.set_value("Omni Identity", self.ident1, "last_inbound_at", at)
		frappe.db.commit()

	def make_draft(self, text="Cho hỏi giờ làm việc", result=None, minutes_ago=2) -> str:
		batch = self.say(text, minutes_ago=minutes_ago)
		self.fake.add_rule(result or reply_ok(reply_text=TEXT), kind="Reply")
		self.assertEqual(reply.reply_batch(batch), "drafted")
		return self.drafts(batch_id=batch)[0].name

	def handoff_draft(self) -> str:
		return self.make_draft(
			"Cho hỏi giá",
			reply_ok(
				action="handoff", question_group=G_PRICE, kb_item_ids=[], handoff_reason="Khách hỏi giá"
			),
		)

	def call(self, fn, *args, user=SALE1, **kw):
		frappe.set_user(user)
		with mock.patch("frappe.enqueue"):
			res = fn(*args, **kw)
			frappe.db.commit()
		frappe.set_user("Administrator")
		return res

	def d(self, name):
		return frappe.db.get_value(DRAFT, name, ["*"], as_dict=True)

	def out_rows(self):
		return frappe.get_all(OUT, filters={"lead": self.lead1}, fields=["*"], order_by="creation asc")


class TestDraftCreation(DraftTestCase):
	def test_draft_mode_creates_draft_and_shadow_does_not_show(self):
		name = self.make_draft()
		self.assertEqual((self.d(name).mode, self.d(name).decision), ("Draft", "Pending"))
		frappe.set_user(SALE1)
		threads = inbox.get_thread(self.lead1)
		self.assertEqual([x["name"] for x in threads["drafts"]], [name])
		frappe.set_user("Administrator")
		set_settings(bot_reply_mode="Shadow")
		shadow = self.make_draft("Cho hỏi giờ làm việc nữa")
		self.assertEqual(self.d(shadow).mode, "Shadow")
		frappe.set_user(SALE1)
		listed = [x["name"] for x in inbox.get_thread(self.lead1)["drafts"]]
		frappe.set_user("Administrator")
		self.assertNotIn(shadow, listed)

	def test_off_creates_nothing_and_no_bot_outbound(self):
		set_settings(bot_reply_mode="Off")
		self.assertEqual(reply.reply_batch(self.say("Cho hỏi giờ làm việc")), "off")
		self.assertEqual(self.drafts(), [])
		self.assertEqual(self.bot_outbound(), 0)

	def test_new_customer_message_expires_old_pending_draft(self):
		old = self.make_draft(minutes_ago=10)
		new = self.make_draft("Cho hỏi giờ làm việc lần nữa", minutes_ago=1)
		self.assertEqual(self.d(old).decision, "Expired")
		self.assertEqual(self.d(new).decision, "Pending")

	def test_new_message_without_draft_still_expires_old(self):
		old = self.make_draft(minutes_ago=10)
		frappe.db.set_value(
			"Omni Identity", self.ident1, "bot_paused_until", add_to_date(now_datetime(), hours=1)
		)
		frappe.db.commit()
		self.assertEqual(reply.reply_batch(self.say("tin mới", minutes_ago=1)), "paused")
		self.assertEqual(self.d(old).decision, "Expired")

	def test_other_leads_draft_not_expired(self):
		mine = self.make_draft(minutes_ago=10)
		batch = self.say("hỏi giờ làm việc", lead=self.lead2, ident=self.ident2)
		self.fake.add_rule(reply_ok(), kind="Reply")
		reply.reply_batch(batch)
		self.assertEqual(self.d(mine).decision, "Pending")
		frappe.db.delete(DRAFT, {"lead": self.lead2})

	def test_stale_after_24_hours_expires_by_schedule(self):
		name = self.make_draft()
		fresh = self.make_draft("Cho hỏi giờ làm việc ngày mai", minutes_ago=1)
		frappe.db.set_value(DRAFT, name, "creation", add_to_date(now_datetime(), hours=-25))
		frappe.db.set_value(DRAFT, fresh, "decision", "Pending")
		frappe.db.commit()
		reply.expire_pending_drafts()
		self.assertEqual(self.d(name).decision, "Expired")
		self.assertEqual(self.d(fresh).decision, "Pending")

	def test_owner_notified_without_customer_text(self):
		name = self.make_draft("Cho hỏi giờ làm việc SECRETTEXT")
		logs = frappe.get_all(
			"Notification Log",
			filters={"document_name": self.lead1},
			fields=["for_user", "subject", "email_content"],
		)
		self.assertEqual([x.for_user for x in logs], [SALE1])
		blob = f"{logs[0].subject} {logs[0].email_content}"
		self.assertIn("omni-inbox", blob)
		self.assertNotIn("SECRETTEXT", blob)
		self.assertNotIn(TEXT, blob)
		self.assertTrue(name)


class TestThreadPayload(DraftTestCase):
	def test_payload_has_kb_content_and_hides_internal_flags(self):
		name = self.make_draft()
		frappe.set_user(SALE1)
		(item,) = inbox.get_thread(self.lead1)["drafts"]
		frappe.set_user("Administrator")
		self.assertEqual((item["name"], item["action"], item["text"]), (name, "Reply", TEXT))
		self.assertEqual(item["question_group"], G_HOURS)
		self.assertEqual([k["id"] for k in item["kb"]], [KB1])
		self.assertEqual(item["kb"][0]["question"], "Mấy giờ làm việc?")
		self.assertNotIn("answer", item["kb"][0])  # nội dung mục chỉ tải khi bấm xem (get_draft_kb_item)
		self.assertTrue(item["can_send"])
		self.assertNotIn("guard_flags", item)

	def test_handoff_payload_has_reason_and_cannot_send(self):
		self.handoff_draft()
		frappe.set_user(SALE1)
		(item,) = inbox.get_thread(self.lead1)["drafts"]
		frappe.set_user("Administrator")
		self.assertEqual(item["action"], "Handoff")
		self.assertEqual(item["handoff_reason"], "Khách hỏi giá")
		self.assertFalse(item["can_send"])
		self.assertFalse(item["text"])

	def test_decided_drafts_not_listed(self):
		name = self.make_draft()
		self.call(drafts.discard_draft, name)
		frappe.set_user(SALE1)
		self.assertEqual(inbox.get_thread(self.lead1)["drafts"], [])
		frappe.set_user("Administrator")


class TestSendDraft(DraftTestCase):
	def test_send_as_is(self):
		name = self.make_draft()
		res = self.call(drafts.send_draft, name)
		self.assertEqual(res["status"], "Queued")
		(row,) = self.out_rows()
		self.assertEqual((row.source, row.bot_draft, row.sent_by, row.text), ("Sale", name, SALE1, TEXT))
		d = self.d(name)
		self.assertEqual(
			(d.decision, d.final_text, d.edit_distance_ratio, d.decided_by), ("Sent As Is", TEXT, 0, SALE1)
		)
		self.assertIsNotNone(d.decided_at)
		self.assertEqual(self.bot_outbound(), 0)
		frappe.set_user("Administrator")
		self.assertEqual(outbound.send_outbound(row.name), "Sent")

	def test_send_edited(self):
		name = self.make_draft()
		edited = "Dạ Miyano làm việc từ 8 giờ đến 17 giờ, thứ Bảy nghỉ ạ."
		self.call(drafts.send_draft, name, edited)
		(row,) = self.out_rows()
		self.assertEqual((row.source, row.bot_draft, row.text), ("Sale", name, edited))
		d = self.d(name)
		self.assertEqual((d.decision, d.final_text), ("Sent Edited", edited))
		self.assertGreater(d.edit_distance_ratio, 0)

	def test_whitespace_or_case_only_edit_is_still_edited_with_positive_ratio(self):
		name = self.make_draft()
		self.call(drafts.send_draft, name, TEXT.upper())
		d = self.d(name)
		self.assertEqual(d.decision, "Sent Edited")
		self.assertGreater(d.edit_distance_ratio, 0)

	def test_same_text_passed_explicitly_counts_as_is(self):
		name = self.make_draft()
		self.call(drafts.send_draft, name, f"  {TEXT}  ")
		self.assertEqual(self.d(name).decision, "Sent As Is")

	def test_discard(self):
		name = self.make_draft()
		self.call(drafts.discard_draft, name)
		d = self.d(name)
		self.assertEqual((d.decision, d.decided_by), ("Discarded", SALE1))
		self.assertIsNotNone(d.decided_at)
		self.assertEqual(self.out_rows(), [])

	def test_cannot_send_twice_or_after_discard_or_expiry(self):
		name = self.make_draft()
		self.call(drafts.send_draft, name)
		for fn in (drafts.send_draft, drafts.discard_draft):
			with self.assertRaises(frappe.ValidationError):
				self.call(fn, name)
		self.assertEqual(len(self.out_rows()), 1)
		gone = self.make_draft("Cho hỏi giờ làm việc nữa", minutes_ago=1)
		self.call(drafts.discard_draft, gone)
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.send_draft, gone)
		old = self.make_draft("Cho hỏi giờ làm việc ba", minutes_ago=1)
		frappe.db.set_value(DRAFT, old, "decision", "Expired")
		frappe.db.commit()
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.send_draft, old)
		self.assertEqual(len(self.out_rows()), 1)

	def test_handoff_draft_cannot_be_sent_even_via_direct_api(self):
		name = self.handoff_draft()
		for text in (None, "Dạ em báo giá ngay ạ"):
			with self.assertRaises(frappe.ValidationError):
				self.call(drafts.send_draft, name, text)
		self.assertEqual(self.out_rows(), [])
		self.assertEqual(self.d(name).decision, "Pending")
		# đường thấp hơn: queue_outbound cũng từ chối nháp Handoff và mọi nguồn khác Sale
		for source in ("Sale", "Bot", "System"):
			with self.assertRaises(frappe.ValidationError), mock.patch("frappe.enqueue"):
				outbound.queue_outbound(
					OutboundRequest(self.channel.name, self.ident1, "x", None, source, bot_draft=name),
					user=SALE1,
				)
		self.assertEqual(self.out_rows(), [])

	def test_queue_outbound_rejects_bot_source_even_with_good_draft(self):
		name = self.make_draft()
		with self.assertRaises(frappe.ValidationError), mock.patch("frappe.enqueue"):
			outbound.queue_outbound(
				OutboundRequest(self.channel.name, self.ident1, TEXT, None, "Bot", bot_draft=name)
			)
		self.assertEqual(self.out_rows(), [])
		self.assertEqual(self.bot_outbound(), 0)

	def test_shadow_draft_cannot_be_sent(self):
		set_settings(bot_reply_mode="Shadow")
		name = self.make_draft()
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.send_draft, name)
		self.assertEqual(self.out_rows(), [])

	def test_empty_edit_rejected_and_draft_stays_pending(self):
		name = self.make_draft()
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.send_draft, name, "   ")
		self.assertEqual(self.d(name).decision, "Pending")

	def test_paid_window_needs_confirmation_and_draft_stays_pending(self):
		name = self.make_draft()
		self.set_last_inbound(hours_ago=72)
		res = self.call(drafts.send_draft, name)
		self.assertEqual(res["status"], "confirm_required")
		self.assertEqual(self.d(name).decision, "Pending")
		self.assertEqual(self.out_rows(), [])
		res = self.call(drafts.send_draft, name, None, 1)
		self.assertEqual(res["status"], "Queued")
		self.assertEqual(self.d(name).decision, "Sent As Is")
		frappe.set_user("Administrator")
		self.assertEqual(outbound.send_outbound(self.out_rows()[0].name), "Sent")

	def test_beyond_window_blocked_draft_stays_pending(self):
		name = self.make_draft()
		self.set_last_inbound(hours_ago=24 * 9)
		res = self.call(drafts.send_draft, name)
		self.assertEqual(res["status"], "Blocked")
		self.assertEqual(self.d(name).decision, "Pending")
		self.assertEqual([r.status for r in self.out_rows()], ["Blocked"])

	def test_email_draft_cannot_be_sent_from_inbox(self):
		name = self.make_draft()
		frappe.db.set_value("Omni Identity", self.ident1, "channel_type", "Email")
		frappe.db.set_value("Omni Channel", self.channel.name, "channel_type", "Email")
		frappe.db.commit()
		self.addCleanup(frappe.db.set_value, "Omni Channel", self.channel.name, "channel_type", "Zalo OA")
		self.addCleanup(frappe.db.set_value, "Omni Identity", self.ident1, "channel_type", "Zalo OA")
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.send_draft, name)
		self.assertEqual(self.out_rows(), [])


class TestStaleAndConcurrency(DraftTestCase):
	def test_send_rejected_when_customer_wrote_again_before_batch_ran(self):
		name = self.make_draft(minutes_ago=10)
		self.say("khách nhắn thêm, lượt chưa chạy", minutes_ago=1)
		with self.assertRaises(frappe.ValidationError) as cm:
			self.call(drafts.send_draft, name)
		self.assertIn("lỗi thời", str(cm.exception))
		self.assertEqual(self.d(name).decision, "Expired")
		self.assertEqual(self.out_rows(), [])

	def test_send_rejected_when_draft_mode_turned_off(self):
		name = self.make_draft()
		set_settings(bot_reply_mode="Off")
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.send_draft, name)
		self.assertEqual(self.d(name).decision, "Pending")
		self.assertEqual(self.out_rows(), [])

	def test_queue_outbound_checks_pending_and_identity(self):
		name = self.make_draft()
		other = self.make_identity("TD301Z9", "Zalo OA", self.lead1, "Khác")
		frappe.db.commit()
		with self.assertRaises(frappe.ValidationError), mock.patch("frappe.enqueue"):
			outbound.queue_outbound(
				OutboundRequest(self.channel.name, other, TEXT, None, "Sale", bot_draft=name), user=SALE1
			)
		frappe.db.set_value(DRAFT, name, "decision", "Discarded")
		frappe.db.commit()
		with self.assertRaises(frappe.ValidationError), mock.patch("frappe.enqueue"):
			outbound.queue_outbound(
				OutboundRequest(self.channel.name, self.ident1, TEXT, None, "Sale", bot_draft=name),
				user=SALE1,
			)
		self.assertEqual(self.out_rows(), [])
		frappe.db.delete("Omni Identity", {"name": other})
		frappe.db.commit()

	def test_two_concurrent_sends_of_same_draft_send_once(self):
		name = self.make_draft()
		site, sites_path = frappe.local.site, frappe.local.sites_path
		a_in, release = threading.Event(), threading.Event()
		real = inbox.queue_zalo_reply
		results: dict[str, object] = {}

		def hooked(*a, **kw):
			if threading.current_thread().name == "A":
				a_in.set()  # A đã giữ khóa dòng nháp
				release.wait(timeout=15)
			return real(*a, **kw)

		def job(label):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			try:
				frappe.set_user(SALE1)
				results[label] = drafts.send_draft(name)
				frappe.db.commit()
			except BaseException as e:
				results[label] = e
				frappe.db.rollback()
			finally:
				frappe.destroy()

		with mock.patch.object(inbox, "queue_zalo_reply", side_effect=hooked), mock.patch("frappe.enqueue"):
			a = threading.Thread(target=job, args=("A",), name="A")
			b = threading.Thread(target=job, args=("B",), name="B")
			a.start()
			self.assertTrue(a_in.wait(timeout=15))
			b.start()
			time.sleep(1.5)  # B đang chờ khóa dòng nháp của A
			release.set()
			a.join(timeout=60)
			b.join(timeout=60)
		self.assertEqual(results["A"]["status"], "Queued", results)
		self.assertIsInstance(results["B"], frappe.ValidationError, results)
		frappe.db.rollback()
		self.assertEqual(len(self.out_rows()), 1)
		self.assertEqual(self.d(name).decision, "Sent As Is")


class TestDraftPermissions(DraftTestCase):
	def test_user_without_lead_access_cannot_see_or_decide(self):
		name = self.make_draft()
		frappe.set_user(SALE2)
		with self.assertRaises(frappe.PermissionError):
			inbox.get_thread(self.lead1)
		for fn, args in (
			(drafts.send_draft, (name,)),
			(drafts.discard_draft, (name,)),
			(drafts.rate_draft, (name, "Đúng")),
			(drafts.get_draft_kb_item, (name, KB1)),
		):
			with self.assertRaises(frappe.PermissionError):
				fn(*args)
		frappe.set_user("Administrator")
		self.assertEqual(self.d(name).decision, "Pending")
		self.assertEqual(self.out_rows(), [])

	def test_guest_denied(self):
		name = self.make_draft()
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			drafts.send_draft(name)
		frappe.set_user("Administrator")

	def test_unknown_draft(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.call(drafts.send_draft, "khong-co-nhap")

	def test_doctype_permission_is_lead_scoped(self):
		name = self.make_draft()
		doc = frappe.get_doc(DRAFT, name)
		self.assertFalse(frappe.has_permission(DRAFT, "read", doc=doc, user=SALE2))
		self.assertTrue(frappe.has_permission(DRAFT, "read", doc=doc, user="Administrator"))
		cond = frappe.get_attr(frappe.get_hooks("permission_query_conditions")[DRAFT][0])
		self.assertEqual(cond("Administrator"), "")
		self.assertIn("lead", cond(SALE2))

	def test_kb_item_view_only_for_items_of_the_draft(self):
		name = self.make_draft()
		res = self.call(drafts.get_draft_kb_item, name, KB1)
		self.assertIn("8 giờ", res["answer"])
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.get_draft_kb_item, name, "KB-90002")


class TestKnowledgeEditorScope(DraftTestCase):
	EDITOR = "td301editor@example.com"

	def test_editor_sees_report_but_not_conversation_drafts(self):
		from omni_crm.omni_crm.report.bot_nháp import bot_nháp as report

		if not frappe.db.exists("User", self.EDITOR):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": self.EDITOR,
					"first_name": "Editor",
					"send_welcome_email": 0,
					"roles": [{"role": "Omni Knowledge Editor"}],
				}
			).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User", self.EDITOR, force=True, ignore_permissions=True)
		name = self.make_draft()
		doc = frappe.get_doc(DRAFT, name)
		self.assertFalse(frappe.has_permission(DRAFT, "read", doc=doc, user=self.EDITOR))
		self.assertEqual(frappe.get_list(DRAFT, filters={"name": name}, user=self.EDITOR), [])
		frappe.set_user(self.EDITOR)
		_cols, rows = report.execute({})
		self.assertTrue(rows)
		with self.assertRaises(frappe.PermissionError):
			inbox.get_thread(self.lead1)
		frappe.set_user("Administrator")


class TestRating(DraftTestCase):
	def test_rate_after_send_and_after_discard(self):
		a = self.make_draft()
		self.call(drafts.send_draft, a)
		self.call(drafts.rate_draft, a, "Đúng")
		self.assertEqual(self.d(a).rating, "Đúng")
		b = self.make_draft("Cho hỏi giờ làm việc nữa", minutes_ago=1)
		self.call(drafts.discard_draft, b)
		self.call(drafts.rate_draft, b, "Sai", "Sai về giờ", 1)
		d = self.d(b)
		self.assertEqual((d.rating, d.rating_note, d.critical), ("Sai", "Sai về giờ", 1))

	def test_rating_validation(self):
		name = self.make_draft()
		with self.assertRaises(frappe.ValidationError):  # chưa quyết định
			self.call(drafts.rate_draft, name, "Đúng")
		self.call(drafts.discard_draft, name)
		for bad in ("", "Tốt", None):
			with self.assertRaises(frappe.ValidationError):
				self.call(drafts.rate_draft, name, bad)
		self.assertFalse(self.d(name).rating)

	def test_rating_of_expired_or_shadow_refused(self):
		name = self.make_draft()
		frappe.db.set_value(DRAFT, name, "decision", "Expired")
		frappe.db.commit()
		with self.assertRaises(frappe.ValidationError):
			self.call(drafts.rate_draft, name, "Đúng")


class TestDraftReport(DraftTestCase):
	def _row(self, group, decision, rating=None, ratio=0, action="Reply", mode="Draft", weeks_ago=0):
		doc = frappe.get_doc(
			{
				"doctype": DRAFT,
				"lead": self.lead1,
				"identity": self.ident1,
				"channel": self.channel.name,
				"batch_id": f"T05-rep-{frappe.generate_hash(length=6)}",
				"mode": mode,
				"action": action,
				"decision": decision,
				"question_group": group,
				"rating": rating,
				"edit_distance_ratio": ratio,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(DRAFT, doc.name, "creation", add_to_date(now_datetime(), weeks=-weeks_ago))
		return doc.name

	def test_by_group_and_by_week(self):
		from omni_crm.omni_crm.report.bot_nháp import bot_nháp as report

		self._row(G_HOURS, "Sent As Is", "Đúng")
		self._row(G_HOURS, "Sent As Is", "Đúng")
		self._row(G_HOURS, "Sent Edited", "Thiếu", 0.3)
		self._row(G_HOURS, "Discarded", "Sai")
		self._row(G_HOURS, "Pending")
		self._row(G_HOURS, "Expired")
		self._row(G_PRICE, "Pending", action="Handoff")
		self._row(G_HOURS, "Shadow Compared", mode="Shadow")  # không tính
		self._row(G_HOURS, "Sent As Is", "Đúng", weeks_ago=2)
		frappe.db.commit()
		cols, rows = report.execute({"view": "Theo nhóm"})
		self.assertTrue(cols)
		by = {r["question_group"]: r for r in rows}
		g = by[G_HOURS]
		self.assertEqual((g["drafts"], g["decided"]), (7, 5))
		self.assertEqual((g["sent_as_is"], g["sent_edited"], g["discarded"], g["expired"]), (3, 1, 1, 1))
		self.assertEqual((g["rate_as_is"], g["rate_edited"], g["rate_discarded"]), (60.0, 20.0, 20.0))
		self.assertEqual((g["rating_ok"], g["rating_wrong"], g["rating_missing"]), (3, 1, 1))
		self.assertEqual(by[G_PRICE]["handoff"], 1)
		_c, weeks = report.execute({"view": "Theo tuần"})
		self.assertEqual(len({r["week"] for r in weeks}), 2)
		this_week = [r for r in weeks if r["week"] == report.week_start(date.today()).isoformat()]
		self.assertEqual(sum(r["drafts"] for r in this_week), 7)

	def test_role_gate_and_no_conversation_text(self):
		from omni_crm.omni_crm.report.bot_nháp import bot_nháp as report

		self._row(G_HOURS, "Sent As Is", "Đúng")
		frappe.set_user(SALE1)
		with self.assertRaises(frappe.PermissionError):
			report.execute({})
		frappe.set_user("Administrator")
		cols, rows = report.execute({})
		blob = repr(rows)
		self.assertNotIn("draft_text", blob)
		self.assertTrue(all(isinstance(c, dict) for c in cols))


class TestNoBotSendPath(DraftTestCase):
	def test_draft_mode_never_creates_bot_outbound_and_api_source_is_sale(self):
		for _i in range(3):
			self.make_draft(f"Cho hỏi giờ làm việc {_i}", minutes_ago=3 - _i)
		self.assertEqual(self.bot_outbound(), 0)
		src = open(drafts.__file__, encoding="utf-8").read()
		self.assertIsNone(re.search(r"""source\s*=\s*["']Bot["']""", src))
		self.assertEqual(flags.bot_reply_mode(), "Draft")
