"""K22: các lỗ hổng trong bản thử ngày 25/11/2026 (68 tin với bot thật) — mỗi lỗ hổng một nhóm test."""

from __future__ import annotations

import json
from datetime import datetime
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.bot import guards
from omni_crm.script import classify, understand
from omni_crm.script.flows import demo
from omni_crm.tests.k11_helpers import NOW, PROD_SWITCHES, ConversationCase
from omni_crm.tests.test_k11_conversations import ATT, CONTACT3, SLOTS
from omni_crm.tests.test_k15_eval_kb import VT_QE

WED = datetime(2026, 11, 25, 9, 49)  # thứ Tư, lúc người thử nhắn


class TestPure(FrappeTestCase):
	def test_times_written_in_the_kb_are_found(self):  # lỗ hổng 2
		corpus = guards._corpus(["Miyano làm việc từ 8:00 đến 17:30, thứ Hai đến thứ Sáu. Hotline 1900 1234."])
		for phrase in ("8:00", "17:30", "từ 8:00 đến 17:30"):
			self.assertTrue(guards._contains(phrase, corpus), phrase)
		self.assertFalse(guards._contains("9:00", corpus))
		self.assertFalse(guards._contains("18:00", corpus))

	def test_spoken_demo_times(self):  # lỗ hổng 1
		cases = {
			"3h chiều t6 này": datetime(2026, 11, 27, 15, 0),
			"10 rưỡi sáng thứ 2 tuần sau": datetime(2026, 11, 30, 10, 30),
			"thứ 6 a bận rồi, đổi sang 9h sáng chủ nhật": datetime(2026, 11, 29, 9, 0),
			"9h30 thứ 5": datetime(2026, 11, 26, 9, 30),
		}
		for said, want in cases.items():
			self.assertEqual(demo.parse_time(said, WED), want, said)

	def test_time_from_the_reading(self):
		old = datetime(2026, 11, 26, 8, 0)
		self.assertEqual(demo.reading_time({"ngay": "2026-11-27", "gio": "15:00"}, WED), datetime(2026, 11, 27, 15, 0))
		self.assertEqual(demo.reading_time({"thu": 8, "gio": "9:00"}, WED), datetime(2026, 11, 29, 9, 0))
		self.assertEqual(demo.reading_time({"thu": 2, "tuan": "SAU", "gio": "10:30"}, WED), datetime(2026, 11, 30, 10, 30))
		self.assertEqual(demo.reading_time({"ngay": "2026-11-28"}, WED, old), datetime(2026, 11, 28, 8, 0))  # giữ giờ cũ
		self.assertIsNone(demo.reading_time({"ngay": "2026-11-28"}, WED))
		self.assertIsNone(demo.reading_time({"ngay": "2026-11-20", "gio": "9:00"}, WED))  # ngày đã qua
		self.assertIsNone(demo.reading_time({"gio": "25:00", "ngay": "2026-11-28"}, WED))

	def test_postpone_phrases(self):  # lỗ hổng 3
		for said in ("thôi để a hỏi sếp đã, lúc khác a nhắn lại", "để anh xem lại đã", "khi nào cần anh nhắn", "de a ban lai voi sep"):
			self.assertTrue(classify.is_postpone(said), said)
		for said in ("số lượng để sau", "chưa cần ngay", "anh cần 300 đôi", "găng tay loại khác", "lúc khác có giao được không?"):
			self.assertFalse(classify.is_postpone(said), said)


def U(relation, ideas, **extra):
	return {"dien_giai": "", "quan_he": relation, "cac_y": [{"y_dinh": i, "do_tin_cay": c, "dich_vu": s} for i, c, s in ideas], **extra}


class Case(ConversationCase):
	"""Hội thoại với các công tắc như trên yhct; `self.readings`: câu khách -> bản đọc hiểu giả."""

	def setUp(self):
		super().setUp()
		self.readings: dict = {}
		self._start(mock.patch.object(understand, "now_datetime", return_value=NOW))
		for key in PROD_SWITCHES:
			self.addCleanup(frappe.conf.__setitem__, key, frappe.conf.get(key))
			frappe.conf[key] = 1
		frappe.db.delete("Omni Inbound Message", {"identity": self.ident1})
		frappe.db.commit()

	def _fake_ai(self, kind, model, system, messages, tool, **kw):
		if (tool or {}).get("name") != "doc_hieu":
			return {"code": "CHUA_RO", "confidence": 0.3}
		raw = self.readings.get(self._text)
		self.ai_calls.append(self._text)
		return json.loads(json.dumps(raw)) if raw else {}


