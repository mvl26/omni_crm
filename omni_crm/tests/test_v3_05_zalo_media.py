"""V3-05: ảnh/GIF/tệp/sticker khách gửi qua Zalo OA: chuẩn hóa đính kèm, lọc URL, danh sách host ảnh, báo cáo chẩn đoán.

Hình dạng payload dựng theo dữ liệu người dùng dán (host zgif-v2.zdn.vn, photo-stal-27.zdn.vn) và hiểu biết về Zalo; KHÔNG
phải bản chép từ tài liệu chính thức (docs/12, mục V3-05).
"""

import json

import frappe

from omni_crm.api import inbox
from omni_crm.channels.zalo import adapter as adapter_mod
from omni_crm.channels.zalo.adapter import parse_event
from omni_crm.ops import zalo_media_report
from omni_crm.tests.bridge_helpers import PREFIX, BridgeTestCase
from omni_crm.utils import media

GIF = "https://zgif-v2.zdn.vn/abc123def456.gif"
JPG = "https://photo-stal-27.zdn.vn/no/jpg/ffee0011/987654321.jpg"
THUMB = "https://photo-stal-27.zdn.vn/no/jpg/ffee0011/thumb.jpg"


def payload(event: str, attachments: list, text: str | None = None, msg_id: str = "m-1") -> dict:
	message: dict = {"msg_id": msg_id, "attachments": attachments}
	if text is not None:
		message["text"] = text
	return {
		"app_id": "1",
		"event_name": event,
		"timestamp": "1761700000000",
		"sender": {"id": f"{PREFIX}U1"},
		"recipient": {"id": f"{PREFIX}OA"},
		"message": message,
	}


class Chan:
	name = f"{PREFIX} kenh"


def parsed(event: str, attachments: list, **kw):
	return parse_event(Chan(), payload(event, attachments, **kw))


class TestMediaHelpers(BridgeTestCase):
	def test_host_allowlist(self):
		self.assertTrue(media.is_inline_image_url(GIF))
		self.assertTrue(media.is_inline_image_url(JPG))
		self.assertTrue(media.is_inline_image_url("https://f20-zpc.zdn.vn/a.png"))
		self.assertTrue(media.is_inline_image_url("/files/a.png"))
		self.assertTrue(media.is_inline_image_url("/private/files/a.png"))

	def test_foreign_and_lookalike_hosts_rejected(self):
		for url in (
			"https://evil.example/a.png",
			"https://zdn.vn.evil.example/a.png",
			"https://evilzdn.vn/a.png",
			"https://zdn.vn@evil.example/a.png",
			"http://photo-stal-27.zdn.vn/a.jpg",  # không https
			"//photo-stal-27.zdn.vn/a.jpg",
			"javascript:alert(1)",
			"data:image/png;base64,AAAA",
			"",
		):
			self.assertFalse(media.is_inline_image_url(url), url)

	def test_safe_link(self):
		self.assertTrue(media.is_safe_link("https://x.example/a"))
		self.assertFalse(media.is_safe_link("javascript:alert(1)"))
		self.assertFalse(media.is_safe_link("data:text/html,x"))
		self.assertFalse(media.is_safe_link("https://x.example/a\nb"))


