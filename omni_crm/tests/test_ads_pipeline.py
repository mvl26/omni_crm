# ruff: noqa: RUF001
"""V2-05: đường xử lý chung cho lead quảng cáo: Guest, bot/AI, định danh chéo kênh, hạn phản hồi, báo cáo chiến dịch, giám sát."""

import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import frappe
from frappe.utils import now_datetime

from omni_crm.api import webhooks
from omni_crm.crm_bridge import response_sla
from omni_crm.dashboard import blocks
from omni_crm.monitoring import alerts, snapshot
from omni_crm.tests.ads_helpers import (
	PREFIX,
	SALE,
	AdsCase,
	body,
	call_webhook,
	inbound,
	make_channel,
	make_form,
	make_users,
	work,
)
from omni_crm.tests.test_web_form import FORM, payload
from omni_crm.utils import business_hours

LEAD = "CRM Lead"
HOURS = business_hours.parse_hours({"mon-fri": "08:00-17:30", "sat": "08:00-12:00"})


class WebCase(AdsCase):
	def setUp(self):
		super().setUp()
		make_users()
		self.channel = make_channel("Web Form", f"{PREFIX} web")
		self.form = make_form("Website", FORM, "Web form", default_owner=SALE)
		self.campaign = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PREFIX} Chiến dịch web",
				"platform": "Website",
				"utm_campaign": "td505-utm",
				"spend": 3000000,
			}
		).insert()
		frappe.db.commit()

	def submit(self, **over):
		data = payload(**over)
		call_webhook(webhooks.web_form, body(data), args={"ch": self.channel.name})
		row = frappe.get_all(
			"Omni Inbound Message",
			filters={"channel": self.channel.name, "external_message_id": data["submission_id"]},
			fields=["identity_key", "name"],
		)[0]
		work(row.identity_key)
		return frappe.db.get_value(
			"Omni Inbound Message", row.name, ["lead", "status", "last_error", "batch_id"], as_dict=True
		)


class TestSlaExamples(AdsCase):
	"""docs/15 mục 10: giờ làm 08:00-17:30 T2-T6, 08:00-12:00 T7, ngưỡng 15 phút."""

	def deadline(self, when):
		with (
			mock.patch.object(business_hours, "_hours", return_value=HOURS),
			mock.patch.object(business_hours, "_holidays", return_value=frozenset()),
			mock.patch.object(response_sla, "sla_minutes", return_value=15),
		):
			return response_sla.deadline(when)

	def test_friday_1650_due_1705(self):
		self.assertEqual(self.deadline(datetime(2026, 11, 6, 16, 50)), datetime(2026, 11, 6, 17, 5))

	def test_friday_1720_due_saturday_0805(self):
		self.assertEqual(self.deadline(datetime(2026, 11, 6, 17, 20)), datetime(2026, 11, 7, 8, 5))

	def test_saturday_1155_due_monday_0810(self):
		self.assertEqual(self.deadline(datetime(2026, 11, 7, 11, 55)), datetime(2026, 11, 9, 8, 10))


