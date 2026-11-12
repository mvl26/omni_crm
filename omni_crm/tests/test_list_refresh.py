"""Danh sách Lead CRM tự tải lại: khóa đệm khớp giao diện CRM, gộp lần phát, phát bù, Lead thật kích hoạt hook."""

import json
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.crm_bridge import list_refresh as lr


def _clear() -> None:
	c = frappe.cache()
	c.delete(c.make_key(lr.THROTTLE_KEY), c.make_key(lr.PENDING_KEY))


class TestListRefresh(FrappeTestCase):
	def setUp(self):
		_clear()
		self.addCleanup(_clear)
		p = mock.patch.object(lr.frappe, "publish_realtime")
		self.pub = p.start()
		self.addCleanup(p.stop)

	def keys_published(self) -> list[str]:
		# Tạo Lead thật còn phát các sự kiện khác (patch là toàn cục): chỉ lấy refetch_resource.
		return [json.dumps(c.args[1]["cache_key"]) for c in self.pub.call_args_list if c.args[0] == lr.EVENT]

	def test_cache_keys_khop_giao_dien_crm(self):
		# Giao diện băm khóa bằng JSON.stringify([doctype, query.view, params.viewType]); undefined -> null.
		with mock.patch.object(
			lr.frappe, "get_all", return_value=[frappe._dict(name="vw-1", type="kanban")]
		) as ga:
			keys = [json.dumps(k) for k in lr.cache_keys()]
		ga.assert_called_once()
		self.assertEqual(ga.call_args.kwargs["filters"], {"dt": "CRM Lead"})
		self.assertIn('["CRM Lead", null, null]', keys)
		self.assertIn('["CRM Lead", null, "list"]', keys)
		self.assertIn('["CRM Lead", "vw-1", "kanban"]', keys)

	def test_lan_dau_phat_sau_commit_lan_sau_bi_gop(self):
		lr.on_lead_insert(None)
		n = len(lr.cache_keys())
		self.assertEqual(self.pub.call_count, n)
		self.assertTrue(all(c.kwargs["after_commit"] for c in self.pub.call_args_list))
		self.assertTrue(all(c.args[0] == "refetch_resource" for c in self.pub.call_args_list))
		# Tin chỉ có khóa đệm, không có dữ liệu Lead.
		self.assertTrue(all(set(c.args[1]) == {"cache_key"} for c in self.pub.call_args_list))

		lr.on_lead_insert(None)  # trong cửa sổ gộp: không phát, chỉ đánh dấu chờ
		self.assertEqual(self.pub.call_count, n)
		c = frappe.cache()
		self.assertTrue(c.get(c.make_key(lr.PENDING_KEY)))

		lr.flush_pending()  # cron phát bù một lần rồi xóa dấu
		self.assertEqual(self.pub.call_count, 2 * n)
		self.assertFalse(c.get(c.make_key(lr.PENDING_KEY)))
		lr.flush_pending()
		self.assertEqual(self.pub.call_count, 2 * n)

	def test_khong_cho_thi_cron_khong_phat(self):
		lr.flush_pending()
		self.pub.assert_not_called()

	def test_loi_redis_khong_lam_hong_tao_lead(self):
		with (
			mock.patch.object(lr.frappe, "cache", side_effect=RuntimeError("redis chết")),
			mock.patch.object(lr.frappe, "log_error") as le,
		):
			lr.on_lead_insert(None)
		le.assert_called_once()
		self.pub.assert_not_called()

	def test_tao_lead_that_kich_hoat_hook(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Thử tải lại danh sách"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(frappe.delete_doc, "CRM Lead", lead.name, force=True, ignore_permissions=True)
		self.assertIn('["CRM Lead", null, "list"]', self.keys_published())
