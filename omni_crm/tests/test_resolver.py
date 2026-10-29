import threading
from unittest import mock

import frappe
from frappe.utils import now_datetime

from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity import resolver
from omni_crm.ingest.events import InboundEvent
from omni_crm.ingest.gateway import save_inbound
from omni_crm.pipeline.locks import identity_lock
from omni_crm.pipeline.retry import TransientError
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests.fake_adapter import make_event
from omni_crm.tests.identity_helpers import IdentityTestCase, e164, make_msg, phone
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase
from omni_crm.tests.test_locks import _in_thread


def zalo(uid, text=None, profile=None, **kw):
	return make_msg("Zalo OA", f"TD105{uid}", text=text, profile=profile, **kw)


class TestResolverBasics(IdentityTestCase):
	def test_zalo_without_phone_makes_one_lead_then_form_updates_it(self):
		before = self.lead_count()
		r1 = self.resolve(zalo("U1", "xin chào", {"display_name": "Lê Thị Mai"}))
		self.assertTrue(r1.created_lead)
		self.assertEqual(self.lead_count(), before + 1)
		lead = frappe.get_doc("CRM Lead", r1.lead)
		self.assertEqual(lead.lead_name, "Lê Thị Mai")
		self.assertEqual(lead.omni_first_channel, "Zalo OA")
		self.assertFalse(lead.omni_phone_e164)
		# form yêu cầu thông tin có SĐT -> cập nhật Lead đó, không tạo Lead mới
		r2 = self.resolve(
			zalo("U1", None, {"display_name": "Lê Thị Mai", "phone": phone(1)}, event_type="user_info")
		)
		self.assertEqual(r2.lead, r1.lead)
		self.assertFalse(r2.created_lead)
		self.assertEqual(self.lead_count(), before + 1)
		lead.reload()
		self.assertEqual(lead.omni_phone_e164, e164(1))
		self.assertEqual(frappe.db.get_value("Omni Identity", r2.identity, "phone_e164"), e164(1))
		self.assertEqual(frappe.db.get_value("Omni Identity", r2.identity, "lead"), r1.lead)

	def test_default_lead_name_when_channel_gives_no_name(self):
		r = self.resolve(zalo("U1234"))
		self.assertEqual(frappe.db.get_value("CRM Lead", r.lead, "lead_name"), "Khách Zalo OA 1234")

	def test_email_signature_phone_attaches_to_existing_lead(self):
		r1 = self.resolve(
			zalo("U1", None, {"display_name": "Lê Thị Mai", "phone": phone(2)}, event_type="user_info")
		)
		before = self.lead_count()
		sig = "Trân trọng,\nLê Thị Mai\nMobile: 0903.105.002\n"
		r2 = self.resolve(
			make_msg("Email", "mai@td105.example", sig, {"display_name": "Lê Thị Mai"}),
		)
		self.assertEqual(r2.lead, r1.lead)
		self.assertEqual(self.lead_count(), before)

	def test_two_leads_same_phone_attach_latest_plus_one_candidate(self):
		old = self.make_lead("TD105 Cu", mobile=phone(3))
		new = self.make_lead("TD105 Moi", mobile="+84 " + phone(3)[1:])
		frappe.db.set_value("CRM Lead", old, "omni_last_inbound_at", self.ago(days=5))
		frappe.db.set_value("CRM Lead", new, "omni_last_inbound_at", self.ago(days=1))
		frappe.db.commit()
		self.assertEqual(frappe.db.get_value("CRM Lead", new, "omni_phone_e164"), e164(3))
		r = self.resolve(make_msg("Telephony", phone(3), "cuộc gọi"))
		self.assertEqual(r.lead, new)
		self.assertEqual(len(self.candidates(new)), 1)
		self.assertEqual(self.candidates(new)[0]["status"], "Open")
		r = self.resolve(make_msg("Telephony", phone(3), "gọi lần nữa"))
		self.assertEqual(len(self.candidates(new)), 1)  # không tạo trùng cặp

	def test_mention_other_persons_phone_creates_new_lead_and_candidate_not_merge(self):
		nam = self.make_lead("Phạm Quốc Nam", mobile=phone(4))
		before = self.lead_count()
		r = self.resolve(
			zalo("U1", f"liên hệ anh Nam {phone(4)} nhé", {"display_name": "Trần Minh Hoa"}),
		)
		self.assertNotEqual(r.lead, nam)
		self.assertTrue(r.created_lead)
		self.assertEqual(self.lead_count(), before + 1)
		cands = self.candidates(nam)
		self.assertEqual(len(cands), 1)
		self.assertEqual({cands[0]["lead_a"], cands[0]["lead_b"]}, {nam, r.lead})
		# Lead mới không mượn SĐT của người khác; Lead anh Nam không bị đụng
		self.assertFalse(frappe.db.get_value("CRM Lead", r.lead, "omni_phone_e164"))
		self.assertEqual(frappe.db.get_value("CRM Lead", nam, "omni_merged_into"), None)
		self.assertEqual(frappe.db.get_value("Omni Identity", r.identity, "lead"), r.lead)

	def test_medium_signal_with_compatible_name_attaches(self):
		nam = self.make_lead("Phạm Quốc Nam", mobile=phone(5))
		r = self.resolve(zalo("U1", f"số em {phone(5)}", {"display_name": "Quốc Nam Phạm"}))
		self.assertEqual(r.lead, nam)
		self.assertEqual(r.matched_by, "medium")

	def test_medium_signal_with_placeholder_sender_name_attaches_per_spec(self):
		# ĐANG MỞ (docs/12 mục D, mức Cao): theo docs/04 mục 3 bước 5 + 3.1, tên không có thông tin thì
		# "không mâu thuẫn" nên khách ẩn danh nhắc số của người khác bị gắn vào Lead của người đó.
		# Test này ghim hành vi hiện tại; nếu người duyệt đổi quyết định, đổi test này cùng lúc.
		nam = self.make_lead("Phạm Quốc Nam", mobile=phone(17))
		for uid, profile in (
			("U1", None),
			("U2", {"display_name": "Khách"}),
			("U3", {"display_name": "Zalo user"}),
		):
			r = self.resolve(zalo(uid, f"liên hệ anh Nam {phone(17)}", profile))
			self.assertEqual((r.lead, r.matched_by), (nam, "medium"))

	def test_medium_signal_ambiguous_between_two_leads_never_attaches(self):
		a = self.make_lead("Phạm Quốc Nam", mobile=phone(6))
		b = self.make_lead("Lê Văn Hải", mobile=phone(6))
		r = self.resolve(zalo("U1", f"gọi {phone(6)}", {"display_name": "Quốc Nam Phạm"}))
		self.assertTrue(r.created_lead)
		self.assertNotIn(r.lead, (a, b))
		self.assertEqual(len(self.candidates(r.lead)), 2)

	def test_name_alone_never_attaches_only_candidate(self):
		old = self.make_lead("Nguyễn Văn Hùng", mobile=phone(7))
		r = self.resolve(zalo("U1", "alo", {"display_name": "nguyen van hung"}))
		self.assertTrue(r.created_lead)
		self.assertNotEqual(r.lead, old)
		cands = self.candidates(old)
		self.assertEqual(len(cands), 1)
		self.assertGreaterEqual(cands[0]["score"], 90)
		self.assertEqual(cands[0]["status"], "Open")

	def test_one_word_or_generic_names_do_not_create_candidates(self):
		old = self.make_lead("Nam", mobile=phone(8))
		r1 = self.resolve(zalo("U1", "alo", {"display_name": "Nam"}))
		r2 = self.resolve(zalo("U2", "alo", {"display_name": "Anh Nam"}))
		r3 = self.resolve(zalo("U3", "alo"))
		r4 = self.resolve(zalo("U4", "alo"))
		for r in (r1, r2, r3, r4):
			self.assertEqual(self.candidates(r.lead), [])
		self.assertEqual(self.candidates(old), [])

	def test_name_candidate_ignores_leads_older_than_90_days(self):
		old = self.make_lead("Nguyễn Văn Hùng", mobile=phone(9))
		frappe.db.set_value("CRM Lead", old, "creation", self.ago(days=120), update_modified=False)
		frappe.db.commit()
		r = self.resolve(zalo("U1", "alo", {"display_name": "Nguyễn Văn Hùng"}))
		self.assertEqual(self.candidates(r.lead), [])

	def test_same_message_twice_is_one_lead_and_no_duplicate_candidates(self):
		msg = zalo("U1", "xin chào", {"display_name": "Lê Thị Mai"})
		before = self.lead_count()
		r1 = self.resolve(msg)
		r2 = self.resolve(msg)
		self.assertEqual(r1.lead, r2.lead)
		self.assertEqual(r1.identity, r2.identity)
		self.assertEqual(self.lead_count(), before + 1)
		self.assertEqual(frappe.db.count("Omni Identity", {"identity_key": msg.identity_key}), 1)

	def test_merged_lead_routes_to_primary(self):
		r1 = self.resolve(zalo("U1", "a", {"display_name": "Lê Thị Mai"}))
		primary = self.make_lead("TD105 Chinh")
		frappe.db.set_value("CRM Lead", r1.lead, "omni_merged_into", primary)
		frappe.db.commit()
		r2 = self.resolve(zalo("U1", "b"))
		self.assertEqual(r2.lead, primary)
		self.assertEqual(frappe.db.get_value("Omni Identity", r2.identity, "lead"), primary)
		self.assertEqual(r2.matched_by, "identity")

	def test_merged_lead_not_matched_by_phone_but_primary_is(self):
		secondary = self.make_lead("TD105 Phu", mobile=phone(10))
		primary = self.make_lead("TD105 Chinh", mobile=phone(10))
		frappe.db.set_value("CRM Lead", secondary, "omni_merged_into", primary)
		frappe.db.commit()
		r = self.resolve(make_msg("Telephony", phone(10)))
		self.assertEqual(r.lead, primary)
		self.assertEqual(self.candidates(primary), [])  # lead đã gộp không sinh nghi trùng

	def test_system_email_is_not_a_signal(self):
		a = self.resolve(make_msg("Email", "no-reply@td105.example", "thông báo"))
		b = self.resolve(make_msg("Email", "noreply@td105.example", "thông báo"))
		self.assertNotEqual(a.lead, b.lead)
		self.assertFalse(frappe.db.get_value("CRM Lead", a.lead, "email"))

	def test_strong_email_matches_lead_with_different_case(self):
		lead = self.make_lead("TD105 Mai", email="Mai.Le+x@TD105.example")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "omni_email_norm"), "mai.le+x@td105.example")
		r = self.resolve(make_msg("Email", " MAI.LE+x@td105.example "))
		self.assertEqual(r.lead, lead)
		other = self.resolve(make_msg("Email", "mai.le@td105.example"))  # không có +tag: người khác
		self.assertNotEqual(other.lead, lead)

	def test_identity_linked_lead_but_phone_held_by_other_lead_flags_candidate(self):
		other = self.make_lead("TD105 Khac", mobile=phone(11))
		r1 = self.resolve(zalo("U1", "a", {"display_name": "Vũ Thị Lan"}))
		self.assertNotEqual(r1.lead, other)
		r2 = self.resolve(zalo("U1", None, {"phone": phone(11)}, event_type="user_info"))
		self.assertEqual(r2.lead, r1.lead)  # không tự gắn/gộp sang Lead khác
		self.assertEqual(len(self.candidates(other)), 1)

	def test_signals_split(self):
		sig = resolver.collect_signals(
			[make_msg("Zalo OA", "TD105U1", f"mình là Lan, bạn mình {phone(12)}", {"phone": phone(13)})]
		)
		self.assertEqual(sig.strong_phones, [e164(13)])
		self.assertEqual(sig.medium_phones, [e164(12)])
		sig = resolver.collect_signals([make_msg("Zalo OA", "TD105U1", f"{phone(13)}", {"phone": phone(13)})])
		self.assertEqual(sig.medium_phones, [])  # đã là tín hiệu mạnh thì không tính lại là trung bình


