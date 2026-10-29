"""D1-11: kịch bản tích hợp bắt buộc Đợt 1 (docs/10 mục 3) còn thiếu so với bộ test sẵn có.

Mỗi test đi hết chuỗi webhook ký thật -> lưu -> worker -> Lead -> dòng thời gian (chỉ mock mạng ra ngoài).
Bảng ánh xạ I-01...I-13 -> tên test nằm ở docs/nghiem-thu/dot-1.md. Các ca I-04, I-08...I-13 đã có test
khớp đúng cột "Kỳ vọng" ở các tệp khác nên không lặp lại ở đây.
"""

import json
from types import SimpleNamespace
from unittest import mock

import frappe
from crm.api.activities import get_activities
from frappe.app import make_form_dict
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from omni_crm.api import webhooks
from omni_crm.channels.registry import register_adapter, unregister_adapter
from omni_crm.channels.zalo.adapter import parse_event
from omni_crm.ingest.gateway import save_inbound
from omni_crm.monitoring import alerts
from omni_crm.monitoring.snapshot import build_snapshot
from omni_crm.pipeline import sweeper, worker
from omni_crm.pipeline.retry import TransientError
from omni_crm.pipeline.worker import process_identity
from omni_crm.tests import zalo_helpers
from omni_crm.tests.email_helpers import EmailTestCase
from omni_crm.tests.fake_adapter import FakeAdapter, make_channel
from omni_crm.tests.zalo_helpers import PREFIX, USER, ZaloTestCase, body_of, load, now_ms, sign

INBOUND = "Omni Inbound Message"


class SignedZaloCase(ZaloTestCase):
	"""ZaloTestCase + gửi webhook có chữ ký như Zalo."""

	def post(self, data: dict, headers: dict | None = None) -> int:
		"""Gọi webhook với payload `data` (ký đúng nếu headers None); trả mã HTTP."""
		raw = body_of(data)
		hdr = sign(raw, str(data["timestamp"])) if headers is None else headers
		frappe.local.request = SimpleNamespace(
			method="POST", content_length=len(raw), get_data=lambda: raw, headers=hdr
		)
		frappe.local.form_dict = frappe._dict(ch=self.channel.name)
		frappe.local.response = frappe._dict()
		with mock.patch("frappe.enqueue"):
			webhooks.zalo()
		frappe.db.commit()
		return frappe.local.response.get("http_status_code", 200)

	def text_event(
		self, msg_id: str, user: str = USER, text: str = "Chào shop, cho mình hỏi giá máy A"
	) -> dict:
		data = load("user_send_text", user=user, timestamp=now_ms())
		data["message"] = {"msg_id": msg_id, "text": text}
		return data

	def rows(self) -> list:
		return frappe.get_all(
			INBOUND,
			filters={"channel": self.channel.name},
			fields=["name", "status", "batch_id", "lead", "attempts"],
			order_by="creation",
		)

	def key(self, user: str = USER) -> str:
		return f"Zalo OA:{user}"

	def drain(self, *users: str) -> None:
		"""Chạy worker cho từng khách như job nền (debounce 0, API hồ sơ Zalo giả lỗi, mọi enqueue giả)."""
		for u in users or (USER,):
			self.run_worker(u)


class TestI01toI02(SignedZaloCase):
	def test_i01_three_messages_one_lead_one_batch_three_timeline_entries(self):
		for i in range(3):
			self.assertEqual(self.post(self.text_event(f"i01-{i}")), 200)
		self.assertEqual(len(self.rows()), 3)
		self.drain()
		rows = self.rows()
		self.assertEqual({r.status for r in rows}, {"Done"})
		self.assertEqual(len({r.batch_id for r in rows}), 1, "3 tin phải cùng một lượt xử lý")
		leads = {r.lead for r in rows}
		self.assertEqual(len(leads), 1)
		lead = leads.pop()
		self.assertTrue(lead)
		self.assertEqual(frappe.db.count("Omni Identity", {"identity_key": self.key(), "lead": lead}), 1)
		shown = [a for a in get_activities(lead)[0] if a["activity_type"] == "communication"]
		self.assertEqual(len(shown), 3, "dòng thời gian CRM phải có đủ 3 dòng")

	def test_i02_same_event_redelivered_five_times_is_one_record(self):
		data = self.text_event("i02-1")
		for _ in range(5):
			self.assertEqual(self.post(data), 200)  # Zalo gửi lại đúng payload, đúng chữ ký
		self.assertEqual(len(self.rows()), 1)
		self.drain()
		rows = self.rows()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].status, "Done")
		self.assertEqual(frappe.db.count("Communication", {"reference_name": rows[0].lead}), 1)


