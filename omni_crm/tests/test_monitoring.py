"""D1-10: snapshot, cảnh báo + chống lặp, dọn raw_payload, xóa dữ liệu khách, khối tình trạng hệ thống.

Site test có thể còn dữ liệu của người khác: test snapshot so sánh độ lệch với số liệu nền, không so số tuyệt đối.
Mọi lời gọi gửi thư đều bị mock.
"""

from datetime import datetime
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from omni_crm.api import admin, inbox
from omni_crm.monitoring import alerts, snapshot
from omni_crm.pipeline import sweeper
from omni_crm.tests.pipeline_helpers import PREFIX, PipelineTestCase
from omni_crm.tests.test_admin import ADMIN, SALES, _user
from omni_crm.utils import business_hours

INBOUND = "Omni Inbound Message"
OUTBOUND = "Omni Outbound Message"


def synthetic(**over):
	"""Snapshot giả ở trạng thái khỏe; ghi đè từng khối bằng over."""
	snap = {
		"generated_at": "2026-10-26 10:00:00",
		"switches": {"sandbox_mode": 0, "developer_mode": 0, "alert_recipients_set": True},
		"channels": [],
		"queue": {"new": 0, "processing": 0, "retry": 0, "manual": 0, "oldest_new_age_seconds": None},
		"timing": {"count": 0, "p50": None, "p95": None},
		"outbound": {"queued": 0, "sent": 0, "failed": 0, "blocked": 0, "failed_last_hour": 0},
		"duplicates": {"open": 0},
	}
	for k, v in over.items():
		snap[k] = {**snap[k], **v} if isinstance(v, dict) else v
	return snap


def zalo_channel(**kw):
	row = {
		"name": "K1",
		"channel_type": "Zalo OA",
		"enabled": 1,
		"outbound_enabled": 1,
		"skip_signature": 0,
		"created": datetime(2026, 10, 1),
		"last_inbound_at": datetime(2026, 10, 26, 9, 30),
		"last_outbound_at": None,
		"last_success_at": None,
		"last_error": None,
		"token_expires_at": datetime(2026, 10, 27),
		"token_seconds_left": 40000,
	}
	row.update(kw)
	return row


MONDAY_10H = datetime(2026, 10, 26, 10, 0)  # thứ hai


def kinds(alert_list):
	return sorted(a.kind + (":" + a.scope if a.scope else "") for a in alert_list)


class TestBusinessHours(PipelineTestCase):
	def test_parse_and_window(self):
		hours = business_hours.parse_hours('{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}')
		self.assertTrue(business_hours.is_business_time(MONDAY_10H, hours))
		self.assertFalse(business_hours.is_business_time(datetime(2026, 10, 26, 18, 0), hours))
		self.assertFalse(business_hours.is_business_time(datetime(2026, 10, 25, 10, 0), hours))  # chủ nhật

	def test_bad_json_falls_back_to_default(self):
		self.assertEqual(business_hours.parse_hours("{khong phai json"), business_hours.parse_hours(None))

	def test_business_seconds_skips_night_and_sunday(self):
		hours = business_hours.parse_hours(None)
		# thứ bảy 11:00 -> thứ hai 09:00: 1 giờ thứ bảy + 1 giờ thứ hai
		got = business_hours.business_seconds_between(
			datetime(2026, 10, 24, 11, 0), datetime(2026, 10, 26, 9, 0), hours
		)
		self.assertEqual(got, 2 * 3600)


