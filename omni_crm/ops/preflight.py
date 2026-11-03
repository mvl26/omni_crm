"""Lệnh kiểm cấu hình trước phát hành (V21-08, F9). CHỈ ĐỌC.

    bench --site <site> execute omni_crm.ops.preflight.run --kwargs "{'target': 'production'}"

Mỗi mục kiểm là một hàm nhỏ trả `(ket_qua, chi_tiet, cach_sua)` với `ket_qua` thuộc `DAT`, `KHONG_DAT`, `CAN_NGUOI_KIEM`.
`run` in bảng, trả dict và RAISE `PreflightFailed` (SystemExit mã 1) khi có mục `KHONG_DAT` để `bench execute` thoát mã khác 0 (truyền
`strict=False` để chỉ lấy kết quả). `check` là phần thuần, không in, không raise.

Nguyên tắc:
- Không ghi DB (không `frappe.log_error`, không `db.set_value`, không commit), không tạo thư mục/tệp, không gọi mạng ra
  ngoài. Chỉ mở một kết nối Redis queue có timeout để đếm worker; không truy cập được thì mục đó `CAN_NGUOI_KIEM`.
- Token Meta chỉ đọc kết quả tác vụ `token_check` đã lưu trên kênh, không gọi Graph.
- Không in bí mật hay PII: chỉ in số lượng, tên kênh/form và tên khóa cấu hình, rồi qua `redact`.
- Quyết định Q1/Q11 và cổng bot đọc từ trường máy đọc được của Omni Settings (`q1_allowed`, `q11_allowed`,
  `gate_phan_tich_passed_at`, `gate_nhap_passed_at`), KHÔNG đoán từ docs/12. Chưa từng lưu = chưa cho phép / chưa đạt.
"""

from __future__ import annotations

import glob
import json
import os
import re
import textwrap
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

DAT = "DAT"
KHONG_DAT = "KHONG_DAT"
CAN_NGUOI_KIEM = "CAN_NGUOI_KIEM"
TARGETS = ("staging", "production")

# Ở production, mục này ném lỗi thì KHONG_DAT (không thể kết luận an toàn); staging giữ CAN_NGUOI_KIEM
CRITICAL = frozenset(
	{
		"developer_mode",
		"zalo_skip_signature",
		"omni_auto_send_allowed",
		"omni_ai_allow_customer_data",
		"bot_analyze_enabled",
		"bot_reply_mode",
	}
)
BACKUP_MAX_AGE_HOURS = 24
TOKEN_MIN_DAYS = 7  # F9: token Meta còn hạn > 7 ngày
TOKEN_CHECK_STALE_DAYS = 3  # kết quả kiểm token cũ hơn thì nhắc (tác vụ chạy mỗi 6 giờ)
REDIS_TIMEOUT = 3  # giây; Redis queue hỏng có thể chấp nhận kết nối rồi treo khi đọc
AD_PLATFORM = {"Meta Lead Ads": "Meta", "Google Lead Form": "Google", "Web Form": "Website"}
_RANK = {DAT: 0, CAN_NGUOI_KIEM: 1, KHONG_DAT: 2}

Result = tuple[str, str, str]


class PreflightFailed(SystemExit):
	"""Có mục KHONG_DAT; `result` là dict kết quả của `check`.

	Kế thừa SystemExit (không phải Exception) có chủ đích: `bench execute` bọc lời gọi trong `except Exception` rồi thử lại
	bằng `eval` (báo NameError khó hiểu); SystemExit đi thẳng ra ngoài, tiến trình thoát mã 1 và in thông điệp ra stderr.
	"""

	def __init__(self, result: dict[str, Any]):
		self.result = result
		names = ", ".join(r["muc"] for r in result["muc"] if r["ket_qua"] == KHONG_DAT)
		super().__init__(f"Kiểm trước phát hành ({result['target']}): KHÔNG ĐẠT ở các mục: {names}")


class Ctx:
	"""Ngữ cảnh một lần chạy: target và bộ đọc Omni Settings (đọc `tabSingles` trực tiếp, không cache)."""

	def __init__(self, target: str):
		self.target = target
		self.prod = target == "production"
		self._stored: dict[str, str] | None = None
		self._doc: Any = None

	def stored(self) -> dict[str, str]:
		if self._stored is None:
			self._stored = {
				f: v
				for f, v in frappe.db.sql(
					"select field, value from `tabSingles` where doctype = 'Omni Settings'"
				)
			}
		return self._stored

	def is_saved(self, field: str) -> bool:
		"""Trường đã từng được lưu (có dòng trong tabSingles) chưa."""
		return field in self.stored()

	def setting(self, field: str) -> Any:
		"""Giá trị đã lưu; chưa từng lưu thì đúng giá trị mà runtime thấy (`get_doc`: Check = 0, còn lại None), KHÔNG lấy default của DocType."""
		if field in self.stored():
			return self.stored()[field]
		if self._doc is None:
			self._doc = frappe.get_doc("Omni Settings")
		return self._doc.get(field)


