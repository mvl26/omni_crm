# ruff: noqa: RUF001
"""V2-08: nghiệm thu sẵn sàng chiến dịch. (1) mọi tên test trong bảng ánh xạ còn tồn tại; (2) webhook quảng cáo không chữ ký/sai khóa."""

import hashlib
import hmac
import importlib

from omni_crm.api import webhooks
from omni_crm.tests.acceptance.scenario_map import MAP
from omni_crm.tests.ads_helpers import (
	APP_SECRET,
	GOOGLE_KEY,
	PREFIX,
	SALE,
	AdsCase,
	body,
	call_webhook,
	inbound,
	load,
	make_channel,
	make_form,
	make_users,
)


def resolve(dotted: str):
	"""`tệp.Lớp.hàm` trong omni_crm/tests -> hàm test (ném lỗi nếu không có)."""
	module, cls, fn = dotted.split(".")
	return getattr(getattr(importlib.import_module(f"omni_crm.tests.{module}"), cls), fn)


class TestScenarioMapExists(AdsCase):
	def test_every_mapped_test_exists_and_every_scenario_has_one(self):
		self.assertGreaterEqual(len(MAP), 40)
		for code, (_desc, names) in MAP.items():
			self.assertTrue(names, code)
			for name in names:
				with self.subTest(code=code, test=name):
					self.assertTrue(callable(resolve(name)), name)

	def test_all_thirteen_phase_one_scenarios_are_present(self):
		for n in range(1, 14):
			self.assertIn(f"I-{n:02d}", MAP)


class TestAdWebhooksRejectUnsigned(AdsCase):
	def setUp(self):
		super().setUp()
		make_users()
		self.meta = make_channel("Meta Lead Ads", f"{PREFIX} meta")
		self.google = make_channel("Google Lead Form", f"{PREFIX} google")
		make_form("Meta", f"{PREFIX}FORM", "Meta form", default_owner=SALE)
		make_form("Google", f"{PREFIX}GFORM", "Google form", secret=GOOGLE_KEY, default_owner=SALE)

	def test_meta_and_google_without_or_with_wrong_credentials_store_nothing(self):
		raw = body(load("meta_leads/webhook_leadgen.json"))
		wrong = "sha256=" + hmac.new(b"khoa-sai", raw, hashlib.sha256).hexdigest()
		good = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
		for headers in ({}, {"X-Hub-Signature-256": wrong}, {"X-Hub-Signature-256": good[:-4] + "0000"}):
			_r, code = call_webhook(webhooks.meta_leads, raw, headers, args={"ch": self.meta.name})
			self.assertEqual(code, 401, headers)
		google = load("google_leads/webhook.json")
		no_key = {k: v for k, v in google.items() if k != "google_key"}
		for payload in (no_key, {**google, "google_key": "khoa-sai"}, {**google, "google_key": ""}):
			_r, code = call_webhook(webhooks.google_leads, body(payload), args={"ch": self.google.name})
			self.assertEqual(code, 401)
		self.assertEqual(inbound(self.meta.name), [])
		self.assertEqual(inbound(self.google.name), [])

	def test_unknown_channel_and_wrong_channel_type_are_404(self):
		raw = body(load("meta_leads/webhook_leadgen.json"))
		_r, code = call_webhook(webhooks.meta_leads, raw, {}, args={"ch": "khong-co-kenh"})
		self.assertEqual(code, 404)
		_r, code = call_webhook(webhooks.google_leads, raw, {}, args={"ch": self.meta.name})
		self.assertEqual(code, 404)