class TestParseZaloMedia(BridgeTestCase):
	def test_image(self):
		ev = parsed("user_send_image", [{"type": "image", "payload": {"thumbnail": THUMB, "url": JPG}}])
		self.assertEqual(ev.event_type, "message")
		att = ev.attachments[0]
		self.assertEqual((att["type"], att["url"], att["thumb"]), ("image", JPG, THUMB))
		self.assertEqual(ev.text, "[Ảnh]")

	def test_gif_type_gif(self):
		ev = parsed("user_send_gif", [{"type": "gif", "payload": {"thumbnail": THUMB, "url": GIF}}])
		self.assertEqual(ev.attachments[0]["type"], "gif")
		self.assertEqual(ev.text, "[GIF]")

	def test_gif_sent_as_image_type_is_reclassified(self):
		ev = parsed("user_send_image", [{"type": "image", "payload": {"url": GIF}}])
		self.assertEqual(ev.attachments[0]["type"], "gif")

	def test_unknown_type_gets_generic_label_but_keeps_url(self):
		ev = parsed("user_send_whatever", [{"type": "hologram", "payload": {"url": GIF}}])
		self.assertEqual(ev.text, "[Tệp đính kèm]")
		self.assertEqual(ev.attachments[0]["url"], GIF)

	def test_file(self):
		ev = parsed(
			"user_send_file",
			[
				{
					"type": "file",
					"payload": {
						"url": "https://f1-zpc.zdn.vn/x/bao-gia.pdf",
						"name": "bao-gia.pdf",
						"size": "1024",
						"checksum": "c",
						"type": "pdf",
					},
				}
			],
		)
		self.assertEqual(ev.attachments[0]["name"], "bao-gia.pdf")
		self.assertEqual(ev.text, "[Tệp: bao-gia.pdf]")

	def test_sticker_video_link_card(self):
		st = parsed("user_send_sticker", [{"type": "sticker", "payload": {"id": "1", "url": JPG}}])
		self.assertEqual((st.attachments[0]["type"], st.attachments[0]["url"]), ("sticker", JPG))
		vd = parsed(
			"user_send_video",
			[
				{
					"type": "video",
					"payload": {"thumbnail": THUMB, "description": "clip", "url": "https://v.zdn.vn/v.mp4"},
				}
			],
		)
		self.assertEqual(vd.attachments[0]["type"], "video")
		self.assertEqual(vd.text, "[Video]")
		lk = parsed(
			"user_send_link",
			[
				{
					"type": "link",
					"payload": {
						"thumbnail": THUMB,
						"description": "mô tả",
						"title": "Tiêu đề",
						"url": "https://x.example/p",
					},
				}
			],
		)
		self.assertEqual(lk.attachments[0]["name"], "Tiêu đề")
		self.assertEqual(lk.text, "[Liên kết]")
		bc = parsed(
			"user_send_business_card",
			[{"type": "business_card", "payload": {"thumbnail": THUMB, "description": "A"}}],
		)
		self.assertEqual(bc.event_type, "message")
		self.assertEqual(bc.text, "[Danh thiếp]")

	def test_location_and_audio(self):
		loc = parsed(
			"user_send_location",
			[{"type": "location", "payload": {"coordinates": {"latitude": "10.7", "longitude": "106.6"}}}],
		)
		self.assertIn("google.com/maps", loc.attachments[0]["url"])
		au = parsed("user_send_audio", [{"type": "audio", "payload": {"url": "https://a.zdn.vn/a.aac"}}])
		self.assertEqual(au.text, "[Tin thoại]")

	def test_malformed_attachments_do_not_crash(self):
		ev = parsed(
			"user_send_image", ["x", None, {"type": "image", "payload": "bad"}, {"payload": {"url": JPG}}]
		)
		self.assertEqual([a["url"] for a in ev.attachments], [None, JPG])

	def test_attachments_not_a_list_does_not_make_event_unparsed(self):
		ev = parse_event(
			Chan(),
			{
				"event_name": "user_send_gif",
				"sender": {"id": "u1"},
				"timestamp": "1",
				"message": {"msg_id": "m", "attachments": {"type": "gif"}},
			},
		)
		self.assertEqual((ev.event_type, ev.text, ev.attachments), ("message", "[GIF]", []))

	def test_event_without_text_or_attachments_still_gets_label(self):
		self.assertEqual(parsed("user_send_gif", []).text, "[GIF]")
		self.assertEqual(parsed("user_send_business_card", []).text, "[Danh thiếp]")
		self.assertEqual(parsed("user_send_text", []).text, "")

	def test_ignored_event_is_counted(self):
		ev = parse_event(Chan(), {"event_name": "mot_su_kien_la", "sender": {"id": "u"}, "timestamp": "1"})
		self.assertEqual(ev.event_type, "ignored")
		cache = frappe.cache()
		key = cache.make_key(f"omni:zalo:reject:{Chan.name}:ignored:mot_su_kien_la")
		self.assertGreaterEqual(int(cache.get(key) or 0), 1)
		cache.delete(key)
		self.assertTrue(adapter_mod.USER_EVENT_PREFIX)