class TestDemoTime(Case):
	def test_unreadable_time_offers_slots_instead_of_staff(self):
		self.talk([*SLOTS, "#DEMO_GIOKHAC", "hôm nào đẹp trời ấy", "thì cứ hôm đẹp trời"])
		last = self.T[-1]
		self.assertEqual(self.handoffs, [], [t.nodes for t in self.T])
		self.assertFalse(last.paused)
		self.assertIn("DEMO_KHUNGGIO", last.nodes, last.nodes)

	def test_time_understood_by_the_reading_is_used(self):
		said = "ba giờ chiều thứ sáu tuần sau nha"
		self.readings[said] = U("TRA_LOI", [("DEMO", 0.9, "SPD")], slots={"thoi_gian_demo": {"nguyen_van": "ba giờ chiều thứ sáu tuần sau", "ngay": "2026-11-27", "gio": "15:00"}})
		self.talk([*SLOTS, said])
		last = self.T[-1]
		self.assertEqual(self.handoffs, [])
		self.assertEqual(str(last.st.get("demo_at"))[:16], "2026-11-27 15:00", (last.nodes, last.st))


class TestLeaving(Case):
	def test_later_at_quote_read_back_leaves_the_flow(self):
		self.talk([*VT_QE, *CONTACT3, "thôi để a hỏi sếp đã, lúc khác a nhắn lại"])
		last = self.T[-1]
		self.assertEqual(last.nodes, ["TAM_DUNG"], [t.nodes for t in self.T[-3:]])
		self.assertEqual(len(self.tasks), 0)
		self.assertFalse(last.st.get("flow"))

	def test_later_at_demo_read_back_books_nothing(self):
		self.talk([*ATT, "5", "để anh hỏi lại sếp đã nhé"])
		self.assertEqual(self.T[-1].nodes, ["TAM_DUNG"])
		self.assertEqual(self.events(), [])


class TestConfirmWithQuestion(Case):
	def test_quote_is_confirmed_when_yes_comes_with_a_question(self):
		said = "ok đúng rồi, bao giờ có báo giá vậy e"
		self.readings[said] = U("TRA_LOI", [("BAOGIA", 0.9, "VATTU"), ("HOI_THONG_TIN", 0.8, "KHONG_RO")], cau_hoi_kb=["Bao giờ có báo giá"])
		self.talk([*VT_QE, *CONTACT3, said])
		self.assertEqual(len(self.tasks), 1, self.T[-1].nodes)
		self.assertTrue(any(n.startswith("BG_XACNHAN") for n in self.T[-1].nodes), self.T[-1].nodes)

	def test_yes_but_change_is_not_a_confirmation(self):
		said = "đúng rồi nhưng đổi số lượng thành 500 hộp nhé"
		self.readings[said] = U("TRA_LOI", [("SUA_THONG_TIN", 0.9, "VATTU")], slots={"mat_hang": []})
		self.talk([*VT_QE, *CONTACT3, said])
		self.assertEqual(len(self.tasks), 0, self.T[-1].nodes)


class TestWaitingForStaff(Case):
	def test_no_staff_needed_plus_reschedule_resumes_into_the_reschedule(self):
		said = "ủa anh có cần gặp nhân viên đâu, anh chỉ muốn đổi lịch demo thôi mà"
		self.readings[said] = U("Y_KHAC", [("KHONG_GAP_NGUOI", 0.95, "KHONG_RO"), ("DOI_LICH", 0.9, "SPD")])
		self.talk([*ATT, "5", "#DEMO_CHOT", "cho anh gặp nhân viên", said])
		last = self.T[-1]
		self.assertFalse(last.silent, [t.nodes for t in self.T[-3:]])
		self.assertFalse(last.paused)
		self.assertFalse(last.main_menu, last.nodes)
		self.assertTrue(any(n.startswith("DEMO_") for n in last.nodes), last.nodes)

	def test_tapping_another_button_while_waiting_resumes(self):
		self.talk(["#MENU_SPD", "cho anh gặp nhân viên", "#MENU_VATTU"])
		last = self.T[-1]
		self.assertTrue(self.T[-2].paused, self.T[-2].nodes)
		self.assertEqual(last.nodes, ["MENU_VATTU"])
		self.assertFalse(last.paused)

	def test_tapping_staff_button_again_keeps_waiting(self):
		self.talk(["#MENU_SPD", "cho anh gặp nhân viên", "#GAP_NHANVIEN"])
		self.assertTrue(self.T[-1].paused)