class TestSnapshot(PipelineTestCase):
	def test_queue_counts_match_test_data(self):
		base = snapshot.build_snapshot()
		a, b, c = self.add_msg("s1"), self.add_msg("s2", sender=f"{PREFIX}U2"), self.add_msg("s3")
		frappe.db.set_value(INBOUND, b, "status", "Manual")
		frappe.db.set_value(INBOUND, c, "status", "Retry")
		frappe.db.set_value(
			INBOUND, a, "creation", add_to_date(now_datetime(), minutes=-30), update_modified=False
		)
		frappe.db.commit()
		snap = snapshot.build_snapshot()
		self.assertEqual(snap["queue"]["new"] - base["queue"]["new"], 1)
		self.assertEqual(snap["queue"]["manual"] - base["queue"]["manual"], 1)
		self.assertEqual(snap["queue"]["retry"] - base["queue"]["retry"], 1)
		self.assertGreaterEqual(snap["queue"]["oldest_new_age_seconds"], 29 * 60)

	def test_percentiles_exact(self):
		t0 = add_to_date(now_datetime(), days=3650)  # cửa sổ tương lai: chỉ thấy dữ liệu test
		for i in range(1, 21):
			name = self.add_msg(f"p{i}", sender=f"{PREFIX}P{i}")
			frappe.db.set_value(
				INBOUND,
				name,
				{"status": "Done", "creation": t0, "modified": add_to_date(t0, seconds=i)},
				update_modified=False,
			)
		frappe.db.commit()
		got = snapshot._percentiles(add_to_date(t0, seconds=-1))
		self.assertEqual(got, {"count": 20, "p50": 11, "p95": 20})

	def test_channels_have_no_secrets_and_report_token(self):
		frappe.db.set_value(
			"Omni Channel",
			self.channel.name,
			{
				"zalo_token_expires_at": add_to_date(now_datetime(), minutes=30),
				"last_error": "lỗi 0903123456",
			},
		)
		frappe.db.commit()
		row = next(c for c in snapshot.build_snapshot()["channels"] if c["name"] == self.channel.name)
		self.assertTrue(0 < row["token_seconds_left"] <= 1800)
		self.assertNotIn("0903123456", row["last_error"])
		self.assertNotIn("zalo_access_token", row)

	def test_outbound_counts(self):
		base = snapshot.build_snapshot()
		ident = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": f"Zalo OA:{PREFIX}O1",
				"channel_type": "Zalo OA",
				"external_id": "o1",
			}
		).insert(ignore_permissions=True)
		for status in ("Failed", "Failed", "Blocked"):
			frappe.get_doc(
				{
					"doctype": OUTBOUND,
					"channel": self.channel.name,
					"identity": ident.name,
					"text": "x",
					"source": "Sale",
					"status": status,
				}
			).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: (frappe.db.delete(OUTBOUND, {"channel": self.channel.name}), frappe.db.commit())
		)
		frappe.db.commit()
		snap = snapshot.build_snapshot()
		self.assertEqual(snap["outbound"]["failed"] - base["outbound"]["failed"], 2)
		self.assertEqual(snap["outbound"]["blocked"] - base["outbound"]["blocked"], 1)
		self.assertEqual(snap["outbound"]["failed_last_hour"] - base["outbound"]["failed_last_hour"], 2)