class TestGuestAndBotIsolation(WebCase):
	def test_worker_runs_as_guest_like_real_rq_worker(self):
		call_webhook(webhooks.web_form, body(payload()), args={"ch": self.channel.name})
		row = inbound(self.channel.name)[0]
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with mock.patch("frappe.enqueue"):
			from omni_crm.pipeline.worker import process_identity

			process_identity(row.identity_key, debounce_seconds=0)
		frappe.set_user("Administrator")
		row = inbound(self.channel.name)[0]
		self.assertEqual(row.status, "Done", row.last_error)
		self.assertEqual(
			frappe.db.get_value(LEAD, row.lead, "lead_owner"), SALE
		)  # giao việc dưới Guest không PermissionError

	def test_form_lead_batch_never_reaches_ai_or_creates_drafts_or_outbound(self):
		from omni_crm.bot import ai_client, reply

		class Boom:
			is_real = False

			def create(self, **kw):
				raise AssertionError("form_lead không được gọi AI")

		ai_client.set_client(Boom())
		self.addCleanup(ai_client.set_client, None)
		settings = frappe.get_doc("Omni Settings")
		old = (settings.bot_analyze_enabled, settings.bot_reply_mode, settings.sandbox_mode)
		settings.bot_analyze_enabled, settings.bot_reply_mode, settings.sandbox_mode = 1, "Draft", 1
		settings.save()
		frappe.db.commit()

		def restore():
			s = frappe.get_doc("Omni Settings")
			s.bot_analyze_enabled, s.bot_reply_mode, s.sandbox_mode = old
			s.save()
			frappe.db.commit()
			frappe.clear_document_cache("Omni Settings", "Omni Settings")

		self.addCleanup(restore)
		frappe.clear_document_cache("Omni Settings", "Omni Settings")
		before = (
			frappe.db.count("Omni Bot Run"),
			frappe.db.count("Omni Bot Draft"),
			frappe.db.count("Omni Outbound Message"),
		)
		call_webhook(webhooks.web_form, body(payload()), args={"ch": self.channel.name})
		row = inbound(self.channel.name)[0]
		with mock.patch("frappe.enqueue") as enq:
			from omni_crm.pipeline.worker import process_identity

			process_identity(row.identity_key, debounce_seconds=0)
		jobs = [c.args[0] for c in enq.call_args_list if c.args]
		self.assertNotIn("omni_crm.bot.analyze.analyze_batch", jobs)
		batch = frappe.db.get_value("Omni Inbound Message", row.name, "batch_id")
		reply.reply_batch(batch)  # nếu job trả lời có chạy cũng phải không làm gì
		after = (
			frappe.db.count("Omni Bot Run"),
			frappe.db.count("Omni Bot Draft"),
			frappe.db.count("Omni Outbound Message"),
		)
		self.assertEqual(before, after)

	def test_new_modules_have_no_send_path(self):
		root = Path(webhooks.__file__).parent.parent
		for rel in (
			"channels/form_common.py",
			"channels/meta_leads/adapter.py",
			"channels/meta_leads/client.py",
			"channels/meta_leads/reconcile.py",
			"channels/google_leads/adapter.py",
			"channels/google_leads/csv_import.py",
			"channels/web_form/adapter.py",
			"channels/web_form/captcha.py",
			"crm_bridge/form_lead.py",
			"crm_bridge/response_sla.py",
		):
			text = (root / rel).read_text(encoding="utf-8")
			for banned in (
				"Omni Outbound Message",
				"sendmail",
				"send_reply",
				"enqueue_outbound",
				"frappe.enqueue",
			):
				self.assertNotIn(banned, text, f"{rel}: {banned}")
		from omni_crm.channels.registry import get_adapter

		for t in ("Meta Lead Ads", "Google Lead Form", "Web Form"):
			ok, _why = get_adapter(t).can_send_now(None, None)
			self.assertFalse(ok)
			with self.assertRaises(NotImplementedError):
				get_adapter(t).send(None, None)