class TestI03CrossChannel(EmailTestCase):
	def setUp(self):
		zalo_helpers.cleanup()
		self.addCleanup(zalo_helpers.cleanup)
		super().setUp()
		self.zalo = zalo_helpers.make_channel(
			f"{PREFIX} kenh", enabled=1, zalo_app_id=zalo_helpers.APP_ID, zalo_oa_secret_key="x"
		)
		frappe.db.commit()

	def _zalo_form(self) -> str:
		event = parse_event(self.zalo, load("user_submit_info"))
		with mock.patch("frappe.enqueue"):
			name = save_inbound(event)
			frappe.db.after_commit.run()
		frappe.db.commit()
		with (
			mock.patch("frappe.enqueue"),
			mock.patch("omni_crm.flags.sandbox", return_value=True),
		):
			process_identity(f"Zalo OA:{USER}", debounce_seconds=0)
		return name

	def test_i03_zalo_form_phone_then_email_signature_same_phone_is_one_lead(self):
		before = frappe.db.count("CRM Lead")
		zalo_msg = self._zalo_form()
		zalo_lead = frappe.db.get_value(INBOUND, zalo_msg, "lead")
		self.assertTrue(zalo_lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", zalo_lead, "omni_phone_e164"), "+84912345678")
		body = "Cho tôi xin báo giá máy A.\n\nTrân trọng,\nNguyễn Văn An\n0912 345 678"
		_comm, mail_msg = self.deliver(
			"td107.an@example.com", "Báo giá máy A", body, sender_name="Nguyễn Văn An"
		)
		self.assertEqual(frappe.db.get_value(INBOUND, mail_msg, "status"), "Done")
		self.assertEqual(self.lead_of(mail_msg), zalo_lead)
		self.assertEqual(frappe.db.count("CRM Lead"), before + 1, "chỉ được tạo đúng 1 Lead")


class TestI05toI07(SignedZaloCase):
	def _row(self, name: str):
		return frappe.db.get_value(INBOUND, name, ["status", "attempts", "next_attempt_at"], as_dict=True)

	def _due(self, name: str) -> None:
		frappe.db.set_value(INBOUND, name, "next_attempt_at", add_to_date(now_datetime(), minutes=-1))
		frappe.db.commit()

	def test_i05_three_failures_manual_and_admin_alert(self):
		self.post(self.text_event("i05-1"))
		name = self.rows()[0].name
		boom = TransientError("mạng")
		with mock.patch.object(worker.timeline, "append", side_effect=boom), mock.patch("frappe.enqueue"):
			for _ in range(3):
				process_identity(self.key(), 0)
				self._due(name)
		r = self._row(name)
		self.assertEqual((r.status, r.attempts), ("Manual", 3))
		kinds = [a.kind for a in alerts.evaluate(build_snapshot())]
		self.assertIn("tin_manual", kinds, "Manual > 0 phải sinh cảnh báo")

	def test_i06_stuck_processing_15_minutes_goes_retry_then_done(self):
		self.post(self.text_event("i06-1"))
		name = self.rows()[0].name
		frappe.db.set_value(
			INBOUND,
			name,
			{"status": "Processing", "processing_started_at": add_to_date(now_datetime(), minutes=-16)},
			update_modified=False,
		)
		frappe.db.commit()
		with mock.patch.object(sweeper, "enqueue_identity"):
			counts = sweeper.requeue_stuck()
		self.assertGreaterEqual(counts["stuck"], 1)
		self.assertEqual(self._row(name).status, "Retry")
		self._due(name)
		with mock.patch.object(sweeper, "enqueue_identity") as enq:
			sweeper.requeue_stuck()
		self.assertIn(mock.call(self.key(), after_commit=False), enq.call_args_list)
		self.drain()
		self.assertEqual(self._row(name).status, "Done")
		self.assertTrue(frappe.db.get_value(INBOUND, name, "lead"))

	def test_i07_channel_off_ten_messages_saved_then_all_processed_after_enable(self):
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 0)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		users = [f"{PREFIX}U{i}" for i in range(1, 4)]
		for i in range(10):
			self.assertEqual(self.post(self.text_event(f"i07-{i}", user=users[i % 3])), 200)
		self.assertEqual({r.status for r in self.rows()}, {"New"})
		self.assertEqual(len(self.rows()), 10, "10 tin phải được lưu dù kênh tắt")
		self.drain(*users)
		self.assertEqual({r.status for r in self.rows()}, {"New"}, "kênh tắt: worker không xử lý")
		frappe.db.set_value("Omni Channel", self.channel.name, "enabled", 1)
		frappe.db.set_value(
			INBOUND, {"channel": self.channel.name}, "creation", add_to_date(now_datetime(), minutes=-5)
		)
		frappe.db.commit()
		frappe.clear_document_cache("Omni Channel", self.channel.name)
		with mock.patch.object(sweeper, "enqueue_identity") as enq:
			sweeper.requeue_stuck()
		keys = {c.args[0] for c in enq.call_args_list if f":{PREFIX}" in c.args[0]}
		self.assertEqual(keys, {self.key(u) for u in users})
		self.drain(*users)
		rows = self.rows()
		self.assertEqual({r.status for r in rows}, {"Done"})
		self.assertEqual(len({r.lead for r in rows}), 3)


