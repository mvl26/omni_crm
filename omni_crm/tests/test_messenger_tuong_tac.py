"""Tương tác Messenger đợt 1 (ADR 0027): trả lời tin cụ thể, sticker, cảm xúc (gửi và nhận), ẩn tin trong CRM.
Dùng chung dữ liệu thử TD23 của test tab Messenger (dọn thủ công)."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from omni_crm.api import messenger_tab
from omni_crm.channels.meta_messenger import client as mc
from omni_crm.channels.meta_messenger.adapter import MetaMessengerAdapter
from omni_crm.ingest.events import OutboundRequest
from omni_crm.intake_files import mapper, runner
from omni_crm.tests.ads_helpers import work
from omni_crm.tests.test_meta_messenger_tab import CH, FLAGS, PAGE, PSID, cleanup, menv, set_flag

OUT = "Omni Outbound Message"
IN = "Omni Inbound Message"
SALE = "td27.sale@example.com"


def drop_sale():
	if frappe.db.exists("User", SALE):
		frappe.delete_doc("User", SALE, force=True, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()


class FakeResp:
	def __init__(self, data, status=200):
		self.data, self.status_code = data, status

	def json(self):
		return self.data


class TestClientBodies(FrappeTestCase):
	"""Thân lời gọi Send API (không cần DB): reply_to, sticker, react/unreact, kho sticker dùng App Token."""

	def setUp(self):
		self.ch = frappe._dict(name="TD27", meta_page_id="P1", meta_app_id="A1")
		self.ch.get_password = lambda f, raise_exception=False: {
			"meta_page_access_token": "PAGE_T",
			"meta_app_secret": "SEC",
		}.get(f, "")
		p = mock.patch.object(mc.flags, "sandbox", return_value=False)
		p.start()
		self.addCleanup(p.stop)

	def call(self, fn, data=None):
		with mock.patch.object(
			mc.requests, "request", return_value=FakeResp(data or {"message_id": "m_1"})
		) as r:
			fn(mc.MessengerClient(self.ch))
		return r

	def test_tra_loi_tin_chi_doan_dau(self):
		r = self.call(lambda c: c.send_text(PSID, "a" * 2500, quote_mid="m_goc"))
		bodies = [k.kwargs["json"] for k in r.call_args_list]
		self.assertEqual(bodies[0]["reply_to"], {"mid": "m_goc"})
		self.assertNotIn("reply_to", bodies[1])

	def test_sticker_va_cam_xuc(self):
		r = self.call(lambda c: c.send_sticker(PSID, "369239263222822", "m_goc"))
		body = r.call_args.kwargs["json"]
		self.assertEqual(body["message"], {"sticker_id": 369239263222822})
		self.assertEqual(body["reply_to"], {"mid": "m_goc"})
		r = self.call(lambda c: c.react(PSID, "m_x", "❤"))
		self.assertEqual(
			r.call_args.kwargs["json"],
			{
				"recipient": {"id": PSID},
				"sender_action": "react",
				"payload": {"message_id": "m_x", "reaction": "❤"},
			},
		)
		r = self.call(lambda c: c.react(PSID, "m_x", ""))
		self.assertEqual(r.call_args.kwargs["json"]["sender_action"], "unreact")
		self.assertNotIn("reaction", r.call_args.kwargs["json"]["payload"])
		with self.assertRaises(mc.MessengerError):
			mc.MessengerClient(self.ch).send_sticker(PSID, "abc")

	def test_tep_kem_tra_loi_va_kho_sticker_dung_app_token(self):
		r = self.call(lambda c: c.send_attachment(PSID, "image", "a.png", b"x", "image/png", "m_goc"))
		self.assertEqual(json.loads(r.call_args.kwargs["data"]["reply_to"]), {"mid": "m_goc"})
		r = self.call(lambda c: c.search_stickers("cảm ơn", "vi_VN"), {"data": [{"id": "1"}]})
		self.assertEqual(r.call_args.kwargs["params"]["access_token"], "A1|SEC")
		self.assertEqual(r.call_args.kwargs["params"]["locale"], "vi_VN")

	def test_sandbox_khong_goi_http(self):
		with (
			mock.patch.object(mc.flags, "sandbox", return_value=True),
			mock.patch.object(mc.requests, "request") as r,
		):
			c = mc.MessengerClient(self.ch)
			self.assertTrue(c.send_sticker(PSID, "1").startswith("sandbox:"))
			c.react(PSID, "m_x", "❤")
		r.assert_not_called()

	def test_adapter_chon_duong_gui(self):
		req = OutboundRequest(
			channel="c", identity="i", text="", reply_to_external_id=None, source="Sale",
			extras={"sticker_id": "5", "quote_mid": "m_q"},
		)  # fmt: skip
		with (
			mock.patch.object(frappe.db, "get_value", return_value=PSID),
			mock.patch.object(mc.MessengerClient, "send_sticker", return_value="m_s") as st,
		):
			self.assertEqual(MetaMessengerAdapter().send(req, self.ch), "m_s")
		st.assert_called_once_with(PSID, "5", "m_q")


class TestTuongTac(FrappeTestCase):
	def setUp(self):
		cleanup()
		drop_sale()
		self.addCleanup(cleanup)
		self.addCleanup(drop_sale)
		saved = {f: frappe.db.get_single_value("Omni Settings", f) for f in FLAGS}
		self.addCleanup(lambda: [set_flag(f, v or 0) for f, v in saved.items()])
		for f in FLAGS:
			set_flag(f, 0)
		sandbox = frappe.db.get_single_value("Omni Settings", "sandbox_mode")
		self.addCleanup(lambda: set_flag("sandbox_mode", sandbox or 0))
		set_flag("sandbox_mode", 1)  # không HTTP ra Meta; mã tin "sandbox:…"
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

	def feed(self, env):
		(self.dir / f"{abs(hash(env['event_key']))}.json").write_text(json.dumps(env), encoding="utf-8")
		runner.run()

	def lead_with_message(self, key="messenger:m_td27_1", **over):
		self.feed(menv(key, **over))
		work(f"Meta Messenger:{PSID}")
		return frappe.db.get_value("Omni Identity", {"identity_key": f"Meta Messenger:{PSID}"}, "lead")

	def conv(self, lead):
		return {m["name"]: m for m in messenger_tab.get_conversation("CRM Lead", lead)["messages"]}

	def reaction_env(self, mid, action="react", emoji="❤", ts="2026-11-13T10:00:00+07:00", psid=PSID):
		return {
			**menv(f"messenger:reaction:{mid}:{psid}:{action}:{ts}"),
			"kind": "reaction",
			"occurred_at": ts,
			"sender": {"type": "psid", "value": psid},
			"raw": {
				"event": {"reaction": {"mid": mid, "action": action, "emoji": emoji, "reaction": "love"}}
			},
		}

	def test_khach_tra_loi_tin_va_trich_dan(self):
		lead = self.lead_with_message()
		raw = {"event": {"message": {"mid": "m_td27_2", "reply_to": {"mid": "m_td27_1"}}}}
		self.lead_with_message(
			"messenger:m_td27_2", raw=raw, content={"text": "Ý em là cái này", "attachments": []}
		)
		rows = list(self.conv(lead).values())
		second = next(r for r in rows if r["mid"] == "m_td27_2")
		self.assertEqual(second["reply_to"]["text"], "Cho mình xin báo giá")
		self.assertEqual(mapper.reply_to_of({"raw": raw}), "m_td27_1")

	def test_sale_tra_loi_tin_cu_the_va_gui_sticker(self):
		lead = self.lead_with_message()
		first = frappe.db.get_value(IN, {"lead": lead}, "name")
		with mock.patch("frappe.enqueue"):
			res = messenger_tab.send("CRM Lead", lead, "Dạ có ạ", IN, first)
			st = messenger_tab.send_sticker(
				"CRM Lead", lead, "369239263222822", "https://evil.example/x.png", IN, first
			)
		self.assertEqual(res["status"], "Queued", res)
		self.assertEqual(frappe.db.get_value(OUT, res["outbound"], "reply_to_mid"), "m_td27_1")
		row = frappe.db.get_value(
			OUT, st["outbound"], ["sticker_id", "sticker_url", "reply_to_mid"], as_dict=True
		)
		self.assertEqual(
			(row.sticker_id, row.sticker_url, row.reply_to_mid), ("369239263222822", "", "m_td27_1")
		)
		with self.assertRaises(frappe.ValidationError):
			messenger_tab.send_sticker("CRM Lead", lead, "12ab")
		other = frappe.get_doc({"doctype": "CRM Lead", "first_name": "TD27 khac"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(
			lambda: frappe.delete_doc("CRM Lead", other.name, force=True, ignore_permissions=True)
		)
		with self.assertRaises(frappe.PermissionError):  # tin của Lead khác
			messenger_tab.send("CRM Lead", other.name, "x", IN, first)

	def test_cam_xuc_cua_khach(self):
		lead = self.lead_with_message()
		msg = frappe.db.get_value(IN, {"lead": lead}, "name")
		self.feed(self.reaction_env("m_td27_1"))
		self.assertEqual(self.conv(lead)[msg]["reactions"]["customer"], "❤")
		self.feed(self.reaction_env("m_td27_1", ts="2026-11-13T09:00:00+07:00", emoji="😢"))  # cũ hơn: bỏ
		self.assertEqual(frappe.db.get_value(IN, msg, "reaction_customer"), "❤")
		self.feed(self.reaction_env("m_td27_1", action="unreact", ts="2026-11-13T11:00:00+07:00"))
		self.assertEqual(frappe.db.get_value(IN, msg, "reaction_customer"), "")
		self.feed(self.reaction_env("m_td27_1", psid="nguoi_khac", ts="2026-11-13T12:00:00+07:00"))
		self.assertEqual(frappe.db.get_value(IN, msg, "reaction_customer"), "")  # khách khác: bỏ
		self.assertFalse(frappe.db.exists(IN, {"external_message_id": ["like", "reaction:%"]}))

	def test_sale_tha_cam_xuc(self):
		lead = self.lead_with_message()
		msg = frappe.db.get_value(IN, {"lead": lead}, "name")
		res = messenger_tab.react("CRM Lead", lead, IN, msg, "👍")
		self.assertEqual(res["reaction"], "👍")
		self.assertEqual(
			frappe.db.get_value(IN, msg, ["reaction_page", "reaction_page_by"]), ("👍", "Administrator")
		)
		messenger_tab.react("CRM Lead", lead, IN, msg, "")
		self.assertEqual(frappe.db.get_value(IN, msg, "reaction_page"), "")
		with self.assertRaises(frappe.ValidationError):
			messenger_tab.react("CRM Lead", lead, IN, msg, "abc")

	def test_an_tin_mot_phia(self):
		lead = self.lead_with_message()
		msg_in = frappe.db.get_value(IN, {"lead": lead}, "name")
		with mock.patch("frappe.enqueue"):
			frappe.get_doc(
				{"doctype": "User", "email": SALE, "first_name": "TD27", "send_welcome_email": 0,
				 "roles": [{"role": "Sales User"}]}
			).insert(ignore_permissions=True)  # fmt: skip
			frappe.share.add("CRM Lead", lead, SALE, read=1, write=1, notify=0)  # sale được giao Lead này
		frappe.db.commit()
		frappe.set_user(SALE)
		with mock.patch("frappe.enqueue"):
			own = messenger_tab.send("CRM Lead", lead, "Tin của sale")["outbound"]
		with self.assertRaises(frappe.PermissionError):  # sale không ẩn tin của khách
			messenger_tab.hide_message("CRM Lead", lead, IN, msg_in)
		messenger_tab.hide_message("CRM Lead", lead, OUT, own)
		self.assertNotIn(own, self.conv(lead))  # sale không còn thấy
		frappe.set_user("Administrator")
		row = self.conv(lead)[own]
		self.assertTrue(row["hidden"])  # quản lý thấy, có đánh dấu
		self.assertEqual(frappe.db.get_value(OUT, own, "hidden_by"), SALE)
		messenger_tab.hide_message("CRM Lead", lead, OUT, own, hidden=0)
		self.assertFalse(frappe.db.get_value(OUT, own, "hidden_at"))
		self.assertEqual(frappe.db.get_value(OUT, own, "text"), "Tin của sale")  # bản ghi gốc giữ nguyên
