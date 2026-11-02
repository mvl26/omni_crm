# ruff: noqa: RUF001
"""V2-04 (docs/13 mục 1, 4; docs/14 mục 3.3, 4, 8): Tổng quan CSKH — chỉ số tính tay, thứ tự ưu tiên, quyền, công tắc, B2B."""

from datetime import datetime, timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import dashboard
from omni_crm.dashboard import access, blocks, priority
from omni_crm.tests.fake_adapter import make_channel
from omni_crm.tests.test_prospect_model import make_lead, make_org, make_user
from omni_crm.utils import business_hours

INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"
SALE_A = "td404_a@example.com"
SALE_B = "td404_b@example.com"
MANAGER = "td404_mgr@example.com"
NOW = datetime(2026, 11, 4, 11, 0)  # thứ tư 11:00
HOURS = business_hours.parse_hours('{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}')
OFF = {"telephony": False, "analyze": False, "scoring": False, "bot": False, "draft": False}


def at(day: int, hh: int, mm: int = 0) -> datetime:
	"""Thời điểm trong tuần mẫu: day=3 là thứ ba 3/11, 4 là thứ tư 4/11."""
	return datetime(2026, 11, day, hh, mm)


class DashCase(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("td404_dash")
		self.addCleanup(frappe.db.rollback, save_point="td404_dash")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.clear_document_cache, "Omni Settings", "Omni Settings")
		for patcher in (
			mock.patch("omni_crm.dashboard.blocks.now_datetime", return_value=NOW),
			mock.patch.object(business_hours, "_hours", return_value=HOURS),
			mock.patch.object(business_hours, "_holidays", return_value=frozenset()),
			mock.patch.object(priority, "features", return_value=dict(OFF)),
			mock.patch("omni_crm.prospect.fit.fit_enabled", return_value=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		make_user(SALE_A)
		make_user(SALE_B)
		make_user(MANAGER, "Sales Manager")
		self.channel = make_channel("TD404 dash").name
		self.n = 0

	# --- dựng dữ liệu ---------------------------------------------------------------------------------------

	def lead(self, name, owner, created, channel="Zalo OA", first_response=None, **kw):
		lead = make_lead(name, owner=owner)
		values = {
			"creation": created,
			"omni_first_channel": channel,
			"omni_first_response_at": first_response,
			"status": "Tiềm năng",
			**kw,
		}
		frappe.db.set_value("CRM Lead", lead, values, update_modified=False)
		return lead

	def inbound(
		self, lead, when, text="hỏi giá", event_type="message", channel_type="Zalo OA", status="Done"
	):
		self.n += 1
		return frappe.get_doc(
			{
				"doctype": INBOUND,
				"dedupe_key": f"td404-{self.n}-{frappe.generate_hash(length=6)}",
				"channel": self.channel,
				"channel_type": channel_type,
				"event_type": event_type,
				"identity_key": f"Zalo OA:TD404{self.n}",
				"lead": lead,
				"received_at": when,
				"text": text,
				"status": status,
			}
		).insert(ignore_permissions=True)

	def outbound(self, lead, when, status="Sent", source="Sale", sandbox=0):
		return frappe.get_doc(
			{
				"doctype": OUTBOUND,
				"channel": self.channel,
				"lead": lead,
				"text": "Dạ em chào anh",
				"source": source,
				"status": status,
				"sent_at": when,
				"is_sandbox": sandbox,
			}
		).insert(ignore_permissions=True)

	def snap(self, user, period="today", scope=None, keys=None):
		frappe.set_user(user)
		q = blocks.make_query(period, scope, user)
		out = blocks.run_blocks(q)
		return out if keys is None else {k: out[k] for k in keys}

	def build_core(self):
		"""Bộ dữ liệu tính tay (xem docstring từng test)."""
		a = {}
		a["A1"] = self.lead("TD404 A1", SALE_A, at(4, 8), first_response=at(4, 8, 10))
		a["A2"] = self.lead("TD404 A2", SALE_A, at(3, 17), first_response=at(4, 8, 20))
		a["A3"] = self.lead("TD404 A3", SALE_A, at(4, 9))
		a["A4"] = self.lead("TD404 A4", SALE_A, at(4, 10, 50))
		a["A5"] = self.lead("TD404 A5", SALE_A, at(4, 8, 30))
		a["A6"] = self.lead(
			"TD404 A6", SALE_A, at(4, 7), channel=None
		)  # nhà thầu từ danh sách: không có kênh vào
		a["A7"] = self.lead("TD404 A7", SALE_A, at(4, 8, 45))
		a["A8"] = self.lead("TD404 A8", SALE_A, at(4, 8))
		a["B1"] = self.lead("TD404 B1", SALE_B, at(4, 9))
		self.inbound(a["A3"], at(4, 9))
		self.inbound(a["A4"], at(4, 10, 50))
		self.inbound(a["A5"], at(4, 8, 30))
		self.outbound(a["A5"], at(4, 8, 35))  # hook V2-01 ghi omni_first_response_at = 08:35
		self.inbound(a["A7"], at(4, 8, 45))
		self.outbound(a["A7"], at(4, 9))  # đã trả lời: phản hồi lúc 09:00 = 15 phút
		self.inbound(a["A8"], at(4, 8))
		self.outbound(a["A8"], at(4, 9), status="Failed")  # gửi lỗi: chưa phải trả lời
		self.inbound(a["B1"], at(4, 9))
		return a


class TestMetrics(DashCase):
	def test_tiles_match_hand_computed_values_for_today(self):
		"""Hôm nay (4/11 00:00-11:00), SALE_A: Lead mới có kênh = A1,A3,A4,A5,A7,A8 = 6 (A2 tạo hôm qua, A6 không kênh).
		Phản hồi lần đầu: A1 10 phút, A5 5 phút (08:30 -> 08:35), A7 15 phút (08:45 -> 09:00) -> TB 10,0 (3 Lead).
		Chờ trả lời: A3, A4, A8 (A5, A7 đã trả lời; A8 chỉ có tin gửi lỗi)."""
		self.build_core()
		tiles = self.snap(SALE_A, keys=["tile_waiting", "tile_first_response", "tile_new_leads"])
		self.assertEqual(tiles["tile_waiting"]["value"], 3)
		self.assertEqual(tiles["tile_new_leads"]["value"], 6)
		fr = tiles["tile_first_response"]
		self.assertEqual((fr["value"], fr["count"]), (10.0, 3))

	def test_first_response_counts_only_business_minutes(self):
		"""7 ngày: thêm A2 tạo thứ ba 17:00, phản hồi thứ tư 08:20 -> 30 phút (17:00-17:30) + 20 = 50 phút làm việc,
		KHÔNG phải 15 giờ 20 phút. TB (10 + 50 + 5 + 15) / 4 = 20,0."""
		self.build_core()
		fr = self.snap(SALE_A, period="7d", keys=["tile_first_response"])["tile_first_response"]
		self.assertEqual((fr["value"], fr["count"]), (20.0, 4))

	def test_delta_compares_with_previous_period_and_has_direction(self):
		"""Kỳ trước của "hôm nay" = thứ ba: 1 Lead mới (A2), phản hồi TB 50 phút. Lead mới 6 vs 1 = +5 (tốt khi tăng);
		phản hồi 10,0 vs 50 = -40,0 (tốt khi giảm)."""
		self.build_core()
		tiles = self.snap(SALE_A, keys=["tile_first_response", "tile_new_leads"])
		new, fr = tiles["tile_new_leads"]["delta"], tiles["tile_first_response"]["delta"]
		self.assertEqual((new["value"], new["good"]), (5, True))
		self.assertEqual((fr["value"], fr["good"]), (-40.0, True))
		self.assertIn("hôm qua", new["label"])

	def test_no_delta_without_a_meaningful_baseline(self):
		self.lead("TD404 Solo", SALE_A, at(4, 9), first_response=at(4, 9, 5))
		tiles = self.snap(SALE_A, keys=["tile_first_response", "tile_new_leads"])
		self.assertIsNone(tiles["tile_new_leads"]["delta"])
		self.assertIsNone(tiles["tile_first_response"]["delta"])

	def test_sandbox_and_system_messages_do_not_set_first_response(self):
		lead = self.lead("TD404 Sb", SALE_A, at(4, 9))
		self.outbound(lead, at(4, 9, 3), sandbox=1)
		self.outbound(lead, at(4, 9, 4), source="System")
		self.assertIsNone(frappe.db.get_value("CRM Lead", lead, "omni_first_response_at"))
		self.assertEqual(
			self.snap(SALE_A, keys=["tile_first_response"])["tile_first_response"]["value"], None
		)

	def test_channels_block_counts_contacts_and_new_leads(self):
		a = self.build_core()
		for n, key in enumerate(("A3", "A4", "A5", "A7", "A8")):  # định danh Zalo có liên hệ hôm nay
			frappe.get_doc(
				{
					"doctype": "Omni Identity",
					"identity_key": f"Zalo OA:TD404C{n}",
					"channel_type": "Zalo OA",
					"external_id": f"TD404C{n}",
					"lead": a[key],
					"last_inbound_at": at(4, 9),
				}
			).insert(ignore_permissions=True)
		old = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": "Email:td404old@example.com",
				"channel_type": "Email",
				"external_id": "td404old@example.com",
				"lead": a["A1"],
				"last_inbound_at": at(2, 9),  # liên hệ từ thứ hai: ngoài kỳ "hôm nay"
			}
		).insert(ignore_permissions=True)
		self.assertTrue(old.name)
		rows = {r["channel"]: r for r in self.snap(SALE_A, keys=["channels"])["channels"]["rows"]}
		self.assertEqual(rows["Zalo OA"]["contacts"], 5)  # A3, A4, A5, A7, A8 nhắn hôm nay
		self.assertEqual(rows["Zalo OA"]["new_leads"], 6)
		self.assertEqual(rows["Email"]["contacts"], 0)
		self.assertNotIn("Telephony", rows)  # chưa có tổng đài: không có dòng Điện thoại

	def test_staff_table_has_waiting_open_and_first_response_per_owner(self):
		self.build_core()
		manager = self.snap(MANAGER, scope="team", keys=["staff"])["staff"]
		rows = {r["user"]: r for r in manager["rows"]}
		self.assertEqual(rows[SALE_A]["waiting"], 3)
		self.assertEqual(rows[SALE_B]["waiting"], 1)
		self.assertEqual(rows[SALE_A]["first_response"], 10.0)
		self.assertGreaterEqual(rows[SALE_A]["open"], 8)
		self.assertFalse(manager["columns"]["callbacks"])
		self.assertFalse(manager["columns"]["drafts"])