class TestBookedService(Case):
	def test_booking_message_names_the_booked_service_not_the_current_topic(self):
		self.talk([*ATT, "5", "#DEMO_CHOT", "#MENU_VATTU", "#BAOGIA_VATTU"])
		from omni_crm.script import scheduler, state

		self.assertEqual(state.get(self.ident1)["script_state"].get("demo_service"), "SPD")
		self.assertEqual(scheduler.current_service(self.ident1, self.lead1), "VATTU")  # khách đã chuyển sang nói về vật tư
		self.assertEqual(demo._service_of_booking(self.ident1), "SPD")


class TestRules(FrappeTestCase):
	def test_rhetorical_no_staff_is_not_a_staff_request(self):  # lỗ hổng 7
		for said in ("ủa anh có cần gặp nhân viên đâu, anh chỉ muốn đổi lịch demo thôi mà", "đâu có cần gặp nhân viên", "ai bảo gặp nhân viên"):
			self.assertNotEqual((classify.classify_by_rules(said) or ("",))[0], "GAP_NHANVIEN", said)
		self.assertEqual((classify.classify_by_rules("cho anh gặp nhân viên") or ("",))[0], "GAP_NHANVIEN")


class TestWaitNotice(FrappeTestCase):
	def test_typed_number_and_menu_do_not_wait(self):
		from omni_crm.script import gather

		for said in ("1", "2.", "menu", " Menu "):
			self.assertEqual(gather.quiet_needed([frappe._dict(text=said)], 15), 0, said)
		for said in ("12 hộp", "anh chọn 1 nhé", "30"):
			self.assertEqual(gather.quiet_needed([frappe._dict(text=said)], 15), 15, said)

	def test_notice_only_after_twenty_seconds(self):
		from omni_crm.script import gather
		from omni_crm.script.flows import menu

		self.assertEqual(gather.NOTICE_AFTER, 20)
		self.assertEqual(menu.thinking_after(), 20)


class TestQuoteLinesPure(FrappeTestCase):  # lỗ hổng 4
	def test_remove_change_and_add_keep_names_and_quantities_in_step(self):
		from omni_crm.script import quote_lines as q

		lines = q.seed("cồn 70 độ, bơm tiêm", "cồn 70 độ: 50 chai; bơm tiêm: 1000 cái")
		lines, changed = q.merge(lines, [{"ten": "bơm tiêm", "so_luong_chu": "1 triệu cái"}, {"ten": "khẩu trang", "so_luong": 50, "don_vi": "hộp"}], ["cồn"])
		self.assertTrue(changed)
		self.assertEqual(q.items_text(lines), "bơm tiêm, khẩu trang")
		self.assertEqual(q.quantity_text(lines), "bơm tiêm: 1 triệu cái; khẩu trang: 50 hộp")

	def test_understanding_keeps_spoken_quantity_and_removed_names_only_if_said(self):
		raw = {"quan_he": "Y_KHAC", "cac_y": [{"y_dinh": "SUA_THONG_TIN", "do_tin_cay": 0.9}], "slots": {"mat_hang": [{"ten": "bơm tiêm", "so_luong_chu": "1 triệu cái"}], "mat_hang_bo": ["cồn", "bông gòn"]}}
		got = understand.clean(raw, "Bỏ cồn, bơm tiêm 1 triệu cái")
		self.assertEqual(got["slots"]["mat_hang"], [{"ten": "bơm tiêm", "so_luong_chu": "1 triệu cái"}])
		self.assertEqual(got["slots"]["mat_hang_bo"], ["cồn"])  # "bông gòn" khách không hề viết
		self.assertEqual(understand.to_detail(got)["slots"]["quantity"], "1 triệu cái")


def _goods(*rows):
	return [{"ten": t, **({"so_luong": n, "don_vi": u} if n else {})} for t, n, u in rows]