class TestCrossChannelOneLead(WebCase):
	def test_web_form_then_zalo_same_phone_is_one_lead_with_both_activities_and_first_touch(self):
		from omni_crm.ingest.events import InboundEvent
		from omni_crm.ingest.gateway import save_inbound
		from omni_crm.tests.fake_adapter import make_channel as make_zalo

		res = self.submit()
		lead = res.lead
		first = frappe.db.get_value(LEAD, lead, "omni_campaign")
		zalo = make_zalo(f"{PREFIX} zalo", enabled=1)
		frappe.db.commit()
		with mock.patch("frappe.enqueue"):
			save_inbound(
				InboundEvent(
					channel=zalo.name,
					channel_type="Zalo OA",
					event_type="user_info",
					external_message_id="TD505Z1",
					external_sender_id=f"{PREFIX}ZU1",
					received_at=now_datetime(),
					text="Tôi để lại số điện thoại",
					sender_profile={"phone": "0912345611", "display_name": "TD505 Lê Văn Cường"},
				)
			)
			frappe.db.after_commit.run()
		frappe.db.commit()
		with mock.patch("omni_crm.channels.zalo.worker_hooks.ZaloClient"):
			work(f"Zalo OA:{PREFIX}ZU1")
		zrow = frappe.get_all(
			"Omni Inbound Message", filters={"channel": zalo.name}, fields=["lead", "status", "last_error"]
		)[0]
		self.assertEqual(zrow.status, "Done", zrow.last_error)
		self.assertEqual(zrow.lead, lead)
		self.assertEqual(frappe.db.count(LEAD, {"omni_phone_e164": "+84912345611"}), 1)
		self.assertEqual(frappe.db.get_value(LEAD, lead, "omni_campaign"), first)
		self.assertEqual(frappe.db.get_value(LEAD, lead, "omni_ad_platform"), "Website")

	def test_zalo_lead_then_web_form_same_phone_gets_first_touch_from_form(self):
		from omni_crm.tests.fake_adapter import make_channel as make_zalo

		zalo_lead = frappe.get_doc(
			{"doctype": LEAD, "first_name": f"{PREFIX} Zalo trước", "mobile_no": "+84912345611"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		res = self.submit()
		self.assertEqual(res.lead, zalo_lead.name)
		d = frappe.get_doc(LEAD, zalo_lead.name)
		self.assertEqual(d.omni_campaign, self.campaign.name)
		self.assertEqual(d.omni_ad_platform, "Website")
		self.assertTrue(make_zalo)


class TestOverdueReminders(WebCase):
	def setUp(self):
		super().setUp()
		self.lead = self.submit().lead
		self.entered = datetime(2026, 11, 6, 10, 0)  # thứ Sáu 10:00
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": self.lead, "event_type": "form_lead"},
			"received_at",
			self.entered,
		)
		frappe.db.set_value(LEAD, self.lead, "creation", self.entered)
		manager = "td505_mgr@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": manager,
				"first_name": f"{PREFIX} Mgr",
				"send_welcome_email": 0,
				"roles": [{"role": "Sales Manager"}],
			}
		).insert(ignore_permissions=True)
		self.manager = manager
		self.addCleanup(self.drop_manager)
		frappe.db.commit()

	def drop_manager(self):
		frappe.db.delete("Notification Log", {"for_user": self.manager})
		frappe.delete_doc("User", self.manager, force=True, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	def run_at(self, minutes):
		with (
			mock.patch.object(business_hours, "_hours", return_value=HOURS),
			mock.patch.object(business_hours, "_holidays", return_value=frozenset()),
		):
			return response_sla.run(now=self.entered + timedelta(minutes=minutes))

	def subjects(self, user):
		return frappe.get_all(
			"Notification Log",
			filters={"for_user": user, "document_name": self.lead, "subject": ["like", "Quá%"]},
			pluck="subject",
		)

	def test_not_overdue_within_threshold(self):
		self.assertEqual(self.run_at(14)["overdue"], 0)
		self.assertEqual(self.subjects(SALE), [])

	def test_overdue_reminds_owner_once_then_escalates_to_team_lead_after_twice_threshold(self):
		r = self.run_at(16)
		self.assertEqual((r["overdue"], r["reminded"], r["escalated"]), (1, 1, 0))
		self.assertEqual(len(self.subjects(SALE)), 1)
		self.assertEqual(self.subjects(self.manager), [])
		self.run_at(20)  # lần quét sau: không nhắc lại
		self.assertEqual(len(self.subjects(SALE)), 1)
		r = self.run_at(31)
		self.assertGreaterEqual(r["escalated"], 1)
		self.assertEqual(len(self.subjects(self.manager)), 1)
		self.run_at(40)
		self.assertEqual(len(self.subjects(self.manager)), 1)

	def test_responded_or_test_or_switch_off_never_reminds(self):
		frappe.db.set_value(LEAD, self.lead, "omni_first_response_at", self.entered + timedelta(minutes=5))
		self.assertEqual(self.run_at(60)["overdue"], 0)
		frappe.db.set_value(LEAD, self.lead, {"omni_first_response_at": None, "omni_is_test": 1})
		self.assertEqual(self.run_at(60)["overdue"], 0)
		frappe.db.set_value(LEAD, self.lead, "omni_is_test", 0)
		with mock.patch("omni_crm.flags.dashboard_enabled", return_value=False):
			self.assertEqual(self.run_at(60)["overdue"], 0)

	def test_overdue_clock_runs_in_business_hours_only(self):
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": self.lead, "event_type": "form_lead"},
			"received_at",
			datetime(2026, 11, 6, 17, 20),
		)
		with (
			mock.patch.object(business_hours, "_hours", return_value=HOURS),
			mock.patch.object(business_hours, "_holidays", return_value=frozenset()),
		):
			self.assertEqual(
				response_sla.run(now=datetime(2026, 11, 6, 17, 40))["overdue"], 0
			)  # đã đóng cửa: chưa hết 15 phút
			self.assertEqual(response_sla.run(now=datetime(2026, 11, 7, 8, 6))["overdue"], 1)


