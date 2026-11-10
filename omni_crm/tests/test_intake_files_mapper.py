"""Ánh xạ file JSON của miyano-intake sang InboundEvent: hàm thuần, không cần DB (chạy được không cần site)."""

import hashlib
import unittest

from omni_crm.intake_files import mapper


def env(**over):
	base = {
		"schema": "1",
		"event_key": "gmail:abc123@mail.example",
		"channel": "gmail",
		"kind": "email",
		"direction": "in",
		"occurred_at": "2026-11-01T18:01:56+07:00",
		"received_at": "2026-11-02T17:46:38+07:00",
		"sender": {"type": "email", "value": "Khach@Cty.vn", "display_name": "Lê Khách"},
		"identifiers": {"phone": None, "email": "khach@cty.vn", "tax_id": None},
		"content": {"text": "Báo giá giúp mình\n\nTrân trọng,\nKhách\n0912345678", "attachments": []},
		"ignored": False,
		"ignore_reasons": [],
		"raw": {"subject": "Báo giá"},
		"source": {"account": "info@miyano.com.vn", "signature_verified": True},
	}
	base.update(over)
	return base


def call(**over):
	c = {
		"call_id": "1700000000.12",
		"event_type": "ended",
		"from": "0912345678",
		"to": "02812345678",
		"agent_ref": "101",
		"started_at": "2026-11-03T09:00:00+07:00",
		"answered_at": "2026-11-03T09:00:05+07:00",
		"ended_at": "2026-11-03T09:02:00+07:00",
		"answer_duration": 115,
		"disposition": "answered",
		"recording": None,
	}
	c.update(over)
	return env(event_key="voice:asterisk:1700000000.12:ended", channel="voice", kind="call", call=c)


class TestMapper(unittest.TestCase):
	def test_loai_khong_ho_tro(self):
		self.assertEqual(mapper.map_envelope(env(schema="9")).skip, mapper.SKIP_UNSUPPORTED_SCHEMA)
		self.assertEqual(mapper.map_envelope("x").skip, mapper.SKIP_UNSUPPORTED_SCHEMA)
		self.assertEqual(mapper.map_envelope(env(kind="message")).skip, mapper.SKIP_NO_CHANNEL_TYPE)
		self.assertEqual(
			mapper.map_envelope(env(kind="lead_form", channel="meta")).skip, mapper.SKIP_LIVE_PATH
		)

	def test_email(self):
		m = mapper.map_envelope(env())
		self.assertEqual(m.channel_type, "Email")
		ev = mapper.with_channel(env(), m, "Hộp thư bán hàng").event
		self.assertEqual(ev.channel_type, "Email")
		self.assertEqual(ev.event_type, "message")
		self.assertEqual(ev.external_message_id, "abc123@mail.example")  # bỏ tiền tố gmail:, trùng Message-ID
		self.assertEqual(ev.external_sender_id, "khach@cty.vn")
		self.assertEqual(ev.received_at.year, 2026)
		self.assertIsNone(ev.received_at.tzinfo)
		self.assertEqual(ev.sender_profile["display_name"], "Lê Khách")
		self.assertEqual(ev.raw["intake"]["event_key"], "gmail:abc123@mail.example")  # JSON gốc được giữ

	def test_email_bi_loai(self):
		e = env(ignored=True, ignore_reasons=["bulk"])
		ev = mapper.with_channel(e, mapper.map_envelope(e), "K").event
		self.assertEqual(ev.event_type, "ignored")

	def test_email_reply_to_la_dinh_danh(self):
		e = env(sender={"type": "email", "value": "form@cty.vn"}, identifiers={"email": "that@khach.vn"})
		ev = mapper.with_channel(e, mapper.map_envelope(e), "K").event
		self.assertEqual(ev.external_sender_id, "that@khach.vn")
		self.assertEqual(ev.sender_profile["reply_to_email"], "that@khach.vn")

	def test_cuoc_goi(self):
		e = call()
		m = mapper.map_envelope(e)
		self.assertEqual(m.channel_type, "Telephony")
		ev = mapper.with_channel(e, m, "Tổng đài").event
		self.assertEqual(ev.event_type, "call_ended")
		self.assertEqual(ev.external_message_id, "1700000000.12")
		self.assertEqual(ev.external_sender_id, "0912345678")  # gọi vào: khách là số gọi
		data = ev.raw["call_event"]
		self.assertEqual(data["direction"], "Inbound")
		self.assertEqual(data["status"], "Answered")
		self.assertEqual(data["duration_seconds"], 115)

	def test_goi_ra_khach_la_so_nhan(self):
		e = call()
		e["direction"] = "out"
		e["call"].update({"from": "02812345678", "to": "0912345678", "disposition": "missed"})
		ev = mapper.with_channel(e, mapper.map_envelope(e), "K").event
		self.assertEqual(ev.external_sender_id, "0912345678")
		self.assertEqual(ev.raw["call_event"]["status"], "Missed")

	def test_goi_chua_ket_thuc_va_ghi_am_bi_bo_qua(self):
		for e in (
			call(ended_at=None, disposition=None),
			call(event_type="recording"),
			call(call_id=None),
		):
			self.assertEqual(mapper.with_channel(e, mapper.map_envelope(e), "K").skip, mapper.SKIP_EMPTY)

	def test_form_landing_cung_submission_id_voi_relay(self):
		e = env(
			event_key="web:spd:abc",
			channel="web_form",
			kind="lead_form",
			sender={"type": "phone", "value": "0912345678", "display_name": "Lê Khách"},
			identifiers={"phone": "+84912345678", "email": "khach@cty.vn", "tax_id": None},
			campaign={"utm_source": "google", "gclid": "G1", "utm_campaign": None},
			raw={"fields": {"need": "Điều phối vật tư", "message": "Cần demo", "organization": "Cty A"}},
			source={"account": "spd", "signature_verified": False},
		)
		m = mapper.map_envelope(e)
		self.assertEqual(m.channel_type, "Web Form")
		b = m.body
		self.assertEqual(b["submission_id"], hashlib.sha1(b"web:spd:abc").hexdigest())
		self.assertEqual(b["form"], "spd")
		self.assertEqual(b["company_name"], "Cty A")
		self.assertEqual(b["message"], "Vấn đề quan tâm: Điều phối vật tư\nCần demo")
		self.assertEqual((b["utm_source"], b["gclid"]), ("google", "G1"))
		self.assertNotIn("utm_campaign", b)  # giá trị null bị bỏ

	def test_dat_lich(self):
		e = env(
			event_key="booking:r:2026-11-10T09:00",
			channel="web_form",
			kind="booking",
			content={"text": "Đặt lịch demo: 10/11 09:00", "attachments": []},
			source={"account": "tong-thau"},
		)
		m = mapper.map_envelope(e)
		self.assertEqual(m.channel_type, "Web Form")
		self.assertEqual(m.body["message"], "Đặt lịch demo: 10/11 09:00")
		self.assertNotIn("company_name", m.body)


if __name__ == "__main__":
	unittest.main()