class TestDealAndCreate(IdentityTestCase):
	def test_converted_lead_returns_deal_info(self):
		lead = self.make_lead("TD105 Deal", mobile=phone(14))
		frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"lead": lead,
				"organization": None,
				"mobile_no": phone(14),
				"first_name": "x",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("CRM Lead", lead, "converted", 1)
		frappe.db.commit()
		deal = frappe.db.get_value("CRM Deal", {"lead": lead}, "name")
		r = self.resolve(make_msg("Telephony", phone(14)))
		self.assertEqual(r.lead, lead)  # vẫn gắn Lead cũ
		self.assertEqual(r.deal, deal)
		self.assertIsNone(self.resolve(make_msg("Telephony", phone(1))).deal)

	def test_create_lead_for_identity_minimal(self):
		ident = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": "Zalo OA:TD105U55",
				"channel_type": "Zalo OA",
				"external_id": "TD105U55",
			}
		).insert(ignore_permissions=True)
		name = lead_bridge.create_lead_for_identity(
			ident.name, {"phone": e164(15), "email": "a@td105.example"}
		)
		doc = frappe.get_doc("CRM Lead", name)
		self.assertEqual(doc.lead_name, "Khách Zalo OA 5U55")  # 4 ký tự cuối của mã
		self.assertEqual(doc.omni_phone_e164, e164(15))
		self.assertEqual(doc.omni_email_norm, "a@td105.example")
		self.assertEqual(doc.omni_first_channel, "Zalo OA")
		self.assertEqual(doc.status, "New")
		frappe.db.commit()

	def test_default_names_are_placeholders_for_all_channels(self):
		from omni_crm.identity.normalize import is_informative_name, is_placeholder_name

		for ct, ext in (
			("Zalo OA", "2973123456789"),
			("Email", "an@gmail.com"),
			("Telephony", "+84903105001"),
		):
			name = lead_bridge.default_lead_name(ct, ext)
			self.assertTrue(is_placeholder_name(name), name)
			self.assertFalse(is_informative_name(name), name)

	def test_nameless_email_leads_do_not_flag_each_other(self):
		a = self.resolve(make_msg("Email", "an@td105.example", "xin chào"))
		b = self.resolve(make_msg("Email", "binh@td105.example", "xin chào"))
		self.assertNotEqual(a.lead, b.lead)
		self.assertEqual(self.candidates(a.lead), [])
		self.assertEqual(self.candidates(b.lead), [])

	def test_new_lead_with_email_does_not_call_gravatar_and_sets_email(self):
		ident = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": "Email:x@td105.example",
				"channel_type": "Email",
				"external_id": "x@td105.example",
			}
		).insert(ignore_permissions=True)
		with mock.patch("crm.fcrm.doctype.crm_lead.crm_lead.has_gravatar") as gr:
			name = lead_bridge.create_lead_for_identity(ident.name, {"email": "x@td105.example"})
		gr.assert_not_called()
		self.assertEqual(
			frappe.db.get_value("CRM Lead", name, ["email", "omni_email_norm"], as_dict=True),
			{"email": "x@td105.example", "omni_email_norm": "x@td105.example"},
		)
		frappe.db.commit()

	def test_lead_phone_field_accepts_dotted_number(self):
		lead = self.make_lead("TD105 Cham", mobile="0912.345.678")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "omni_phone_e164"), "+84912345678")

	def test_compute_normalized_fields_prefers_mobile(self):
		lead = self.make_lead("TD105 Mobile", mobile="024 3123 4567")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "omni_phone_e164"), "+842431234567")
		doc = frappe.get_doc("CRM Lead", lead)
		doc.phone = phone(16)
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.omni_phone_e164, e164(16))  # di động được ưu tiên hơn số bàn ở mobile_no
		doc.phone = None
		doc.mobile_no = "12345"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.omni_phone_e164, "")