# ------------------------------------------------------------------ tiện ích


def _worst(rows: list[Result]) -> str:
	return max((r[0] for r in rows), key=_RANK.__getitem__, default=DAT)


def _join(items: list[str], limit: int = 5) -> str:
	shown = ", ".join(items[:limit])
	return shown + (f" (+{len(items) - limit})" if len(items) > limit else "")


def _on(ctx: Ctx, field: str) -> bool:
	return bool(cint(ctx.setting(field)))


def _conf_on(key: str) -> bool:
	return bool(cint(frappe.conf.get(key)))


def _conf_truthy(key: str) -> bool:
	"""Đúng như Frappe đọc cờ (`frappe.conf.developer_mode` dùng độ đúng của Python: "true" cũng là bật)."""
	return bool(frappe.conf.get(key))


def _channels(channel_type: str, **extra) -> list[Any]:
	return frappe.get_all(
		"Omni Channel", filters={"channel_type": channel_type, "enabled": 1, **extra}, fields=["*"]
	)


def _gate_passed(ctx: Ctx, field: str) -> bool:
	value = ctx.setting(field)
	if not value:
		return False
	try:
		return get_datetime(value) <= now_datetime()
	except Exception:
		return False


# ------------------------------------------------------------------ các mục kiểm


def _developer_mode(ctx: Ctx) -> Result:
	if _conf_truthy("developer_mode"):
		return (
			KHONG_DAT,
			_("developer_mode đang bật"),
			_("Đặt developer_mode = 0 (site_config.json / common_site_config.json) rồi khởi động lại."),
		)
	return DAT, _("developer_mode tắt"), ""


def _zalo_skip_signature(ctx: Ctx) -> Result:
	names = frappe.get_all("Omni Channel", filters={"zalo_skip_signature": 1}, pluck="name")
	if names:
		return (
			KHONG_DAT,
			_("{0} kênh đang bật zalo_skip_signature (bỏ kiểm chữ ký): {1}").format(len(names), _join(names)),
			_("Bỏ chọn zalo_skip_signature ở các kênh này (Omni Channel)."),
		)
	return DAT, _("Không kênh nào bật zalo_skip_signature"), ""


def _auto_send_key(ctx: Ctx) -> Result:
	if not _conf_on("omni_auto_send_allowed"):
		return DAT, _("Khóa omni_auto_send_allowed vắng hoặc 0 (bot không thể tự gửi)"), ""
	if ctx.prod and cint(ctx.setting("q1_allowed")):
		return (
			DAT,
			_("omni_auto_send_allowed đang mở và Omni Settings.q1_allowed = 1 (Q1 đã chốt)"),
			"",
		)
	if ctx.prod:
		return (
			KHONG_DAT,
			_("omni_auto_send_allowed đang mở nhưng Q1 chưa được ghi là đã chốt (q1_allowed = 0)"),
			_(
				"Gỡ omni_auto_send_allowed khỏi site_config.json, hoặc chủ dự án chốt Q1 rồi cập nhật q1_allowed."
			),
		)
	return (
		KHONG_DAT,
		_("omni_auto_send_allowed đang mở trên staging"),
		_("Gỡ omni_auto_send_allowed khỏi site_config.json của staging."),
	)


def _ai_customer_data(ctx: Ctx) -> Result:
	conf = _conf_on("omni_ai_allow_customer_data")
	if not ctx.prod:
		if conf:
			return (
				KHONG_DAT,
				_("omni_ai_allow_customer_data đang bật trên staging"),
				_("Gỡ omni_ai_allow_customer_data khỏi site_config.json của staging."),
			)
		return DAT, _("omni_ai_allow_customer_data vắng (chỉ dữ liệu tổng hợp được gửi sang AI)"), ""
	q11 = bool(cint(ctx.setting("q11_allowed")))
	if conf == q11:
		return (
			DAT,
			_("omni_ai_allow_customer_data = {0} khớp q11_allowed = {1}").format(int(conf), int(q11)),
			"",
		)
	if conf:
		return (
			KHONG_DAT,
			_("omni_ai_allow_customer_data đang bật nhưng Omni Settings.q11_allowed = 0 (Q11 chưa cho phép)"),
			_(
				"Gỡ omni_ai_allow_customer_data khỏi site_config.json, hoặc chủ dự án chốt Q11 rồi cập nhật q11_allowed."
			),
		)
	return (
		KHONG_DAT,
		_(
			"Q11 ghi là cho phép (q11_allowed = 1) nhưng omni_ai_allow_customer_data đang tắt: bot sẽ bị cổng Q11 chặn"
		),
		_(
			"Đặt omni_ai_allow_customer_data = 1 trong site_config.json theo quyết định, hoặc sửa q11_allowed cho khớp."
		),
	)


