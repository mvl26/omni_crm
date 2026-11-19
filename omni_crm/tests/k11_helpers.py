"""K11: bộ lái hội thoại giả lập cho bot kịch bản (dùng bởi `test_k11_conversations`).

Mỗi lượt khách (gõ chữ, bấm nút `#CODE`, gửi tệp...) đi qua đúng điểm vào của pipeline (`router.handle_batch`, nhờ khung
`FlowCase`), rồi ghi lại các tin bot trả lời trong lượt đó theo thứ tự tạo.

Khác `FlowCase`:
- Luật phân loại (`classify.classify_by_rules`) chạy THẬT; chỉ lớp AI bị giả ở `ai_client.call_tool` (bảng `self.ai`:
  câu khách -> kết quả mà một bộ phân loại tử tế có thể trả). Câu không có trong bảng: CHUA_RO 0.3.
- Chuyển nhân viên (`flows.handoff`) chạy THẬT (câu chờ, tạm dừng bot), chỉ giả phần giao việc/thông báo.
- Câu Kho tri thức giả ở `menu.kb_draft` (mặc định: kho không có câu trả lời).
- Đồng hồ của luồng demo và luồng chuyển người cố định: thứ Năm 19/11/2026 10:00, giờ làm việc T2-T6 08:00-17:30.

Không gọi AI/Zalo thật: sandbox bật, `frappe.enqueue` giả.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from unittest import mock

import frappe

from omni_crm.bot import ai_client
from omni_crm.script import classify as cl
from omni_crm.script import engine, send, state, timeutil
from omni_crm.script import tasks as script_tasks
from omni_crm.script.flows import demo, menu
from omni_crm.script.flows import handoff as k3_handoff
from omni_crm.tests.test_k2_core import OUT
from omni_crm.tests.test_k4_collect import CollectCase
from omni_crm.utils.business_hours import parse_hours

LEAD = "CRM Lead"
IDENTITY = "Omni Identity"
NOW = datetime(2026, 11, 19, 10, 0)  # thứ Năm, trong giờ
AFTER_HOURS = datetime(2026, 11, 19, 19, 0)
WEEKDAYS = (parse_hours({"mon-fri": "08:00-17:30"}), frozenset())
EMAIL, PHONE = "nam.tran@benhvien-abc.vn", "0912345678"
DUMP_ENV = "K11_DUMP"  # đường dẫn tệp .jsonl để ghi lại toàn bộ hội thoại (chỉ khi cần đọc bằng mắt)
REASK_WORDS = ("chưa rõ ý", "chưa rõ lựa chọn")
MAIN_MENU_NODES = ("MENU_CHINH", "REASK_CHUARO", "GREETING")


def A(code: str, conf: float = 0.9, service: str | None = None, **slots) -> dict:
	"""Kết quả giả của AI phân loại: mã, độ chắc, dịch vụ, và `mat_hang`/`so_luong`/`thoi_gian` nếu có."""
	out = {"code": code, "confidence": conf, **slots}
	if service:
		out["service"] = service
	return out


@dataclass
class Turn:
	"""Một lượt: khách nói gì, bot trả lời gì."""

	customer: str
	handled: bool = True
	replies: list = field(default_factory=list)  # [(script_node, text)]
	handoffs: list = field(default_factory=list)
	ai: list = field(default_factory=list)  # [(câu tới AI, mã giả trả về | "KHONG_CO_TRONG_BANG")]
	node: str | None = None
	st: dict = field(default_factory=dict)
	paused: bool = False

	@property
	def nodes(self) -> list[str]:
		return [n for n, _t in self.replies]

	@property
	def text(self) -> str:
		return "\n---\n".join(t for _n, t in self.replies)

	@property
	def silent(self) -> bool:
		return not self.replies

	@property
	def reasked(self) -> bool:
		"""Bot nói "chưa rõ ý / chưa rõ lựa chọn"."""
		return any(w in self.text for w in REASK_WORDS)

	@property
	def main_menu(self) -> bool:
		"""Bot đưa khách về menu chính."""
		return any(n in MAIN_MENU_NODES for n in self.nodes)


def _stop_patch(target, attribute: str) -> None:
	"""Gỡ bản giả mà lớp cha đã đặt lên `target.attribute` (lớp cha stop lần nữa lúc dọn cũng không sao)."""
	for p in list(mock._patch._active_patches):
		try:
			if getattr(p, "attribute", None) == attribute and p.getter() is target:
				p.stop()
		except Exception:
			continue


class ConversationCase(CollectCase):
	"""Khách thử = ident1 (khách cũ, chưa có email/SĐT); `talk([...])` chạy cả hội thoại và trả danh sách `Turn`."""

	def setUp(self):
		super().setUp()
		self.ai: dict[str, dict] = {}
		self.kb: dict[str, str] = {}  # câu khách -> câu trả lời của Kho tri thức (bot tự gửi)
		self.T: list[Turn] = []
		self._text = ""
		self._ai_seen: list = []
		self.ai_calls: list = []  # mọi câu đã tới lớp AI trong cả hội thoại
		_stop_patch(cl, "classify")  # luật thật + AI giả
		self._start(mock.patch.object(ai_client, "call_tool", side_effect=self._fake_ai))
		self._start(mock.patch.object(menu, "kb_draft", side_effect=self._fake_kb))
		_stop_patch(k3_handoff, "start")  # chuyển người thật
		real_start = k3_handoff.start

		def start(ctx, service=None, reason="", task_only=False):
			self.handoffs.append(("start", reason))
			return real_start(ctx, service, reason, task_only)

		self._start(mock.patch.object(k3_handoff, "start", side_effect=start))
		engine.register_action("handoff", k3_handoff.action_handoff)
		for target, name in ((k3_handoff, "_assign_lead"), (k3_handoff, "_notify"), (script_tasks, "append_note")):
			self._start(mock.patch.object(target, name, return_value=None))
		self._start(mock.patch("omni_crm.monitoring.alerts.deliver_now", return_value=True))
		self._start(mock.patch.object(timeutil, "load_config", return_value=WEEKDAYS))
		self._clock = self._start(mock.patch.object(k3_handoff, "now_datetime", return_value=NOW))
		self._start(mock.patch.object(demo, "_now", return_value=NOW))
		self._start(mock.patch.object(demo, "demo_user", return_value=None))
		self.kb_auto(1)  # khách thử: câu Kho tri thức (nếu có) được bot tự gửi
		self.contact(False)
		self.addCleanup(self._k11_cleanup)
		frappe.db.commit()

	def _start(self, patcher):
		value = patcher.start()
		self.addCleanup(patcher.stop)
		return value

	def _k11_cleanup(self):
		frappe.db.rollback()
		leads = [self.lead1, self.lead2]
		frappe.db.delete("Omni Scheduled Message", {"lead": ["in", leads]})
		frappe.db.delete("Event", {"reference_doctype": LEAD, "reference_docname": ["in", leads]})
		frappe.db.commit()
		self._dump()

	# --- Giả lập --------------------------------------------------------------------------------------------------

	def _fake_ai(self, kind, model, system, messages, tool, **kw):
		if (tool or {}).get("name") != "classify_message":
			raise AssertionError("test K11 không được gọi AI ngoài bộ phân loại")
		raw = self.ai.get(self._text)
		self._ai_seen.append((self._text, raw["code"] if raw else "KHONG_CO_TRONG_BANG"))
		self.ai_calls.append(self._text)
		return dict(raw) if raw else {"code": "CHUA_RO", "confidence": 0.3}

	def _fake_kb(self, ctx):
		answer = self.kb.get((ctx.text or "").strip())
		if not answer:
			return None
		return {"name": "K11-DRAFT", "action": "Reply", "text": answer, "group": None, "flags": "info:k11"}

	def contact(self, known: bool = True):
		"""Lead đã có sẵn email + SĐT + đơn vị (khách cũ đã để lại liên hệ) hay chưa có gì."""
		values = {"email": EMAIL, "mobile_no": PHONE, "organization": "Bệnh viện ABC"}
		if not known:
			values = {**dict.fromkeys(values), "omni_email_norm": None, "omni_phone_e164": None}
		frappe.db.set_value(LEAD, self.lead1, values)
		frappe.db.commit()

	def after_hours(self):
		self._clock.return_value = AFTER_HOURS

	def unpause(self):
		"""Nhân viên bấm "Trả lại cho bot"."""
		for name in frappe.get_all(IDENTITY, filters={"lead": self.lead1}, pluck="name"):
			frappe.db.set_value(IDENTITY, name, "bot_paused_until", None)
		k3_handoff.clear(self.lead1)
		frappe.db.commit()

	def age_tap(self):
		"""Coi như đã quá 30 giây kể từ lần bấm nút gần nhất."""
		state.update_state(self.ident1, tap_at="2026-01-01 00:00:00")
		frappe.db.commit()

	# --- Lái hội thoại ---------------------------------------------------------------------------------------------

	def _out_rows(self) -> list:
		return frappe.get_all(
			OUT,
			filters={"identity": self.ident1},
			fields=["name", "script_node", "text", "creation"],
			order_by="creation asc",
		)

	def step(self, spec) -> Turn | None:
		"""Một lượt khách. `spec`: chuỗi (gõ; `#CODE` = bấm nút), hàm `f(case) -> spec`, hoặc bộ:
		("file"|"image"|"sticker", chữ kèm), ("multi", [các tin cùng một lượt]), ("follow",), ("unpause",),
		("age",), ("after_hours",), ("contact",)."""
		if callable(spec):
			spec = spec(self)
		if isinstance(spec, tuple) and spec[0] in ("unpause", "age", "after_hours", "contact"):
			{"unpause": self.unpause, "age": self.age_tap, "after_hours": self.after_hours, "contact": self.contact}[
				spec[0]
			]()
			return None
		before = {r.name for r in self._out_rows()}
		seen_handoffs = len(self.handoffs)
		self._ai_seen = []
		kw: dict = {}
		texts = [spec] if isinstance(spec, str) else []
		label = spec if isinstance(spec, str) else ""
		if isinstance(spec, tuple):
			kind = spec[0]
			if kind == "multi":
				texts = list(spec[1])
				label = " ⏎ ".join(texts)
			elif kind == "follow":
				texts, kw, label = [""], {"event_type": "follow"}, "(quan tâm OA)"
			else:
				caption = spec[1] if len(spec) > 1 else ""
				ext = {"file": "xlsx", "image": "jpg", "sticker": "png"}[kind]
				att = {"type": kind, "url": f"https://files.example/k11.{ext}", "name": f"danhmuc.{ext}"}
				texts, kw, label = [caption], {"attachments": [att]}, f"({kind}) {caption}".strip()
		self._text = "\n".join(t.strip() for t in texts if t.strip())
		handled = self.turn(*texts, **kw)
		st = state.get(self.ident1)
		turn = Turn(
			customer=label,
			handled=bool(handled),
			replies=[(r.script_node, r.text) for r in self._out_rows() if r.name not in before],
			handoffs=list(self.handoffs[seen_handoffs:]),
			ai=list(self._ai_seen),
			node=st.get("script_node"),
			st={k: v for k, v in st["script_state"].items() if k not in ("choices", "tap_at")},
			paused=send.block_code(self.ident1) == send.PAUSED,
		)
		self.T.append(turn)
		return turn

	def talk(self, turns: list) -> list[Turn]:
		for spec in turns:
			self.step(spec)
		return self.T

	def choice_label(self, index: int) -> str:
		"""Nhãn lựa chọn thứ `index` (từ 0) của tin có nút gần nhất."""
		items = (state.get(self.ident1)["script_state"].get("choices") or {}).get("items") or []
		return items[index][0]

	def lead_row(self, *fields):
		return frappe.db.get_value(LEAD, self.lead1, list(fields), as_dict=True)

	def events(self) -> list:
		return frappe.get_all(
			"Event",
			filters={"reference_doctype": LEAD, "reference_docname": self.lead1, "status": "Open"},
			pluck="starts_on",
		)

	def _dump(self):
		path = os.environ.get(DUMP_ENV)
		if not path or not self.T:
			return
		row = {
			"test": self._testMethodName,
			"turns": [
				{
					"khach": t.customer,
					"bot": t.replies,
					"handoffs": t.handoffs,
					"ai": t.ai,
					"node": t.node,
					"st": t.st,
					"paused": t.paused,
					"handled": t.handled,
				}
				for t in self.T
			],
		}
		with open(path, "a", encoding="utf-8") as f:
			f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
