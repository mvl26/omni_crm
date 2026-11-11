"""V3-09: sale gửi ảnh (JPG/PNG/GIF) và tệp (PDF/DOC/DOCX/CSV) qua Zalo OA từ tab Zalo.

Mọi lời gọi Zalo đều giả (mock `requests`). Tệp tạo thật trong File (gắn Lead) và được xóa sau mỗi test.
"""

from __future__ import annotations

import io
import json
import os
from unittest import mock

import frappe
from PIL import Image

from omni_crm.api import zalo_tab
from omni_crm.channels.zalo import media_out
from omni_crm.channels.zalo.client import ZaloClient
from omni_crm.channels.zalo.errors import ZaloError
from omni_crm.channels.zalo.phone_request import WHITELIST_USER_IDS
from omni_crm.pipeline import outbound
from omni_crm.tests import test_inbox
from omni_crm.tests.test_outbound import REQ, OutboundTestCase
from omni_crm.tests.zalo_helpers import response

SALE1 = test_inbox.SALE1
OUT = "Omni Outbound Message"
TEST_USER = next(iter(WHITELIST_USER_IDS))
UPLOAD_IMG = {"error": 0, "data": {"attachment_id": "att-img-1"}}
UPLOAD_FILE = {"error": 0, "data": {"token": "tok-file-1"}}
SENT = {"error": 0, "data": {"message_id": "zl-media-1", "quota": {"quota_type": "reply"}}}


def png_bytes(w: int = 40, h: int = 30, noise: bool = False) -> bytes:
	if noise:
		img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
	else:
		img = Image.new("RGB", (w, h), (200, 30, 30))
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


def pdf_bytes() -> bytes:
	from pypdf import PdfWriter

	w = PdfWriter()
	w.add_blank_page(width=200, height=200)
	buf = io.BytesIO()
	w.write(buf)
	return buf.getvalue()


def gif_bytes(w: int = 32, h: int = 24) -> bytes:
	frames = [Image.new("P", (w, h), c) for c in (1, 2)]
	buf = io.BytesIO()
	frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
	return buf.getvalue()


def switch(on: bool):
	return mock.patch("omni_crm.channels.zalo.media_out.enabled", return_value=on)


class MediaCase(OutboundTestCase):
	def setUp(self):
		super().setUp()
		self.set_token()
		self.sandbox(False)
		for kind in media_out.KINDS:
			frappe.cache().delete(media_out._upload_key(kind))

	def attach(self, name: str, content: bytes, doctype: str = "CRM Lead", docname: str | None = None) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": name,
				"attached_to_doctype": doctype,
				"attached_to_name": docname or self.lead1,
				"content": content,
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(self._drop_file, doc.name)
		return doc.file_url

	@staticmethod
	def _drop_file(name: str) -> None:
		if frappe.db.exists("File", name):
			frappe.delete_doc("File", name, force=True, ignore_permissions=True)
			frappe.db.commit()

	def as_sale(self):
		frappe.set_user(SALE1)

	def send_tab(self, file_url: str, text: str = "", confirm_paid: int = 0) -> dict:
		self.as_sale()
		with mock.patch("frappe.enqueue"):
			res = zalo_tab.send_attachment("CRM Lead", self.lead1, file_url, text, confirm_paid)
			frappe.db.commit()
		frappe.set_user("Administrator")
		return res

	def deliver(self, name: str, replies: list[dict]):
		with mock.patch(REQ, side_effect=[response(r) for r in replies]) as http:
			status = outbound.send_outbound(name)
		return status, http

	def out_row(self, name: str):
		return frappe.db.get_value(
			OUT,
			name,
			[
				"status",
				"text",
				"attachment",
				"attachment_kind",
				"attachment_name",
				"last_error",
				"message_kind",
			],
			as_dict=True,
		)


# --- Luật tệp (thuần) ----------------------------------------------------------------------------------------