def _bot_analyze(ctx: Ctx) -> Result:
	on = _on(ctx, "bot_analyze_enabled")
	passed = _gate_passed(ctx, "gate_phan_tich_passed_at")
	state = _("bot_analyze_enabled = {0}, cổng phân tích {1}").format(
		int(on), _("đã đạt") if passed else _("chưa đạt")
	)
	if ctx.prod and on and not passed:
		return (
			KHONG_DAT,
			state,
			_(
				"Tắt bot_analyze_enabled (Omni Settings) cho tới khi cổng phan_tich đạt bằng AI thật trên bộ đề đã duyệt; "
				"khi đạt, người chốt cập nhật gate_phan_tich_passed_at."
			),
		)
	return DAT, state, ""


def _bot_reply(ctx: Ctx) -> Result:
	mode = str(ctx.setting("bot_reply_mode") or "Off")
	if mode not in ("Off", "Shadow", "Draft"):
		mode = "Off"  # giống flags.bot_reply_mode: giá trị lạ coi như Off
	passed = _gate_passed(ctx, "gate_nhap_passed_at")
	state = _("bot_reply_mode = {0}, cổng nháp {1}").format(mode, _("đã đạt") if passed else _("chưa đạt"))
	if ctx.prod and mode != "Off" and not passed:
		return (
			KHONG_DAT,
			state,
			_(
				"Đặt bot_reply_mode = Off (Omni Settings) cho tới khi cổng nhap đạt bằng AI thật trên bộ đề đã duyệt; "
				"khi đạt, người chốt cập nhật gate_nhap_passed_at."
			),
		)
	return DAT, state, ""


def _sandbox(ctx: Ctx) -> Result:
	on = _on(ctx, "sandbox_mode")
	if ctx.prod:
		if on:
			return (
				CAN_NGUOI_KIEM,
				_("sandbox_mode đang BẬT: hệ thống không gửi gì ra khách và không gọi API Zalo"),
				_(
					"Trước ngày chạy thật, người phụ trách tắt sandbox_mode CÓ CHỦ ĐÍCH (không tắt trước khi xong các mục còn lại)."
				),
			)
		return (
			DAT,
			_("sandbox_mode đang TẮT: hệ thống sẽ gửi thật ra khách (xác nhận đây là chủ đích)"),
			"",
		)
	if on:
		return DAT, _("sandbox_mode bật"), ""
	return (
		KHONG_DAT,
		_("sandbox_mode đang TẮT trên staging: có thể gửi thật ra khách và làm mới token Zalo thật"),
		_("Bật sandbox_mode (Omni Settings), trừ khi đang chạy thử có người nhận chỉ định."),
	)


def _scheduler(ctx: Ctx) -> Result:
	from frappe.utils.scheduler import is_scheduler_inactive

	try:
		inactive = is_scheduler_inactive(verbose=False)
	except TypeError:
		inactive = is_scheduler_inactive()
	if not inactive:
		return DAT, _("Scheduler đang bật"), ""
	reasons = []
	if frappe.conf.get("maintenance_mode"):
		reasons.append("maintenance_mode")
	if frappe.conf.get("pause_scheduler"):
		reasons.append("pause_scheduler")
	if frappe.conf.get("disable_scheduler"):
		reasons.append("disable_scheduler")
	if not reasons:
		reasons.append("System Settings.enable_scheduler = 0")
	return (
		KHONG_DAT,
		_("Scheduler đang tắt ({0}): tác vụ quét, đối soát, kiểm token, dọn hàng dự phòng không chạy").format(
			", ".join(reasons)
		),
		_(
			"Bật lại: bench --site <site> enable-scheduler; gỡ maintenance_mode / pause_scheduler / disable_scheduler."
		),
	)


def _redis_connection():
	"""Kết nối Redis queue riêng, có timeout (không dùng get_redis_conn: nó thử lại và có thể treo)."""
	import redis

	url = frappe.conf.get("redis_queue")
	if not url:
		raise RuntimeError("thiếu redis_queue trong cấu hình")
	conn = redis.Redis.from_url(url, socket_timeout=REDIS_TIMEOUT, socket_connect_timeout=REDIS_TIMEOUT)
	conn.ping()
	return conn


def _workers_for(queue_name: str) -> int:
	"""Số worker RQ đang lắng nghe hàng đợi; ném lỗi nếu Redis không truy cập được."""
	from frappe.utils.background_jobs import generate_qname
	from rq import Queue, Worker

	conn = _redis_connection()
	try:
		queue = Queue(generate_qname(queue_name), connection=conn)
		return len(Worker.all(connection=conn, queue=queue))
	finally:
		try:
			conn.close()
		except Exception:
			pass