class TestAttentionOrder(DashCase):
	def test_overdue_sorted_by_longest_wait_then_plain_waiting(self):
		"""A8 chờ từ 08:00 (180 phút làm việc), A3 từ 09:00 (120), A4 từ 10:50 (10 phút < 15 nên chỉ "Đang chờ")."""
		a = self.build_core()
		rows = self.snap(SALE_A, keys=["attention"])["attention"]["rows"]
		self.assertEqual([r["lead"] for r in rows], [a["A8"], a["A3"], a["A4"]])
		self.assertEqual([r["reason"] for r in rows], ["overdue", "overdue", "waiting"])
		self.assertEqual(rows[0]["waited_minutes"], 180)
		self.assertEqual(rows[1]["waited_minutes"], 120)
		self.assertEqual(rows[2]["waited_minutes"], 10)
		self.assertEqual(rows[2]["last_text"], "hỏi giá")

	def test_wait_outside_business_hours_is_not_counted(self):
		"""Khách nhắn thứ ba 17:20, bây giờ thứ tư 08:10: chỉ 10 + 10 = 20 phút làm việc (qua đêm không tính)."""
		lead = self.lead("TD404 Dem", SALE_A, at(3, 17))
		self.inbound(lead, at(3, 17, 20))
		with mock.patch("omni_crm.dashboard.blocks.now_datetime", return_value=at(4, 8, 10)):
			row = self.snap(SALE_A, keys=["attention"])["attention"]["rows"][0]
		self.assertEqual(row["waited_minutes"], 20)
		self.assertEqual(row["reason"], "overdue")

	def test_feature_priorities_complaint_callback_hot_draft_only_when_on(self):
		a = self.build_core()
		frappe.db.set_value("CRM Lead", a["A4"], {"omni_intent": "Khiếu nại"})
		frappe.db.set_value("CRM Lead", a["A3"], {"omni_score": 90})
		with mock.patch.object(priority, "features", return_value=dict(OFF)):
			# tính năng tắt: khiếu nại và Lead nóng không đổi thứ tự (không bịa lý do)
			rows = self.snap(SALE_A, keys=["attention"])["attention"]["rows"]
			self.assertEqual([r["reason"] for r in rows], ["overdue", "overdue", "waiting"])
		on = {**OFF, "analyze": True, "scoring": True}
		with mock.patch.object(priority, "features", return_value=on):
			rows = self.snap(SALE_A, keys=["attention"])["attention"]["rows"]
			self.assertEqual(rows[0]["lead"], a["A4"])  # khiếu nại đứng đầu dù mới chờ 10 phút
			self.assertEqual(rows[0]["reason"], "complaint")
			self.assertEqual(rows[1]["lead"], a["A8"])  # quá hạn lâu hơn A3
			self.assertEqual(rows[1]["reason"], "overdue")

	def test_hot_lead_with_waiting_message_under_sla_ranks_above_plain_waiting(self):
		lead = self.lead("TD404 Hot", SALE_A, at(4, 10, 55), omni_score=80)
		self.inbound(lead, at(4, 10, 55))
		plain = self.lead("TD404 Plain", SALE_A, at(4, 10, 50))
		self.inbound(plain, at(4, 10, 50))
		with mock.patch.object(priority, "features", return_value={**OFF, "scoring": True}):
			rows = self.snap(SALE_A, keys=["attention"])["attention"]["rows"]
		self.assertEqual([(r["lead"], r["reason"]) for r in rows], [(lead, "hot"), (plain, "waiting")])

	def test_attention_limit_is_twenty(self):
		for i in range(23):
			lead = self.lead(f"TD404 L{i}", SALE_A, at(4, 8))
			self.inbound(lead, at(4, 8, i))
		attention = self.snap(SALE_A, keys=["attention"])["attention"]
		self.assertEqual(len(attention["rows"]), 20)
		self.assertEqual(attention["total"], 23)

	def test_customer_with_open_callback_is_listed_even_without_waiting_message(self):
		lead = self.lead("TD404 Goi", SALE_A, at(4, 9))
		ctx_cb = {lead: at(4, 9, 30)}
		feats = {**OFF, "telephony": True}
		with (
			mock.patch.object(priority, "features", return_value=feats),
			mock.patch.object(priority, "open_callbacks", return_value=ctx_cb),
		):
			rows = self.snap(SALE_A, keys=["attention"])["attention"]["rows"]
		self.assertEqual([(r["lead"], r["reason"]) for r in rows], [(lead, "callback")])


