from unittest import mock

import frappe

from omni_crm.identity import duplicates, merge
from omni_crm.ingest.gateway import save_inbound
from omni_crm.tests.fake_adapter import make_channel, make_event
from omni_crm.tests.identity_helpers import IdentityTestCase, e164, phone


class TestMergeLeads(IdentityTestCase):
	def setUp(self):
		super().setUp()
		enq = mock.patch("frappe.enqueue")  # hook Comment của CRM đẩy job nền; không cần Redis
		enq.start()
		self.addCleanup(enq.stop)
		self.channel = make_channel("TD105 kenh", enabled=1)
		self.p = self.make_lead("TD105 Chinh", mobile=phone(30))
		self.s = self.make_lead(
			"TD105 Phu", mobile=phone(31), email="phu@td105.example", organization="Cty Phu", job_title="GD"
		)
		self.third = self.make_lead("TD105 Ba", mobile=phone(32))
		self._seed()
		frappe.db.commit()

	def _seed(self):
		s = self.s
		self.identity = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": "Zalo OA:TD105U1",
				"channel_type": "Zalo OA",
				"external_id": "TD105U1",
				"lead": s,
			}
		).insert(ignore_permissions=True)
		with mock.patch("frappe.enqueue"):
			self.inbound = save_inbound(make_event(self.channel.name, "m1", sender="TD105U1"))
			frappe.db.after_commit.run()
		frappe.db.set_value("Omni Inbound Message", self.inbound, {"lead": s, "identity": self.identity.name})
		self.outbound = (
			frappe.get_doc({"doctype": "Omni Outbound Message", "lead": s, "channel": self.channel.name})
			.insert(ignore_permissions=True)
			.name
		)
		self.comm = (
			frappe.get_doc(
				{
					"doctype": "Communication",
					"communication_type": "Communication",
					"communication_medium": "Chat",
					"sent_or_received": "Received",
					"subject": "TD105 tin",
					"content": "nội dung",
					"reference_doctype": "CRM Lead",
					"reference_name": s,
					"timeline_links": [{"link_doctype": "CRM Lead", "link_name": s}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.comment = frappe.get_doc("CRM Lead", s).add_comment("Comment", "ghi chú cũ").name
		self.call = (
			frappe.get_doc(
				{
					"doctype": "CRM Call Log",
					"id": "TD105-call-1",
					"from": "a",
					"to": "b",
					"type": "Incoming",
					"status": "Completed",
					"reference_doctype": "CRM Lead",
					"reference_docname": s,
					"links": [{"link_doctype": "CRM Lead", "link_name": s}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.task = (
			frappe.get_doc(
				{
					"doctype": "CRM Task",
					"title": "TD105 gọi lại",
					"reference_doctype": "CRM Lead",
					"reference_docname": s,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.note = (
			frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": "TD105 ghi chú",
					"content": "x",
					"reference_doctype": "CRM Lead",
					"reference_docname": s,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.cand_main = duplicates.create_candidate(self.p, s, "cùng SĐT", 90)
		self.cand_other = duplicates.create_candidate(s, self.third, "cùng tên", 95)

	def _refs(self, lead):
		g = frappe.db.get_value
		return {
			"identity": g("Omni Identity", self.identity.name, "lead") == lead,
			"inbound": g("Omni Inbound Message", self.inbound, "lead") == lead,
			"outbound": g("Omni Outbound Message", self.outbound, "lead") == lead,
			"communication": g("Communication", self.comm, "reference_name") == lead,
			"comm_link": frappe.db.exists("Communication Link", {"parent": self.comm, "link_name": lead})
			is not None,
			"comment": g("Comment", self.comment, "reference_name") == lead,
			"call": g("CRM Call Log", self.call, "reference_docname") == lead,
			"call_link": frappe.db.exists("Dynamic Link", {"parent": self.call, "link_name": lead})
			is not None,
			"task": g("CRM Task", self.task, "reference_docname") == lead,
			"note": g("FCRM Note", self.note, "reference_docname") == lead,
		}

	def test_all_references_move_and_secondary_is_kept(self):
		self.assertTrue(all(self._refs(self.s).values()), self._refs(self.s))
		result = merge.merge_leads(self.p, self.s, "Administrator", candidate=self.cand_main)
		frappe.db.commit()
		self.assertTrue(all(self._refs(self.p).values()), self._refs(self.p))
		self.assertFalse(result["already_merged"])
		# Lead phụ còn nguyên, bị đánh dấu
		s = frappe.get_doc("CRM Lead", self.s)
		self.assertEqual(s.omni_merged_into, self.p)
		self.assertEqual(s.status, "Junk")
		# Lead phụ vẫn lưu lại được (Junk thuộc nhóm Lost: CRM đòi lost_reason khi validate)
		try:
			s.save(ignore_permissions=True)
			saved = True
		except frappe.ValidationError:
			saved = False
		self.assertTrue(saved, "Lead phụ không lưu được sau khi gộp (thiếu lost_reason?)")
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "CRM Lead",
					"reference_name": self.s,
					"content": ["like", "%Đã gộp vào%"],
				},
			)
		)

	def test_blank_fields_filled_and_differences_recorded(self):
		merge.merge_leads(self.p, self.s, "Administrator")
		p = frappe.get_doc("CRM Lead", self.p)
		self.assertEqual(p.email, "phu@td105.example")
		self.assertEqual(p.organization, "Cty Phu")
		self.assertEqual(p.job_title, "GD")
		self.assertEqual(p.omni_email_norm, "phu@td105.example")
		self.assertEqual(p.mobile_no, phone(30))  # đã có: giữ nguyên
		self.assertEqual(p.omni_phone_e164, e164(30))
		note = frappe.db.get_value(
			"Comment",
			{
				"reference_doctype": "CRM Lead",
				"reference_name": self.p,
				"content": ["like", "%Khác biệt mobile_no%"],
			},
			"content",
		)
		self.assertIn(phone(31), note)

	def test_candidates_updated(self):
		merge.merge_leads(self.p, self.s, "Administrator", candidate=self.cand_main)
		g = frappe.db.get_value
		self.assertEqual(g("Omni Duplicate Candidate", self.cand_main, "status"), "Merged")
		self.assertEqual(g("Omni Duplicate Candidate", self.cand_main, "decided_by"), "Administrator")
		# cặp (phụ, ba) được trỏ lại thành (chính, ba)
		a, b = sorted((self.p, self.third))
		self.assertEqual(
			g("Omni Duplicate Candidate", self.cand_other, ["lead_a", "lead_b", "status"], as_dict=True),
			{"lead_a": a, "lead_b": b, "status": "Open"},
		)

	def test_duplicate_pair_after_repoint_is_closed(self):
		pre = duplicates.create_candidate(self.p, self.third, "đã có sẵn", 70)
		merge.merge_leads(self.p, self.s, "Administrator")
		self.assertEqual(
			frappe.db.get_value("Omni Duplicate Candidate", self.cand_other, "status"), "Dismissed"
		)
		self.assertEqual(frappe.db.get_value("Omni Duplicate Candidate", pre, "status"), "Open")

	def test_second_run_is_noop_without_error(self):
		first = merge.merge_leads(self.p, self.s, "Administrator", candidate=self.cand_main)
		frappe.db.commit()
		comments = frappe.db.count("Comment", {"reference_name": ["in", [self.p, self.s]]})
		second = merge.merge_leads(self.p, self.s, "Administrator", candidate=self.cand_main)
		self.assertTrue(second["already_merged"])
		self.assertTrue(first["moved"])
		self.assertEqual(frappe.db.count("Comment", {"reference_name": ["in", [self.p, self.s]]}), comments)

	def test_cannot_merge_into_merged_lead_or_self(self):
		merge.merge_leads(self.p, self.s, "Administrator")
		with self.assertRaises(frappe.ValidationError):
			merge.merge_leads(self.s, self.third, "Administrator")  # chính đã bị gộp
		with self.assertRaises(frappe.ValidationError):
			merge.merge_leads(self.third, self.s, "Administrator")  # phụ đã gộp vào lead khác
		with self.assertRaises(frappe.ValidationError):
			merge.merge_leads(self.p, self.p, "Administrator")

	def test_requires_write_permission_on_both(self):
		def only_primary(doctype, ptype, doc=None, user=None, **kw):
			return doc == self.p

		with mock.patch.object(merge.frappe, "has_permission", side_effect=only_primary):
			with self.assertRaises(frappe.PermissionError):
				merge.merge_leads(self.p, self.s, "Administrator")
		self.assertTrue(all(self._refs(self.s).values()))

	def test_failure_midway_rolls_back_everything(self):
		with mock.patch.object(merge, "_close_related_candidates", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				merge.merge_leads(self.p, self.s, "Administrator")
		self.assertTrue(all(self._refs(self.s).values()), self._refs(self.s))
		self.assertIsNone(frappe.db.get_value("CRM Lead", self.s, "omni_merged_into"))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.p, "email"), None)
		self.assertEqual(frappe.db.get_value("Omni Duplicate Candidate", self.cand_main, "status"), "Open")

	def test_resolver_follows_merge_afterwards(self):
		from omni_crm.tests.identity_helpers import make_msg

		merge.merge_leads(self.p, self.s, "Administrator")
		frappe.db.commit()
		r = self.resolve(make_msg("Zalo OA", "TD105U1", "tin mới"))
		self.assertEqual(r.lead, self.p)

	def test_every_link_to_crm_lead_in_this_app_is_covered(self):
		"""Nhắc bổ sung vào merge.OMNI_LEAD_LINKS khi thêm DocType có Link tới CRM Lead."""
		rows = frappe.db.sql(
			"""select f.parent, f.fieldname from `tabDocField` f join `tabDocType` d on d.name = f.parent
			where d.module = 'Omni CRM' and f.fieldtype = 'Link' and f.options = 'CRM Lead'""",
		)
		covered = set(merge.OMNI_LEAD_LINKS)
		for parent, fieldname in rows:
			if parent == "Omni Duplicate Candidate":
				continue  # xử lý riêng bằng _close_related_candidates
			self.assertIn((parent, fieldname), covered)


class TestMergeTodoDealAndRace(IdentityTestCase):
	def setUp(self):
		super().setUp()
		enq = mock.patch("frappe.enqueue")
		enq.start()
		self.addCleanup(enq.stop)
		self.p = self.make_lead("TD105 Chinh", mobile=phone(70))
		self.s = self.make_lead("TD105 Phu", mobile=phone(71))

	def test_todo_moves_to_primary(self):
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"reference_type": "CRM Lead",
				"reference_name": self.s,
				"allocated_to": "Administrator",
				"description": "TD105 gọi lại",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: (frappe.db.delete("ToDo", {"name": todo.name}), frappe.db.commit()))
		merge.merge_leads(self.p, self.s, "Administrator")
		row = frappe.db.get_value("ToDo", todo.name, ["reference_type", "reference_name"], as_dict=True)
		self.assertEqual((row.reference_type, row.reference_name), ("CRM Lead", self.p))

	def test_deal_of_secondary_is_found_from_primary(self):
		from omni_crm.crm_bridge.lead import get_deal_for_lead

		frappe.get_doc({"doctype": "CRM Deal", "lead": self.s, "first_name": "x"}).insert(
			ignore_permissions=True, ignore_mandatory=True
		)
		frappe.db.set_value("CRM Lead", self.s, "converted", 1)
		deal = frappe.db.get_value("CRM Deal", {"lead": self.s}, "name")
		self.assertIsNone(get_deal_for_lead(self.p))
		merge.merge_leads(self.p, self.s, "Administrator")
		self.assertEqual(get_deal_for_lead(self.p), deal)  # tin mới của khách đi về Deal cũ
		self.assertEqual(frappe.db.get_value("CRM Deal", deal, "lead"), self.s)  # Deal không bị sửa

	def test_opposite_concurrent_merges_never_make_a_cycle(self):
		import threading
		import time

		from omni_crm.tests.test_locks import _in_thread

		barrier = threading.Barrier(2)
		real = merge._move_references

		def slow(primary, secondary):
			time.sleep(1)  # nới cửa sổ tranh chấp
			return real(primary, secondary)

		def run(primary, secondary):
			def go():
				barrier.wait(10)
				try:
					return merge.merge_leads(primary, secondary, "Administrator")
				finally:
					frappe.db.commit()

			return go

		frappe.db.commit()
		with mock.patch.object(merge, "_move_references", slow):
			t1, r1 = _in_thread(run(self.p, self.s))
			t2, r2 = _in_thread(run(self.s, self.p))
			t1.join(60)
			t2.join(60)
		frappe.db.rollback()
		into = {n: frappe.db.get_value("CRM Lead", n, "omni_merged_into") for n in (self.p, self.s)}
		self.assertEqual(sum(1 for v in into.values() if v), 1, into)  # đúng một Lead bị gộp, không vòng
		errors = [r.get("error") for r in (r1, r2) if r.get("error")]
		self.assertEqual(len(errors), 1)
		self.assertIsInstance(errors[0], frappe.ValidationError)


class TestCandidateApi(IdentityTestCase):
	def setUp(self):
		super().setUp()
		enq = mock.patch("frappe.enqueue")
		enq.start()
		self.addCleanup(enq.stop)
		self.a = self.make_lead("TD105 A", mobile=phone(40))
		self.b = self.make_lead("TD105 B", mobile=phone(41))

	def test_create_candidate_orders_pair_and_skips_existing(self):
		name = duplicates.create_candidate(self.b, self.a, "x", 80)
		doc = frappe.get_doc("Omni Duplicate Candidate", name)
		self.assertEqual((doc.lead_a, doc.lead_b), tuple(sorted((self.a, self.b))))
		self.assertIsNone(duplicates.create_candidate(self.a, self.b, "lại", 80))
		self.assertIsNone(duplicates.create_candidate(self.a, self.a, "tự", 80))

	def test_dismissed_pair_is_not_recreated(self):
		name = duplicates.create_candidate(self.a, self.b, "x", 80)
		duplicates.dismiss(name)
		self.assertEqual(frappe.db.get_value("Omni Duplicate Candidate", name, "status"), "Dismissed")
		self.assertIsNone(duplicates.create_candidate(self.a, self.b, "lại", 80))

	def test_merge_api_validates_primary_and_status(self):
		name = duplicates.create_candidate(self.a, self.b, "x", 80)
		with self.assertRaises(frappe.ValidationError):
			duplicates.merge(name, "CRM-LEAD-KHONG-CO")
		out = duplicates.merge(name, self.a)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.b, "omni_merged_into"), self.a)
		self.assertEqual(frappe.db.get_value("Omni Duplicate Candidate", name, "status"), "Merged")
		self.assertFalse(out["already_merged"])
		again = duplicates.merge(name, self.a)  # lần hai không lỗi
		self.assertTrue(again["already_merged"])

	def test_merge_api_refuses_dismissed(self):
		name = duplicates.create_candidate(self.a, self.b, "x", 80)
		duplicates.dismiss(name)
		with self.assertRaises(frappe.ValidationError):
			duplicates.merge(name, self.a)

	def test_api_checks_write_permission_on_both_leads(self):
		name = duplicates.create_candidate(self.a, self.b, "x", 80)
		real = frappe.has_permission

		def deny_b(doctype, ptype=None, doc=None, **kw):
			if doctype == "CRM Lead" and doc == self.b:
				return False
			return real(doctype, ptype, doc, **kw)

		with mock.patch.object(duplicates.frappe, "has_permission", side_effect=deny_b):
			with self.assertRaises(frappe.PermissionError):
				duplicates.dismiss(name)
			with self.assertRaises(frappe.PermissionError):
				duplicates.merge(name, self.a)
		self.assertIsNone(frappe.db.get_value("CRM Lead", self.b, "omni_merged_into"))
