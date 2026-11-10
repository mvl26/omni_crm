"""Nạp file miyano-intake vào site thật: file -> Omni Inbound Message -> worker -> Lead / Omni Call.

Dữ liệu được commit thật nên dọn thủ công (tiền tố TD16). Envelope dựng theo file mẫu thật trong spool của intake.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.intake_files import runner
from omni_crm.tests.ads_helpers import make_form, work
from omni_crm.tests.telephony_helpers import make_channel as make_tel_channel

PREFIX = "TD16"
ACCOUNT = f"{PREFIX} hop thu"
MAIL = f"{PREFIX.lower()}.sale@example.org"
CH_MAIL, CH_TEL, CH_WEB = f"{PREFIX} email", f"{PREFIX} tong dai", f"{PREFIX} web"
CUSTOMER_MAIL = f"{PREFIX.lower()}.khach@cty-td16.vn"
PHONE = "0912345616"


def envelope(kind, key, **over):
	base = {
		"schema": "1",
		"event_key": key,
		"kind": kind,
		"direction": "in",
		"occurred_at": "2026-11-03T09:00:00+07:00",
		"received_at": "2026-11-03T09:00:05+07:00",
		"sender": {"type": "email", "value": CUSTOMER_MAIL, "display_name": f"{PREFIX} Lê Khách"},
		"identifiers": {"phone": None, "email": CUSTOMER_MAIL, "tax_id": None},
		"content": {"text": "Cần báo giá điều phối vật tư", "attachments": []},
		"campaign": {},
		"call": None,
		"ignored": False,
		"ignore_reasons": [],
		"source": {"account": MAIL, "signature_verified": True},
		"raw": {"subject": "Báo giá"},
	}
	base.update(over)
	return base


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	channels = [CH_MAIL, CH_TEL, CH_WEB]
	idents = frappe.get_all(
		"Omni Identity",
		or_filters=[
			["identity_key", "like", f"%{PREFIX.lower()}%"],
			["identity_key", "like", f"%{PHONE[1:]}%"],
		],
		pluck="name",
	)
	leads = set(frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead"))
	leads.update(frappe.get_all("Omni Inbound Message", filters={"channel": ["in", channels]}, pluck="lead"))
	leads.update(frappe.get_all("CRM Lead", filters={"email": ["like", f"{PREFIX.lower()}%"]}, pluck="name"))
	leads.discard(None)
	lead_list = list(leads) or [""]
	for dt in ("Comment", "Communication", "ToDo"):
		frappe.db.delete(dt, {"reference_name": ["in", lead_list]})
	frappe.db.delete("CRM Call Log", {"reference_docname": ["in", lead_list]})
	frappe.db.delete("Omni Care Log", {"lead": ["in", lead_list]})
	frappe.db.delete("Omni Inbound Message", {"channel": ["in", channels]})
	frappe.db.delete("Omni Call", {"call_id": ["like", f"{PREFIX.lower()}%"]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_a": ["in", lead_list]})
	frappe.db.delete("Omni Duplicate Candidate", {"lead_b": ["in", lead_list]})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	for name in channels:
		frappe.db.delete("Omni Channel", {"channel_name": name})
	frappe.db.delete("Omni Lead Form", {"form_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Email Account", {"name": ACCOUNT})
	frappe.db.commit()


class TestIntakeFilesIntegration(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		frappe.cache().delete_keys("omni:")
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name)
		self.dir = self.root / "inbox" / "2026" / "11" / "03"
		patch = mock.patch.dict(frappe.conf, {"omni_intake_dir": str(self.root)})
		patch.start()
		self.addCleanup(patch.stop)
		acc = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": ACCOUNT,
				"email_id": MAIL,
				"enable_incoming": 0,
				"enable_outgoing": 0,
				"password": "khong-co-that",
			}
		)
		acc.flags.ignore_validate = True
		acc.flags.ignore_mandatory = True
		acc.insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Omni Channel",
				"channel_name": CH_MAIL,
				"channel_type": "Email",
				"email_account": ACCOUNT,
				"enabled": 1,
			}
		).insert()
		make_tel_channel(CH_TEL)
		frappe.get_doc(
			{"doctype": "Omni Channel", "channel_name": CH_WEB, "channel_type": "Web Form", "enabled": 1}
		).insert()
		make_form("Website", "spd", "Web spd")
		frappe.db.commit()
		# Nếu site đã có kênh khác cùng loại thì chọn rõ kênh của test
		patch2 = mock.patch.dict(
			frappe.conf,
			{"omni_intake_channels": {"Telephony": CH_TEL, "Web Form": CH_WEB}},
		)
		patch2.start()
		self.addCleanup(patch2.stop)

	def put(self, sub, name, env):
		d = self.dir / sub
		d.mkdir(parents=True, exist_ok=True)
		(d / f"{name}.json").write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")

	def inbound(self, channel):
		return frappe.get_all(
			"Omni Inbound Message",
			filters={"channel": channel},
			fields=[
				"name",
				"status",
				"event_type",
				"identity_key",
				"lead",
				"raw_payload",
				"external_message_id",
			],
		)

	def test_email_goi_khach_thanh_lead(self):
		env = envelope("email", f"gmail:{PREFIX.lower()}-m1@mail.example", channel="gmail")
		self.put("gmail", "m1", env)
		stats = runner.run()
		self.assertEqual(stats["moi"], 1, stats)
		(msg,) = self.inbound(CH_MAIL)
		self.assertEqual((msg.status, msg.event_type), ("New", "message"))
		self.assertEqual(msg.external_message_id, f"{PREFIX.lower()}-m1@mail.example")
		comm = frappe.get_doc("Communication", msg.communication)  # đi qua Communication có sẵn của CRM
		self.assertEqual((comm.email_account, comm.sent_or_received), (ACCOUNT, "Received"))
		self.assertTrue((self.root / "archive" / "2026/11/03/gmail/m1.json").exists())
		work(msg.identity_key)
		lead = frappe.db.get_value("Omni Inbound Message", msg.name, "lead")
		self.assertTrue(lead, "worker phải tạo / gắn Lead")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "email"), CUSTOMER_MAIL)

	def test_nap_hai_lan_khong_nhan_doi(self):
		env = envelope("email", f"gmail:{PREFIX.lower()}-m2@mail.example", channel="gmail")
		self.put("gmail", "m2", env)
		self.assertEqual(runner.run()["moi"], 1)
		self.put("gmail", "m2-lai", env)  # cùng thư xuất hiện lại (vd intake backfill)
		stats = runner.run()
		self.assertEqual((stats["moi"], stats["trung"]), (0, 1), stats)
		self.assertEqual(len(self.inbound(CH_MAIL)), 1)

	def test_thu_tu_dong_khong_vao_crm(self):
		env = envelope(
			"email",
			f"gmail:{PREFIX.lower()}-bulk@mail.example",
			ignored=True,
			ignore_reasons=["bulk"],
			channel="gmail",
		)
		self.put("gmail", "bulk", env)
		stats = runner.run()
		self.assertEqual((stats["bo_qua"], stats["moi"]), (1, 0), stats)
		self.assertEqual(self.inbound(CH_MAIL), [])

	def test_hop_thu_crm_tu_keo_thi_khong_nap_lai(self):
		frappe.db.set_value("Email Account", ACCOUNT, "enable_incoming", 1, update_modified=False)
		frappe.db.commit()
		self.put("gmail", "m", envelope("email", f"gmail:{PREFIX.lower()}-m9@mail.example", channel="gmail"))
		self.assertEqual(runner.run()["bo_qua"], 1)
		self.assertEqual(self.inbound(CH_MAIL), [])

	def test_cuoc_goi_thanh_omni_call_va_lead(self):
		call_id = f"{PREFIX.lower()}-1700000000.1"
		env = envelope(
			"call",
			f"voice:asterisk:{call_id}:ended",
			channel="voice",
			sender={"type": "phone", "value": PHONE, "display_name": None},
			identifiers={"phone": "+84912345616", "email": None, "tax_id": None},
			call={
				"call_id": call_id,
				"event_type": "ended",
				"from": PHONE,
				"to": "02812345678",
				"agent_ref": "101",
				"started_at": "2026-11-03T09:00:00+07:00",
				"answered_at": "2026-11-03T09:00:05+07:00",
				"ended_at": "2026-11-03T09:02:00+07:00",
				"answer_duration": 115,
				"disposition": "answered",
				"recording": None,
			},
		)
		self.put("voice", "c1", env)
		self.assertEqual(runner.run()["moi"], 1)
		(msg,) = self.inbound(CH_TEL)
		self.assertEqual(msg.event_type, "call_ended")
		work(msg.identity_key)
		call = frappe.get_doc("Omni Call", {"call_id": call_id})
		self.assertEqual((call.status, call.direction, call.duration_seconds), ("Answered", "Inbound", 115))
		self.assertTrue(frappe.db.get_value("Omni Inbound Message", msg.name, "lead"))

	def test_form_landing_va_dat_lich_cung_mot_khach(self):
		form = envelope(
			"lead_form",
			f"web:spd:{PREFIX.lower()}-1",
			channel="web_form",
			sender={"type": "phone", "value": PHONE, "display_name": f"{PREFIX} Lê Form"},
			identifiers={"phone": "+84912345616", "email": CUSTOMER_MAIL, "tax_id": None},
			content={"text": None, "attachments": []},
			campaign={"utm_source": "google", "gclid": "TD16G"},
			raw={"fields": {"need": "Điều phối", "message": "Cần demo", "organization": f"Công ty {PREFIX}"}},
			source={"account": "spd", "signature_verified": False},
		)
		booking = envelope(
			"booking",
			f"booking:{PREFIX.lower()}:2026-11-10T09:00",
			channel="web_form",
			sender={"type": "phone", "value": PHONE, "display_name": f"{PREFIX} Lê Form"},
			identifiers={"phone": "+84912345616", "email": CUSTOMER_MAIL, "tax_id": None},
			content={"text": "Đặt lịch demo: 10/11 09:00", "attachments": []},
			source={"account": "spd", "signature_verified": False},
		)
		self.put("web_form", "f1", form)
		self.put("web_form", "b1", booking)
		stats = runner.run()
		self.assertEqual(stats["moi"], 2, stats)
		msgs = self.inbound(CH_WEB)
		self.assertEqual(len(msgs), 2)
		self.assertTrue(all(m.event_type == "form_lead" for m in msgs))
		for m in msgs:
			work(m.identity_key)
		leads = {frappe.db.get_value("Omni Inbound Message", m.name, "lead") for m in msgs}
		leads.discard(None)
		self.assertEqual(len(leads), 1, "form và lịch demo của cùng một khách phải về một Lead")

	def test_messenger_duoc_cat_lai_va_thieu_kenh_giu_nguyen(self):
		self.put("messenger", "m", envelope("message", "messenger:m_1", channel="messenger"))
		self.put(
			"web_form", "f", envelope("lead_form", "web:spd:x", channel="web_form", source={"account": "spd"})
		)
		with mock.patch.dict(frappe.conf, {"omni_intake_channels": {"Web Form": "khong-ton-tai"}}):
			stats = runner.run()
		self.assertEqual((stats["chua_ho_tro"], stats["thieu_kenh"], stats["moi"]), (1, 1, 0), stats)
		self.assertTrue((self.root / "chua_nap" / "2026/11/03/messenger/m.json").exists())
		self.assertTrue((self.dir / "web_form" / "f.json").exists())  # nạp lại được khi kênh sẵn sàng
		self.assertEqual(runner.run()["moi"], 1)


class TestEnsureEmailChannel(FrappeTestCase):
	"""setup.ensure_email_channel: mặc định chỉ in kế hoạch; apply=1 tạo vỏ + kênh; chạy lại không nhân đôi."""

	box = f"{PREFIX.lower()}.vo@example.org"

	def setUp(self):
		self._clean()
		self.addCleanup(self._clean)

	def _clean(self):
		frappe.db.rollback()
		frappe.db.delete("Omni Channel", {"channel_name": f"email-{PREFIX.lower()}.vo"})
		frappe.db.delete("Email Account", {"email_id": self.box})
		frappe.db.commit()

	def test_ke_hoach_roi_ap_dung_roi_chay_lai(self):
		from omni_crm.intake_files import setup

		plan = setup.ensure_email_channel(self.box)
		self.assertFalse(plan["apply"])
		self.assertFalse(frappe.db.exists("Email Account", {"email_id": self.box}))
		setup.ensure_email_channel(self.box, apply=1)
		acc = frappe.get_doc("Email Account", {"email_id": self.box})
		self.assertEqual(
			(acc.enable_incoming, acc.enable_outgoing, acc.create_lead_from_incoming_email), (0, 0, 0)
		)
		channel = frappe.get_doc("Omni Channel", f"email-{PREFIX.lower()}.vo")
		self.assertEqual(
			(channel.channel_type, channel.email_account, channel.enabled), ("Email", acc.name, 1)
		)
		again = setup.ensure_email_channel(self.box, apply=1)
		self.assertEqual(again["steps"], ["đã đủ, không có gì phải làm"])

	def test_crm_tu_keo_thi_khong_tao_gi(self):
		from omni_crm.intake_files import setup

		acc = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": self.box,
				"email_id": self.box,
				"enable_incoming": 0,
				"password": "x",
			}
		)
		acc.flags.ignore_validate = acc.flags.ignore_mandatory = True
		acc.insert(ignore_permissions=True)
		frappe.db.set_value("Email Account", acc.name, "enable_incoming", 1)
		frappe.db.commit()
		plan = setup.ensure_email_channel(self.box, apply=1)
		self.assertFalse(frappe.db.exists("Omni Channel", f"email-{PREFIX.lower()}.vo"))
		self.assertIn("CRM đã tự kéo", plan["steps"][0])
