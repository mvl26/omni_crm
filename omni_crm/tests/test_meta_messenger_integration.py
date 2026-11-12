"""Kênh Meta Messenger trên site thật (ADR 0022): file intake -> Omni Inbound Message (+ File) -> worker -> Lead,
echo, gửi trả lời từ Hộp thư, nạp bù. Dữ liệu được commit thật nên dọn thủ công (tiền tố TD22)."""

import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from PIL import Image

from omni_crm.api import inbox
from omni_crm.intake_files import runner
from omni_crm.tests.ads_helpers import work

PREFIX = "TD22"
CH = f"{PREFIX} messenger"
PAGE, APP = "td22page", "td22app"
PSID = "td22psid0001"


def menv(key, **over):
	base = {
		"schema": "1",
		"event_key": key,
		"channel": "messenger",
		"kind": "message",
		"direction": "in",
		"occurred_at": now_datetime().strftime("%Y-%m-%dT%H:%M:%S+07:00"),
		"received_at": now_datetime().strftime("%Y-%m-%dT%H:%M:%S+07:00"),
		"sender": {"type": "psid", "value": PSID},
		"content": {"text": "Cho mình xin báo giá", "attachments": []},
		"thread_ref": f"messenger:{PSID}",
		"campaign": {},
		"source": {"account": PAGE, "signature_verified": True},
		"raw": {
			"entry_id": PAGE,
			"event": {"sender": {"id": PSID}, "message": {"mid": key.split(":", 1)[1]}},
		},
	}
	base.update(over)
	return base


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	inbound = frappe.get_all("Omni Inbound Message", filters={"channel": CH}, pluck="name")
	leads = set(frappe.get_all("Omni Inbound Message", filters={"channel": CH}, pluck="lead"))
	leads.update(
		frappe.get_all("Omni Identity", filters={"identity_key": ["like", f"%{PSID}%"]}, pluck="lead")
	)
	leads.discard(None)
	lead_list = list(leads) or [""]
	for dt in ("Comment", "Communication", "ToDo"):
		frappe.db.delete(dt, {"reference_name": ["in", lead_list]})
	frappe.db.delete("Omni Care Log", {"lead": ["in", lead_list]})
	frappe.db.delete("Omni Outbound Message", {"channel": CH})
	if inbound:  # chỉ File gắn vào tin của test; "in ['']" sẽ khớp cả thư mục Home/Attachments
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Omni Inbound Message", "attached_to_name": ["in", inbound]},
			pluck="name",
		)
		for f in files:
			frappe.delete_doc("File", f, force=True, ignore_permissions=True)
	frappe.db.delete("Omni Inbound Message", {"channel": CH})
	frappe.db.delete("Omni Identity", {"identity_key": ["like", f"%{PSID}%"]})
	for lead in leads:
		frappe.delete_doc("CRM Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Omni Channel", {"channel_name": CH})
	frappe.db.commit()


