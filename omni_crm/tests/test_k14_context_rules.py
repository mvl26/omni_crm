"""Yêu cầu "hiểu ngữ cảnh hội thoại" 20/11 — Bước 1: chặn lỗi nguy hiểm (luật 1, 3, 7).

Ô thông tin chỉ nhận giá trị đã trích và đúng kiểu (không ghi nguyên văn câu nói); câu trả lời AI chưa chắc thì không
ghi; ngừng nhận tin / chuyển người không làm theo phỏng đoán độ tin cậy thấp. Ca lấy từ hội thoại thật 19–20/11.
"""

from unittest import mock

import frappe

from omni_crm.script import classify as cl
from omni_crm.script.flows import collect
from omni_crm.tests.test_k2_flow import FlowCase


class Ctx:
	def __init__(self, detail=None, batch="b1"):
		self.batch_id = batch
		self.state = {"_classify_detail": {**(detail or {}), "batch_id": batch}} if detail is not None else {}


class TestSlotValue(FlowCase):
	def test_items_never_take_a_sentence(self):
		for text in ("Anh vừa nói bên trên thây", "Em vẫn k hiểu", "sao không trả lời", "anh hỏi rồi mà", "giường bệnh gì?"):
			self.assertIsNone(collect.slot_value(Ctx(), "items", text), text)
		for text in ("găng tay nitrile không bột size M", "bom tiem 5ml voi kim g23", "giường bệnh 3 tay quay"):
			self.assertEqual(collect.slot_value(Ctx(), "items", text), text)

	def test_extracted_value_wins_over_raw_text(self):
		ctx = Ctx({"relation": "Y_KHAC", "confidence": 0.9, "slots": {"items": "giường bệnh", "quantity": "30 chiếc"}})
		self.assertEqual(collect.slot_value(ctx, "items", "Anh muốn đặt 30 chiếc giường bệnh"), "giường bệnh")
		self.assertEqual(collect.slot_value(ctx, "quantity", "Anh muốn đặt 30 chiếc giường bệnh"), "30 chiếc")

	def test_quantity_needs_a_number(self):
		self.assertEqual(collect.slot_value(Ctx(), "quantity", "tôi cần 300 đôi găng tay phẫu thuật loại dày"), "300 đôi")
		self.assertEqual(collect.slot_value(Ctx(), "quantity", "200 hộp và 5000 cái"), "200 hộp và 5000 cái")
		self.assertEqual(collect.slot_value(Ctx(), "quantity", "theo file"), "theo file")
		self.assertEqual(collect.slot_value(Ctx(), "quantity", "khoảng vài chục hộp thôi"), "khoảng vài chục hộp thôi")
		for text in ("Em vẫn k hiểu", "hôm nay trời đẹp", "asdfgh"):
			self.assertIsNone(collect.slot_value(Ctx(), "quantity", text), text)

	def test_need_by_needs_a_time(self):
		self.assertEqual(collect.slot_value(Ctx(), "need_by", "Thứ 3 tuần sau được k em"), "Thứ 3 tuần sau được k em")
		self.assertEqual(collect.slot_value(Ctx(), "need_by", "ngày mai"), "ngày mai")
		for text in ("Bên mình có thể giao sớm nhất vào khi nào", "Em vẫn k hiểu"):
			self.assertIsNone(collect.slot_value(Ctx(), "need_by", text), text)
		for text in ("bỏ qua", "chưa biết em ạ", "tuỳ em"):  # khách chủ động bỏ qua: ghi lại đúng như vậy
			self.assertEqual(collect.slot_value(Ctx(), "quantity", text), text)

	def test_unsure_answer_is_not_recorded(self):
		unsure = {"relation": "TRA_LOI", "confidence": 0.4, "slots": {}}
		self.assertIsNone(collect.slot_value(Ctx(unsure), "substitute", "cái nào cũng thế"))
		sure = {"relation": "TRA_LOI", "confidence": 0.9, "slots": {}}
		self.assertEqual(collect.slot_value(Ctx(sure), "substitute", "Hãng moba em nhé"), "Hãng moba em nhé")