class TestPermissions(DashCase):
	def test_sales_user_sees_only_own_leads_in_every_block(self):
		a = self.build_core()
		mine = self.snap(SALE_A)
		self.assertEqual(mine["tile_waiting"]["value"], 3)
		self.assertNotIn(a["B1"], [r["lead"] for r in mine["attention"]["rows"]])
		self.assertEqual([r["user"] for r in mine["staff"]["rows"]], [SALE_A])
		theirs = self.snap(SALE_B)
		self.assertEqual([r["lead"] for r in theirs["attention"]["rows"]], [a["B1"]])

	def test_manager_sees_whole_team_and_scope_mine_narrows(self):
		a = self.build_core()
		team = self.snap(MANAGER, scope="team")
		self.assertIn(a["B1"], [r["lead"] for r in team["attention"]["rows"]])
		self.assertGreaterEqual(team["tile_waiting"]["value"], 4)  # A3, A4, A8 + B1 (+ dữ liệu khác của site)
		rows = {r["user"]: r["waiting"] for r in team["staff"]["rows"]}
		self.assertEqual((rows[SALE_A], rows[SALE_B]), (3, 1))
		manager_mine = self.snap(MANAGER, scope="mine")
		self.assertEqual(manager_mine["tile_waiting"]["value"], 0)  # quản lý không phụ trách Lead nào

	def test_scope_team_never_widens_a_sales_user(self):
		self.build_core()
		forged = self.snap(SALE_A, scope="team")
		self.assertEqual(forged["tile_waiting"]["value"], 3)
		self.assertEqual([r["user"] for r in forged["staff"]["rows"]], [SALE_A])
		self.assertFalse(access.is_team_lead(SALE_A))
		self.assertTrue(access.is_team_lead(MANAGER))

	def test_snapshot_api_is_cached_per_user_and_guest_is_denied(self):
		self.build_core()
		dashboard.clear_cache()
		frappe.set_user(SALE_A)
		first = dashboard.snapshot("today")
		self.assertTrue(first["enabled"])
		self.assertFalse(first["can_team"])
		self.assertEqual(first["scope"], "mine")
		with mock.patch.object(blocks, "run_blocks", side_effect=AssertionError("phải lấy từ cache")):
			self.assertEqual(dashboard.snapshot("today")["blocks"], first["blocks"])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			dashboard.snapshot("today")
		dashboard.clear_cache()

	def test_dashboard_switch_off_returns_no_blocks(self):
		frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field='dashboard_enabled'")
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values ('Omni Settings','dashboard_enabled','0')"
		)
		self.addCleanup(
			frappe.db.sql,
			"delete from `tabSingles` where doctype='Omni Settings' and field='dashboard_enabled'",
		)
		dashboard.clear_cache()
		frappe.set_user(SALE_A)
		res = dashboard.snapshot("today")
		self.assertEqual((res["enabled"], res["blocks"]), (False, {}))