class TestEvaluate(PipelineTestCase):
	def ev(self, snap):
		return alerts.evaluate(snap, MONDAY_10H)

	def test_healthy_has_no_alerts(self):
		self.assertEqual(self.ev(synthetic(channels=[zalo_channel()])), [])

	def test_thresholds(self):
		self.assertEqual(kinds(self.ev(synthetic(queue={"oldest_new_age_seconds": 300}))), [])
		self.assertEqual(
			kinds(self.ev(synthetic(queue={"oldest_new_age_seconds": 301, "new": 4}))), ["tin_cho_lau"]
		)
		self.assertEqual(kinds(self.ev(synthetic(queue={"manual": 1}))), ["tin_manual"])
		self.assertEqual(kinds(self.ev(synthetic(timing={"count": 9, "p95": 120}))), [])
		self.assertEqual(kinds(self.ev(synthetic(timing={"count": 9, "p95": 121}))), ["p95_cham"])
		self.assertEqual(kinds(self.ev(synthetic(outbound={"failed_last_hour": 5}))), [])
		self.assertEqual(kinds(self.ev(synthetic(outbound={"failed_last_hour": 6}))), ["gui_loi"])
		self.assertEqual(kinds(self.ev(synthetic(duplicates={"open": 20}))), [])
		self.assertEqual(kinds(self.ev(synthetic(duplicates={"open": 21}))), ["nghi_trung"])

	def test_silent_channel_only_in_business_time(self):
		ch = zalo_channel(last_inbound_at=datetime(2026, 10, 26, 8, 0))  # 2 giờ làm việc
		self.assertEqual(self.ev(synthetic(channels=[ch])), [])
		ch = zalo_channel(last_inbound_at=datetime(2026, 10, 23, 8, 0))  # nhiều ngày
		self.assertEqual(kinds(self.ev(synthetic(channels=[ch]))), ["kenh_im_lang:K1"])
		night = alerts.evaluate(synthetic(channels=[ch]), datetime(2026, 10, 26, 22, 0))
		self.assertEqual(night, [])

	def test_silent_overnight_gap_not_counted(self):
		# nhận tin cuối thứ bảy 12:00, thứ hai 08:30 mới 0,5 giờ làm việc
		ch = zalo_channel(last_inbound_at=datetime(2026, 10, 24, 12, 0))
		got = alerts.evaluate(synthetic(channels=[ch]), datetime(2026, 10, 26, 8, 30))
		self.assertEqual(got, [])

	def test_disabled_channel_ignored(self):
		ch = zalo_channel(enabled=0, last_inbound_at=datetime(2026, 10, 1), token_seconds_left=10)
		self.assertEqual(self.ev(synthetic(channels=[ch])), [])

	def test_token_rules(self):
		self.assertEqual(
			kinds(self.ev(synthetic(channels=[zalo_channel(token_seconds_left=3599)]))),
			["token_zalo_sap_het:K1"],
		)
		self.assertEqual(self.ev(synthetic(channels=[zalo_channel(token_seconds_left=3600)])), [])
		# còn 90 phút: tác vụ làm mới chưa coi là thất bại
		self.assertEqual(self.ev(synthetic(channels=[zalo_channel(token_seconds_left=5400)])), [])
		got = self.ev(synthetic(channels=[zalo_channel(last_error="Zalo từ chối (mã -118)")]))
		self.assertEqual(kinds(got), ["token_zalo_loi:K1"])
		# sandbox: không làm mới token nên không cảnh báo token
		sb = synthetic(
			channels=[zalo_channel(token_seconds_left=5, last_error="x")], switches={"sandbox_mode": 1}
		)
		self.assertEqual(self.ev(sb), [])

	def test_refresh_error_mail_suppressed_when_token_module_already_mailed(self):
		ch = zalo_channel(last_error="Zalo từ chối (mã -118)")
		cache = frappe.cache()
		key = f"omni:alert:zalo:K1:{alerts.ZALO_REFRESH_ALERT_KIND}"
		self.assertTrue(self.ev(synthetic(channels=[ch]))[0].email)
		cache.set_value(key, 1, expires_in_sec=60)
		self.assertFalse(self.ev(synthetic(channels=[ch]))[0].email)
		cache.delete_value(key)


