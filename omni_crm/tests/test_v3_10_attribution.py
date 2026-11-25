# ruff: noqa: RUF001
"""V3-10 Prompt 5: Lead từ form landing page gắn đúng chiến dịch và bài đăng (utm_campaign + utm_content)."""

from __future__ import annotations

import frappe

from omni_crm.marketing import attribution
from omni_crm.tests.ads_helpers import PREFIX
from omni_crm.tests.test_web_form import WebCase, payload

ITEM = "Omni Content Item"


class AttributionCase(WebCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(self._drop_items)
		self.campaign = f"{PREFIX} Chiến dịch web"
		self.item = self._item(self.campaign, "SPD là gì", "Nhận biết")
		frappe.db.commit()

	def _drop_items(self):
		camps = frappe.get_all("Omni Campaign", filters={"name": ["like", f"{PREFIX}%"]}, pluck="name")
		frappe.db.delete(ITEM, {"campaign": ["in", camps or [""]]})
		frappe.db.commit()

	def _item(self, campaign: str, title: str, stage: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": ITEM,
					"campaign": campaign,
					"title": title,
					"caption": "Miyano",
					"funnel_stage": stage,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def lead_of(self, data):
		_row, lead = self.process(data)
		return frappe.get_doc("CRM Lead", lead)

	def comments(self, lead: str) -> str:
		return " | ".join(
			frappe.get_all(
				"Comment", filters={"reference_doctype": "CRM Lead", "reference_name": lead}, pluck="content"
			)
		)


class TestLandingAttribution(AttributionCase):
	def test_matching_code_fills_campaign_post_and_funnel(self):
		code = frappe.db.get_value(ITEM, self.item, "utm_content")
		self.assertEqual(code, "bai-01")
		lead = self.lead_of(payload(utm_source="facebook", utm_medium="social", utm_content=code))
		self.assertEqual(lead.omni_campaign, self.campaign)
		self.assertEqual(lead.omni_content_item, self.item)
		self.assertEqual(lead.omni_funnel_stage, "Nhận biết")
		self.assertEqual((lead.omni_utm_source, lead.omni_utm_content), ("facebook", "bai-01"))
		text = self.comments(lead.name)
		self.assertIn("Lead tạo từ form landing page /lien-he", text)
		self.assertIn(f"Chiến dịch {self.campaign}", text)
		self.assertIn("bài SPD là gì (Facebook)", text)

	def test_wrong_code_still_creates_lead_and_keeps_raw_utm(self):
		lead = self.lead_of(payload(utm_content="bai-99"))
		self.assertEqual(lead.omni_campaign, self.campaign)
		self.assertFalse(lead.omni_content_item)
		self.assertEqual(lead.omni_utm_content, "bai-99")
		self.assertIn("mã bài bai-99 không khớp", self.comments(lead.name))

	def test_unknown_campaign_code_creates_lead_without_campaign(self):
		lead = self.lead_of(payload(utm_campaign="khong-co", utm_content="bai-01"))
		self.assertFalse(lead.omni_campaign)
		self.assertFalse(lead.omni_content_item)
		self.assertEqual(lead.omni_utm_campaign, "khong-co")

	def test_first_source_is_kept_later_source_goes_to_activity(self):
		first = self.lead_of(payload(utm_content="bai-01"))
		other = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PREFIX} Chiến dịch hai",
				"platform": "Website",
				"utm_campaign": "td505-hai",
			}
		).insert()
		other_item = self._item(other.name, "Ưu đãi", "Chuyển đổi")
		frappe.db.commit()
		again = self.lead_of(
			payload(submission_id="td505-sub-0002", utm_campaign="td505-hai", utm_content="bai-01")
		)
		self.assertEqual(again.name, first.name)
		self.assertEqual(again.omni_campaign, self.campaign)
		self.assertEqual(again.omni_content_item, self.item)
		self.assertEqual(again.omni_funnel_stage, "Nhận biết")
		self.assertEqual(again.omni_last_campaign, other.name)
		text = self.comments(again.name)
		self.assertIn("Khách điền thêm form landing page", text)
		self.assertIn(f"Chiến dịch {other.name} - bài Ưu đãi", text)
		self.assertNotEqual(other_item, self.item)


class TestHelpers(AttributionCase):
	def test_content_item_lookup_is_scoped_to_campaign(self):
		self.assertEqual(attribution.content_item_for(self.campaign, "BAI-01").name, self.item)
		self.assertIsNone(attribution.content_item_for(None, "bai-01"))
		self.assertIsNone(attribution.content_item_for(self.campaign, ""))

	def test_lead_ads_line(self):
		line = attribution.activity_line(
			{"platform": "Meta", "form_name": "Form SPD", "utm": {}}, self.campaign, True
		)
		self.assertEqual(line, f"Lead tạo từ form Lead Ads Form SPD - Chiến dịch {self.campaign}")
		self.assertIsNone(attribution.activity_line({"platform": "Meta"}, None, True))


class TestLeadUiPatch(AttributionCase):
	def test_section_goes_after_bot_ai_once(self):
		from omni_crm.patches.v3 import marketing_lead_ui as p

		layout = [{"name": "details_section"}, {"name": "omni_bot_section"}, {"name": "person_section"}]
		self.assertTrue(p.add_section(layout))
		self.assertEqual([s["name"] for s in layout][2], p.SECTION)
		self.assertFalse(p.add_section(layout))
		self.assertEqual(len(layout), 4)
		self.assertEqual(layout[2]["columns"][0]["fields"][0], "omni_campaign")

	def test_quick_filter_added_once(self):
		from omni_crm.patches.v3 import marketing_lead_ui as p

		filters = ["lead_name", "status"]
		self.assertTrue(p.add_quick_filter(filters))
		self.assertFalse(p.add_quick_filter(filters))
		self.assertEqual(filters, ["lead_name", "status", "omni_campaign"])