class TestBlockSwitches(DashCase):
	CORE = frozenset(
		{"tile_waiting", "tile_first_response", "tile_new_leads", "attention", "channels", "staff"}
	)

	def keys(self, feats, fit=True):
		with (
			mock.patch.object(priority, "features", return_value=feats),
			mock.patch("omni_crm.prospect.fit.fit_enabled", return_value=fit),
		):
			frappe.set_user(MANAGER)
			return set(blocks.run_blocks(blocks.make_query("today", "team", MANAGER)))

	def test_all_switches_off_shows_only_phase_one_blocks_no_fake_zeros(self):
		keys = self.keys(dict(OFF), fit=False)
		self.assertEqual(keys - {"funnel"}, set(self.CORE))
		for absent in (
			"tile_missed_calls",
			"tile_hot_leads",
			"tile_drafts",
			"bot_today",
			"prospects_ab",
			"tier_region",
		):
			self.assertNotIn(absent, keys)

	def test_each_phase_two_three_block_follows_its_own_switch(self):
		cases = {
			"telephony": "tile_missed_calls",
			"scoring": "tile_hot_leads",
			"draft": "tile_drafts",
			"bot": "bot_today",
		}
		for feat, key in cases.items():
			with self.subTest(feat):
				self.assertIn(key, self.keys({**OFF, feat: True}))
				self.assertNotIn(key, self.keys(dict(OFF)))
		self.assertIn("prospects_ab", self.keys(dict(OFF), fit=True))
		self.assertIn("tier_region", self.keys(dict(OFF), fit=True))

	def test_blocks_render_when_every_feature_is_on_without_errors(self):
		on = {k: True for k in OFF}
		frappe.set_user(MANAGER)
		with mock.patch.object(priority, "features", return_value=on):
			out = blocks.run_blocks(blocks.make_query("30d", "team", MANAGER))
		self.assertTrue(
			{"tile_missed_calls", "tile_hot_leads", "tile_drafts", "bot_today", "funnel", "prospects_ab"}
			<= set(out)
		)
		self.assertEqual(out["bot_today"]["drafted"], 0)  # khối bật thì hiện số thật, kể cả 0


