"""Tương tác Messenger đợt 2 (ADR 0027): ghi âm đổi sang MP3 (ffmpeg), chuyển tiếp tin Page đã gửi sang khách khác.
Dùng chung dữ liệu thử TD23 của test tab Messenger; khách thứ hai mang PSID `td28psid…` (dọn thủ công).

Cần ffmpeg (site_config `omni_ffmpeg_path` hoặc PATH) cho test đổi định dạng; không có thì bỏ qua các test đó."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import messenger_tab
from omni_crm.channels.meta_messenger import media_out
from omni_crm.intake_files import runner
from omni_crm.tests.ads_helpers import work
from omni_crm.tests.test_meta_messenger_tab import CH, FLAGS, PAGE, PSID, cleanup, menv, set_flag

OUT = "Omni Outbound Message"
IN = "Omni Inbound Message"
PSID2 = "td28psid0002"


def cleanup2():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	frappe.db.delete("Omni Identity", {"identity_key": ["like", "%td28psid%"]})
	frappe.db.commit()


def make_audio(ffmpeg: str, ext: str) -> bytes:
	"""Một giây âm thanh thử (sóng sin) theo định dạng trình duyệt hay ghi ra."""
	codec = {"webm": ["-c:a", "libopus"], "ogg": ["-c:a", "libopus"], "m4a": ["-c:a", "aac"]}[ext]
	with tempfile.TemporaryDirectory() as tmp:
		out = os.path.join(tmp, f"a.{ext}")
		subprocess.run(
			[
				ffmpeg,
				"-nostdin",
				"-loglevel",
				"error",
				"-f",
				"lavfi",
				"-i",
				"sine=frequency=440:duration=1",
				*codec,
				out,
			],
			check=True,
			timeout=60,
		)
		return Path(out).read_bytes()


class TestGhiAmChuyenTiep(FrappeTestCase):
	def setUp(self):
		cleanup()
		cleanup2()
		self.addCleanup(cleanup2)
		self.addCleanup(cleanup)
		saved = {f: frappe.db.get_single_value("Omni Settings", f) for f in (*FLAGS, "sandbox_mode")}
		self.addCleanup(lambda: [set_flag(f, v or 0) for f, v in saved.items()])
		for f in FLAGS:
			set_flag(f, 0)
		set_flag("sandbox_mode", 1)
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		self.dir = Path(tmp.name) / "inbox" / now_datetime().strftime("%Y/%m/%d") / "messenger"
		self.dir.mkdir(parents=True)
		patch = mock.patch.dict(
			frappe.conf,
			{
				"omni_intake_dir": tmp.name,
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

	def lead_of(self, psid, key):
		env = menv(key, sender={"type": "psid", "value": psid}, thread_ref=f"messenger:{psid}")
		(self.dir / f"{key.split(':')[1]}.json").write_text(json.dumps(env), encoding="utf-8")
		runner.run()
		work(f"Meta Messenger:{psid}")
		return frappe.db.get_value("Omni Identity", {"identity_key": f"Meta Messenger:{psid}"}, "lead")

	def upload(self, lead, name, content):
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

	def ffmpeg(self):
		path = media_out.ffmpeg_path()
		if not path:
			self.skipTest("không có ffmpeg trên máy chạy test")
		return path

	# ---- ghi âm ----

	def test_ghi_am_doi_mp3_va_gui(self):
		ffmpeg = self.ffmpeg()
		lead = self.lead_of(PSID, "messenger:m_td28_1")
		set_flag("messenger_media_send_enabled", 1)
		self.assertTrue(messenger_tab.get_conversation("CRM Lead", lead)["compose"]["can_send_voice"])
		for ext in ("webm", "ogg", "m4a"):
			src = self.upload(lead, f"ghi-am-td28.{ext}", make_audio(ffmpeg, ext))
			with mock.patch("frappe.enqueue"):
				res = messenger_tab.send_voice("CRM Lead", lead, src.file_url)
			self.assertEqual(res["status"], "Queued", res)
			row = frappe.db.get_value(
				OUT, res["outbound"], ["attachment", "attachment_kind", "attachment_name"], as_dict=True
			)
			self.assertEqual(row.attachment_kind, "audio")
			self.assertTrue(
				row.attachment_name.startswith("ghi-am-") and row.attachment_name.endswith(".mp3")
			)
			mp3 = media_out.read_content(row.attachment)
			self.assertTrue(mp3[:3] == b"ID3" or mp3[0] == 0xFF, ext)  # đầu tệp MP3
			self.assertFalse(frappe.db.exists("File", src.name), ext)  # bản ghi gốc đã xóa

	def test_ghi_am_bi_chan(self):
		lead = self.lead_of(PSID, "messenger:m_td28_1")
		src = self.upload(lead, "ghi-am-td28.webm", b"khong phai am thanh")
		with self.assertRaises(frappe.PermissionError):  # công tắc gửi tệp đang tắt
			messenger_tab.send_voice("CRM Lead", lead, src.file_url)
		set_flag("messenger_media_send_enabled", 1)
		with mock.patch.object(media_out, "ffmpeg_path", return_value=None):
			compose = messenger_tab.get_conversation("CRM Lead", lead)["compose"]
			self.assertFalse(compose["can_send_voice"])
			self.assertIn("ffmpeg", compose["voice_reason"])
			with self.assertRaises(media_out.MediaError):
				messenger_tab.send_voice("CRM Lead", lead, src.file_url)
		if media_out.ffmpeg_path():
			with self.assertRaises(media_out.MediaError):  # tệp hỏng: ffmpeg báo lỗi, không gửi
				messenger_tab.send_voice("CRM Lead", lead, src.file_url)
		exe = self.upload(lead, "ghi-am-td28.exe", b"MZ")
		with self.assertRaises(media_out.MediaError):
			messenger_tab.send_voice("CRM Lead", lead, exe.file_url)
		self.assertFalse(frappe.db.exists(OUT, {"lead": lead, "attachment_kind": "audio"}))

	# ---- chuyển tiếp ----

	def test_chuyen_tiep_tin_page_da_gui(self):
		lead1 = self.lead_of(PSID, "messenger:m_td28_1")
		lead2 = self.lead_of(PSID2, "messenger:m_td28_2")
		set_flag("messenger_media_send_enabled", 1)
		with mock.patch("frappe.enqueue"):
			sent = messenger_tab.send("CRM Lead", lead1, "Bảng giá tháng 11")["outbound"]
		frappe.db.set_value(OUT, sent, {"status": "Sent", "external_message_id": "sandbox:td28"})
		f = self.upload(lead1, "bang-gia-td28.txt", b"td28 bang gia")
		with mock.patch("frappe.enqueue"):
			sent_file = messenger_tab.send_attachment("CRM Lead", lead1, f.file_url)["outbound"]
		frappe.db.set_value(OUT, sent_file, "status", "Sent")
		targets = messenger_tab.forward_targets("CRM Lead", lead1, "")["items"]
		self.assertIn(lead2, [t["lead"] for t in targets])
		self.assertNotIn(lead1, [t["lead"] for t in targets])
		with mock.patch("frappe.enqueue"):
			res = messenger_tab.forward("CRM Lead", lead1, OUT, sent, lead2)
			res_file = messenger_tab.forward("CRM Lead", lead1, OUT, sent_file, lead2)
		self.assertEqual(res["status"], "Queued", res)
		copy = frappe.db.get_value(OUT, res["outbounds"][0], ["lead", "text", "forwarded_from"], as_dict=True)
		self.assertEqual(
			(copy.lead, copy.text, copy.forwarded_from), (lead2, "Bảng giá tháng 11", f"{OUT}:{sent}")
		)
		att = frappe.db.get_value(OUT, res_file["outbounds"][0], "attachment")
		self.assertTrue(
			frappe.db.exists(
				"File", {"file_url": att, "attached_to_doctype": "CRM Lead", "attached_to_name": lead2}
			)
		)  # tệp đã chép sang Lead nhận
		rows = {m["name"]: m for m in messenger_tab.get_conversation("CRM Lead", lead2)["messages"]}
		self.assertTrue(rows[res["outbounds"][0]]["forwarded"])

	def test_chuyen_tiep_bi_chan(self):
		lead1 = self.lead_of(PSID, "messenger:m_td28_1")
		lead2 = self.lead_of(PSID2, "messenger:m_td28_2")
		customer_msg = frappe.db.get_value(IN, {"lead": lead1}, "name")
		with self.assertRaises(frappe.PermissionError):  # tin của khách không chuyển cho khách khác
			messenger_tab.forward("CRM Lead", lead1, IN, customer_msg, lead2)
		with mock.patch("frappe.enqueue"):
			queued = messenger_tab.send("CRM Lead", lead1, "Chưa gửi xong")["outbound"]
		with self.assertRaises(frappe.ValidationError):  # tin chưa gửi
			messenger_tab.forward("CRM Lead", lead1, OUT, queued, lead2)
		frappe.db.set_value(OUT, queued, "status", "Sent")
		with self.assertRaises(frappe.ValidationError):  # chuyển cho chính khách đó
			messenger_tab.forward("CRM Lead", lead1, OUT, queued, lead1)
		rows = {m["name"]: m for m in messenger_tab.get_conversation("CRM Lead", lead1)["messages"]}
		self.assertTrue(rows[queued]["can_forward"])
		self.assertFalse(rows[customer_msg]["can_forward"])

	def test_gui_tep_am_thanh_va_video_qua_kep_tep(self):
		"""Lỗi có sẵn từ ADR 0023: attachment_kind của tin ra chỉ nhận image/gif/file nên MP3/MP4 bị từ chối khi xếp hàng."""
		lead = self.lead_of(PSID, "messenger:m_td28_1")
		set_flag("messenger_media_send_enabled", 1)
		for name, kind in (("td28.mp3", "audio"), ("td28.mp4", "video")):
			f = self.upload(lead, name, b"td28 media")
			with mock.patch("frappe.enqueue"):
				res = messenger_tab.send_attachment("CRM Lead", lead, f.file_url)
			self.assertEqual(res["status"], "Queued", res)
			self.assertEqual(frappe.db.get_value(OUT, res["outbound"], "attachment_kind"), kind)