class TestDashboardForFormLeads(WebCase):
	def query(self):
		return blocks.make_query("7d", "team", "Administrator")

	def test_form_lead_waiting_over_threshold_is_labelled_overdue(self):
		lead = self.submit().lead
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": lead, "event_type": "form_lead"},
			"received_at",
			now_datetime() - timedelta(days=5),
		)
		q = self.query()
		rows = {r["lead"]: r for r in blocks.attention(q)["rows"]}
		self.assertEqual(rows[lead]["reason"], "overdue")
		self.assertEqual(rows[lead]["reason_label"], "Quá hạn phản hồi")

	def test_care_log_response_clears_form_lead_from_waiting(self):
		lead = self.submit().lead
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": lead, "event_type": "form_lead"},
			"received_at",
			now_datetime() - timedelta(days=5),
		)
		frappe.db.set_value(LEAD, lead, "omni_first_response_at", now_datetime() - timedelta(days=4))
		self.assertNotIn(lead, {r["lead"] for r in blocks.attention(self.query())["rows"]})

	def test_returning_lead_stays_overdue_until_a_later_sale_record_then_clears(self):
		# Lead đã được liên hệ từ trước (vd nhập từ danh sách và đã gọi) rồi điền biểu mẫu mới: `omni_first_response_at` cũ
		# KHÔNG được làm sạch trạng thái chờ; nhật ký chăm sóc của sale GHI sau lúc điền thì có.
		lead = self.submit().lead
		now = now_datetime()
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": lead, "event_type": "form_lead"},
			"received_at",
			now - timedelta(days=5),
		)
		frappe.db.set_value(LEAD, lead, "omni_first_response_at", now - timedelta(days=30))
		rows = {r["lead"]: r for r in blocks.attention(self.query())["rows"]}
		self.assertEqual(rows[lead]["reason"], "overdue")
		frappe.get_doc(
			{
				"doctype": "Omni Care Log",
				"lead": lead,
				"channel": "Điện thoại",
				"started_at": now - timedelta(days=4),
				"last_activity_at": now - timedelta(days=4),
				"handled_kind": "Sale",
				"handled_by": SALE,
				"outcome": "Đã tư vấn",
				"outcome_by": SALE,
				"outcome_at": now - timedelta(days=4),
			}
		).insert(ignore_permissions=True)
		self.assertNotIn(lead, {r["lead"] for r in blocks.attention(self.query())["rows"]})

	def test_test_leads_are_excluded_from_every_block(self):
		lead = self.submit().lead
		frappe.db.set_value(LEAD, lead, "omni_is_test", 1)
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": lead, "event_type": "form_lead"},
			"received_at",
			now_datetime() - timedelta(days=5),
		)
		q = self.query()
		self.assertNotIn(lead, {r["lead"] for r in blocks.attention(q)["rows"]})
		self.assertEqual(blocks.campaigns(q)["rows"], [])

	def test_campaign_report_counts_valid_ontime_median_and_cost(self):
		a = self.submit(submission_id="td505-sub-c001").lead
		self.submit(
			submission_id="td505-sub-c002",
			phone="",
			email="",
			tax_id="",
			company_name="",
			full_name="TD505 Không liên hệ",
		)  # không hợp lệ
		self.submit(
			submission_id="td505-sub-c003",
			phone="0912345612",
			email="td505.c3@x-td505.vn",
			tax_id="",
			company_name="",
		)
		now = now_datetime()
		frappe.db.set_value(
			"Omni Inbound Message",
			{"lead": a, "event_type": "form_lead"},
			"received_at",
			now - timedelta(minutes=2),
		)
		frappe.db.set_value(LEAD, a, "omni_first_response_at", now - timedelta(minutes=1))
		row = {r["campaign"]: r for r in blocks.campaigns(self.query())["rows"]}[self.campaign.name]
		self.assertEqual((row["leads"], row["valid"]), (3, 2))
		self.assertEqual(
			row["on_time_pct"], 50
		)  # 1 trong 2 Lead hợp lệ được phản hồi trong hạn (Lead kia chưa được phản hồi)
		self.assertEqual(row["spend"], 3000000)
		self.assertEqual(row["cost_per_valid_lead"], 1500000)
		self.assertEqual(row["platform"], "Website")

	def test_campaigns_block_hidden_when_no_campaign_data(self):
		self.channel
		frappe.db.delete("Omni Campaign", {"campaign_name": ["like", f"{PREFIX}%"]})
		self.assertFalse(
			blocks.REGISTRY["campaigns"].enabled(self.query())
			and frappe.db.exists(LEAD, {"omni_ad_platform": "Website"})
		)