class TestQuoteLines(Case):
	ITEMS, QTY = "cồn 70 độ, bơm tiêm", "cồn 50 chai, bơm tiêm 1000 cái"
	ADD, CUT = "à thêm 50 hộp khẩu trang nữa nhé", "Bỏ cồn, bơm tiêm 1 triệu cái"

	def setUp(self):
		super().setUp()
		edit = [("SUA_THONG_TIN", 0.9, "VATTU")]
		self.readings.update(
			{
				self.ITEMS: U("TRA_LOI", [("BAOGIA", 0.9, "VATTU")], slots={"mat_hang": _goods(("cồn 70 độ", None, ""), ("bơm tiêm", None, ""))}),
				self.QTY: U("TRA_LOI", [("BAOGIA", 0.9, "VATTU")], slots={"mat_hang": _goods(("cồn", 50, "chai"), ("bơm tiêm", 1000, "cái"))}),
				self.ADD: U("Y_KHAC", edit, slots={"mat_hang": _goods(("khẩu trang", 50, "hộp"))}),
				self.CUT: U("Y_KHAC", edit, slots={"mat_hang": [{"ten": "bơm tiêm", "so_luong_chu": "1 triệu cái"}], "mat_hang_bo": ["cồn"]}),
			}
		)

	def _answers(self):
		return self.T[-1].st.get("answers") or {}

	def test_add_then_remove_while_other_questions_are_pending(self):
		self.talk(["#MENU_VATTU", "#BAOGIA_VATTU", self.ITEMS, self.QTY])
		asked = self.T[-1].node
		self.step(self.ADD)
		added = self.T[-1]
		self.assertNotIn("chưa ghi được", added.text)
		self.assertEqual(added.node, asked, added.nodes)  # hỏi tiếp đúng câu đang dở
		self.assertIn("khẩu trang", self._answers()["items"])
		self.assertIn("khẩu trang: 50 hộp", self._answers()["quantity"])
		self.step(self.CUT)
		cut = self.T[-1]
		self.assertNotIn("cồn", self._answers()["items"])
		self.assertNotIn("cồn", self._answers()["quantity"])
		self.assertIn("bơm tiêm: 1 triệu cái", self._answers()["quantity"])
		self.assertIn("khẩu trang: 50 hộp", self._answers()["quantity"])
		self.assertEqual(cut.node, asked)
		self.assertEqual(self.handoffs, [])

	def test_edit_at_read_back_reads_back_again(self):
		self.talk(["#MENU_VATTU", "#BAOGIA_VATTU", self.ITEMS, self.QTY, "#BG_VT_TG_GOITHAU", "#BG_VT_TT_DUOC", *CONTACT3])
		self.assertIn("BG_DOCLAI", self.T[-1].nodes, [t.nodes for t in self.T])
		self.step(self.CUT)
		again = self.T[-1]
		self.assertEqual(again.nodes, ["BG_DOCLAI"], again.nodes)
		self.assertNotIn("cồn", again.text)
		self.assertIn("1 triệu cái", again.text)
		self.assertEqual(len(self.tasks), 0)


def _docx(rows, paragraphs=()):
	"""Tệp Word tối thiểu: vài đoạn văn + một bảng."""
	import io
	import zipfile

	def para(text):
		return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

	table = "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows) + "</w:tbl>"
	xml = "<?xml version='1.0'?><w:document xmlns:w='x'><w:body>" + "".join(para(p) for p in paragraphs) + table + "</w:body></w:document>"
	out = io.BytesIO()
	with zipfile.ZipFile(out, "w") as z:
		z.writestr("word/document.xml", xml)
	return out.getvalue()


ORDER = _docx([["STT", "Tên hàng", "Quy cách", "Số lượng"], ["1", "Găng tay nitrile", "size M", "300 hộp"], ["2", "Bơm tiêm", "5ml", "2000 cái"]], ["ĐƠN DỰ TRÙ VẬT TƯ THÁNG 12"])


