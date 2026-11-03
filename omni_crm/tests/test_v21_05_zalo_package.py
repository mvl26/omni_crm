"""V21-05: hạn mức tin ngoài 48 giờ, cảnh báo gói OA, công cụ chẩn đoán chữ ký Zalo."""

import json
import os
import shutil
import stat
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import admin as admin_api
from omni_crm.api import inbox as inbox_api
from omni_crm.channels.zalo import quota, sigprobe
from omni_crm.channels.zalo.adapter import ZaloAdapter
from omni_crm.monitoring import snapshot
from omni_crm.tests.fake_adapter import make_channel
from omni_crm.tests.zalo_helpers import body_of, sign

PREFIX = "TD2105"
APP_ID = "app-td2105"
SECRET = "oa-secret-td2105-khong-that"
NOW = datetime(2026, 3, 15, 12, 0, 0)


def _cleanup() -> None:
	frappe.db.rollback()
	frappe.db.delete("Omni Inbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Outbound Message", {"channel": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Omni Identity", {"identity_key": ["like", f"%:{PREFIX}%"]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.commit()
	shutil.rmtree(sigprobe.probe_dir(), ignore_errors=True)


class _Base(FrappeTestCase):
	def setUp(self):
		_cleanup()
		self.addCleanup(_cleanup)
		self.channel = make_channel(
			f"{PREFIX} kenh",
			enabled=1,
			outbound_enabled=1,
			zalo_app_id=APP_ID,
			zalo_oa_secret_key=SECRET,
		)
		self.n = 0

	def identity(self) -> str:
		self.n += 1
		doc = frappe.get_doc(
			{
				"doctype": "Omni Identity",
				"identity_key": f"Zalo OA:{PREFIX}U{self.n}",
				"channel_type": "Zalo OA",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def out(self, identity: str, sent_at: datetime, **kw) -> str:
		row = {
			"doctype": "Omni Outbound Message",
			"channel": self.channel.name,
			"identity": identity,
			"text": "x",
			"source": "Sale",
			"sent_by": "Administrator",
			"status": "Sent",
			"sent_at": sent_at,
			"is_sandbox": 0,
			**kw,
		}
		return frappe.get_doc(row).insert(ignore_permissions=True).name

	def inbound(self, identity: str, received_at: datetime, event_type: str = "message") -> None:
		self.n += 1
		frappe.get_doc(
			{
				"doctype": "Omni Inbound Message",
				"dedupe_key": f"{PREFIX}-{self.n}-{received_at.timestamp()}",
				"identity_key": f"Zalo OA:{PREFIX}",
				"channel": self.channel.name,
				"channel_type": "Zalo OA",
				"event_type": event_type,
				"identity": identity,
				"received_at": received_at,
				"status": "Done",
			}
		).insert(ignore_permissions=True)

	def used(self, now: datetime = NOW) -> int:
		return quota.count_out_of_window(self.channel.name, 48, now)


class TestQuotaCount(_Base):
	def test_month_boundaries(self):
		for sent in (
			datetime(2026, 2, 28, 23, 59, 59),
			datetime(2026, 3, 1, 0, 0, 0),
			datetime(2026, 3, 31, 23, 59, 59),
			datetime(2026, 4, 1, 0, 0, 0),
		):
			self.out(self.identity(), sent)  # khách chưa từng nhắn: luôn ngoài khung
		self.assertEqual(self.used(NOW), 2)  # chỉ 1/3 00:00:00 và 31/3 23:59:59
		self.assertEqual(self.used(datetime(2026, 2, 10)), 1)
		self.assertEqual(self.used(datetime(2026, 4, 30, 8)), 1)
		self.assertEqual(quota.month_bounds(datetime(2026, 12, 31, 23))[1], datetime(2027, 1, 1))

	def test_window_edge_is_inclusive_like_send_window(self):
		sent = datetime(2026, 3, 10, 12, 0, 0)
		for delta in (timedelta(hours=47, minutes=59), timedelta(hours=48), timedelta(hours=48, seconds=1)):
			ident = self.identity()
			self.inbound(ident, sent - delta)
			self.out(ident, sent)
		self.assertEqual(self.used(), 1)  # chỉ tin mà khách nhắn cuối cách 48 giờ + 1 giây

	def test_inbound_after_send_does_not_count_as_window(self):
		ident = self.identity()
		self.inbound(ident, datetime(2026, 3, 10, 12, 0, 1))
		self.out(ident, datetime(2026, 3, 10, 12, 0, 0))
		self.assertEqual(self.used(), 1)

	def test_echo_and_unfollow_do_not_open_the_window(self):
		ident = self.identity()
		self.inbound(ident, datetime(2026, 3, 10, 11, 0, 0), "oa_echo")
		self.inbound(ident, datetime(2026, 3, 10, 11, 30, 0), "unfollow")
		self.out(ident, datetime(2026, 3, 10, 12, 0, 0))
		self.assertEqual(self.used(), 1)
		self.inbound(ident, datetime(2026, 3, 10, 11, 45, 0), "follow")
		self.assertEqual(self.used(), 0)

	def test_only_sent_sale_api_messages_non_sandbox_of_this_channel(self):
		when = datetime(2026, 3, 10, 12)
		ident = self.identity()
		self.out(ident, when)  # đếm
		self.out(ident, when, is_sandbox=1)
		self.out(ident, when, sent_by=None)  # tin sale trả lời thẳng trên ứng dụng Zalo (oa_echo)
		self.out(ident, when, source="Bot")
		self.out(ident, when, status="Failed")
		self.out(ident, when, status="Blocked")
		self.assertEqual(self.used(), 1)
		other = make_channel(f"{PREFIX} kenh2", zalo_app_id=APP_ID)
		self.assertEqual(quota.count_out_of_window(other.name, 48, NOW), 0)

	def test_usage_text_known_unknown_and_over(self):
		for _i in range(3):
			self.out(self.identity(), datetime(2026, 3, 10, 12))
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		doc.zalo_cs_paid_quota_month = 500
		info = quota.usage(doc, NOW)
		self.assertEqual((info["used"], info["quota"], info["over"]), (3, 500, False))
		self.assertIn("Đã dùng 3 / 500 tin ngoài 48 giờ", info["text"])
		doc.zalo_cs_paid_quota_month = 3
		self.assertTrue(quota.usage(doc, NOW)["over"])  # chỉ báo, không có gì bị chặn
		doc.zalo_cs_paid_quota_month = None
		info = quota.usage(doc, NOW)
		self.assertIsNone(info["quota"])
		self.assertIn("chưa biết hạn mức", info["text"])
		doc.zalo_cs_paid_quota_month = 0
		self.assertIsNone(quota.usage(doc, NOW)["quota"])

	def test_negative_quota_rejected_and_defaults(self):
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		self.assertEqual(doc.zalo_oa_package, "Chưa xác nhận")
		doc.zalo_cs_paid_quota_month = -1
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_compose_quota_never_breaks_compose_and_does_not_block(self):
		doc = frappe.get_doc("Omni Channel", self.channel.name)
		info = inbox_api._quota_safe(doc)
		self.assertEqual(info["used"], 0)
		with mock.patch("omni_crm.channels.zalo.quota.usage", side_effect=RuntimeError("x")):
			self.assertIsNone(inbox_api._quota_safe(doc))
		# vượt hạn mức không đổi kết quả can_send_now
		doc.zalo_cs_paid_quota_month = 1
		ident = frappe._dict(last_inbound_at=now_datetime() - timedelta(hours=60))
		ok, _why = ZaloAdapter().can_send_now(ident, doc)
		self.assertTrue(ok)


class TestPackageWarning(_Base):
	def row(self):
		return next(c for c in snapshot._channel_rows(now_datetime()) if c["name"] == self.channel.name)

	def test_warning_for_unconfirmed_basic_standard_only(self):
		expected = {
			"Chưa xác nhận": True,
			"Cơ bản": True,
			"Tiêu chuẩn": True,
			"Tăng trưởng": False,
			"Toàn diện": False,
		}
		for package, warn in expected.items():
			frappe.db.set_value("Omni Channel", self.channel.name, "zalo_oa_package", package)
			row = self.row()
			self.assertEqual(row["oa_package_warning"], warn, package)
			self.assertEqual(row["oa_package"], package)
		# không chặn kênh: kênh vẫn bật
		self.assertEqual(self.row()["enabled"], 1)

	def test_non_zalo_channel_has_no_warning(self):
		doc = frappe.get_doc(
			{"doctype": "Omni Channel", "channel_name": f"{PREFIX} mail", "channel_type": "Web Form"}
		)
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		row = next(c for c in snapshot._channel_rows(now_datetime()) if c["name"] == doc.name)
		self.assertFalse(row["oa_package_warning"])
		self.assertIsNone(row["oa_package"])


class _ProbeBase(_Base):
	def conf(self, value):
		patcher = mock.patch.dict(frappe.conf, {sigprobe.CONF_KEY: value})
		patcher.start()
		self.addCleanup(patcher.stop)

	def at(self, when: datetime):
		patcher = mock.patch("omni_crm.channels.zalo.sigprobe.now_datetime", return_value=when)
		patcher.start()
		self.addCleanup(patcher.stop)

	def records(self) -> list[str]:
		folder = sigprobe.probe_dir()
		return sorted(n for n in os.listdir(folder) if n.endswith(".json")) if os.path.isdir(folder) else []


class TestProbeSwitch(_ProbeBase):
	def test_off_without_flag_or_with_bad_value(self):
		for value in (None, "", "khong-phai-ngay", 12345):
			with mock.patch.dict(frappe.conf, {sigprobe.CONF_KEY: value}):
				self.assertFalse(sigprobe.is_active())
				self.assertFalse(sigprobe.record_rejection(b"{}", {}, APP_ID, SECRET, "chu_ky_sai", "c"))
		self.assertEqual(self.records(), [])

	def test_expired_flag_is_off(self):
		self.conf((now_datetime() - timedelta(minutes=1)).isoformat())
		self.assertFalse(sigprobe.is_active())

	def test_far_future_is_cut_to_two_hours_from_first_use_and_stays_off(self):
		self.conf("2099-01-01T00:00:00")
		t0 = datetime(2026, 11, 3, 8, 0, 0)
		self.at(t0)
		self.assertEqual(sigprobe.effective_until(), t0 + timedelta(hours=2))
		self.at(t0 + timedelta(hours=1, minutes=59))
		self.assertTrue(sigprobe.is_active())
		self.at(t0 + timedelta(hours=2, minutes=1))
		self.assertFalse(sigprobe.is_active())
		self.at(t0 + timedelta(days=3))  # đặt hạn xa không bật lại được
		self.assertFalse(sigprobe.is_active())
		sigprobe.cleanup(t0 + timedelta(days=3))  # dọn không quên dấu bật khi cờ vẫn còn
		self.assertFalse(sigprobe.is_active())

	def test_short_value_is_respected_and_new_value_rearms(self):
		t0 = datetime(2026, 11, 3, 8, 0, 0)
		self.at(t0)
		self.conf((t0 + timedelta(minutes=30)).isoformat())
		self.assertEqual(sigprobe.effective_until(), t0 + timedelta(minutes=30))
		self.at(t0 + timedelta(minutes=31))
		self.assertFalse(sigprobe.is_active())
		self.conf("2099-01-01T00:00:00")  # giá trị mới: đếm lại 2 giờ
		self.assertEqual(sigprobe.effective_until(), t0 + timedelta(minutes=31, hours=2))

	def test_timezone_aware_value(self):
		self.conf((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
		self.assertIsNotNone(
			sigprobe.effective_until()
		)  # đổi đúng múi giờ, không ném lỗi so sánh naive/aware
		self.conf((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"))
		self.assertIsNone(sigprobe.effective_until())


class TestProbeRecord(_ProbeBase):
	def raw(self, ts="1730000000000", extra=None):
		return body_of({"event_name": "user_send_text", "timestamp": ts, **(extra or {})})

	def test_record_content_has_no_secret_and_masks_it_in_body(self):
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		raw = self.raw(extra={"message": {"text": "mat khau " + SECRET + " " + "a" * 400}})
		headers = {"X-ZEvent-Signature": "mac=" + "0" * 64, "Authorization": "Bearer khong-ghi"}
		self.assertTrue(
			sigprobe.record_rejection(raw, headers, APP_ID, SECRET, "chu_ky_sai", self.channel.name)
		)
		name = self.records()[0]
		text = open(os.path.join(sigprobe.probe_dir(), name), encoding="utf-8").read()
		self.assertNotIn(SECRET, text)
		self.assertNotIn("khong-ghi", text)
		data = json.loads(text)
		self.assertEqual(data["reason"], "chu_ky_sai")
		self.assertEqual(data["signature_received"], "0" * 64)
		self.assertEqual(data["body_length"], len(raw))
		self.assertEqual(len(data["body_preview"]), 200)
		self.assertIn("authorization", data["header_names"])
		self.assertEqual(len(data["signature_computed"]), 64)
		self.assertEqual(data["matched_variants"], [])
		self.assertFalse(data["body_has_mac_field"])

	def test_computed_mac_and_matching_variant_names(self):
		raw = self.raw()
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		headers = sign(raw, "1730000000000", APP_ID, SECRET)
		rec = sigprobe.build_record(raw, headers, APP_ID, SECRET, "timestamp_lech", "c")
		self.assertEqual(rec["signature_computed"], rec["signature_received"])
		self.assertEqual(rec["matched_variants"], ["sha256(app_id+body+timestamp+secret)"])
		import hashlib
		import hmac as _hmac

		alt = {"X-ZEvent-Signature": _hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()}
		rec = sigprobe.build_record(raw, alt, APP_ID, SECRET, "chu_ky_sai", "c")
		self.assertEqual(rec["matched_variants"], ["hmac_sha256(secret,body)"])
		self.assertNotEqual(rec["signature_computed"], rec["signature_received"])

	def test_garbage_and_missing_header_do_not_raise(self):
		rec = sigprobe.build_record(b"\xff\xfenot json", {}, APP_ID, SECRET, "thieu_chu_ky", "c")
		self.assertEqual(rec["signature_received"], "")
		self.assertEqual(rec["matched_variants"], [])

	def test_files_are_private_and_capped(self):
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		sigprobe.record_rejection(self.raw(), {}, APP_ID, SECRET, "thieu_chu_ky", "c")
		folder = sigprobe.probe_dir()
		self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
		self.assertEqual(stat.S_IMODE(os.stat(os.path.join(folder, self.records()[0])).st_mode), 0o600)
		with mock.patch.object(sigprobe, "MAX_RECORDS", 1):
			self.assertFalse(sigprobe.record_rejection(self.raw(), {}, APP_ID, SECRET, "x", "c"))
		self.assertEqual(len(self.records()), 1)

	def test_cleanup_removes_after_24h_only(self):
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		sigprobe.record_rejection(self.raw(), {}, APP_ID, SECRET, "a", "c")
		sigprobe.record_rejection(self.raw(), {}, APP_ID, SECRET, "b", "c")
		old, fresh = [os.path.join(sigprobe.probe_dir(), n) for n in self.records()]
		past = (now_datetime() - timedelta(hours=24, minutes=1)).timestamp()
		os.utime(old, (past, past))
		self.assertEqual(sigprobe.cleanup(), 1)
		self.assertFalse(os.path.exists(old))
		self.assertTrue(os.path.exists(fresh))

	def test_records_stay_readable_after_the_flag_expires_until_24h(self):
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		sigprobe.record_rejection(self.raw(), {}, APP_ID, SECRET, "a", "c")
		self.conf(None)
		self.assertFalse(sigprobe.is_active())
		self.assertEqual(len(sigprobe.list_records()), 1)


class TestProbeIntegration(_ProbeBase):
	def verify(self, raw, headers):
		return ZaloAdapter().verify_request(raw, headers, frappe.get_doc("Omni Channel", self.channel.name))

	def test_only_rejected_webhooks_are_recorded_and_only_when_on(self):
		raw = body_of({"event_name": "user_send_text", "timestamp": str(int(time.time() * 1000))})
		good = sign(raw, json.loads(raw)["timestamp"], APP_ID, SECRET)
		self.assertFalse(self.verify(raw, {"X-ZEvent-Signature": "mac=" + "1" * 64}))
		self.assertEqual(self.records(), [])  # cờ tắt
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		self.assertTrue(self.verify(raw, good))
		self.assertEqual(self.records(), [])  # webhook đúng không ghi
		self.assertFalse(self.verify(raw, {"X-ZEvent-Signature": "mac=" + "1" * 64}))
		self.assertEqual(len(self.records()), 1)

	def test_probe_failure_never_breaks_the_webhook(self):
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		with mock.patch("omni_crm.channels.zalo.sigprobe.build_record", side_effect=RuntimeError("hong")):
			self.assertFalse(self.verify(b"{}", {}))

	def test_admin_api_is_omni_admin_only(self):
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		sigprobe.record_rejection(b"{}", {}, APP_ID, SECRET, "thieu_chu_ky", "c")
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, admin_api.zalo_signature_probes)
		frappe.set_user("Administrator")
		res = admin_api.zalo_signature_probes()
		self.assertTrue(res["active"])
		self.assertEqual(len(res["records"]), 1)
		self.assertNotIn(SECRET, json.dumps(res))

	def test_snapshot_reports_active_probe(self):
		self.assertIsNone(snapshot._sigprobe_until())
		self.conf((now_datetime() + timedelta(minutes=30)).isoformat())
		self.assertIsNotNone(snapshot._sigprobe_until())

	def test_hourly_cleanup_is_scheduled(self):
		from omni_crm import hooks

		self.assertIn("omni_crm.channels.zalo.sigprobe.cleanup", hooks.scheduler_events["hourly"])