class TestResolverConcurrency(IdentityTestCase):
	def test_two_identities_same_phone_in_parallel_produce_one_lead(self):
		barrier = threading.Barrier(2)
		real = lead_bridge.create_lead_for_identity

		def slow_create(identity, signals):
			import time

			time.sleep(1)  # nới cửa sổ tranh chấp: nếu không có khóa, lượt kia sẽ tạo Lead thứ hai
			return real(identity, signals)

		def run(msg):
			def go():
				barrier.wait(10)
				return resolver.resolve_full([msg])

			return go

		m1 = make_msg("Telephony", phone(20), "alo")
		m2 = make_msg("Zalo OA", "TD105U1", None, {"phone": "+84 " + phone(20)[1:]}, event_type="user_info")
		# Vá một lần ở thread chính (vá/hoàn tác từ hai thread sẽ giẫm lên nhau)
		with mock.patch.object(lead_bridge, "create_lead_for_identity", slow_create):
			t1, r1 = _in_thread(run(m1))
			t2, r2 = _in_thread(run(m2))
			t1.join(30)
			t2.join(30)
		self.assertNotIn("error", r1, r1.get("error"))
		self.assertNotIn("error", r2, r2.get("error"))
		self.assertEqual(r1["value"].lead, r2["value"].lead)
		frappe.db.rollback()
		self.assertEqual(frappe.db.count("CRM Lead", {"omni_phone_e164": e164(20)}), 1)
		self.assertEqual(self.candidates(r1["value"].lead), [])
		self.assertEqual({r1["value"].created_lead, r2["value"].created_lead}, {True, False})

	def test_busy_signal_lock_is_transient(self):
		with identity_lock(f"sig:{e164(21)}", wait=1):
			with mock.patch.object(resolver, "SIGNAL_LOCK_WAIT", 1):
				t, res = _in_thread(lambda: resolver.resolve_full([make_msg("Telephony", phone(21))]))
				t.join(10)
		self.assertIsInstance(res.get("error"), TransientError)
		self.assertEqual(frappe.db.count("CRM Lead", {"omni_phone_e164": e164(21)}), 0)


