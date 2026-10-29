import json
from unittest import mock

import frappe
from crm.api.activities import get_activities

from omni_crm import flags
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.crm_bridge import timeline
from omni_crm.tests.bridge_helpers import PREFIX, SOURCE, USERS, BridgeTestCase, make_users

ASSIGN_RULE = "td106-rule"


def _activity_communications(name: str) -> list[dict]:
	"""Hoạt động Communication của Lead qua chính API của CRM."""
	acts = get_activities(name)[0]
	return [a for a in acts if a["activity_type"] == "communication"]


class TestLeadSource(BridgeTestCase):
	def test_new_lead_source_follows_channel(self):
		lead = self.lead_of(self.send("m1"))
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "source"), "Zalo OA")
		self.assertTrue(frappe.db.exists("CRM Lead Source", "Zalo OA"))

	def test_source_map_from_settings_creates_missing_source(self):
		real = frappe.db.get_single_value

		def fake(doctype, field, *a, **kw):
			if (doctype, field) == ("Omni Settings", "default_lead_source_map"):
				return json.dumps({"Zalo OA": SOURCE})
			return real(doctype, field, *a, **kw)

		self.assertFalse(frappe.db.exists("CRM Lead Source", SOURCE))
		with mock.patch.object(frappe.db, "get_single_value", side_effect=fake):
			lead = self.lead_of(self.send("m1"))
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "source"), SOURCE)
		self.assertTrue(frappe.db.exists("CRM Lead Source", SOURCE))

	def test_bad_map_json_falls_back_to_default(self):
		real = frappe.db.get_single_value

		def fake(doctype, field, *a, **kw):
			if (doctype, field) == ("Omni Settings", "default_lead_source_map"):
				return "{khong phai json"
			return real(doctype, field, *a, **kw)

		with mock.patch.object(frappe.db, "get_single_value", side_effect=fake):
			self.assertEqual(lead_bridge.lead_source_for_channel("Telephony"), "Điện thoại")
			self.assertIsNone(lead_bridge.lead_source_for_channel("Kenh la"))

	def test_last_inbound_updated_each_round(self):
		lead = self.lead_of(self.send("m1"))
		first = frappe.db.get_value("CRM Lead", lead, "omni_last_inbound_at")
		self.assertTrue(first)
		self.send("m2")
		self.assertGreaterEqual(frappe.db.get_value("CRM Lead", lead, "omni_last_inbound_at"), first)

	def test_inbox_link_comment_added_once(self):
		lead = self.lead_of(self.send("m1"))
		self.send("m2")
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": lead, "comment_type": "Comment"},
			pluck="content",
		)
		self.assertEqual(len(comments), 1)
		self.assertIn(f"/app/omni-inbox?lead={lead}", comments[0])
		self.assertFalse(lead_bridge.add_inbox_link(lead))