def _worker_queue(ctx: Ctx, queue_name: str) -> Result:
	from frappe.utils.background_jobs import get_queues_timeout

	try:
		declared = queue_name in get_queues_timeout()
	except Exception:
		declared = False
	if not declared:
		return (
			KHONG_DAT,
			_("Hàng đợi {0} chưa khai báo trong khóa workers của common_site_config.json").format(queue_name),
			_(
				'Thêm "{0}": {{"timeout": 300}} vào workers (common_site_config.json), tạo worker cho hàng đợi này rồi khởi động lại.'
			).format(queue_name),
		)
	try:
		count = _workers_for(queue_name)
	except Exception as e:
		return (
			CAN_NGUOI_KIEM,
			_("Không đọc được worker từ Redis queue ({0})").format(type(e).__name__),
			_(
				"Kiểm tay: bench doctor; supervisor/systemd có worker cho hàng đợi {0}; Redis queue đang chạy."
			).format(queue_name),
		)
	if count > 0:
		return DAT, _("{0} worker đang lắng nghe hàng đợi {1}").format(count, queue_name), ""
	return (
		KHONG_DAT,
		_("Không có worker nào lắng nghe hàng đợi {0}").format(queue_name),
		_(
			"Chạy worker cho hàng đợi {0} (bench worker --queue {0} hoặc cấu hình supervisor) rồi kiểm lại."
		).format(queue_name),
	)


def _worker_omni(ctx: Ctx) -> Result:
	return _worker_queue(ctx, "omni")


def _worker_omni_leads(ctx: Ctx) -> Result:
	return _worker_queue(ctx, "omni_leads")


def _alert_recipients(ctx: Ctx) -> Result:
	raw = str(ctx.setting("alert_recipients") or "")
	items = [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]
	valid = [x for x in items if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x)]
	if not valid:
		return (
			KHONG_DAT,
			_(
				"alert_recipients trống hoặc không có địa chỉ email hợp lệ: không có thư cảnh báo nào được gửi"
			),
			_("Điền alert_recipients (Omni Settings), mỗi dòng một email của người trực."),
		)
	if len(valid) != len(items):
		return (
			CAN_NGUOI_KIEM,
			_("alert_recipients có {0} mục, trong đó {1} mục không phải email hợp lệ").format(
				len(items), len(items) - len(valid)
			),
			_("Sửa các dòng sai trong alert_recipients."),
		)
	return DAT, _("alert_recipients có {0} người nhận").format(len(valid)), ""


def _business_hours(ctx: Ctx) -> Result:
	from omni_crm.utils.business_hours import _RANGE, _parse_days

	def valid_range(value: Any) -> bool:
		m = _RANGE.match(str(value))
		if not m:
			return False
		h1, m1, h2, m2 = (int(x) for x in m.groups())
		return (
			h1 <= 23 and h2 <= 24 and m1 <= 59 and m2 <= 59 and (h2, m2) > (h1, m1) and not (h2 == 24 and m2)
		)

	if not ctx.is_saved("business_hours"):
		return (
			CAN_NGUOI_KIEM,
			_("business_hours chưa từng được lưu: hệ thống đang dùng giờ mặc định chưa ai xác nhận"),
			_("Mở Omni Settings, kiểm giờ làm việc thật của đội sale rồi Lưu."),
		)
	try:
		data = json.loads(str(ctx.setting("business_hours") or ""))
	except ValueError:
		data = None
	ok = isinstance(data, dict) and bool(data)
	if ok:
		ok = all(_parse_days(k) and valid_range(v) for k, v in data.items())
	if not ok:
		return (
			KHONG_DAT,
			_('business_hours trống hoặc sai định dạng (cần JSON như {"mon-fri":"08:00-17:30"})'),
			_("Sửa business_hours trong Omni Settings."),
		)
	return DAT, _("business_hours có {0} khung giờ hợp lệ").format(len(data)), ""


def _holidays(ctx: Ctx) -> Result:
	from omni_crm.utils.business_hours import parse_holidays

	days = parse_holidays(str(ctx.setting("holidays") or ""))
	upcoming = [d for d in days if d >= date.today()]
	if not days:
		return (
			CAN_NGUOI_KIEM,
			_("holidays trống: ngày lễ sẽ bị tính là ngày làm việc (hạn gọi lại, SLA sai)"),
			_('Điền holidays (Omni Settings) dạng ["2027-01-01", ...] hoặc xác nhận không có ngày nghỉ.'),
		)
	if not upcoming:
		return (
			CAN_NGUOI_KIEM,
			_("holidays có {0} ngày nhưng đều đã qua").format(len(days)),
			_("Bổ sung ngày nghỉ sắp tới vào holidays (Omni Settings)."),
		)
	return DAT, _("holidays có {0} ngày sắp tới").format(len(upcoming)), ""