class TestLeadPatch(IdentityTestCase):
	def test_patch_fills_old_leads_and_is_rerunnable(self):
		from omni_crm.patches import compute_lead_normalized_fields as patch

		a = self.make_lead("TD105 Cu 1", mobile="0903 105 050", email="Cu1@TD105.example")
		b = self.make_lead("TD105 Cu 2", mobile="024 3123 4567")
		# giả lập Lead cũ chưa có trường chuẩn hóa / có giá trị sai
		frappe.db.set_value(
			"CRM Lead", a, {"omni_phone_e164": None, "omni_email_norm": None}, update_modified=False
		)
		frappe.db.set_value(
			"CRM Lead", b, {"omni_phone_e164": "+84000", "omni_email_norm": "x"}, update_modified=False
		)
		frappe.db.commit()
		patch.execute()
		patch.execute()  # chạy lại lần hai không lỗi
		self.assertEqual(frappe.db.get_value("CRM Lead", a, "omni_phone_e164"), e164(50))
		self.assertEqual(frappe.db.get_value("CRM Lead", a, "omni_email_norm"), "cu1@td105.example")
		self.assertEqual(frappe.db.get_value("CRM Lead", b, "omni_phone_e164"), "+842431234567")
		self.assertFalse(frappe.db.get_value("CRM Lead", b, "omni_email_norm"))
		modified = frappe.db.get_value("CRM Lead", a, "modified")
		patch.execute()
		self.assertEqual(frappe.db.get_value("CRM Lead", a, "modified"), modified)  # không ghi khi không đổi


