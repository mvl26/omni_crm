"""V21-04 (G-V21-03-1): tạo CRM Lead Source lần đầu chịu đua giữa hai worker."""

import threading

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.crm_bridge.lead import get_or_create_lead_source
from omni_crm.tests.test_locks import _in_thread

SOURCE = "TD2104 nguon dua"


def _drop() -> None:
	frappe.db.rollback()
	frappe.db.delete("CRM Lead", {"first_name": ["like", "TD2104%"]})
	frappe.db.delete("CRM Lead Source", {"name": SOURCE})
	frappe.db.commit()


class TestLeadSourceRace(FrappeTestCase):
	def setUp(self):
		_drop()
		self.addCleanup(_drop)

	def _rows(self) -> int:
		frappe.db.rollback()
		return frappe.db.count("CRM Lead Source", {"name": SOURCE})

	def test_creates_when_missing_and_reuses_when_present(self):
		self.assertEqual(get_or_create_lead_source(SOURCE), SOURCE)
		frappe.db.commit()
		self.assertEqual(get_or_create_lead_source(SOURCE), SOURCE)
		self.assertEqual(self._rows(), 1)

	def test_loser_of_the_race_reads_the_winner_and_can_use_it_in_the_same_transaction(self):
		"""Ảnh chụp REPEATABLE READ cũ: `exists` báo chưa có, insert trùng khóa, vẫn phải dùng được cho Lead ngay sau đó."""
		frappe.db.sql("select count(*) from `tabCRM Lead Source`")  # mở ảnh chụp trước khi bên kia tạo
		t, res = _in_thread(lambda: (get_or_create_lead_source(SOURCE), frappe.db.commit())[0])
		t.join(20)
		self.assertEqual(res.get("value"), SOURCE, res.get("error"))
		self.assertEqual(
			get_or_create_lead_source(SOURCE), SOURCE
		)  # trước khi sửa: DuplicateEntry/IntegrityError
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD2104 dua", "source": SOURCE})
		lead.flags.ignore_permissions = True
		lead.insert()  # kiểm liên kết `source` phải thấy bản ghi
		self.assertEqual(lead.source, SOURCE)
		frappe.db.rollback()
		self.assertEqual(self._rows(), 1)

	def test_six_workers_at_once_give_one_row_and_no_error(self):
		barrier = threading.Barrier(6)

		def work():
			frappe.db.sql("select count(*) from `tabCRM Lead Source`")
			barrier.wait(10)
			name = get_or_create_lead_source(SOURCE)
			frappe.db.commit()
			return name

		runs = [_in_thread(work) for _ in range(6)]
		for t, _ in runs:
			t.join(30)
		self.assertEqual([r.get("value") for _, r in runs], [SOURCE] * 6, [r.get("error") for _, r in runs])
		self.assertEqual(self._rows(), 1)