class TestRules(MediaCase):
	def test_kind_by_extension(self):
		self.assertEqual(media_out.kind_of("Bao gia.PDF"), "file")
		self.assertEqual(media_out.kind_of("a.jpeg"), "image")
		self.assertEqual(media_out.kind_of("b.gif"), "gif")
		self.assertEqual(media_out.kind_of("c.docx"), "file")
		self.assertIsNone(media_out.kind_of("d.xlsx"))  # Zalo không nhận xlsx
		self.assertIsNone(media_out.kind_of("e.exe"))

	def test_small_image_kept_large_image_shrunk_under_1mb_as_jpeg(self):
		small = png_bytes()
		self.assertEqual(media_out.shrink_image(small), (small, ".png"))
		big = png_bytes(1400, 1000, noise=True)
		self.assertGreater(len(big), media_out.MB)
		out, ext = media_out.shrink_image(big)
		self.assertLessEqual(len(out), media_out.MB)
		self.assertEqual(ext, ".jpg")
		self.assertEqual(Image.open(io.BytesIO(out)).format, "JPEG")

	def test_gif_size(self):
		self.assertEqual(media_out.gif_size(gif_bytes(32, 24)), (32, 24))


class TestAttachmentBelongsToLead(MediaCase):
	def test_file_of_lead_or_its_deal_accepted(self):
		url = self.attach("bao-gia.pdf", pdf_bytes())
		att = media_out.attachment_for_lead(url, self.lead1)
		self.assertEqual((att.kind, att.name), ("file", "bao-gia.pdf"))
		deal = frappe.get_doc(
			{"doctype": "CRM Deal", "lead": self.lead1, "first_name": "TD109 deal", "deal_owner": SALE1}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.commit()
		self.addCleanup(lambda: frappe.delete_doc("CRM Deal", deal.name, force=True, ignore_permissions=True))
		url2 = self.attach("anh.png", png_bytes(), "CRM Deal", deal.name)
		self.assertEqual(media_out.attachment_for_lead(url2, self.lead1).kind, "image")

	def test_other_lead_external_url_and_bad_types_refused(self):
		other = self.attach("cua-lead-khac.pdf", pdf_bytes(), "CRM Lead", self.lead2)
		for url in (other, "https://evil.example/a.pdf", "/files/khong-co.pdf", ""):
			with self.assertRaises(media_out.MediaError):
				media_out.attachment_for_lead(url, self.lead1)
		xlsx = self.attach("bang.xlsx", b"PK td")
		with self.assertRaises(media_out.MediaError):
			media_out.attachment_for_lead(xlsx, self.lead1)

	def test_gif_or_file_over_5mb_refused(self):
		url = self.attach("lon.csv", b"0" * (5 * media_out.MB + 1))
		with self.assertRaises(media_out.MediaError) as ctx:
			media_out.attachment_for_lead(url, self.lead1)
		self.assertIn("5 MB", str(ctx.exception))


# --- ZaloClient: danh sách trắng, tải lên, gửi ------------------------------------------------------------------


class TestClient(MediaCase):
	def client(self):
		return ZaloClient(self.channel.name)

	def test_switch_off_blocks_non_test_user_before_sandbox_and_http(self):
		with switch(False), mock.patch(REQ) as http:
			with self.assertRaises(ZaloError) as ctx:
				self.client().send_media("khach-that-1", "image", png_bytes(), "a.png")
			self.assertEqual(ctx.exception.kind, "permanent")
			with mock.patch("omni_crm.flags.sandbox", return_value=True):
				with self.assertRaises(ZaloError):
					self.client().send_media("khach-that-1", "file", pdf_bytes(), "a.pdf")
		http.assert_not_called()

	def test_switch_off_test_account_allowed(self):
		with switch(False), mock.patch(REQ, side_effect=[response(UPLOAD_IMG), response(SENT)]) as http:
			mid = self.client().send_media(TEST_USER, "image", png_bytes(), "a.png", caption="Mẫu A")
		self.assertEqual(mid, "zl-media-1")
		self.assertEqual(http.call_count, 2)

	def test_image_upload_then_media_template_with_caption(self):
		with switch(True), mock.patch(REQ, side_effect=[response(UPLOAD_IMG), response(SENT)]) as http:
			mid = self.client().send_media("khach-1", "image", png_bytes(), "a.png", caption="Mẫu A")
		self.assertEqual(mid, "zl-media-1")
		up, send = http.call_args_list
		self.assertTrue(up.args[1].endswith("/v2.0/oa/upload/image"))
		self.assertEqual(up.kwargs["files"]["file"][2], "image/png")
		self.assertNotIn("Content-Type", up.kwargs["headers"])  # requests tự đặt multipart
		body = json.loads(send.kwargs["data"])
		self.assertEqual(body["message"]["text"], "Mẫu A")
		self.assertEqual(
			body["message"]["attachment"]["payload"],
			{"template_type": "media", "elements": [{"media_type": "image", "attachment_id": "att-img-1"}]},
		)
		self.assertEqual(media_out.uploads_this_month("image"), 1)

	def test_gif_sends_width_height(self):
		with switch(True), mock.patch(REQ, side_effect=[response(UPLOAD_IMG), response(SENT)]) as http:
			self.client().send_media("khach-1", "gif", gif_bytes(), "a.gif", size=(32, 24))
		self.assertTrue(http.call_args_list[0].args[1].endswith("/v2.0/oa/upload/gif"))
		el = json.loads(http.call_args_list[1].kwargs["data"])["message"]["attachment"]["payload"][
			"elements"
		][0]
		self.assertEqual((el["media_type"], el["width"], el["height"]), ("gif", 32, 24))

	def test_file_sends_token_without_text(self):
		with switch(True), mock.patch(REQ, side_effect=[response(UPLOAD_FILE), response(SENT)]) as http:
			self.client().send_media("khach-1", "file", pdf_bytes(), "bao-gia.pdf", caption="bỏ qua")
		self.assertTrue(http.call_args_list[0].args[1].endswith("/v2.0/oa/upload/file"))
		self.assertEqual(http.call_args_list[0].kwargs["files"]["file"][2], "application/pdf")
		message = json.loads(http.call_args_list[1].kwargs["data"])["message"]
		self.assertEqual(message, {"attachment": {"type": "file", "payload": {"token": "tok-file-1"}}})

	def test_monthly_upload_quota_refuses(self):
		frappe.cache().set(media_out._upload_key("file"), media_out.UPLOAD_MONTHLY_QUOTA)
		with switch(True), mock.patch(REQ) as http:
			with self.assertRaises(media_out.MediaError):
				self.client().send_media("khach-1", "file", pdf_bytes(), "a.pdf")
		http.assert_not_called()


# --- Tab Zalo -> hàng đợi -> gửi ----------------------------------------------------------------------------------


class TestEndToEnd(MediaCase):
	def test_image_with_caption_queued_sent_and_shown_inline(self):
		url = self.attach("mau-a.png", png_bytes())
		with switch(True):
			res = self.send_tab(url, "Dạ mẫu A ạ")
			self.assertEqual(res["status"], "Queued")
			row = self.out_row(res["outbound"])
			self.assertEqual(
				(row.text, row.attachment, row.attachment_kind, row.attachment_name),
				("Dạ mẫu A ạ", url, "image", "mau-a.png"),
			)
			status, http = self.deliver(res["outbound"], [UPLOAD_IMG, SENT])
		self.assertEqual(status, "Sent")
		self.assertEqual(http.call_count, 2)
		self.as_sale()
		msgs = zalo_tab.get_conversation("CRM Lead", self.lead1)["messages"]
		frappe.set_user("Administrator")
		mine = next(m for m in msgs if m["name"] == res["outbound"])
		self.assertEqual(
			mine["attachments"], [{"url": url, "name": "mau-a.png", "type": "image", "inline": True}]
		)

	def test_large_image_compressed_before_upload(self):
		url = self.attach("lon.png", png_bytes(1400, 1000, noise=True))
		with switch(True):
			res = self.send_tab(url)
			status, http = self.deliver(res["outbound"], [UPLOAD_IMG, SENT])
		self.assertEqual(status, "Sent")
		name, content, mime = http.call_args_list[0].kwargs["files"]["file"]
		self.assertTrue(
			name.startswith("lon") and name.endswith(".jpg"), name
		)  # Frappe có thể thêm hậu tố chống trùng
		self.assertEqual(mime, "image/jpeg")
		self.assertLessEqual(len(content), media_out.MB)

	def test_file_with_text_queues_text_first_then_file(self):
		url = self.attach("bao-gia.pdf", pdf_bytes())
		with switch(True):
			res = self.send_tab(url, "Em gửi báo giá ạ")
		self.assertEqual(res["status"], "Queued")
		text_row = self.out_row(res["text_outbound"])
		file_row = self.out_row(res["outbound"])
		self.assertEqual((text_row.text, text_row.attachment), ("Em gửi báo giá ạ", None))
		self.assertEqual((file_row.text, file_row.attachment_kind), ("", "file"))
		created = frappe.get_all(
			OUT,
			filters={"name": ["in", [res["text_outbound"], res["outbound"]]]},
			order_by="creation asc",
			pluck="name",
		)
		self.assertEqual(created, [res["text_outbound"], res["outbound"]])

	def test_switch_off_real_customer_fails_without_http(self):
		url = self.attach("mau.png", png_bytes())
		with switch(False):
			self.as_sale()
			compose = zalo_tab.get_conversation("CRM Lead", self.lead1)["compose"]
			frappe.set_user("Administrator")
			self.assertFalse(compose["can_send_media"])
			res = self.send_tab(url)
			status, http = self.deliver(res["outbound"], [])
		self.assertEqual(status, "Failed")
		http.assert_not_called()
		self.assertIn("thử nghiệm", self.out_row(res["outbound"]).last_error)

	def test_switch_off_test_account_gets_button(self):
		frappe.db.set_value("Omni Identity", self.ident1, "external_id", TEST_USER)
		frappe.db.commit()
		with switch(False):
			self.as_sale()
			compose = zalo_tab.get_conversation("CRM Lead", self.lead1)["compose"]
			frappe.set_user("Administrator")
		self.assertTrue(compose["can_send_media"])

	def test_out_of_48h_needs_confirmation_like_text(self):
		self.set_last_inbound(hours_ago=50)
		url = self.attach("mau.png", png_bytes())
		with switch(True):
			res = self.send_tab(url)
		self.assertEqual(res["status"], "confirm_required")
		self.assertFalse(frappe.db.exists(OUT, {"lead": self.lead1, "attachment": url}))

	def test_sandbox_no_http(self):
		url = self.attach("mau.png", png_bytes())
		with switch(True), mock.patch("omni_crm.flags.sandbox", return_value=True):
			res = self.send_tab(url)
			status, http = self.deliver(res["outbound"], [])
		self.assertEqual(status, "Sent")
		http.assert_not_called()

	def test_other_lead_file_refused_at_api(self):
		other = self.attach("khac.pdf", pdf_bytes(), "CRM Lead", self.lead2)
		with switch(True), self.assertRaises(media_out.MediaError):
			self.send_tab(other)
		frappe.set_user("Administrator")

	def test_file_only_row_has_label_on_crm_timeline(self):
		url = self.attach("bao-gia.pdf", pdf_bytes())
		with switch(True):
			res = self.send_tab(url)
			status, _http = self.deliver(res["outbound"], [UPLOAD_FILE, SENT])
		self.assertEqual(status, "Sent")
		content = frappe.db.get_value("Communication", {"omni_outbound_message": res["outbound"]}, "content")
		self.assertIn("[Tệp] bao-gia.pdf", content)
		self.assertEqual(media_out.display_text("Dạ", "image", "a.png"), "Dạ\n[Ảnh] a.png")
		self.assertEqual(media_out.display_text("Dạ", None, None), "Dạ")