def _lead_forms(ctx: Ctx) -> Result:
	channels = frappe.get_all(
		"Omni Channel",
		filters={"channel_type": ["in", list(AD_PLATFORM)], "enabled": 1},
		fields=["name", "channel_type"],
	)
	if not channels:
		return DAT, _("Không có kênh quảng cáo nào đang bật"), ""
	forms = frappe.get_all(
		"Omni Lead Form",
		filters={"enabled": 1},
		fields=["name", "platform", "default_owner", "assignment_rule"],
	)
	problems: list[str] = []
	for ctype in sorted({c.channel_type for c in channels}):
		platform = AD_PLATFORM[ctype]
		mine = [f for f in forms if f.platform == platform]
		if not mine:
			problems.append(
				_("kênh {0} bật nhưng không có Omni Lead Form {1} nào bật").format(ctype, platform)
			)
	missing = []
	for form in forms:
		if form.platform not in {AD_PLATFORM[c.channel_type] for c in channels}:
			continue
		owner_ok = bool(form.default_owner) and cint(
			frappe.db.get_value("User", form.default_owner, "enabled")
		)
		rule_ok = False
		if form.assignment_rule:
			disabled = frappe.db.get_value("Assignment Rule", form.assignment_rule, "disabled")
			rule_ok = disabled is not None and not cint(disabled)
		if not (owner_ok or rule_ok):
			missing.append(form.name)
	if missing:
		problems.append(
			_(
				"{0} form bật thiếu người phụ trách hợp lệ (default_owner đang dùng được hoặc assignment_rule đang bật): {1}"
			).format(len(missing), _join(missing))
		)
	if problems:
		return (
			KHONG_DAT,
			"; ".join(problems),
			_(
				"Tạo/bật Omni Lead Form cho từng nền tảng đang chạy và đặt default_owner hoặc assignment_rule cho mỗi form."
			),
		)
	return (
		DAT,
		_("{0} kênh quảng cáo bật; {1} form bật, đều có người phụ trách").format(len(channels), len(forms)),
		"",
	)


def _token_meta(ctx: Ctx) -> Result:
	from omni_crm.channels.meta_leads.errors import TOKEN_NOTE_NEVER, TOKEN_NOTE_OK

	channels = _channels("Meta Lead Ads")
	if not channels:
		return DAT, _("Không có kênh Meta Lead Ads nào đang bật"), ""
	now = now_datetime()
	rows: list[Result] = []
	for c in channels:
		name = c.name
		if not c.meta_token_checked_at:
			rows.append(
				(
					CAN_NGUOI_KIEM,
					_("{0}: chưa có kết quả kiểm token Meta").format(name),
					_(
						"Điền meta_app_id/App Secret/Page token và chờ tác vụ token_check (6 giờ), hoặc kiểm tay."
					),
				)
			)
			continue
		if not cint(c.meta_token_valid):
			rows.append(
				(
					KHONG_DAT,
					_("{0}: token Meta không hợp lệ").format(name),
					_("Cấp lại token Page (khuyến nghị token System User, docs/15 mục 2)."),
				)
			)
			continue
		if (c.meta_token_note or "") not in (TOKEN_NOTE_OK, TOKEN_NOTE_NEVER):
			# cùng điều kiện snapshot._meta_token: kết quả kiểm có ghi chú lỗi/thiếu quyền dù valid = 1
			rows.append(
				(
					KHONG_DAT,
					_(
						"{0}: lần kiểm token Meta gần nhất báo vấn đề (ghi chú trên kênh khác 'đủ quyền')"
					).format(name),
					_(
						"Mở kênh, đọc meta_token_note và xử lý (thiếu quyền leads_retrieval, Graph từ chối...); cấp lại token Page nếu cần."
					),
				)
			)
			continue
		problem = None
		if not cint(c.meta_token_never_expires) and c.meta_token_expires_at:
			problem = ("Token", get_datetime(c.meta_token_expires_at) - now)
		for label, at in (("Quyền dữ liệu", c.meta_token_data_expires_at),):
			if at:
				left = get_datetime(at) - now
				if problem is None or left < problem[1]:
					problem = (label, left)
		if problem and problem[1] <= timedelta(0):
			rows.append(
				(KHONG_DAT, _("{0}: {1} Meta đã hết hạn").format(name, problem[0]), _("Cấp lại token Page."))
			)
		elif problem and problem[1] < timedelta(days=TOKEN_MIN_DAYS):
			rows.append(
				(
					KHONG_DAT if ctx.prod else CAN_NGUOI_KIEM,
					_("{0}: {1} Meta còn {2} ngày (cần > {3})").format(
						name, problem[0], max(problem[1].days, 0), TOKEN_MIN_DAYS
					),
					_("Cấp lại token Page trước khi chạy chiến dịch."),
				)
			)
		elif get_datetime(c.meta_token_checked_at) < now - timedelta(days=TOKEN_CHECK_STALE_DAYS):
			rows.append(
				(
					CAN_NGUOI_KIEM,
					_("{0}: kết quả kiểm token đã cũ (> {1} ngày)").format(name, TOKEN_CHECK_STALE_DAYS),
					_(
						"Kiểm tác vụ token_check và scheduler; có thể token_check đang tắt (meta_token_check_disabled)."
					),
				)
			)
		else:
			rows.append((DAT, _("{0}: token Meta còn hạn").format(name), ""))
	overall = _worst(rows)
	bad = [r for r in rows if r[0] == overall]
	if overall == DAT:
		return DAT, _("{0} kênh Meta: token hợp lệ, còn hạn > {1} ngày").format(len(rows), TOKEN_MIN_DAYS), ""
	return overall, "; ".join(r[1] for r in bad), " ".join(dict.fromkeys(r[2] for r in bad if r[2]))