class TestFileTextPure(FrappeTestCase):  # phản hồi 26/11: khách gửi tệp có đủ thông tin mà bot không đọc
	def test_word_table_becomes_rows_of_text(self):
		from omni_crm.script import filedoc

		text = filedoc.extract("don-du-tru.docx", ORDER)
		self.assertIn("ĐƠN DỰ TRÙ VẬT TƯ THÁNG 12", text)
		self.assertIn("1 | Găng tay nitrile | size M | 300 hộp", text)
		self.assertIn("2 | Bơm tiêm | 5ml | 2000 cái", text)

	def test_excel_and_csv(self):
		import io

		import openpyxl

		from omni_crm.script import filedoc

		book = openpyxl.Workbook()
		book.active.append(["Tên hàng", "Số lượng"])
		book.active.append(["Bông gòn", 20])
		buf = io.BytesIO()
		book.save(buf)
		self.assertIn("Bông gòn | 20", filedoc.extract("dm.xlsx", buf.getvalue()))
		self.assertIn("Cồn 70 độ, 50 chai", filedoc.extract("dm.csv", "Tên,SL\nCồn 70 độ, 50 chai\n".encode()))

	def test_unknown_types_and_broken_files_give_nothing(self):
		from omni_crm.script import filedoc

		self.assertEqual(filedoc.extract("anh.jpg", b"\xff\xd8"), "")
		self.assertEqual(filedoc.extract("hong.docx", b"khong phai zip"), "")
		self.assertLessEqual(len(filedoc.extract("dai.txt", ("dòng\n" * 5000).encode())), filedoc.MAX_CHARS)

	def test_only_zalo_https_hosts_are_fetched(self):
		from omni_crm.script import filedoc

		self.assertTrue(filedoc.url_ok("https://file-stal-7.dlfl.vn/no/abc/xyz"))
		for bad in ("http://file-stal-7.dlfl.vn/a", "https://evil.example/a.docx", "https://dlfl.vn.evil.example/a", "https://a@file.dlfl.vn/x", "https://file.dlfl.vn:8443/x", "https://127.0.0.1/a", "file:///etc/passwd", None):
			self.assertFalse(filedoc.url_ok(bad), bad)


class TestFileInQuote(Case):
	FILE = ("file", "")

	def setUp(self):
		super().setUp()
		from omni_crm.script import filedoc

		self.addCleanup(frappe.conf.__setitem__, filedoc.SWITCH, frappe.conf.get(filedoc.SWITCH))
		frappe.conf[filedoc.SWITCH] = 1
		self.fetched = []
		self._start(mock.patch.object(filedoc, "_fetch", side_effect=lambda url: self.fetched.append(url) or ORDER))
		self._start(mock.patch.object(filedoc, "url_ok", return_value=True))
		self._start(mock.patch.object(filedoc, "kind_of", return_value=".docx"))
		frappe.cache().delete_keys("omni:script:filedoc:*")
		goods = [{"ten": "Găng tay nitrile", "quy_cach": "size M", "so_luong": 300, "don_vi": "hộp"}, {"ten": "Bơm tiêm", "quy_cach": "5ml", "so_luong": 2000, "don_vi": "cái"}]
		self.file_reading = U("TRA_LOI", [("BAOGIA", 0.95, "VATTU")], slots={"mat_hang": goods})
		self.seen_input = []

	def _fake_ai(self, kind, model, system, messages, tool, **kw):
		self.seen_input.append(messages[0]["content"])
		if "<tep_khach_gui>" in messages[0]["content"]:
			self.ai_calls.append(self._text)
			return json.loads(json.dumps(self.file_reading))
		return super()._fake_ai(kind, model, system, messages, tool, **kw)

	def test_file_sent_for_the_items_question_fills_every_line(self):
		self.talk(["#MENU_VATTU", "#BAOGIA_VATTU", self.FILE])
		last = self.T[-1]
		self.assertTrue(self.fetched)
		self.assertIn("Găng tay nitrile | size M | 300 hộp", self.seen_input[-1])
		answers = last.st.get("answers") or {}
		self.assertEqual(answers.get("items"), "Găng tay nitrile size M, Bơm tiêm 5ml", answers)
		self.assertEqual(answers.get("quantity"), "Găng tay nitrile: 300 hộp; Bơm tiêm: 2000 cái")
		self.assertNotIn("BG_VT_SOLUONG", last.nodes)  # không hỏi lại số lượng đã ghi trong tệp
		self.assertEqual(last.node, "BG_VT_THOIGIAN", last.nodes)

	def test_switch_off_keeps_the_old_behaviour(self):
		from omni_crm.script import filedoc

		frappe.conf[filedoc.SWITCH] = 0
		self.talk(["#MENU_VATTU", "#BAOGIA_VATTU", self.FILE])
		self.assertEqual(self.fetched, [])
		self.assertEqual(self.T[-1].node, "BG_VT_SOLUONG")
