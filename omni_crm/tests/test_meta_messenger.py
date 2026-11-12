"""Kênh Meta Messenger (ADR 0022): client Graph, adapter, ánh xạ file, chép đính kèm, hiển thị. Không gọi mạng."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import requests
from frappe import _dict
from frappe.utils import now_datetime

from omni_crm.channels.meta_messenger import client as mc
from omni_crm.channels.meta_messenger.adapter import MetaMessengerAdapter
from omni_crm.crm_bridge import timeline
from omni_crm.intake_files import attachments, mapper

PAGE, PSID, APP = "571684352685074", "26000000000000001", "1012569668539635"


def menv(**over):
	"""Envelope Messenger theo đúng dạng miyano-intake ghi (intake/channels/meta.py::_message_event)."""
	base = {
		"schema": "1",
		"event_key": "messenger:m_ABC",
		"channel": "messenger",
		"kind": "message",
		"direction": "in",
		"occurred_at": "2026-11-12T10:00:00+07:00",
		"received_at": "2026-11-12T10:00:02+07:00",
		"sender": {"type": "psid", "value": PSID},
		"content": {"text": "Cho mình xin báo giá", "attachments": []},
		"thread_ref": f"messenger:{PSID}",
		"campaign": {"ad_id": None},
		"source": {"account": PAGE, "signature_verified": True},
		"raw": {"entry_id": PAGE, "event": {"sender": {"id": PSID}, "message": {"mid": "m_ABC"}}},
	}
	base.update(over)
	return base


class Channel(_dict):
	def get_password(self, field, raise_exception=True):
		return self.get("_" + field)


def channel(**kw):
	values = {
		"name": "messenger-test",
		"meta_page_id": PAGE,
		"meta_app_id": APP,
		"outbound_enabled": 1,
		"_meta_page_access_token": "tok",
	}
	values.update(kw)
	return Channel(values)


class Resp:
	def __init__(self, status, data):
		self.status_code, self._data = status, data

	def json(self):
		return self._data


class TestMapper(unittest.TestCase):
	def test_tin_khach(self):
		m = mapper.map_envelope(menv())
		self.assertEqual(m.channel_type, mapper.MESSENGER)
		ev = mapper.with_channel(menv(), m, "messenger-test").event
		self.assertEqual(
			(ev.event_type, ev.external_message_id, ev.external_sender_id), ("message", "m_ABC", PSID)
		)
		self.assertEqual(ev.sender_profile["page_id"], PAGE)
		self.assertEqual(ev.raw["intake"]["event_key"], "messenger:m_ABC")

	def test_dinh_kem_giu_path_va_ten(self):
		atts = [
			{"type": "image", "url": "https://cdn/x", "path": "attachments/abc/anh 1.jpg"},
			{"type": "file", "path": None},
		]
		env = menv(content={"text": None, "attachments": atts})
		ev = mapper.with_channel(env, mapper.map_envelope(env), "c").event
		self.assertEqual(ev.text, None)
		self.assertEqual(
			ev.attachments[0], {"type": "image", "path": "attachments/abc/anh 1.jpg", "name": "anh 1.jpg"}
		)
		self.assertEqual(ev.attachments[1]["name"], "file")

	def test_echo_postback_referral(self):
		echo = menv(direction="out", sender={"type": "page", "value": PAGE}, event_key="messenger:m_E")
		ev = mapper.with_channel(echo, mapper.map_envelope(echo), "c").event
		self.assertEqual((ev.event_type, ev.external_sender_id), ("oa_echo", PSID))
		pb = menv(event_key="messenger:postback:m_P", content={"text": "Bắt đầu", "attachments": []})
		self.assertEqual(mapper.with_channel(pb, mapper.map_envelope(pb), "c").event.event_type, "message")
		ref = menv(event_key=f"messenger:referral:{PSID}:1", content={"text": None, "attachments": []})
		self.assertEqual(mapper.with_channel(ref, mapper.map_envelope(ref), "c").event.event_type, "ignored")

	def test_bo_tin_rong_va_khoa_sai(self):
		empty = menv(content={"text": "", "attachments": []})
		self.assertEqual(mapper.with_channel(empty, mapper.map_envelope(empty), "c").skip, mapper.SKIP_EMPTY)
		bad = menv(event_key="khac:1")
		self.assertEqual(mapper.with_channel(bad, mapper.map_envelope(bad), "c").skip, mapper.SKIP_EMPTY)

	def test_echo_app_id(self):
		env = menv(raw={"event": {"message": {"mid": "m", "is_echo": True, "app_id": int(APP)}}})
		self.assertEqual(mapper.echo_app_id(env), APP)
		self.assertEqual(mapper.echo_app_id(menv()), "")


class TestClient(unittest.TestCase):
	def setUp(self):
		for p in (
			mock.patch.object(mc.frappe, "conf", _dict()),
			mock.patch.object(mc.flags, "sandbox", return_value=False),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_tach_tin_dai(self):
		parts = mc.split_text("a" * 1500 + "\n" + "b" * 1500)
		self.assertEqual([len(p) for p in parts], [1500, 1500])
		self.assertTrue(all(len(p) <= mc.MAX_CHARS for p in mc.split_text("x" * 4500)))

	def test_gui_chu_dung_yeu_cau(self):
		with mock.patch.object(
			mc.requests, "request", return_value=Resp(200, {"message_id": "m_OUT"})
		) as req:
			self.assertEqual(mc.MessengerClient(channel()).send_text(PSID, "Chào anh"), "m_OUT")
		method, url = req.call_args.args
		self.assertEqual((method, url), ("POST", f"https://graph.facebook.com/v23.0/{PAGE}/messages"))
		body = req.call_args.kwargs["json"]
		self.assertEqual(
			body, {"recipient": {"id": PSID}, "messaging_type": "RESPONSE", "message": {"text": "Chào anh"}}
		)
		self.assertEqual(req.call_args.kwargs["params"]["access_token"], "tok")
		self.assertEqual(req.call_args.kwargs["timeout"], mc.TIMEOUT)

	def test_ngoai_24_gio_la_window_khong_lo_token(self):
		err = {"error": {"code": 10, "error_subcode": 2018278, "message": "outside window"}}
		with mock.patch.object(mc.requests, "request", return_value=Resp(400, err)):
			with self.assertRaises(mc.MessengerError) as cm:
				mc.MessengerClient(channel()).send_text(PSID, "x")
		self.assertEqual(cm.exception.kind, mc.WINDOW)
		self.assertNotIn("tok", str(cm.exception) + cm.exception.message_vi)

	def test_phan_loai_loi(self):
		self.assertEqual(mc.classify(613, None, 400)[0], mc.TRANSIENT)
		self.assertEqual(mc.classify(None, None, 503)[0], mc.TRANSIENT)
		self.assertEqual(mc.classify(190, None, 400)[0], mc.PERMANENT)
		self.assertEqual(mc.classify(999, None, 400)[0], mc.PERMANENT)

	def test_loi_mang_giu_nguyen_cause(self):
		with mock.patch.object(mc.requests, "request", side_effect=requests.ConnectTimeout("t")):
			with self.assertRaises(mc.MessengerError) as cm:
				mc.MessengerClient(channel()).send_text(PSID, "x")
		self.assertEqual(cm.exception.kind, mc.TRANSIENT)
		self.assertIsInstance(cm.exception.__cause__, requests.ConnectTimeout)

	def test_tin_tach_loi_giua_chung_ghi_sent_ids(self):
		responses = [Resp(200, {"message_id": "m1"}), Resp(500, {"error": {"code": 2}})]
		with mock.patch.object(mc.requests, "request", side_effect=responses):
			with self.assertRaises(mc.MessengerError) as cm:
				mc.MessengerClient(channel()).send_text(PSID, "a" * 2500)
		self.assertEqual(cm.exception.sent_ids, ["m1"])

	def test_lay_ten_qua_hoi_thoai(self):
		conv = {
			"data": [
				{"participants": {"data": [{"id": PAGE, "name": "Miyano"}, {"id": PSID, "name": "Lê Khách"}]}}
			]
		}
		with mock.patch.object(mc.requests, "request", return_value=Resp(200, conv)) as req:
			self.assertEqual(mc.MessengerClient(channel()).get_name(PSID), "Lê Khách")
		self.assertEqual(req.call_args.args[1], f"https://graph.facebook.com/v23.0/{PAGE}/conversations")
		self.assertEqual(req.call_args.kwargs["params"]["user_id"], PSID)

	def test_lay_ten_du_phong_ho_so_va_bao_loi_ro(self):
		no_perm = Resp(400, {"error": {"code": 100, "error_subcode": 33}})
		with mock.patch.object(
			mc.requests,
			"request",
			side_effect=[Resp(200, {"data": []}), Resp(200, {"first_name": "A", "last_name": "B"})],
		):
			self.assertEqual(mc.MessengerClient(channel()).get_name(PSID), "A B")
		with mock.patch.object(mc.requests, "request", side_effect=[Resp(200, {"data": []}), no_perm]):
			with self.assertRaises(mc.MessengerError) as cm:
				mc.MessengerClient(channel()).get_name(PSID)
		self.assertIn("không trả tên", cm.exception.message_vi)
		self.assertIn("100/33", cm.exception.message_vi)

	def test_sandbox_khong_goi_http(self):
		with (
			mock.patch.object(mc.flags, "sandbox", return_value=True),
			mock.patch.object(mc.requests, "request") as req,
		):
			self.assertTrue(mc.MessengerClient(channel()).send_text(PSID, "x").startswith("sandbox:"))
		req.assert_not_called()

	def test_thieu_token(self):
		ch = channel()
		ch["_meta_page_access_token"] = None
		with self.assertRaises(mc.MessengerError):
			mc.MessengerClient(ch).send_text(PSID, "x")


class TestAdapter(unittest.TestCase):
	def test_khung_24_gio(self):
		a = MetaMessengerAdapter()
		ok, _why = a.can_send_now(_dict(last_inbound_at=now_datetime() - timedelta(hours=2)), channel())
		self.assertTrue(ok)
		ok, why = a.can_send_now(_dict(last_inbound_at=now_datetime() - timedelta(hours=25)), channel())
		self.assertFalse(ok)
		self.assertIn("24", why)
		self.assertFalse(a.can_send_now(_dict(last_inbound_at=None), channel())[0])
		self.assertFalse(
			a.can_send_now(_dict(last_inbound_at=now_datetime()), channel(outbound_enabled=0))[0]
		)

	def test_khong_nhan_webhook_truc_tiep(self):
		a = MetaMessengerAdapter()
		self.assertFalse(a.verify_request(b"{}", {"x-hub-signature-256": "sha256=x"}, channel()))
		self.assertEqual(a.parse(b"{}", {}, channel()), [])


class TestAttachments(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name)
		(self.root / "attachments" / "abc").mkdir(parents=True)
		(self.root / "attachments" / "abc" / "anh.jpg").write_bytes(b"\xff\xd8jpg")
		(self.root / "bimat.txt").write_text("khong duoc doc")

	def _store(self, items, limit=10 * 1024 * 1024):
		created = []

		def fake_get_doc(values):
			doc = mock.MagicMock()
			doc.name, doc.file_url, doc.file_size = (
				f"F{len(created)}",
				f"/private/files/{values['file_name']}",
				4,
			)
			created.append(values)
			doc.insert.return_value = doc
			return doc

		with (
			mock.patch.object(attachments.frappe, "get_doc", side_effect=fake_get_doc),
			mock.patch.object(attachments, "get_max_file_size", return_value=limit),
		):
			return attachments.store(self.root, items), created

	def test_chep_vao_file_rieng_tu(self):
		(out, names), created = self._store(
			[{"type": "image", "path": "attachments/abc/anh.jpg", "name": "anh.jpg"}]
		)
		self.assertEqual(names, ["F0"])
		self.assertEqual(created[0]["is_private"], 1)
		self.assertEqual(out[0]["url"], "/private/files/anh.jpg")
		self.assertEqual(out[0]["mime"], "image/jpeg")

	def test_chan_duong_dan_ra_ngoai_va_tep_thieu(self):
		items = [
			{"type": "file", "path": "attachments/../bimat.txt", "name": "bimat.txt"},
			{"type": "file", "path": "attachments/abc/khong-co.pdf", "name": "khong-co.pdf"},
			{"type": "file", "path": None, "name": "file"},
		]
		(out, names), created = self._store(items)
		self.assertEqual((names, created), ([], []))
		self.assertTrue(all(o.get("note") and not o.get("url") for o in out))

	def test_qua_gioi_han(self):
		(out, names), _c = self._store(
			[{"type": "video", "path": "attachments/abc/anh.jpg", "name": "v.mp4"}], limit=2
		)
		self.assertEqual(names, [])
		self.assertIn("note", out[0])


class TestRender(unittest.TestCase):
	def test_tep_trong_he_thong_hien_anh_va_trinh_phat(self):
		html = timeline.render_text(
			"Gửi anh",
			[
				{"url": "/private/files/a.jpg", "name": "a.jpg", "type": "image"},
				{"url": "/private/files/v.mp4", "name": "v.mp4", "type": "video"},
				{"url": "/private/files/g.mp3", "name": "g.mp3", "type": "audio"},
				{"url": "/private/files/b.docx", "name": "b.docx", "type": "file"},
				{"url": "", "name": "lon.zip", "type": "file", "note": "Tệp lớn"},
			],
		)
		self.assertIn('<img src="/private/files/a.jpg"', html)
		self.assertIn('<video controls preload="metadata" src="/private/files/v.mp4"', html)
		self.assertIn('<audio controls preload="none" src="/private/files/g.mp3"', html)
		self.assertIn('href="/private/files/b.docx"', html)
		self.assertIn("lon.zip (Tệp lớn)", html)

	def test_link_ngoai_giu_nhu_cu(self):
		html = timeline.render_text("x", [{"url": "https://zalo.me/a.jpg", "name": "a.jpg", "type": "image"}])
		self.assertIn('<a href="https://zalo.me/a.jpg" target="_blank" rel="noopener">a.jpg</a>', html)
		self.assertNotIn("<img", html)

	def test_ten_tep_doc_hai_bi_thoat(self):
		html = timeline.render_text(
			"", [{"url": "/private/files/x.jpg", "name": '"><script>', "type": "image"}]
		)
		self.assertNotIn("<script>", html)