class TestWorkerEndToEnd(PipelineTestCase):
	def test_zalo_then_user_info_through_worker_one_lead(self):
		with mock.patch("frappe.enqueue"):
			m1 = save_inbound(
				make_event(
					self.channel.name,
					"e1",
					sender=f"{PREFIX}U9",
					sender_profile={"display_name": "Vũ Thị Lan"},
				)
			)
			frappe.db.after_commit.run()
		frappe.db.commit()
		key = f"Zalo OA:{PREFIX}U9"
		process_identity(key, 0)
		lead = frappe.db.get_value("Omni Inbound Message", m1, "lead")
		self.assertTrue(lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_name"), "Vũ Thị Lan")
		with mock.patch("frappe.enqueue"):
			m2 = save_inbound(
				InboundEvent(
					channel=self.channel.name,
					channel_type="Zalo OA",
					event_type="user_info",
					external_message_id="e2",
					external_sender_id=f"{PREFIX}U9",
					received_at=now_datetime(),
					sender_profile={"display_name": "Vũ Thị Lan", "phone": "0903 105 060"},
				)
			)
			frappe.db.after_commit.run()
		frappe.db.commit()
		process_identity(key, 0)
		self.assertEqual(frappe.db.get_value("Omni Inbound Message", m2, "lead"), lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "omni_phone_e164"), e164(60))
		self.assertEqual(frappe.db.count("CRM Lead", {"lead_name": "Vũ Thị Lan"}), 1)