class TestAssignment(BridgeTestCase):
	def test_assignment_rule_assigns_lead_created_in_background_job(self):
		"""G11: Assignment Rule của Frappe chạy khi worker (người dùng Guest) insert Lead."""
		make_users()
		with mock.patch("frappe.enqueue"):
			frappe.get_doc(
				{
					"doctype": "Assignment Rule",
					"name": ASSIGN_RULE,
					"document_type": "CRM Lead",
					"assign_condition": "status == 'New'",
					"rule": "Round Robin",
					"priority": 1,
					"description": "test D1-06",
					"assignment_days": [
						{"day": d}
						for d in (
							"Monday",
							"Tuesday",
							"Wednesday",
							"Thursday",
							"Friday",
							"Saturday",
							"Sunday",
						)
					],
					"users": [{"user": u} for u in USERS[:2]],
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()
			l1 = self.lead_of(self.send("m1", sender=f"{PREFIX}A", user="Guest"))
			l2 = self.lead_of(self.send("m2", sender=f"{PREFIX}B", user="Guest"))
		owners = {frappe.db.get_value("CRM Lead", x, "lead_owner") for x in (l1, l2)}
		self.assertEqual(owners, set(USERS[:2]))  # chia vòng tròn, CRM đồng bộ lead_owner từ ToDo


class TestTimeline(BridgeTestCase):
	def test_inbound_visible_in_crm_activities(self):
		msg = self.send("m1", text="Cho tôi hỏi giá máy cắt")
		lead = self.lead_of(msg)
		acts = _activity_communications(lead)
		self.assertEqual(len(acts), 1)
		self.assertIn("Cho tôi hỏi giá máy cắt", acts[0]["data"]["content"])
		self.assertIn("Zalo OA", acts[0]["data"]["subject"])
		comm = self.communications("CRM Lead", lead)[0]
		self.assertEqual(comm.communication_medium, "Chat")
		self.assertEqual(comm.sent_or_received, "Received")
		self.assertEqual(comm.omni_inbound_message, msg)

	def test_reprocessing_does_not_duplicate(self):
		msg = self.send("m1")
		lead = self.lead_of(msg)
		row = frappe._dict(name=msg)
		self.assertEqual(timeline.append(lead, [row]), 0)
		self.assertEqual(timeline.append(lead, [row]), 0)
		self.assertEqual(len(self.communications("CRM Lead", lead)), 1)
		# Đặt lại tin về New và xử lý lại cùng lượt: vẫn một bản ghi
		frappe.db.set_value("Omni Inbound Message", msg, "status", "New")
		frappe.db.commit()
		from omni_crm.pipeline.worker import process_identity

		key = frappe.db.get_value("Omni Inbound Message", msg, "identity_key")
		with mock.patch("frappe.enqueue"):
			process_identity(key, debounce_seconds=0)
		self.assertEqual(len(self.communications("CRM Lead", lead)), 1)

	def test_second_message_adds_second_entry_in_order(self):
		lead = self.lead_of(self.send("m1", text="tin một"))
		self.send("m2", text="tin hai")
		contents = [c.content for c in self.communications("CRM Lead", lead)]
		self.assertEqual(len(contents), 2)
		self.assertIn("tin một", contents[0])
		self.assertIn("tin hai", contents[1])

	def test_converted_lead_writes_to_deal(self):
		lead = self.lead_of(self.send("m1", text="trước khi chuyển"))
		frappe.get_doc({"doctype": "CRM Deal", "lead": lead, "organization": None, "first_name": "x"}).insert(
			ignore_permissions=True, ignore_mandatory=True
		)
		frappe.db.set_value("CRM Lead", lead, "converted", 1)
		frappe.db.commit()
		deal = frappe.db.get_value("CRM Deal", {"lead": lead}, "name")
		msg2 = self.send("m2", text="sau khi chuyển")
		self.assertEqual(self.lead_of(msg2), lead)
		self.assertEqual(len(self.communications("CRM Lead", lead)), 1)  # tin cũ ở lại Lead
		on_deal = self.communications("CRM Deal", deal)
		self.assertEqual(len(on_deal), 1)
		self.assertIn("sau khi chuyển", on_deal[0].content)
		self.assertEqual(on_deal[0].omni_inbound_message, msg2)

	def test_text_is_escaped(self):
		# Frappe tự trung hòa <script> khi lưu; thẻ khác (img onerror...) được lưu nguyên nên timeline phải thoát
		lead = self.lead_of(self.send("m1", text="<img src=x onerror=alert(1)> A & B\ndòng 2"))
		content = self.communications("CRM Lead", lead)[0].content
		self.assertNotIn("<img", content)
		self.assertIn("&lt;img", content)
		self.assertIn("<br>dòng 2", content)
		self.assertEqual(timeline.render_text("A & B < C"), "<p>A &amp; B &lt; C</p>")

	def test_attachment_links_only_http(self):
		html = timeline.render_text(
			"ảnh", [{"url": "https://x.example/a.png", "name": "a.png"}, {"url": "javascript:alert(1)"}]
		)
		self.assertIn('href="https://x.example/a.png"', html)
		self.assertNotIn("javascript:", html)

	def test_non_conversation_events_not_shown(self):
		lead = self.lead_of(self.send("m1", text="xin chào"))
		self.send("f1", text="", event_type="follow")
		self.assertEqual(len(self.communications("CRM Lead", lead)), 1)

	def test_oa_echo_shown_as_sent(self):
		lead = self.lead_of(self.send("m1"))
		self.send("e1", text="Dạ em chào anh", event_type="oa_echo")
		sent = [c for c in self.communications("CRM Lead", lead) if c.sent_or_received == "Sent"]
		self.assertEqual(len(sent), 1)

	def test_email_links_existing_communication_without_copy(self):
		lead = self.lead_of(self.send("m1"))
		comm = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"subject": f"{PREFIX} thư báo giá",
				"content": "<p>nội dung thư</p>",
				"sender": "td106@example.com",
			}
		).insert(ignore_permissions=True)
		inbound = frappe.get_doc(
			{
				"doctype": "Omni Inbound Message",
				"dedupe_key": f"{self.channel.name}:mail1",
				"channel": self.channel.name,
				"channel_type": "Email",
				"event_type": "message",
				"external_message_id": "mail1",
				"external_sender_id": "td106@example.com",
				"identity_key": "Email:td106@example.com",
				"received_at": frappe.utils.now_datetime(),
				"text": "nội dung thư",
				"communication": comm.name,
				"status": "Done",
			}
		).insert(ignore_permissions=True)
		before = frappe.db.count("Communication")
		self.assertEqual(timeline.append(lead, [inbound]), 1)
		self.assertEqual(timeline.append(lead, [inbound]), 1)  # chạy lại: vẫn đúng một Communication
		self.assertEqual(frappe.db.count("Communication"), before)
		self.assertEqual(
			frappe.db.get_value("Communication", comm.name, ["reference_doctype", "reference_name"]),
			("CRM Lead", lead),
		)
		self.assertEqual(
			frappe.db.get_value("Communication", comm.name, "omni_inbound_message"), inbound.name
		)

	def test_append_outbound_once(self):
		lead = self.lead_of(self.send("m1"))
		out = frappe.get_doc(
			{
				"doctype": "Omni Outbound Message",
				"channel": self.channel.name,
				"lead": lead,
				"text": "Dạ báo giá gửi anh",
				"source": "Sale",
				"sent_by": "Administrator",
				"status": "Sent",
				"sent_at": frappe.utils.now_datetime(),
			}
		).insert(ignore_permissions=True)
		c1 = timeline.append_outbound(lead, out.name)
		c2 = timeline.append_outbound(lead, out)
		self.assertTrue(c1)
		self.assertEqual(c1, c2)
		sent = [c for c in self.communications("CRM Lead", lead) if c.sent_or_received == "Sent"]
		self.assertEqual(len(sent), 1)
		self.assertEqual(frappe.db.get_value("Communication", c1, "omni_outbound_message"), out.name)
		self.assertIsNone(timeline.append_outbound(None, out.name))

	def test_append_with_no_target_does_nothing(self):
		self.assertEqual(timeline.append(None, [frappe._dict(name="x")]), 0)
		self.assertEqual(timeline.append("khong-co-lead", [frappe._dict(name="x")]), 0)

	def test_realtime_has_no_message_content(self):
		with mock.patch("frappe.publish_realtime") as pub:
			lead = self.lead_of(self.send("m1", text="bí mật của khách"))
		self.assertTrue(pub.called)
		for call in pub.call_args_list:
			self.assertNotIn("bí mật", json.dumps(call.args + tuple(call.kwargs.values()), default=str))
		rooms = [c for c in pub.call_args_list if c.kwargs.get("docname") == lead]
		self.assertTrue(rooms)  # phòng của chính Lead (Frappe kiểm quyền khi đăng ký)


