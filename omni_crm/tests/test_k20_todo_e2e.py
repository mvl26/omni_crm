"""K20: tin nhiều nội dung, chạy trọn hội thoại qua `router.handle_batch` với các công tắc đang bật trên yhct.

Kiểm phần mà K17 chỉ kiểm từng mảnh: việc khách nêu kèm được xếp hàng, và TỰ MỞ sau khi việc chính chốt xong
(yêu cầu báo giá được khách xác nhận / lịch demo được khách xác nhận), không cần khách nhắc lại.
"""

from __future__ import annotations

import json
import os
from unittest import mock

import frappe

from omni_crm.script import understand
from omni_crm.tests.k11_helpers import NOW, PROD_SWITCHES, ConversationCase
from omni_crm.tests.test_k11_conversations import ATT, CONTACT3

DOCS_AND_QUOTE = "Gửi anh tài liệu spd với báo giá găng tay 300 đôi"
DEMO_AND_DOCS = "Em đặt giúp anh lịch demo spd, tiện gửi anh tài liệu phần mềm supplycore luôn"
DOCS_LINE = "nhận tài liệu"

READINGS = {
	DOCS_AND_QUOTE: {
		"dien_giai": "",
		"quan_he": "Y_KHAC",
		"cac_y": [
			{"y_dinh": "BAOGIA", "do_tin_cay": 0.93, "dich_vu": "VATTU"},
			{"y_dinh": "TAILIEU", "do_tin_cay": 0.9, "dich_vu": "SPD"},
		],
		"slots": {"mat_hang": [{"ten": "găng tay", "so_luong": 300, "don_vi": "đôi"}]},
	},
	"5 người, à mà gửi anh tài liệu phần mềm supplycore luôn nhé": {
		"dien_giai": "",
		"quan_he": "TRA_LOI",
		"cac_y": [
			{"y_dinh": "DEMO", "do_tin_cay": 0.9, "dich_vu": "SPD"},
			{"y_dinh": "TAILIEU", "do_tin_cay": 0.9, "dich_vu": "SUPPLYCORE"},
		],
		"slots": {"so_nguoi": 5},
	},
}


class TestK20TodoEndToEnd(ConversationCase):
	def setUp(self):
		super().setUp()
		self._start(mock.patch.object(understand, "now_datetime", return_value=NOW))
		for key in PROD_SWITCHES:
			self.addCleanup(frappe.conf.__setitem__, key, frappe.conf.get(key))
			frappe.conf[key] = 1
		frappe.db.delete("Omni Inbound Message", {"identity": self.ident1})
		frappe.db.commit()

	def _fake_ai(self, kind, model, system, messages, tool, **kw):
		if (tool or {}).get("name") != "doc_hieu":
			return {"code": "CHUA_RO", "confidence": 0.3}
		raw = READINGS.get(self._text)
		return json.loads(json.dumps(raw)) if raw else {"dien_giai": "", "quan_he": "KHONG_RO", "cac_y": []}

	def _show(self):
		if os.environ.get("K20_SHOW"):
			for t in self.T:
				print("\nKH:", t.customer, "\n  ->", t.nodes, "| node", t.node, "| todo", t.st.get("todo"))
				for _n, text in t.replies:
					print("    ", text.replace("\n", " / ")[:200])

	def test_docs_open_by_themselves_after_the_quote_is_confirmed(self):
		try:
			self.talk([DOCS_AND_QUOTE])
			first = self.T[-1]
			self.assertEqual([(i["intent"], i["service"]) for i in first.st.get("todo") or []], [("TAILIEU", "SPD")])
			self.assertIn("300 đôi", json.dumps(first.st.get("answers") or {}, ensure_ascii=False))
			for _ in range(12):  # trả lời các câu còn lại của luồng báo giá cho tới khi bot đọc lại
				if "BG_DOCLAI" in self.T[-1].nodes:
					break
				self.step(self._answer_next())
			self.assertIn("BG_DOCLAI", self.T[-1].nodes, [t.nodes for t in self.T])
			self.assertEqual(len(self.tasks), 0)  # chưa xác nhận: chưa tạo việc
			self.assertTrue(self.T[-1].st.get("todo"))  # việc xếp hàng vẫn còn
			done = self.step("#BG_CHOT")
			self.assertEqual(len(self.tasks), 1)
			self.assertIn("TAILIEU_SPD", done.nodes, done.nodes)  # tự mở, khách không phải nhắc
			self.assertIn(DOCS_LINE, done.text)
			self.assertFalse(done.st.get("todo"))
			self.assertFalse(any(t.silent for t in self.T))
		finally:
			self._show()

	def _answer_next(self):
		"""Câu trả lời hợp lý cho câu bot đang hỏi trong luồng báo giá vật tư."""
		from omni_crm.script import state

		st = state.get(self.ident1)
		items = (st["script_state"].get("choices") or {}).get("items") or []
		if items:
			return "1"
		text = self.T[-1].text.lower()
		if "email" in text:
			return CONTACT3[0]
		if "số điện thoại" in text or "sđt" in text:
			return CONTACT3[1]
		return CONTACT3[2]

	def test_docs_open_by_themselves_after_the_demo_is_confirmed(self):
		try:
			self.talk([*ATT, "5 người, à mà gửi anh tài liệu phần mềm supplycore luôn nhé"])
			asked = self.T[-1]
			self.assertIn("DEMO_DOCLAI", asked.nodes, asked.nodes)
			self.assertEqual([(i["intent"], i["service"]) for i in asked.st.get("todo") or []], [("TAILIEU", "PHANMEM")])
			self.assertEqual(self.events(), [])  # chưa xác nhận: chưa có lịch
			done = self.step("#DEMO_CHOT")
			self.assertEqual(len(self.events()), 1)
			self.assertTrue(any(n.startswith("TAILIEU_") for n in done.nodes), done.nodes)
			self.assertIn(DOCS_LINE, done.text)
			self.assertFalse(done.st.get("todo"))
		finally:
			self._show()