def _token_zalo(ctx: Ctx) -> Result:
	from frappe.utils.password import get_decrypted_password

	channels = _channels("Zalo OA")
	if not channels:
		return DAT, _("Không có kênh Zalo OA nào đang bật"), ""
	sandbox = _on(ctx, "sandbox_mode")
	now = now_datetime()
	rows: list[Result] = []
	for c in channels:
		name = c.name
		refresh = get_decrypted_password("Omni Channel", name, "zalo_refresh_token", raise_exception=False)
		if not refresh:
			rows.append(
				(
					KHONG_DAT,
					_("{0}: chưa có refresh token Zalo").format(name),
					_('Bấm "Kết nối Zalo OA" để cấp quyền lại.'),
				)
			)
			continue
		expires = c.zalo_token_expires_at
		if not expires:
			rows.append(
				(
					KHONG_DAT,
					_("{0}: chưa có hạn access token (chưa từng làm mới)").format(name),
					_("Cấp quyền lại hoặc chờ tác vụ làm mới token."),
				)
			)
		elif get_datetime(expires) <= now:
			# Access token Zalo sống ngắn và được làm mới mỗi 30 phút; sandbox bật thì tác vụ làm mới bỏ qua.
			status = CAN_NGUOI_KIEM if sandbox else KHONG_DAT
			rows.append(
				(
					status,
					_("{0}: access token đã hết hạn{1}").format(
						name, _(" (sandbox bật nên không làm mới)") if sandbox else ""
					),
					_(
						"Tắt sandbox có chủ đích để tác vụ làm mới chạy, hoặc cấp quyền lại; xem last_error của kênh."
					),
				)
			)
		elif c.last_error:
			rows.append(
				(
					CAN_NGUOI_KIEM,
					_("{0}: kênh có last_error chưa xóa (lần lỗi gần nhất)").format(name),
					_("Mở kênh, đọc last_error và xử lý."),
				)
			)
		else:
			rows.append((DAT, _("{0}: Zalo còn token hợp lệ").format(name), ""))
	overall = _worst(rows)
	bad = [r for r in rows if r[0] == overall]
	if overall == DAT:
		return DAT, _("{0} kênh Zalo OA: có refresh token, access token chưa hết hạn").format(len(rows)), ""
	return overall, "; ".join(r[1] for r in bad), " ".join(dict.fromkeys(r[2] for r in bad if r[2]))


def _zalo_package(ctx: Ctx) -> Result:
	channels = _channels("Zalo OA")
	if not channels:
		return DAT, _("Không có kênh Zalo OA nào đang bật"), ""
	packages = {c.name: (c.zalo_oa_package or "Chưa xác nhận") for c in channels}
	if not ctx.prod:
		return (
			DAT,
			_("Staging không yêu cầu; gói hiện ghi: {0}").format(
				_join([f"{k}={v}" for k, v in packages.items()])
			),
			"",
		)
	unconfirmed = [n for n, p in packages.items() if p == "Chưa xác nhận"]
	basic = [n for n, p in packages.items() if p in ("Cơ bản", "Tiêu chuẩn")]
	if unconfirmed:
		return (
			KHONG_DAT,
			_("{0} kênh Zalo OA chưa xác nhận gói: {1}").format(len(unconfirmed), _join(unconfirmed)),
			_(
				"Xác nhận gói OA trên trang quản lý Zalo rồi đặt zalo_oa_package (Omni Channel); gói thấp có thể chưa cho API."
			),
		)
	if basic:
		return (
			CAN_NGUOI_KIEM,
			_("{0} kênh ở gói Cơ bản/Tiêu chuẩn có thể chưa cho kết nối API: {1}").format(
				len(basic), _join(basic)
			),
			_("Xác nhận với Zalo gói hiện tại cho gọi API gửi tin tư vấn."),
		)
	return DAT, _("Gói Zalo OA đã xác nhận ({0} kênh)").format(len(packages)), ""