class TestFeaturesReal(FrappeTestCase):
	"""`priority.features()` thật (không patch) đọc đúng công tắc của hệ thống."""

	def test_features_follow_real_switches(self):
		with (
			mock.patch("omni_crm.flags.bot_reply_mode", return_value="Draft"),
			mock.patch("omni_crm.flags.bot_analyze_enabled", return_value=True),
			mock.patch.object(priority, "telephony_enabled", return_value=True),
			mock.patch("omni_crm.bot.scoring.scoring_enabled", return_value=True),
			mock.patch("omni_crm.bot.scoring.load_rules", return_value={"x": {}}),
		):
			on = priority.features()
		self.assertEqual(on, {k: True for k in OFF})
		with (
			mock.patch("omni_crm.flags.bot_reply_mode", return_value="Shadow"),
			mock.patch("omni_crm.flags.bot_analyze_enabled", return_value=False),
			mock.patch.object(priority, "telephony_enabled", return_value=False),
			mock.patch("omni_crm.bot.scoring.scoring_enabled", return_value=True),
			mock.patch("omni_crm.bot.scoring.load_rules", return_value={}),  # bảng điểm rỗng = không chấm
		):
			off = priority.features()
		self.assertEqual(
			off, {"telephony": False, "analyze": False, "scoring": False, "bot": True, "draft": False}
		)


