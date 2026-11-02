"""V2-01: các kiểm tra rà soát còn thiếu (công tắc mặc định phát hành, giờ làm việc dùng chung, đồng bộ Facebook của CRM)."""

import json
import re
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm import flags
from omni_crm.monitoring import alerts, snapshot

APP = Path(__file__).resolve().parent.parent


def default_of(doctype: str, fieldname: str):
	"""Giá trị `default` khai báo trong DocType (mặc định PHÁT HÀNH, không phải giá trị đang lưu trên site test)."""
	return frappe.get_meta(doctype).get_field(fieldname).default


class TestReleaseDefaults(FrappeTestCase):
	"""Bản phát hành khởi đầu an toàn: gửi thật tắt, bot tắt (docs/09, gap-report 09.07). `omni.test` có thể lệch có chủ đích."""

	def test_settings_defaults_are_off(self):
		self.assertEqual(str(default_of("Omni Settings", "sandbox_mode")), "1")
		self.assertEqual(default_of("Omni Settings", "bot_reply_mode"), "Off")
		for fieldname in ("bot_analyze_enabled", "lead_scoring_enabled"):
			self.assertEqual(str(default_of("Omni Settings", fieldname) or 0), "0", fieldname)

	def test_group_auto_send_default_off_and_infra_lock_closed_without_site_config(self):
		self.assertEqual(str(default_of("Omni Question Group", "auto_send_enabled") or 0), "0")
		with mock.patch.dict(frappe.conf, {}, clear=False):
			frappe.conf.pop("omni_auto_send_allowed", None)
			self.assertFalse(flags.auto_send_infra_allowed())

	def test_recording_switch_is_off_and_documented_as_not_built(self):
		self.assertFalse(flags.recording_enabled())  # D3-04 chưa làm: không có trường, công tắc luôn tắt


class TestBusinessHoursShared(FrappeTestCase):
	def test_only_the_shared_module_reads_business_hours_setting(self):
		pattern = re.compile(
			r"get_single_value\([^)]*business_hours|(?<!utils)\.business_hours\b|get\(\"business_hours\""
		)
		offenders = []
		for path in APP.rglob("*.py"):
			rel = path.relative_to(APP).as_posix()
			if rel.startswith("tests/") or rel == "utils/business_hours.py":
				continue
			if pattern.search(path.read_text(encoding="utf-8")):
				offenders.append(rel)
		self.assertEqual(offenders, [], "tính hạn/giờ làm việc phải dùng utils/business_hours")


class TestCrmFacebookSync(FrappeTestCase):
	"""ĐQ-11: không dùng đồng bộ lead Facebook có sẵn của app crm; bật thì có cảnh báo."""

	def snap(self, enabled):
		return {
			"queue": {"new": 0, "manual": 0, "oldest_new_age_seconds": None},
			"timing": {"count": 0, "p95": None},
			"outbound": {"failed_last_hour": 0},
			"duplicates": {"open": 0},
			"channels": [],
			"switches": {"sandbox_mode": 0, "developer_mode": 0, "crm_lead_sync_sources_enabled": enabled},
			"bot": {},
		}

	def test_alert_only_when_a_source_is_enabled(self):
		self.assertNotIn("dong_bo_lead_crm_dang_bat", {a.kind for a in alerts.evaluate(self.snap(0))})
		got = [a for a in alerts.evaluate(self.snap(2)) if a.kind == "dong_bo_lead_crm_dang_bat"]
		self.assertEqual(len(got), 1)
		self.assertIn("2", got[0].detail)

	def test_snapshot_counts_enabled_sources(self):
		self.assertEqual(
			snapshot._crm_lead_sync_enabled(), frappe.db.count("Lead Sync Source", {"enabled": 1})
		)
		with mock.patch.object(frappe.db, "count", side_effect=Exception("không có bảng")):
			self.assertEqual(snapshot._crm_lead_sync_enabled(), 0)
		self.assertIn("crm_lead_sync_sources_enabled", snapshot.build_snapshot()["switches"])

	def test_omni_does_not_depend_on_crm_lead_sync_code(self):
		for path in APP.rglob("*.py"):
			if path.relative_to(APP).as_posix().startswith("tests/"):
				continue
			self.assertNotIn("lead_syncing", path.read_text(encoding="utf-8"), path.name)