def _spool(ctx: Ctx) -> Result:
	from omni_crm.ingest import spool

	# Chỉ spool_count/oldest_age_seconds (không chạm đĩa để ghi); KHÔNG dùng spool.stats()/is_writable() vì chúng makedirs/chmod.
	path = spool.spool_dir()
	if os.path.isdir(path) and not os.access(path, os.R_OK | os.X_OK):
		return (
			CAN_NGUOI_KIEM,
			_("Không đọc được thư mục hàng dự phòng webhook"),
			_("Kiểm quyền private/omni_spool (0700, cùng người chạy web)."),
		)
	count = spool.spool_count()
	if count == 0:
		# spool_count() nuốt lỗi đọc (chmod 0) và không đếm dòng cụt thiếu \n: tệp có dữ liệu mà đếm 0 = không đọc/đếm được, coi là KHONG_DAT
		stuck = []
		for fpath in spool._files():
			try:
				if os.path.getsize(fpath) > 0:
					stuck.append(os.path.basename(fpath))
			except OSError:
				stuck.append(os.path.basename(fpath))
		if stuck:
			return (
				KHONG_DAT,
				_(
					"Hàng dự phòng webhook có tệp không rỗng nhưng không đếm được tin ({0} tệp: không đọc được hoặc dòng cụt)"
				).format(len(stuck)),
				_(
					"Kiểm quyền private/omni_spool (0700/0600, cùng người chạy web) và nội dung tệp; chạy spool.drain; không xóa tay khi chưa chắc."
				),
			)
	if count > 0:
		age = spool.oldest_age_seconds()
		return (
			KHONG_DAT,
			_("Hàng dự phòng webhook còn {0} tin (tin cũ nhất {1} giây)").format(
				count, age if age is not None else "?"
			),
			_(
				"Kiểm DB, rồi chạy: bench --site <site> execute omni_crm.ingest.spool.drain; đến khi spool_count() = 0."
			),
		)
	if cint(ctx.setting("webhook_spool_disabled")):
		return (
			CAN_NGUOI_KIEM,
			_("Hàng dự phòng rỗng nhưng webhook_spool_disabled = 1: lỗi DB sẽ trả 503, không có chỗ giữ tin"),
			_("Bỏ chọn webhook_spool_disabled (Omni Settings) trừ khi cố ý."),
		)
	return DAT, _("Hàng dự phòng webhook rỗng"), ""


def _backup_dir() -> str:
	return frappe.get_site_path("private", "backups")


def _backup(ctx: Ctx) -> Result:
	files = [
		p
		for pattern in ("*-database.sql.gz", "*-database.sql")
		for p in glob.glob(os.path.join(_backup_dir(), pattern))
		if os.path.getsize(p) > 0  # tệp rỗng (sao lưu hỏng) không tính
	]
	newest = max((os.path.getmtime(p) for p in files), default=None)
	age_h = None if newest is None else (time.time() - newest) / 3600
	if not ctx.prod:
		if age_h is None:
			return DAT, _("Staging không yêu cầu; chưa có bản sao lưu nào"), ""
		return DAT, _("Staging không yêu cầu; bản sao lưu mới nhất cách đây {0} giờ").format(int(age_h)), ""
	if age_h is None:
		return (
			KHONG_DAT,
			_("Không tìm thấy bản sao lưu nào trong private/backups của site"),
			_("Chạy bench --site <site> backup --with-files và kiểm lịch sao lưu tự động."),
		)
	if age_h > BACKUP_MAX_AGE_HOURS:
		return (
			KHONG_DAT,
			_("Bản sao lưu mới nhất cách đây {0} giờ (cần <= {1})").format(int(age_h), BACKUP_MAX_AGE_HOURS),
			_(
				"Chạy bench --site <site> backup --with-files ngay trước khi phát hành; kiểm lịch sao lưu tự động."
			),
		)
	return DAT, _("Có bản sao lưu cách đây {0} giờ").format(int(age_h)), ""


def _prompt_pin(ctx: Ctx) -> Result:
	"""Thông tin (không bao giờ KHONG_DAT): phiên bản câu lệnh đang chạy thật so với bản mặc định trong mã."""
	from omni_crm.bot.prompts import loader

	parts = []
	diff = False
	for name in ("analyze", "reply"):
		try:
			version = loader.pinned_version(name)
		except Exception:
			version = None
		parts.append(f"{name}={version}")
		diff = diff or version != loader.PINNED_DEFAULTS.get(name)
	if diff:
		return (
			CAN_NGUOI_KIEM,
			_("Phiên bản câu lệnh ghim khác bản mặc định trong mã: {0}").format(", ".join(parts)),
			_(
				"Xác nhận bản này đã qua cổng bot; quay lui = đặt analyze_prompt_version/reply_prompt_version về v1."
			),
		)
	return DAT, _("Câu lệnh ghim theo mặc định: {0}").format(", ".join(parts)), ""