class TestTrackManualEdits(BridgeTestCase):
	def make_lead(self, **kw) -> str:
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": f"{PREFIX} Lead", **kw})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name

	def manual(self, lead: str) -> list[str]:
		raw = frappe.db.get_value("CRM Lead", lead, "omni_manual_fields")
		return json.loads(raw) if raw else []

	def test_user_edit_is_recorded(self):
		lead = self.make_lead(mobile_no="0903106001")
		self.assertEqual(self.manual(lead), [])  # tạo mới không tính
		doc = frappe.get_doc("CRM Lead", lead)
		doc.mobile_no = "0903106002"
		doc.save(ignore_permissions=True)
		self.assertEqual(self.manual(lead), ["mobile_no"])
		doc = frappe.get_doc("CRM Lead", lead)
		doc.mobile_no = "0903106003"
		doc.first_name = f"{PREFIX} Tên mới"
		doc.save(ignore_permissions=True)
		self.assertEqual(self.manual(lead), ["mobile_no", "first_name"])  # không trùng, giữ thứ tự

	def test_untracked_field_edit_ignored(self):
		lead = self.make_lead()
		doc = frappe.get_doc("CRM Lead", lead)
		doc.job_title = "Giám đốc"
		doc.save(ignore_permissions=True)
		self.assertEqual(self.manual(lead), [])

	def test_system_edit_not_recorded(self):
		lead = self.make_lead(mobile_no="0903106004")
		doc = frappe.get_doc("CRM Lead", lead)
		doc.mobile_no = "0903106005"
		with flags.omni_bot():
			doc.save(ignore_permissions=True)
		self.assertEqual(self.manual(lead), [])
		self.assertFalse(frappe.flags.get("in_omni_bot"))  # cờ được trả về như cũ

	def test_background_job_edit_not_recorded(self):
		lead = self.make_lead(mobile_no="0903106006")
		doc = frappe.get_doc("CRM Lead", lead)
		doc.mobile_no = "0903106007"
		frappe.local.job = frappe._dict(method="x")
		try:
			doc.save(ignore_permissions=True)
		finally:
			frappe.local.job = None
		self.assertEqual(self.manual(lead), [])

	def test_worker_created_lead_has_no_manual_fields(self):
		lead = self.lead_of(self.send("m1"))
		self.assertEqual(self.manual(lead), [])