class TestB2B(DashCase):
	def setUp(self):
		super().setUp()
		self.o1 = make_org("TD404 Org A", "0900404001", omni_region="Miền Bắc")
		self.o2 = make_org("TD404 Org B", "0900404002", omni_region="Miền Nam")
		self.o3 = make_org("TD404 Org C", "0900404003")
		for org, tier, score in ((self.o1, "A", 82), (self.o2, "B", 64), (self.o3, "C", 20)):
			frappe.db.set_value(
				"CRM Organization",
				org,
				{"omni_fit_tier": tier, "omni_fit_score": score},
				update_modified=False,
			)
		self.la = self.lead("TD404 La", SALE_A, at(4, 9), organization=self.o1, channel=None)
		self.lb = self.lead("TD404 Lb", SALE_B, at(4, 9), organization=self.o2, channel=None)
		self.lc = self.lead("TD404 Lc", SALE_A, at(4, 9), organization=self.o3, channel=None)

	def test_ab_not_contacted_sorted_by_fit_score_and_respects_lead_permission(self):
		rows = self.snap(MANAGER, scope="team", keys=["prospects_ab"])["prospects_ab"]["rows"]
		mine = [r["organization"] for r in rows if r["organization"].startswith("TD404")]
		self.assertEqual(mine, [self.o1, self.o2])  # 82 trước 64; hạng C không có
		sale = self.snap(SALE_A, keys=["prospects_ab"])["prospects_ab"]["rows"]
		self.assertEqual(
			[r["organization"] for r in sale if r["organization"].startswith("TD404")], [self.o1]
		)
		other = self.snap(SALE_B, keys=["prospects_ab"])["prospects_ab"]["rows"]
		self.assertEqual(
			[r["organization"] for r in other if r["organization"].startswith("TD404")], [self.o2]
		)

	def test_contacted_organization_leaves_the_list(self):
		frappe.db.set_value("CRM Lead", self.la, "omni_first_response_at", at(4, 9, 30))
		rows = self.snap(SALE_A, keys=["prospects_ab"])["prospects_ab"]["rows"]
		self.assertNotIn(self.o1, [r["organization"] for r in rows])
		frappe.db.set_value(
			"CRM Lead", self.lb, "status", "Đã liên hệ"
		)  # đã đổi giai đoạn bằng tay cũng là đã liên hệ
		rows = self.snap(MANAGER, scope="team", keys=["prospects_ab"])["prospects_ab"]["rows"]
		self.assertNotIn(self.o2, [r["organization"] for r in rows])

	def test_organization_without_a_visible_lead_never_appears(self):
		lonely = make_org("TD404 Org Lonely", "0900404004")
		frappe.db.set_value("CRM Organization", lonely, {"omni_fit_tier": "A", "omni_fit_score": 99})
		for user in (SALE_A, SALE_B):
			rows = self.snap(user, keys=["prospects_ab"])["prospects_ab"]["rows"]
			self.assertNotIn(lonely, [r["organization"] for r in rows])

	def test_tier_by_region_counts_only_visible_organizations(self):
		def cell(user, tier, region):
			rows = self.snap(user, scope="team", keys=["tier_region"])["tier_region"]["rows"]
			return next((r["regions"][region] for r in rows if r["tier"] == tier), 0)

		# O1 (A, Bắc) và O3 (C, chưa rõ khu vực) có Lead của SALE_A; O2 (B, Nam) chỉ có Lead của SALE_B
		self.assertEqual(cell(MANAGER, "B", "Miền Nam") - cell(SALE_A, "B", "Miền Nam"), 1)
		self.assertEqual(cell(MANAGER, "A", "Miền Bắc") - cell(SALE_A, "A", "Miền Bắc"), 0)
		self.assertGreaterEqual(cell(SALE_A, "A", "Miền Bắc"), 1)
		self.assertGreaterEqual(cell(SALE_A, "C", "unknown"), 1)
		rows = self.snap(MANAGER, scope="team", keys=["tier_region"])["tier_region"]
		self.assertEqual(rows["regions"], ["Miền Bắc", "Miền Trung", "Miền Nam", "Chưa rõ"])

	def test_funnel_counts_stages_for_visible_leads(self):
		self.lead("TD404 F2", SALE_A, at(4, 9), status="Đã liên hệ")
		self.lead("TD404 F3", SALE_A, at(4, 9), status="Đã trao đổi nhu cầu")
		self.lead("TD404 F4", SALE_A, at(4, 9), status="Không phù hợp")
		funnel = {s["stage"]: s for s in self.snap(SALE_A, keys=["funnel"])["funnel"]["stages"]}
		self.assertEqual(funnel["Tiềm năng"]["count"], 2)  # La và Lc
		self.assertEqual(funnel["Đã liên hệ"]["count"], 1)
		self.assertEqual(funnel["Đã trao đổi nhu cầu"]["count"], 1)
		self.assertEqual(funnel["Không phù hợp"]["count"], 1)
		self.assertEqual(funnel["Tiềm năng"]["kind"], "Lead")
		self.assertEqual(funnel["Đề xuất gói"]["kind"], "Deal")
		other = {s["stage"]: s for s in self.snap(SALE_B, keys=["funnel"])["funnel"]["stages"]}
		self.assertEqual(other["Tiềm năng"]["count"], 1)  # chỉ Lb của SALE_B

	def test_fit_switch_off_hides_ab_list_and_matrix(self):
		with mock.patch("omni_crm.prospect.fit.fit_enabled", return_value=False):
			out = self.snap(MANAGER, scope="team")
		self.assertNotIn("prospects_ab", out)
		self.assertNotIn("tier_region", out)
		self.assertIn("funnel", out)


class TestBusinessClock(FrappeTestCase):
	def test_clock_equals_loop_implementation_for_many_pairs(self):
		import random

		rnd = random.Random(7)
		holidays = frozenset({datetime(2026, 11, 11).date()})
		clock = business_hours.BusinessClock(
			datetime(2026, 10, 20).date(), datetime(2026, 12, 5).date(), HOURS, holidays
		)
		for _ in range(300):
			a = datetime(2026, 10, 20) + timedelta(minutes=rnd.randint(0, 31 * 24 * 60))
			b = a + timedelta(minutes=rnd.randint(-60, 9 * 24 * 60))
			self.assertAlmostEqual(
				clock.minutes_between(a, b),
				business_hours.business_minutes_between(a, b, HOURS, holidays),
				places=6,
				msg=f"{a} -> {b}",
			)