class TestSafeAttachments(BridgeTestCase):
	def test_keeps_type_thumb_and_marks_inline(self):
		raw = json.dumps(
			[
				{"type": "image", "url": JPG, "name": JPG, "thumb": THUMB},
				{"type": "gif", "url": GIF, "name": None},
				{
					"type": "file",
					"url": "https://f1-zpc.zdn.vn/x/bao-gia.pdf",
					"name": "https://f1-zpc.zdn.vn/x/bao-gia.pdf",
				},
			]
		)
		img, gif, fil = inbox._safe_attachments(raw)
		self.assertEqual(
			(img["type"], img["url"], img["thumb"], img.get("inline")), ("image", JPG, THUMB, True)
		)
		self.assertEqual(img["name"], "")  # không in URL dài làm tên
		self.assertTrue(gif["inline"])
		self.assertEqual(fil["name"], "bao-gia.pdf")
		self.assertNotIn("inline", fil)

	def test_foreign_host_is_link_only_and_bad_schemes_dropped(self):
		raw = json.dumps(
			[
				{"type": "image", "url": "https://evil.example/a.png", "thumb": "https://evil.example/t.png"},
				{"type": "image", "url": "javascript:alert(1)"},
				{"type": "image", "url": "data:image/png;base64,AAAA"},
				{"type": "image", "url": JPG, "thumb": "javascript:alert(1)"},
			]
		)
		foreign, good = inbox._safe_attachments(raw)
		self.assertEqual(foreign["url"], "https://evil.example/a.png")
		self.assertNotIn("inline", foreign)
		self.assertNotIn("thumb", foreign)
		self.assertTrue(good["inline"])
		self.assertNotIn("thumb", good)

	def test_local_files_inline_and_non_media_type_never_inline(self):
		a, b = inbox._safe_attachments(
			json.dumps([{"type": "image", "url": "/files/a.png"}, {"type": "file", "url": JPG}])
		)
		self.assertTrue(a["inline"])
		self.assertNotIn("inline", b)


class TestThreadData(BridgeTestCase):
	def test_end_to_end_image_and_gif_reach_thread(self):
		ev_img = parsed(
			"user_send_image",
			[{"type": "image", "payload": {"thumbnail": THUMB, "url": JPG}}],
			msg_id="V305A",
		)
		ev_gif = parsed("user_send_gif", [{"type": "gif", "payload": {"url": GIF}}], msg_id="V305B")
		m1 = self.send("V305A", attachments=ev_img.attachments, text=ev_img.text)
		self.send("V305B", attachments=ev_gif.attachments, text=ev_gif.text)
		lead = self.lead_of(m1)
		msgs = inbox.thread_data(lead)["messages"]
		by_text = {m["text"]: m for m in msgs}
		self.assertEqual(by_text["[Ảnh]"]["attachments"][0]["url"], JPG)
		self.assertTrue(by_text["[Ảnh]"]["attachments"][0]["inline"])
		self.assertEqual(by_text["[GIF]"]["attachments"][0]["type"], "gif")


class TestReport(BridgeTestCase):
	def test_report_is_privacy_safe(self):
		ev = parsed(
			"user_send_image",
			[
				{"type": "image", "payload": {"url": JPG}},
				{"type": "image", "payload": {"url": "https://evil.example/s/secret.png"}},
			],
			text="x" * 100,
			msg_id="V305R",
		)
		msg = self.send("V305R", attachments=ev.attachments, text="x" * 100)
		frappe.db.set_value(
			"Omni Inbound Message", msg, "raw_payload", json.dumps({"event_name": "user_send_image"})
		)
		frappe.db.commit()
		rows = zalo_media_report.run(limit=50)["messages"]
		row = next(r for r in rows if r["name"] == msg)
		self.assertEqual(row["event_name"], "user_send_image")
		self.assertTrue(row["has_attachments"])
		self.assertLessEqual(len(row["text"]), 41)
		hosts = [a["host"] for a in row["attachments"]]
		self.assertEqual(hosts, ["photo-stal-27.zdn.vn", "evil.example"])
		self.assertEqual([a["filtered"] for a in row["attachments"]], [False, False])
		self.assertEqual([a["inline"] for a in row["attachments"]], [True, False])
		self.assertIn("unparsed_7d", zalo_media_report.run(limit=1))
		self.assertNotIn("secret.png", json.dumps(row))
		self.assertNotIn(JPG, json.dumps(row))