class TestRunChecks(PipelineTestCase):
	def setUp(self):
		super().setUp()
		_user(ADMIN, "Omni Admin")
		self.addCleanup(self._drop)
		for patcher in (
			mock.patch.object(alerts, "_recipients", return_value=["ops@example.invalid"]),
			mock.patch.object(alerts, "_admin_users", return_value=[ADMIN]),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def _drop(self):
		frappe.db.delete("Notification Log", {"for_user": ADMIN})
		frappe.delete_doc("User", ADMIN, force=True, ignore_permissions=True)
		frappe.db.commit()

	def run_with(self, snap):
		with (
			mock.patch.object(alerts, "build_snapshot", return_value=snap),
			mock.patch("frappe.sendmail") as mail,
			mock.patch.object(alerts, "is_business_time", return_value=False),
		):
			res = alerts.run_checks()
		# bỏ thư do Notification Log tự sinh (khác tiêu đề tiền tố)
		own = [c for c in mail.call_args_list if str(c.kwargs.get("subject", "")).startswith("[Omni CRM]")]
		return res, own

	def test_fires_once_then_deduped_for_30_minutes(self):
		snap = synthetic(queue={"manual": 3})
		res, mails = self.run_with(snap)
		self.assertEqual(res, {"triggered": 1, "sent": 1})
		self.assertEqual(len(mails), 1)
		self.assertEqual(mails[0].kwargs["recipients"], ["ops@example.invalid"])
		res, mails = self.run_with(snap)
		self.assertEqual((res["triggered"], res["sent"], len(mails)), (1, 0, 0))
		# hết hạn khóa chống lặp -> gửi lại
		frappe.cache().delete_keys("omni:alert:")
		res, mails = self.run_with(snap)
		self.assertEqual((res["sent"], len(mails)), (1, 1))

	def test_dedupe_key_has_30_minute_ttl(self):
		self.run_with(synthetic(queue={"manual": 3}))
		cache = frappe.cache()
		ttl = cache.ttl(cache.make_key("omni:alert:tin_manual:"))
		self.assertTrue(1700 < ttl <= 1800, ttl)

	def test_each_kind_has_its_own_key(self):
		res, mails = self.run_with(synthetic(queue={"manual": 3}, duplicates={"open": 30}))
		self.assertEqual((res["sent"], len(mails)), (2, 2))

	def test_notification_log_created_for_admin_and_no_secrets(self):
		ch = zalo_channel(last_error="lỗi token " + "A" * 40 + " sdt 0903123456")
		self.run_with(synthetic(channels=[ch]))
		logs = frappe.get_all(
			"Notification Log", filters={"for_user": ADMIN}, fields=["subject", "email_content", "link"]
		)
		self.assertEqual(len(logs), 1)
		self.assertIn("/app/omni-monitor", logs[0].link)
		self.assertNotIn("A" * 40, logs[0].email_content)
		self.assertNotIn("0903123456", logs[0].email_content)

	def test_mail_body_has_link_and_no_secret(self):
		ch = zalo_channel(last_error="lỗi token " + "B" * 40)
		_, mails = self.run_with(synthetic(channels=[ch]))
		body = mails[0].kwargs["message"]
		self.assertIn("/app/omni-monitor", body)
		self.assertNotIn("B" * 40, body)

	def test_send_failure_releases_key_and_does_not_stop_others(self):
		snap = synthetic(queue={"manual": 3}, duplicates={"open": 30})
		with (
			mock.patch.object(alerts, "build_snapshot", return_value=snap),
			mock.patch("frappe.sendmail", side_effect=[RuntimeError("smtp"), None, None, None]) as mail,
			mock.patch.object(alerts, "is_business_time", return_value=False),
			mock.patch("frappe.log_error"),
		):
			res = alerts.run_checks()
		self.assertEqual(res["sent"], 1)
		self.assertGreaterEqual(mail.call_count, 2)
		# alert lỗi đã nhả khóa -> lần sau gửi lại
		res, mails = self.run_with(snap)
		self.assertEqual(res["sent"], 1)

	def test_no_alert_on_healthy_snapshot(self):
		res, mails = self.run_with(synthetic())
		self.assertEqual((res, mails), ({"triggered": 0, "sent": 0}, []))

	def test_empty_recipients_still_notifies_admins(self):
		with mock.patch.object(alerts, "_recipients", return_value=[]):
			res, mails = self.run_with(synthetic(queue={"manual": 1}))
		self.assertEqual((res["sent"], len(mails)), (1, 0))
		self.assertEqual(frappe.db.count("Notification Log", {"for_user": ADMIN}), 1)


class TestCleanup(PipelineTestCase):
	def _setup_rows(self):
		old, new, pending = (
			self.add_msg("c1"),
			self.add_msg("c2", sender=f"{PREFIX}U2"),
			self.add_msg("c3", sender=f"{PREFIX}U3"),
		)
		for n in (old, new, pending):
			frappe.db.set_value(INBOUND, n, "raw_payload", '{"a": 1}', update_modified=False)
		frappe.db.set_value(
			INBOUND,
			old,
			{"status": "Done", "creation": add_to_date(now_datetime(), days=-40)},
			update_modified=False,
		)
		frappe.db.set_value(INBOUND, new, {"status": "Done"}, update_modified=False)
		frappe.db.set_value(
			INBOUND, pending, "creation", add_to_date(now_datetime(), days=-40), update_modified=False
		)
		frappe.db.commit()
		return old, new, pending

	def _raw(self, name):
		return frappe.db.get_value(INBOUND, name, "raw_payload")

	def _retention(self, days):
		real = frappe.db.get_single_value
		return mock.patch.object(
			frappe.db,
			"get_single_value",
			lambda dt, f=None, *a, **k: days if f == "raw_payload_retention_days" else real(dt, f, *a, **k),
		)

	def test_only_expired_processed_rows_cleared(self):
		old, new, pending = self._setup_rows()
		with self._retention(30):
			sweeper.cleanup_raw_payloads()
		self.assertFalse(self._raw(old))
		self.assertTrue(self._raw(new))
		self.assertTrue(self._raw(pending))  # chưa xử lý xong: giữ payload
		# không đổi thời gian
		self.assertEqual(frappe.db.get_value(INBOUND, old, "status"), "Done")

	def test_disabled_when_retention_zero(self):
		old, _, _ = self._setup_rows()
		with self._retention(0):
			self.assertEqual(sweeper.cleanup_raw_payloads(), 0)
		self.assertTrue(self._raw(old))

	def test_batches_and_time_budget(self):
		names = [self.add_msg(f"b{i}", sender=f"{PREFIX}B{i}") for i in range(5)]
		for n in names:
			frappe.db.set_value(
				INBOUND,
				n,
				{
					"raw_payload": '{"a": 1}',
					"status": "Done",
					"creation": add_to_date(now_datetime(), days=-40),
				},
				update_modified=False,
			)
		frappe.db.commit()
		with self._retention(30):
			self.assertEqual(sweeper.cleanup_raw_payloads(budget_seconds=0), 0)  # hết ngân sách: không làm gì
			self.assertTrue(all(self._raw(n) for n in names))
			self.assertGreaterEqual(sweeper.cleanup_raw_payloads(batch=2), 5)  # 3 lô
		self.assertFalse(any(self._raw(n) for n in names))


class TestErase(PipelineTestCase):
	def setUp(self):
		super().setUp()
		_user(SALES, "Sales User")
		_user(ADMIN, "Omni Admin")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(self._drop)
		enq = mock.patch(
			"frappe.enqueue"
		)  # hook Comment của CRM enqueue job nền (Redis queue thật không cần)
		enq.start()
		self.addCleanup(enq.stop)
		self.lead_a = self._lead("PLT Erase A")
		self.lead_b = self._lead("PLT Erase B")
		self.ident_a = self._ident("EA", self.lead_a)
		self.ident_b = self._ident("EB", self.lead_b)
		self.in_a = self._inbound("ea1", f"{PREFIX}EA", self.lead_a, self.ident_a)
		self.in_a_noleadlink = self._inbound("ea2", f"{PREFIX}EA", None, self.ident_a)  # chỉ gắn qua Identity
		self.in_b = self._inbound("eb1", f"{PREFIX}EB", self.lead_b, self.ident_b)
		self.out_a = self._outbound(self.lead_a, self.ident_a)
		self.out_b = self._outbound(self.lead_b, self.ident_b)

	def _drop(self):
		frappe.set_user("Administrator")
		frappe.db.delete(OUTBOUND, {"channel": self.channel.name})
		frappe.db.delete(
			"Comment", {"reference_doctype": "CRM Lead", "reference_name": ["in", [self.lead_a, self.lead_b]]}
		)
		for u in (SALES, ADMIN):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _lead(self, first):
		return (
			frappe.get_doc({"doctype": "CRM Lead", "first_name": first}).insert(ignore_permissions=True).name
		)

	def _ident(self, key, lead):
		return (
			frappe.get_doc(
				{
					"doctype": "Omni Identity",
					"identity_key": f"Zalo OA:{PREFIX}{key}",
					"channel_type": "Zalo OA",
					"external_id": key,
					"lead": lead,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _inbound(self, ext, sender, lead, ident):
		name = self.add_msg(ext, sender=sender)
		frappe.db.set_value(
			INBOUND,
			name,
			{
				"lead": lead,
				"identity": ident,
				"text": "noi dung bi mat",
				"attachments": '[{"url": "/files/a.png"}]',
				"raw_payload": '{"secret": "x"}',
				"sender_profile": '{"phone": "0903123456"}',
				"status": "Done",
			},
			update_modified=False,
		)
		frappe.db.commit()
		return name

	def _outbound(self, lead, ident):
		return (
			frappe.get_doc(
				{
					"doctype": OUTBOUND,
					"channel": self.channel.name,
					"identity": ident,
					"lead": lead,
					"text": "tra loi bi mat",
					"source": "Sale",
					"status": "Sent",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _row(self, name):
		return frappe.db.get_value(
			INBOUND,
			name,
			["text", "attachments", "raw_payload", "sender_profile", "status", "received_at"],
			as_dict=True,
		)

	def test_sales_user_denied_and_nothing_changed(self):
		frappe.set_user(SALES)
		with self.assertRaises(frappe.PermissionError):
			admin.erase_customer_data(self.lead_a, "yeu cau khach")
		frappe.set_user("Administrator")
		self.assertEqual(self._row(self.in_a).text, "noi dung bi mat")

	def test_reason_required(self):
		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.ValidationError):
			admin.erase_customer_data(self.lead_a, "  ")

	def test_admin_erases_only_that_lead(self):
		frappe.set_user(ADMIN)
		before = self._row(self.in_a)
		res = admin.erase_customer_data(self.lead_a, "Khách yêu cầu xóa dữ liệu")
		frappe.set_user("Administrator")
		self.assertEqual((res["inbound"], res["outbound"], res["identities"]), (2, 1, 1))
		for name in (self.in_a, self.in_a_noleadlink):
			row = self._row(name)
			self.assertEqual(row.text, "[đã xóa]")
			self.assertFalse(row.attachments)
			self.assertFalse(row.raw_payload)
			self.assertFalse(row.sender_profile)
			self.assertEqual(row.status, "Done")  # bản ghi kỹ thuật còn nguyên
		self.assertEqual(self._row(self.in_a).received_at, before.received_at)
		self.assertEqual(frappe.db.get_value(OUTBOUND, self.out_a, "text"), "[đã xóa]")
		# Lead B không bị đụng
		row_b = self._row(self.in_b)
		self.assertEqual(row_b.text, "noi dung bi mat")
		self.assertTrue(row_b.raw_payload)
		self.assertEqual(frappe.db.get_value(OUTBOUND, self.out_b, "text"), "tra loi bi mat")
		# Lead còn nguyên, có Comment lý do + người làm
		self.assertTrue(frappe.db.exists("CRM Lead", self.lead_a))
		comment = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": self.lead_a},
			pluck="content",
		)
		self.assertTrue(any("Khách yêu cầu xóa dữ liệu" in c and ADMIN in c for c in comment))
		self.assertFalse(
			frappe.get_all(
				"Comment", filters={"reference_doctype": "CRM Lead", "reference_name": self.lead_b}
			)
		)

	def test_unknown_lead(self):
		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.DoesNotExistError):
			admin.erase_customer_data("CRM-LEAD-9999-99999", "ly do ro rang")

	def test_reason_is_escaped_in_comment(self):
		frappe.set_user(ADMIN)
		admin.erase_customer_data(self.lead_a, "<script>alert(1)</script>")
		content = frappe.get_all("Comment", filters={"reference_name": self.lead_a}, pluck="content")[0]
		self.assertNotIn("<script>", content)


class TestAdminApis(PipelineTestCase):
	def setUp(self):
		super().setUp()
		_user(SALES, "Sales User")
		_user(ADMIN, "Omni Admin")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(self._drop)

	def _drop(self):
		frappe.set_user("Administrator")
		for u in (SALES, ADMIN):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_sales_denied_everywhere(self):
		frappe.set_user(SALES)
		for fn, args in (
			(admin.monitor_snapshot, ()),
			(admin.list_manual, ()),
			(admin.sweep_now, ()),
			(admin.refresh_zalo_token, ("x",)),
		):
			with self.assertRaises(frappe.PermissionError, msg=fn.__name__):
				fn(*args)

	def test_snapshot_for_admin_has_all_blocks(self):
		frappe.set_user(ADMIN)
		snap = admin.monitor_snapshot()
		for key in ("switches", "channels", "queue", "timing", "outbound", "duplicates", "sweeper"):
			self.assertIn(key, snap)

	def test_list_manual_masks_and_hides_text(self):
		name = self.add_msg("lm1")
		frappe.db.set_value(INBOUND, name, {"status": "Manual", "last_error": "loi voi 0903123456"})
		frappe.db.commit()
		frappe.set_user(ADMIN)
		rows = [r for r in admin.list_manual(100) if r.name == name]
		self.assertEqual(len(rows), 1)
		self.assertNotIn("0903123456", rows[0].last_error)
		self.assertNotIn("text", rows[0])

	def test_sweep_now_runs_sweeper(self):
		frappe.set_user(ADMIN)
		with mock.patch("omni_crm.pipeline.sweeper.enqueue_identity"):
			counts = admin.sweep_now()
		self.assertIn("checked_at", counts)

	def test_refresh_token_sandbox_gives_friendly_error(self):
		frappe.set_user(ADMIN)
		with (
			mock.patch("omni_crm.flags.sandbox", return_value=True),
			self.assertRaises(frappe.ValidationError),
		):
			admin.refresh_zalo_token(self.channel.name)

	def test_refresh_token_calls_force_refresh(self):
		frappe.set_user(ADMIN)
		with mock.patch("omni_crm.channels.zalo.token.refresh_channel", return_value=True) as refresh:
			self.assertEqual(admin.refresh_zalo_token(self.channel.name), {"refreshed": True})
		refresh.assert_called_once_with(self.channel.name, force=True)


class TestSystemStatus(PipelineTestCase):
	def setUp(self):
		super().setUp()
		frappe.cache().delete_value(inbox.STATUS_CACHE_KEY)
		self.addCleanup(frappe.cache().delete_value, inbox.STATUS_CACHE_KEY)

	def test_zalo_error_message_is_fixed_text(self):
		frappe.db.set_value(
			"Omni Channel", self.channel.name, "last_error", "chi tiet 0903123456 " + "Z" * 40
		)
		frappe.db.commit()
		with (
			mock.patch("omni_crm.flags.sandbox", return_value=False),
			mock.patch.object(snapshot.frappe, "get_cached_doc") as doc,
		):
			doc.return_value = mock.Mock(sandbox_mode=0, alert_recipients="", raw_payload_retention_days=0)
			msgs = inbox.system_status()["messages"]
		self.assertIn("Zalo đang mất kết nối, tin đến vẫn được lưu", msgs)
		self.assertFalse(any("0903" in m or "ZZZZ" in m for m in msgs))

	def test_outbound_disabled_message(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "outbound_enabled", 0)
		frappe.db.commit()
		msgs = inbox.system_status()["messages"]
		self.assertIn("Gửi tin qua Zalo OA đang tạm tắt", msgs)