class TestMessengerIntegration(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		frappe.cache().delete_keys("omni:")
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name)
		self.day = now_datetime().strftime("%Y/%m/%d")
		self.dir = self.root / "inbox" / self.day / "messenger"
		self.dir.mkdir(parents=True)
		patch = mock.patch.dict(
			frappe.conf,
			{
				"omni_intake_dir": str(self.root),
				"omni_intake_kinds": ["messenger"],
				"omni_intake_channels": {"Meta Messenger": CH},
			},
		)
		patch.start()
		self.addCleanup(patch.stop)
		frappe.get_doc(
			{
				"doctype": "Omni Channel",
				"channel_name": CH,
				"channel_type": "Meta Messenger",
				"meta_page_id": PAGE,
				"meta_app_id": APP,
				"enabled": 1,
				"outbound_enabled": 1,
			}
		).insert()
		frappe.db.commit()

	def put(self, name, env):
		(self.dir / f"{name}.json").write_text(json.dumps(env), encoding="utf-8")

	def att(self, name, data=None):
		if data is None:  # ảnh JPEG thật: File của Frappe mở ảnh bằng PIL khi lưu
			buf = io.BytesIO()
			Image.new("RGB", (2, 2), "red").save(buf, format="JPEG")
			data = buf.getvalue()
		d = self.root / "attachments" / "td22"
		d.mkdir(parents=True, exist_ok=True)
		(d / name).write_bytes(data)
		return f"attachments/td22/{name}"

	def rows(self):
		return frappe.get_all(
			"Omni Inbound Message",
			filters={"channel": CH},
			fields=["name", "event_type", "identity_key", "attachments", "status", "lead"],
			order_by="creation asc",
		)

	def test_tin_va_dinh_kem_thanh_lead(self):
		path = self.att("anh.jpg")
		content = {
			"text": "Ảnh mẫu",
			"attachments": [{"type": "image", "url": "https://cdn/x", "path": path}],
		}
		self.put("a", menv("messenger:m_td22_1", content=content))
		stats = runner.run()
		dead = [p.read_text() for p in (self.root / "dead_crm").rglob("*.loi.txt")]
		self.assertEqual(stats["moi"], 1, (stats, dead))
		(row,) = self.rows()
		self.assertEqual((row.event_type, row.identity_key), ("message", f"Meta Messenger:{PSID}"))
		(att,) = json.loads(row.attachments)
		self.assertTrue(att["url"].startswith("/private/files/"))
		f = frappe.get_doc("File", {"file_url": att["url"], "attached_to_name": row.name})
		self.assertEqual((f.is_private, f.attached_to_doctype), (1, "Omni Inbound Message"))
		work(row.identity_key)
		row = self.rows()[0]
		self.assertEqual(row.status, "Done")
		self.assertTrue(row.lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", row.lead, "source"), "Facebook Messenger")
		comm = frappe.get_all(
			"Communication",
			filters={"reference_doctype": "CRM Lead", "reference_name": row.lead},
			fields=["content", "communication_type"],
		)
		self.assertTrue(any("<img" in (c.content or "") for c in comm), comm)
		self.assertTrue(all(c.communication_type == "Communication" for c in comm))  # C1: hiện ở Hoạt động
		# Chạy lại: trùng, không sinh File thừa
		before = frappe.db.count("File", {"attached_to_name": row.name})
		self.assertEqual(runner.ingest_envelope(menv("messenger:m_td22_1", content=content)), "trung")
		self.assertEqual(frappe.db.count("File", {"attached_to_name": row.name}), before)

	def test_tep_hong_khong_lam_hong_tin(self):
		bad = self.att("hong.jpg", b"\xff\xd8khong-phai-anh")
		good = self.att("bao-gia.docx", b"PK\x03\x04 docx")
		atts = [{"type": "image", "path": bad}, {"type": "file", "path": good}]
		self.put("a", menv("messenger:m_td22_bad", content={"text": None, "attachments": atts}))
		self.assertEqual(runner.run()["moi"], 1)
		(row,) = self.rows()
		saved = json.loads(row.attachments)
		self.assertIn("note", saved[0])
		self.assertTrue(saved[1]["url"].startswith("/private/files/"))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": row.name}), 1)

	def test_loc_page_va_echo(self):
		self.put("a", menv("messenger:m_td22_k"))
		self.put("b", menv("messenger:m_td22_other", source={"account": "page-khac"}))
		own = menv(
			"messenger:m_td22_own",
			direction="out",
			raw={"event": {"message": {"mid": "m_td22_own", "is_echo": True, "app_id": APP}}},
		)
		staff = menv(
			"messenger:m_td22_staff",
			direction="out",
			content={"text": "Dạ em gửi báo giá ạ", "attachments": []},
			raw={"event": {"message": {"mid": "m_td22_staff", "is_echo": True, "app_id": 263902037430900}}},
		)
		self.put("c", own)
		self.put("d", staff)
		stats = runner.run()
		self.assertEqual((stats["moi"], stats["bo_qua"]), (2, 2), stats)
		types = sorted(r.event_type for r in self.rows())
		self.assertEqual(types, ["message", "oa_echo"])
		work(f"Meta Messenger:{PSID}")
		out = frappe.get_all(
			"Omni Outbound Message", filters={"channel": CH}, fields=["status", "source", "text"]
		)
		self.assertEqual([(o.status, o.source) for o in out], [("Sent", "Sale")])

	def test_tra_loi_tu_hop_thu(self):
		self.put("a", menv("messenger:m_td22_r"))
		runner.run()
		work(f"Meta Messenger:{PSID}")
		lead = self.rows()[0].lead
		self.assertTrue(inbox.compose_state(lead)["messenger"]["can_send"])
		with mock.patch("frappe.enqueue"):
			res = inbox.send_messenger(lead, "Dạ em chào anh")
		self.assertEqual(res["status"], "Queued")
		ident = frappe.db.get_value("Omni Identity", {"identity_key": f"Meta Messenger:{PSID}"}, "name")
		frappe.db.set_value("Omni Identity", ident, "last_inbound_at", now_datetime() - timedelta(hours=30))
		res = inbox.send_messenger(lead, "Tin muộn")
		self.assertEqual(res["status"], "Blocked")
		self.assertIn("24", res["reason"])

	def test_nap_bu(self):
		self.put("a", menv("messenger:m_td22_b1"))
		self.put("b", menv("messenger:m_td22_b2"))
		self.assertEqual(runner.backfill(["messenger"], apply=0)["file"], 2)
		self.assertEqual(runner.backfill(["messenger"], apply=1)["moi"], 2)
		self.assertEqual(runner.backfill(["messenger"], apply=1)["trung"], 2)