CHECKS: list[tuple[str, Callable[[Ctx], Result]]] = [
	("developer_mode", _developer_mode),
	("zalo_skip_signature", _zalo_skip_signature),
	("omni_auto_send_allowed", _auto_send_key),
	("omni_ai_allow_customer_data", _ai_customer_data),
	("bot_analyze_enabled", _bot_analyze),
	("bot_reply_mode", _bot_reply),
	("sandbox_mode", _sandbox),
	("scheduler", _scheduler),
	("worker_omni", _worker_omni),
	("worker_omni_leads", _worker_omni_leads),
	("alert_recipients", _alert_recipients),
	("business_hours", _business_hours),
	("holidays", _holidays),
	("lead_form", _lead_forms),
	("token_meta", _token_meta),
	("token_zalo", _token_zalo),
	("zalo_oa_package", _zalo_package),
	("hang_du_phong_webhook", _spool),
	("sao_luu_24h", _backup),
	("prompt_ghim", _prompt_pin),
]


# ------------------------------------------------------------------ chạy và in


def _safe(text: str) -> str:
	from omni_crm.pipeline.retry import redact

	try:
		return redact(text)
	except Exception:
		return text


def check(target: str = "production") -> dict[str, Any]:
	"""Chạy mọi mục kiểm (chỉ đọc) và trả dict {target, ket_qua, dem, muc: [...]}; không in, không raise vì KHONG_DAT."""
	if target not in TARGETS:
		raise ValueError(f"target phải là một trong {TARGETS}, nhận được: {target!r}")
	ctx = Ctx(target)
	rows = []
	for key, func in CHECKS:
		try:
			ket_qua, chi_tiet, cach_sua = func(ctx)
		except (
			Exception
		) as e:  # một mục lỗi không làm hỏng cả lệnh; chỉ in tên lỗi (message có thể chứa bí mật)
			ket_qua = KHONG_DAT if (ctx.prod and key in CRITICAL) else CAN_NGUOI_KIEM
			chi_tiet = _("Không kiểm được mục này ({0})").format(type(e).__name__)
			cach_sua = _("Kiểm tay mục này.")
		rows.append(
			{"muc": key, "ket_qua": ket_qua, "chi_tiet": _safe(chi_tiet), "cach_sua": _safe(cach_sua)}
		)
	counts = {k: sum(1 for r in rows if r["ket_qua"] == k) for k in (DAT, KHONG_DAT, CAN_NGUOI_KIEM)}
	overall = KHONG_DAT if counts[KHONG_DAT] else CAN_NGUOI_KIEM if counts[CAN_NGUOI_KIEM] else DAT
	return {"target": target, "ket_qua": overall, "dem": counts, "muc": rows}


def format_table(result: dict[str, Any]) -> str:
	"""Bảng văn bản: mục · kết quả · chi tiết · cách sửa."""
	lines = [
		f"KIỂM TRƯỚC PHÁT HÀNH · target={result['target']} · site={getattr(frappe.local, 'site', '?')} · CHỈ ĐỌC",
		"=" * 100,
	]
	for r in result["muc"]:
		head = f"{r['muc']:<28} {r['ket_qua']:<15} "
		body = textwrap.wrap(r["chi_tiet"], 56) or [""]
		lines.append(head + body[0])
		lines.extend(" " * len(head) + b for b in body[1:])
		if r["cach_sua"]:
			for i, b in enumerate(textwrap.wrap(r["cach_sua"], 54)):
				lines.append(" " * 44 + ("-> " if i == 0 else "   ") + b)
	d = result["dem"]
	lines += [
		"=" * 100,
		f"TỔNG: {result['ket_qua']} (ĐẠT {d[DAT]} · KHÔNG ĐẠT {d[KHONG_DAT]} · CẦN NGƯỜI KIỂM {d[CAN_NGUOI_KIEM]})",
	]
	return "\n".join(lines)


def run(target: str = "production", strict: bool = True) -> dict[str, Any]:
	"""In bảng kiểm trước phát hành, trả dict; có mục KHONG_DAT và strict thì raise PreflightFailed (bench execute thoát mã != 0)."""
	if target not in TARGETS:  # SystemExit: bench execute nuốt Exception và báo NameError khó hiểu
		raise SystemExit(f"target phải là một trong {TARGETS}, nhận được: {target!r}")
	result = check(target)
	print(format_table(result))
	if strict and result["dem"][KHONG_DAT]:
		raise PreflightFailed(result)
	return result