class TestStep1Flow(FlowCase):
	def _quote(self):
		self.greeted()
		for code in ("MENU_VATTU", "BAOGIA_VATTU"):
			self.tap(code)

	def test_kb09_sentence_is_not_written_as_the_item(self):
		self._quote()
		self.turn("Anh vừa nói bên trên thây")
		self.assertNotIn("items", self.sstate().get("answers") or {})
		self.assertEqual(self.sent()[-1], "BAOGIA_VATTU")
		self.assertTrue(any("chưa ghi được" in t for t in self.texts()))
		self.assertEqual(self.sstate().get("field"), "items")  # vẫn đang chờ đúng câu đó

	def test_third_unusable_answer_goes_to_staff_not_into_the_slot(self):
		self._quote()
		for text in ("Anh vừa nói bên trên thây", "Em vẫn k hiểu", "sao không trả lời"):
			self.turn(text)
		self.assertNotIn("items", self.sstate().get("answers") or {})
		self.assertEqual(len(self.handoffs), 1)

	def test_quantity_question_never_stores_a_sentence(self):
		self._quote()
		self.codes["găng tay nitrile"] = ("MENU_VATTU", 0.9)
		self.turn("găng tay nitrile")
		self.assertEqual(self.sent()[-1], "BG_VT_SOLUONG")
		self.turn("Em vẫn k hiểu")
		self.assertNotIn("quantity", self.sstate().get("answers") or {})
		self.turn("100 hộp")
		self.assertEqual(self.sstate()["answers"]["quantity"], "100 hộp")

	def test_kb01_question_is_not_written_as_the_delivery_time(self):
		self._quote()
		self.codes["găng tay nitrile"] = ("MENU_VATTU", 0.9)
		self.turn("găng tay nitrile")
		self.turn("100 hộp")
		self.assertEqual(self.sent()[-1], "BG_VT_THOIGIAN")
		self.turn("Bên mình có thể giao sớm nhất vào khi nào")
		self.assertNotIn("need_by", self.sstate().get("answers") or {})

	def test_unsure_need_for_staff_asks_first(self):
		self.greeted()
		text = "Anh muốn biết mã số thuế của công ty"
		self.codes[text] = ("CAN_NGUOI", 0.6)
		self.turn(text)
		self.assertEqual(self.sent()[-1], "GAP_NV_XACNHAN")
		self.assertEqual(self.handoffs, [])
		self.assertIn(text, frappe.db.get_value("CRM Lead", self.lead1, "omni_unanswered_questions") or "")

	def test_sure_or_explicit_request_for_staff_is_immediate(self):
		self.greeted()
		self.codes["bên em cho anh hỏi về công nợ"] = ("CAN_NGUOI", 0.95)
		self.turn("bên em cho anh hỏi về công nợ")
		self.assertNotIn("GAP_NV_XACNHAN", self.sent())
		self.reset()
		self.greeted()
		self.turn("cho anh gặp nhân viên")
		self.assertNotIn("GAP_NV_XACNHAN", self.sent())


class TestEchoIsNotStaff(FlowCase):
	"""Lỗi 20/11 14:50: echo của câu "đang suy nghĩ" bị coi là nhân viên đã trả lời, câu Kho tri thức không tới khách."""

	def _echo(self, external_id, text="x"):
		frappe.get_doc(
			{
				"doctype": "Omni Inbound Message", "dedupe_key": frappe.generate_hash(length=12), "channel": self.channel.name,
				"channel_type": self.channel.channel_type, "event_type": "oa_echo", "identity": self.ident1, "lead": self.lead1,
				"identity_key": frappe.db.get_value("Omni Identity", self.ident1, "identity_key"),
				"external_message_id": external_id, "received_at": frappe.utils.now_datetime(), "text": text, "status": "Done",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def test_echo_of_a_bot_message_is_not_a_staff_reply(self):
		from omni_crm.script import send

		self.greeted()
		start = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-5)
		self.tap("MENU_SPD")
		out = frappe.get_all("Omni Outbound Message", filters={"identity": self.ident1}, pluck="name")[-1]
		frappe.db.set_value("Omni Outbound Message", out, {"external_message_id": "bot-msg-1", "status": "Sent", "sent_at": frappe.utils.now_datetime()})
		self._echo("bot-msg-1")
		self.assertFalse(send.staff_replied_after(self.lead1, start))
		self._echo("typed-by-staff-9", "Chào anh, em là Lan bên Miyano ạ")
		self.assertTrue(send.staff_replied_after(self.lead1, start))