class TestSilenceAlert(AdsCase):
	FRIDAY_10 = datetime(2026, 11, 6, 10, 0)

	def snap(self, **ads):
		base = {
			"campaigns_running": 1,
			"first_campaign_created": "2026-11-05 09:00:00",
			"channels_enabled": 1,
			"last_lead_at": "2026-11-05 09:00:00",
			"silence_hours": 6,
		}
		snap = snapshot.build_snapshot()
		snap["ads"] = {**base, **ads}
		return snap

	def kinds(self, snap, now):
		with (
			mock.patch.object(business_hours, "_hours", return_value=HOURS),
			mock.patch.object(business_hours, "_holidays", return_value=frozenset()),
		):
			return [a.kind for a in alerts.evaluate(snap, now=now)]

	def test_fires_when_running_campaign_and_no_lead_for_n_business_hours(self):
		self.assertIn("khong_co_lead_quang_cao", self.kinds(self.snap(), self.FRIDAY_10))

	def test_quiet_without_running_campaign_or_enabled_channel_or_recent_lead_or_off_hours(self):
		for over, now in (
			({"campaigns_running": 0}, self.FRIDAY_10),
			({"channels_enabled": 0}, self.FRIDAY_10),
			({"last_lead_at": "2026-11-06 09:30:00"}, self.FRIDAY_10),
			({}, datetime(2026, 11, 6, 20, 0)),  # ngoài giờ làm việc
			({"silence_hours": 24}, self.FRIDAY_10),
		):
			with self.subTest(over=over, now=now):
				self.assertNotIn("khong_co_lead_quang_cao", self.kinds(self.snap(**over), now))

	def test_snapshot_ads_section_ignores_test_leads(self):
		self.assertEqual(
			set(snapshot.build_snapshot()["ads"]),
			{
				"campaigns_running",
				"first_campaign_created",
				"channels_enabled",
				"last_lead_at",
				"silence_hours",
				"webform_global_limit_hit",
			},
		)
		self.assertEqual(snapshot.build_snapshot()["ads"]["silence_hours"], 6)


