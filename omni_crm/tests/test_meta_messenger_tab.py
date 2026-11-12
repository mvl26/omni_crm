"""Tab Messenger và gửi ảnh/tệp (ADR 0023) trên site thật: ẩn khỏi Hoạt động, lệnh đổi tin cũ, API tab, gửi tệp.
Dữ liệu commit thật nên dọn thủ công (tiền tố TD23)."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import messenger_tab
from omni_crm.channels.meta_messenger import media_out
from omni_crm.intake_files import runner
from omni_crm.ops import hide_messenger_activity
from omni_crm.tests.ads_helpers import work

PREFIX = "TD23"
CH = f"{PREFIX} messenger"
PAGE, PSID = "td23page", "td23psid0001"
FLAGS = ("messenger_tab_disabled", "messenger_in_activity_enabled", "messenger_media_send_enabled")


def menv(key, **over):
	ts = now_datetime().strftime("%Y-%m-%dT%H:%M:%S+07:00")
	base = {
		"schema": "1",
		"event_key": key,
		"channel": "messenger",
		"kind": "message",
		"direction": "in",
		"occurred_at": ts,
		"received_at": ts,
		"sender": {"type": "psid", "value": PSID},
		"content": {"text": "Cho mình xin báo giá", "attachments": []},
		"thread_ref": f"messenger:{PSID}",
		"campaign": {},
		"source": {"account": PAGE, "signature_verified": True},
		"raw": {"event": {"message": {"mid": key.split(":", 1)[1]}}},
	}
	base.update(over)
	return base


def set_flag(field, value):
	frappe.db.set_single_value("Omni Settings", field, value)
	frappe.db.commit()


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	inbound = frappe.get_all("Omni Inbound Message", filters={"channel": CH}, pluck="name")
	leads = set(
		frappe.get_all("Omni Identity", filters={"identity_key": ["like", f"%{PSID}%"]}, pluck="lead")
	)
	leads.update(frappe.get_all("Omni Inbound Message", filters={"channel": CH}, pluck="lead"))
	leads.discard(None)
	lead_list = list(leads) or [""]
	for dt in ("Comment", "Communication", "ToDo"):
		frappe.db.delete(dt, {"reference_name": ["in", lead_list]})
	frappe.db.delete("Omni Care Log", {"lead": ["in", lead_list]})
	frappe.db.delete("Omni Outbound Message", {"channel": CH})
	files = frappe.get_all(
		"File",
		or_filters=[
			["attached_to_name", "in", inbound or ["-"]],
			["attached_to_name", "in", lead_list if leads else ["-"]],
		],
		filters={"is_folder": 0},
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


class TestMessengerTab(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		saved = {f: frappe.db.get_single_value("Omni Settings", f) for f in FLAGS}
		self.addCleanup(lambda: [set_flag(f, v or 0) for f, v in saved.items()])
		for f in FLAGS:
			set_flag(f, 0)
		frappe.cache().delete_keys("omni:")
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		self.root = Path(tmp.name)
		self.dir = self.root / "inbox" / now_datetime().strftime("%Y/%m/%d") / "messenger"
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
				"enabled": 1,
				"outbound_enabled": 1,
			}
		).insert()
		frappe.db.commit()

	def lead_with_message(self, key="messenger:m_td23_1", content=None):
		env = menv(key, **({"content": content} if content else {}))
		(self.dir / f"{key.split(':')[1]}.json").write_text(json.dumps(env), encoding="utf-8")
		runner.run()
		work(f"Meta Messenger:{PSID}")
		return frappe.db.get_value("Omni Identity", {"identity_key": f"Meta Messenger:{PSID}"}, "lead")

	def comms(self, lead):
		return frappe.get_all(
			"Communication",
			filters={"reference_doctype": "CRM Lead", "reference_name": lead},
			pluck="communication_type",
		)

	def test_an_khoi_hoat_dong_va_cong_tac(self):
		lead = self.lead_with_message()
		self.assertEqual(set(self.comms(lead)), {"Chat"})
		set_flag("messenger_in_activity_enabled", 1)
		self.lead_with_message("messenger:m_td23_2")
		self.assertIn("Communication", self.comms(lead))

	def test_lenh_doi_tin_cu(self):
		set_flag("messenger_in_activity_enabled", 1)
		lead = self.lead_with_message()
		self.assertEqual(set(self.comms(lead)), {"Communication"})
		self.assertEqual(hide_messenger_activity.run(dry_run=1)["changed"], 0)
		self.assertGreaterEqual(hide_messenger_activity.run(dry_run=0)["changed"], 1)
		self.assertEqual(set(self.comms(lead)), {"Chat"})
		hide_messenger_activity.run(dry_run=0, reverse=1)
		self.assertEqual(set(self.comms(lead)), {"Communication"})

	def test_api_tab(self):
		lead = self.lead_with_message()
		self.assertTrue(messenger_tab.tab_state()["enabled"])
		data = messenger_tab.get_conversation("CRM Lead", lead)
		self.assertEqual(data["lead"], lead)
		self.assertEqual([m["direction"] for m in data["messages"]], ["in"])
		self.assertTrue(data["compose"]["can_send"])
		self.assertFalse(data["compose"]["can_send_media"])  # công tắc gửi tệp mặc định tắt
		self.assertEqual(data["contact"]["psid"], PSID)
		set_flag("messenger_tab_disabled", 1)
		self.assertFalse(messenger_tab.tab_state()["enabled"])
		self.assertTrue(messenger_tab.get_conversation("CRM Lead", lead)["disabled"])

	def _lead_file(self, lead, name="bao-gia.txt", content=b"td23 bao gia"):
		# .txt (loại "file"): Frappe đọc thử PDF lúc lưu File nên PDF giả bị từ chối
		return frappe.get_doc(
			{
				"doctype": "File",
				"file_name": name,
				"is_private": 1,
				"content": content,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead,
			}
		).insert(ignore_permissions=True)

	def test_gui_tep_chu_truoc_tep_sau(self):
		lead = self.lead_with_message()
		f = self._lead_file(lead)
		with self.assertRaises(frappe.PermissionError):  # công tắc tắt
			messenger_tab.send_attachment("CRM Lead", lead, f.file_url, "Báo giá ạ")
		set_flag("messenger_media_send_enabled", 1)
		self.assertTrue(messenger_tab.get_conversation("CRM Lead", lead)["compose"]["can_send_media"])
		with mock.patch("frappe.enqueue"):
			res = messenger_tab.send_attachment("CRM Lead", lead, f.file_url, "Báo giá ạ")
		self.assertEqual(res["status"], "Queued", res)
		rows = frappe.get_all(
			"Omni Outbound Message",
			filters={"channel": CH},
			fields=["name", "text", "attachment", "attachment_kind"],
			order_by="creation asc",
		)
		self.assertEqual([(r.text, r.attachment_kind or "") for r in rows], [("Báo giá ạ", ""), ("", "file")])
		self.assertEqual(rows[1].attachment, f.file_url)
		self.assertEqual(res["text_outbound"], rows[0].name)

	def test_tep_khong_thuoc_lead_va_sai_loai(self):
		lead = self.lead_with_message()
		set_flag("messenger_media_send_enabled", 1)
		other = frappe.get_doc(
			{"doctype": "File", "file_name": "td23-khac.txt", "is_private": 1, "content": b"td23 khac"}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("File", other.name, force=True, ignore_permissions=True))
		with self.assertRaises(media_out.MediaError):
			messenger_tab.send_attachment("CRM Lead", lead, other.file_url)
		exe = self._lead_file(lead, "td23.exe", b"MZ")
		with self.assertRaises(media_out.MediaError):
			messenger_tab.send_attachment("CRM Lead", lead, exe.file_url)
