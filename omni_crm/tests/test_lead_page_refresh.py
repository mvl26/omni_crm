"""Trang Lead/Deal đang mở tự tải lại khối hoạt động/email khi có tin mới: chỉ gửi vào phòng của đúng tài liệu."""

from unittest import mock

from frappe.tests.utils import FrappeTestCase

from omni_crm.crm_bridge import timeline


class TestLeadPageRefresh(FrappeTestCase):
	def setUp(self):
		p = mock.patch.object(timeline.frappe, "publish_realtime")
		self.pub = p.start()
		self.addCleanup(p.stop)

	def refetch_calls(self):
		return [c for c in self.pub.call_args_list if c.args[0] == timeline.REFETCH_EVENT]

	def test_notify_bao_trang_lead_va_deal_tai_lai(self):
		with mock.patch.object(timeline.lead_bridge, "get_deal_for_lead", return_value="CRM-DEAL-X"):
			timeline.notify("CRM-LEAD-X", ["Email"])
		calls = self.refetch_calls()
		self.assertEqual(
			[(c.args[1], c.kwargs["doctype"], c.kwargs["docname"]) for c in calls],
			[
				({"cache_key": ["activity", "CRM-LEAD-X"]}, "CRM Lead", "CRM-LEAD-X"),
				({"cache_key": ["activity", "CRM-DEAL-X"]}, "CRM Deal", "CRM-DEAL-X"),
			],
		)
		# Chỉ vào phòng của tài liệu (Frappe kiểm quyền khi đăng ký phòng), sau commit, không phát cho cả site.
		self.assertTrue(all(c.kwargs["after_commit"] for c in calls))
		self.assertTrue(all("room" not in c.kwargs and "user" not in c.kwargs for c in calls))

	def test_lead_chua_co_deal_chi_bao_trang_lead(self):
		with mock.patch.object(timeline.lead_bridge, "get_deal_for_lead", return_value=None):
			timeline.notify("CRM-LEAD-Y", ["Email"])
		self.assertEqual([c.kwargs["docname"] for c in self.refetch_calls()], ["CRM-LEAD-Y"])

	def test_khong_co_lead_khong_bao(self):
		timeline.notify(None, ["Email"])
		self.pub.assert_not_called()

	def test_khoa_dem_khop_giao_dien_crm(self):
		# Giao diện băm khóa bằng JSON.stringify(["activity", docname]) (crm/frontend Activities.vue).
		with mock.patch.object(timeline.lead_bridge, "get_deal_for_lead", return_value=None):
			timeline.refresh_activity("CRM-LEAD-Z")
		self.assertEqual(self.refetch_calls()[0].args[1], {"cache_key": ["activity", "CRM-LEAD-Z"]})