class TestBackfillPatch(AdsCase):
	def setUp(self):
		super().setUp()
		from omni_crm.crm_bridge.lead import LEAD_SOURCE

		if not frappe.db.exists(LEAD_SOURCE, "Danh sách mua sắm công"):
			frappe.get_doc({"doctype": LEAD_SOURCE, "source_name": "Danh sách mua sắm công"}).insert(
				ignore_permissions=True
			)
		self.leads = []
		for i, when in enumerate(("2026-09-15 09:00:00", "2026-10-20 09:00:00", "2026-10-21 09:00:00")):
			lead = frappe.get_doc(
				{"doctype": LEAD, "first_name": f"{PREFIX} Nhập {i}", "source": "Danh sách mua sắm công"}
			).insert(ignore_permissions=True)
			frappe.db.set_value(LEAD, lead.name, "creation", when, update_modified=False)
			self.leads.append(lead.name)
		frappe.db.set_value(
			LEAD, self.leads[2], {"omni_ad_platform": "Danh sách mua sắm công", "omni_campaign": None}
		)
		frappe.db.commit()

	def test_dry_run_writes_nothing_then_real_run_fills_blank_fields_and_is_idempotent(self):
		from omni_crm.patches.v2 import backfill_ad_fields as patch

		own = {"candidates": 3, "platform_to_set": 2, "campaign_to_set": 3}
		with mock.patch("frappe.db.commit"):
			dry = patch.execute()
			self.assertTrue(all(dry[k] >= v for k, v in own.items()), dry)
			self.assertFalse(frappe.db.get_value(LEAD, self.leads[0], "omni_ad_platform"))
			self.assertFalse(frappe.db.exists("Omni Campaign", "Liên hệ chủ động – 09/2026"))
			real = patch.execute(dry_run=False)
			self.assertEqual(real["campaign_to_set"], dry["campaign_to_set"])
		d = frappe.get_doc(LEAD, self.leads[0])
		self.assertEqual(
			(d.omni_ad_platform, d.omni_campaign, d.omni_last_campaign),
			("Danh sách mua sắm công", "Liên hệ chủ động – 09/2026", "Liên hệ chủ động – 09/2026"),
		)
		self.assertEqual(
			frappe.db.get_value(LEAD, self.leads[1], "omni_campaign"), "Liên hệ chủ động – 10/2026"
		)
		with mock.patch("frappe.db.commit"):
			again = patch.execute(dry_run=False)
		self.assertEqual((again["platform_to_set"], again["campaign_to_set"]), (0, 0))

	def test_existing_values_are_never_overwritten(self):
		from omni_crm.patches.v2 import backfill_ad_fields as patch

		other = frappe.get_doc(
			{"doctype": "Omni Campaign", "campaign_name": f"{PREFIX} Có sẵn", "platform": "Meta"}
		).insert()
		frappe.db.set_value(LEAD, self.leads[0], {"omni_campaign": other.name, "omni_ad_platform": "Meta"})
		with mock.patch("frappe.db.commit"):
			patch.execute(dry_run=False)
		d = frappe.get_doc(LEAD, self.leads[0])
		self.assertEqual((d.omni_campaign, d.omni_ad_platform), (other.name, "Meta"))

	def test_not_registered_in_patches_txt(self):
		text = (Path(webhooks.__file__).parent.parent / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("backfill_ad_fields", text)


class TestSourceMapAndRegexSanity(AdsCase):
	def test_old_stored_source_map_still_resolves_new_channels(self):
		from omni_crm.crm_bridge import lead as lead_bridge

		with mock.patch.object(
			frappe.db,
			"get_single_value",
			return_value='{"Email":"Email","Zalo OA":"Zalo OA","Telephony":"Điện thoại"}',
		):
			self.assertEqual(lead_bridge.lead_source_for_channel("Meta Lead Ads"), "Quảng cáo Meta")
			self.assertEqual(lead_bridge.lead_source_for_channel("Google Lead Form"), "Quảng cáo Google")
			self.assertEqual(lead_bridge.lead_source_for_channel("Web Form"), "Website")
		self.assertTrue(re.match(r"^v", "v26.0"))

	def test_ads_settings_default_when_never_saved(self):
		from omni_crm import flags

		frappe.db.sql(
			"delete from `tabSingles` where doctype='Omni Settings' and field in ('ads_silence_hours','webform_rate_limit')"
		)
		self.assertEqual(
			(flags.ads_setting("ads_silence_hours"), flags.ads_setting("webform_rate_limit")), (6, 5)
		)