class TestRealRequestParsing(FrappeTestCase):
	"""Lỗi D1-11: Frappe bỏ tham số query `?ch=` khi thân là JSON (Zalo luôn gửi JSON), nên webhook thật trả 404.

	Các test webhook cũ gán `frappe.local.form_dict` bằng tay nên không bắt được; test này dựng Request thật
	của werkzeug và chạy `make_form_dict` của Frappe như tiến trình web.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.channel = make_channel("WH kenh JSON", enabled=1)
		register_adapter("Zalo OA", FakeAdapter())
		self.addCleanup(unregister_adapter, "Zalo OA")
		old = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", old)
		for target in ("frappe.enqueue", "frappe.db.commit"):
			p = mock.patch(target)
			p.start()
			self.addCleanup(p.stop)

	def _call(self, content_type: str) -> int:
		body = json.dumps({"events": [{"id": "json-1", "from": "U9", "text": "hi"}]}).encode()
		env = EnvironBuilder(
			method="POST",
			path="/api/method/omni_crm.api.webhooks.zalo",
			query_string={"ch": self.channel.name},
			data=body,
			content_type=content_type,
			headers={"X-Fake-Signature": FakeAdapter.GOOD_SIGNATURE},
		).get_environ()
		request = Request(env)
		frappe.local.request = request
		frappe.local.response = frappe._dict()
		make_form_dict(request)  # đúng như frappe.app.application làm trước khi gọi hàm whitelisted
		webhooks.zalo()
		return frappe.local.response.get("http_status_code", 200)

	def test_json_body_with_channel_in_query_string_is_accepted(self):
		before = frappe.db.count(INBOUND, {"channel": self.channel.name})
		self.assertEqual(self._call("application/json"), 200)
		self.assertEqual(frappe.db.count(INBOUND, {"channel": self.channel.name}), before + 1)

	def test_form_encoded_body_not_regressed(self):
		"""Chống hồi quy: ca không phải JSON vốn đã đọc được ch, bản sửa E1 không đổi nó."""
		self.assertEqual(self._call("text/plain"), 200)
