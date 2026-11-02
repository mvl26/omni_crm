# ruff: noqa: RUF001
"""Nhập danh sách nhà thầu từ mẫu Excel vào CRM (docs/14 mục 5; V2-03, ADR 0012).

Thứ tự: NhaThau -> CRM Organization (khóa mã số thuế) + CRM Lead; GoiThau -> Omni Bid Award (khóa `import_key`);
NguoiLienHe -> Contact; chuyển Deal (giai đoạn Deal); tính điểm phù hợp MỘT lần cho mỗi nhà thầu bị chạm.

Nguyên tắc an toàn:
- Chạy thử (`dry_run`) chỉ ĐỌC DB và lập kế hoạch; mọi lệnh ghi nằm sau `if not self.dry_run`. Không giả lập bằng
  "ghi rồi rollback" vì hook của CRM/omni sinh việc nền, ToDo, DocShare thoát khỏi rollback.
- Mỗi dòng nằm trong savepoint; lỗi một dòng không làm hỏng cả tệp; commit theo lô 100 dòng.
- Không ghi đè trường người dùng đã sửa trong CRM (so với ảnh chụp lần nhập trước, ADR 0012).
- Không tự gán mã số thuế cho Organization đã có; trùng tên/trùng mã khác tên -> "cần xác nhận", giữ nguyên.
- Không tạo Contact đã bị Xóa dữ liệu khách; importer không có đường gửi tin nào cho khách (có test dò mã nguồn).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import frappe
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, strip_html

from omni_crm import flags
from omni_crm.flags import omni_bot
from omni_crm.identity.normalize import is_mobile_phone, normalize_email, normalize_phone
from omni_crm.prospect import contacts as contact_lib
from omni_crm.prospect import fit, hospital, import_errors, parsing
from omni_crm.prospect.normalize import company_name_key
from omni_crm.prospect.parsing import RowError
from omni_crm.prospect.xlsx_reader import SheetRow, WorkbookData, WorkbookError, read_workbook

LOG = "Omni Prospect Import"
ORG = "CRM Organization"
LEAD = "CRM Lead"
DEAL = "CRM Deal"
CONTACT = "Contact"
AWARD = "Omni Bid Award"
NOTE = "FCRM Note"
ALLOWED_ROLES = frozenset({"Omni Admin", "System Manager"})
QUEUE = "omni"
JOB_METHOD = "omni_crm.prospect.importer.run_job"
JOB_TIMEOUT = 1800
BATCH = 100
MAX_ISSUES = 300  # số cảnh báo / mục cần xác nhận lưu trong báo cáo (đếm vẫn đủ)
LEAD_SOURCE = "Danh sách mua sắm công"
AD_PLATFORM = "Danh sách mua sắm công"
NOTE_TITLE = "Ghi chú khi nhập danh sách"
IMPORT_LOST_NOTE = "Nhập từ file"
DEFAULT_CONSENT = "Chưa hỏi"
SHEETS = ("NhaThau", "GoiThau", "NguoiLienHe")
ORG_FIELDS = [c[0] for c in parsing.ORG_COLUMNS.values()]
AWARD_FIELDS = (
	"package_name",
	"hospital",
	"hospital_province",
	"category",
	"award_value",
	"item_count",
	"duration_months",
	"role",
	"source_url",
)


# --- Quyền, công tắc, tệp -------------------------------------------------------------------------------------------


def require_runner(user: str | None = None) -> str:
	"""Chỉ Omni Admin / System Manager (và Administrator) được nhập; trả tên người dùng đã kiểm."""
	user = user or frappe.session.user
	if not ALLOWED_ROLES & set(frappe.get_roles(user)):
		frappe.throw(_("Chỉ Omni Admin được nhập danh sách nhà thầu"), frappe.PermissionError)
	return user


def require_enabled() -> None:
	"""Từ chối chạy khi công tắc `prospect_import_enabled` tắt."""
	if not flags.prospect_import_enabled():
		frappe.throw(
			_("Chức năng nhập danh sách nhà thầu đang tắt (Omni Settings > prospect_import_enabled)")
		)


def file_sha256(path: str | Path) -> str:
	"""SHA-256 của tệp (để nhận ra tệp đã nhập)."""
	h = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			h.update(chunk)
	return h.hexdigest()


def resolve_file(file_ref: str) -> tuple[str, str]:
	"""(đường dẫn thật, file_url hoặc ""). Đường dẫn do người dùng gửi chỉ được là tệp RIÊNG TƯ của site;
	đường dẫn tuyệt đối chỉ chấp nhận khi chạy từ dòng lệnh (bench execute), không qua yêu cầu web."""
	ref = (file_ref or "").strip()
	if ref.startswith("/private/files/"):
		root = os.path.realpath(frappe.get_site_path("private", "files"))
		path = os.path.realpath(frappe.get_site_path(ref.lstrip("/")))
		if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
			frappe.throw(_("Không tìm thấy tệp đã tải lên"))
		return path, ref
	if ref.startswith(("/files/", "http:", "https:")):
		frappe.throw(_("Tệp danh sách chứa dữ liệu cá nhân: chỉ nhận tệp riêng tư (private)"))
	if getattr(frappe.local, "request", None):
		frappe.throw(_("Không nhận đường dẫn tệp từ yêu cầu web"), frappe.PermissionError)
	path = os.path.realpath(ref)
	if not os.path.isfile(path):
		frappe.throw(_("Không tìm thấy tệp: {0}").format(Path(ref).name))
	return path, ""


def _repo_warning(path: str) -> str | None:
	repo = os.path.realpath(os.path.join(frappe.get_app_path("omni_crm"), os.pardir))
	if os.path.commonpath([repo, path]) == repo:
		return "Tệp nằm trong thư mục mã nguồn app: dữ liệu thật phải để NGOÀI repo, không commit"
	return None


# --- Khởi chạy ---------------------------------------------------------------------------------------------------


def create_log(
	file_url: str = "", source_name: str = "", dry_run: bool = True, user: str | None = None
) -> str:
	"""Tạo bản ghi nhật ký ở trạng thái Draft (người dùng đính kèm tệp, bấm Chạy thử / Nhập)."""
	doc = frappe.get_doc(
		{
			"doctype": LOG,
			"file": file_url or None,
			"source_name": source_name or None,
			"dry_run": 1 if dry_run else 0,
			"run_by": user or frappe.session.user,
			"status": "Draft",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def start_log(
	log: str,
	dry_run: bool,
	user: str,
	*,
	now: bool = False,
	commit: bool = True,
	path: str | None = None,
) -> str:
	"""Đặt nhật ký ở Queued và đẩy việc nền lên hàng đợi `omni` (hoặc chạy ngay khi `now`).

	`path` (đường dẫn tuyệt đối, chỉ từ dòng lệnh) bắt buộc chạy ngay trong tiến trình gọi; nó không được lưu vào
	nhật ký nên việc nền không bao giờ đọc đường dẫn tùy ý.
	"""
	require_runner(user)
	require_enabled()
	doc = frappe.get_doc(LOG, log)
	if doc.status in ("Queued", "Running"):
		frappe.throw(_("Lần nhập này đang chạy"))
	if not (path or doc.file):
		frappe.throw(_("Chưa đính kèm tệp danh sách"))
	resolved, _url = resolve_file(path or doc.file)
	doc.db_set(
		{
			"dry_run": 1 if dry_run else 0,
			"run_by": user,
			"status": "Queued",
			"message": "",
			"file_hash": file_sha256(resolved),
		},
		update_modified=False,
	)
	if now or path:
		run_job(log, commit=commit, path=path)
	else:
		frappe.enqueue(JOB_METHOD, queue=QUEUE, timeout=JOB_TIMEOUT, log=log, enqueue_after_commit=True)
	return log


def import_workbook(
	file_url_or_path: str,
	dry_run: bool = False,
	user: str | None = None,
	*,
	now: bool = False,
	commit: bool = True,
) -> str:
	"""Nhập tệp danh sách nhà thầu; trả tên nhật ký `Omni Prospect Import`. Chạy nền (hàng đợi omni) trừ khi `now`."""
	user = require_runner(user)
	require_enabled()
	path, url = resolve_file(file_url_or_path)
	log = create_log(file_url=url, source_name=Path(path).name, dry_run=dry_run, user=user)
	return start_log(log, dry_run, user, now=now, commit=commit, path=None if url else path)


def run_job(log: str, commit: bool = True, path: str | None = None) -> str:
	"""Việc nền: chạy một lần nhập đã Queued và ghi kết quả vào nhật ký. Trả trạng thái cuối."""
	doc = frappe.get_doc(LOG, log)
	if doc.status != "Queued":
		return doc.status
	try:
		require_runner(doc.run_by)
		require_enabled()
	except (frappe.ValidationError, frappe.PermissionError) as e:
		return _finish(doc, "Refused", commit, message=_reason(e))
	doc.db_set({"status": "Running", "started_at": now_datetime()}, update_modified=False)
	if commit:
		frappe.db.commit()
	try:
		path = path or resolve_file(doc.file)[0]
		report = Importer(path, dry_run=bool(doc.dry_run), user=doc.run_by, log=log, commit=commit).run()
	except WorkbookError as e:
		return _finish(doc, "Failed", commit, message=str(e))
	except Exception as e:
		frappe.log_error(title="Nhập danh sách nhà thầu lỗi", message=f"{type(e).__name__} (nhật ký {log})")
		return _finish(
			doc, "Failed", commit, message=_("Lỗi hệ thống khi nhập: {0}").format(type(e).__name__)
		)
	return _finish(doc, "Done", commit, report=report)


def _finish(doc, status: str, commit: bool, *, message: str = "", report: dict | None = None) -> str:
	values: dict[str, Any] = {"status": status, "message": message[:500], "finished_at": now_datetime()}
	if report:
		values.update(_log_values(report))
		values["error_file"] = _attach_error_file(doc, report)
	doc.db_set(values, update_modified=False)
	if commit:
		frappe.db.commit()
	return status


def _log_values(report: dict) -> dict[str, Any]:
	out: dict[str, Any] = {
		"duration_seconds": report["duration_seconds"],
		"summary_json": json.dumps(report["summary"], ensure_ascii=False, indent=1, default=str),
		"confirm_json": json.dumps(report["confirm"], ensure_ascii=False, indent=1, default=str),
		"reconcile_json": json.dumps(report["reconcile"], ensure_ascii=False, indent=1, default=str),
		"warnings": "\n".join(report["warnings"])[:20000],
		"confirm_count": len(report["confirm"]),
	}
	for prefix, sheet in (("nt", "NhaThau"), ("gt", "GoiThau"), ("lh", "NguoiLienHe")):
		s = report["sheets"][sheet]
		for key in ("created", "updated", "skipped", "errors"):
			out[f"{prefix}_{key}"] = s[key]
	return out


def _attach_error_file(doc, report: dict) -> str | None:
	content = report.get("error_file")
	if not content:
		return None
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{doc.name}-loi.xlsx",
			"content": content,
			"is_private": 1,
			"attached_to_doctype": LOG,
			"attached_to_name": doc.name,
			"attached_to_field": "error_file",
		}
	)
	f.insert(ignore_permissions=True)
	return f.file_url


def format_log_report(log: str) -> str:
	"""Báo cáo chữ của một nhật ký nhập (đọc từ dòng lệnh): số dòng, cần xác nhận, đối chiếu, cảnh báo."""
	require_runner()
	doc = frappe.get_doc(LOG, log)
	summary = json.loads(doc.summary_json or "{}")
	confirm = json.loads(doc.confirm_json or "[]")
	rc = json.loads(doc.reconcile_json or "{}")
	lines = [
		f"{doc.name} · {doc.status} · {'CHẠY THỬ' if doc.dry_run else 'NHẬP THẬT'} · {doc.duration_seconds or 0}s"
	]
	if doc.message:
		lines.append(f"Thông báo: {doc.message}")
	for sheet, st in (summary.get("sheets") or {}).items():
		lines.append(
			f"{sheet}: {st['rows']} dòng · tạo {st['created']} · cập nhật {st['updated']} · bỏ qua {st['skipped']} · lỗi {st['errors']}"
		)
	lines.append(f"Cần xác nhận: {len(confirm)}")
	for c in confirm:
		lines.append(f"  - [{c['kind']}] {c['message']}")
		lines.extend(f"      {json.dumps(r, ensure_ascii=False)}" for r in c.get("records") or [])
	if rc:
		lines.append(
			f"Đối chiếu ({rc.get('date')}, {rc.get('date_source')}): so {rc.get('compared')} nhà thầu, "
			f"{len(rc.get('mismatches') or [])} chênh lệch, top 10 khớp: {rc.get('top10_match')}, "
			f"TongHop khớp: {(rc.get('tonghop') or {}).get('match')}"
		)
		lines.extend(f"  - chênh: {json.dumps(m, ensure_ascii=False)}" for m in rc.get("mismatches") or [])
		lines.extend(
			f"  - tham số lệch: {json.dumps(d, ensure_ascii=False)}" for d in rc.get("param_diffs") or []
		)
		lines.append("Top 10 trong CRM:")
		lines.extend(
			f"  {r['score']:>3} {r['tier']} {r['tax']} {r['name']}" for r in rc.get("top10_crm") or []
		)
	lines.extend(f"Cảnh báo: {w}" for w in (doc.warnings or "").splitlines())
	lines.append(f"Tệp lỗi: {doc.error_file or '(không có)'}")
	return "\n".join(lines)


def print_log_report(log: str) -> None:
	"""In báo cáo chữ của một nhật ký ra terminal (bench execute không hiện được xuống dòng của chuỗi trả về)."""
	print(format_log_report(log))


def _reason(e: BaseException) -> str:
	"""Lý do gọn từ ngoại lệ của Frappe (thông điệp người dùng); dọn hàng đợi thông điệp."""
	log = list(getattr(frappe.local, "message_log", None) or [])
	message = ""
	if log:
		last = log[-1]
		if isinstance(last, str):
			try:
				last = json.loads(last)
			except ValueError:
				last = {"message": last}
		message = (last or {}).get("message") or ""
	message = strip_html(message or str(e) or type(e).__name__).strip()
	frappe.clear_messages()
	return message[:300]


# --- Chuẩn hóa so sánh -----------------------------------------------------------------------------------------------


def _norm(value: Any) -> Any:
	"""Dạng so sánh của giá trị trường: trống -> None; số -> float; ngày -> iso; chữ -> strip."""
	if value is None or value == "":
		return None
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, int | float):
		return round(float(value), 6)
	if isinstance(value, date):
		return value.isoformat()
	return str(value).strip() or None


def _jsonable(value: Any) -> Any:
	if isinstance(value, date):
		return value.isoformat()
	return value


def _snapshot(raw: str | None) -> dict[str, Any]:
	try:
		data = json.loads(raw or "{}")
	except ValueError:
		return {}
	return data if isinstance(data, dict) else {}


def merge_fields(
	current: dict[str, Any], desired: dict[str, Any], snap: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
	"""(thay đổi, trường xung đột). Chỉ ghi đè trường trống hoặc trường importer ghi lần trước mà chưa ai sửa (ADR 0012).

	Cập nhật `snap` tại chỗ. Trường người dùng đã sửa (khác ảnh chụp) hoặc đã xóa trống thì giữ nguyên và báo xung đột.
	"""
	changes: dict[str, Any] = {}
	conflicts: list[str] = []
	for f, new in desired.items():
		cur = _norm(current.get(f))
		if cur == _norm(new):
			snap[f] = _jsonable(new)
			continue
		was = _norm(snap[f]) if f in snap else None
		if (cur is None and was is None) or (cur is not None and f in snap and was == cur):
			changes[f] = new
			snap[f] = _jsonable(new)
		else:
			conflicts.append(f)
	return changes, conflicts


# --- Importer ------------------------------------------------------------------------------------------------------


class Importer:
	"""Một lần nhập. `run()` trả báo cáo (dict); không ghi nhật ký (việc của `run_job`)."""

	def __init__(
		self, path: str | Path, *, dry_run: bool, user: str, log: str = "", commit: bool = True
	) -> None:
		self.path = str(path)
		self.dry_run = dry_run
		self.user = user
		self.log = log
		self.commit = commit and not dry_run
		self.stats = {s: Counter() for s in SHEETS}
		self.errors: dict[str, list[tuple[int, str]]] = {s: [] for s in SHEETS}
		self.skip_reasons: dict[str, Counter] = {s: Counter() for s in SHEETS}
		self.confirm: list[dict[str, Any]] = []
		self.warnings: list[str] = []
		self.counters: Counter = Counter()
		self.touched: set[str] = set()
		self.held: dict[str, dict[str, Any]] = {}
		self.org: dict[str, dict[str, Any]] = {}  # mã số thuế -> bản ghi Organization (CRM hoặc vừa tạo)
		self.name_index: dict[str, list[tuple[str, str | None]]] = {}
		self.tax_by_name: dict[str, str] = {}
		self.file_orgs: dict[str, dict[str, Any]] = {}  # mã số thuế -> dòng NhaThau đầu tiên (đã phân tích)
		self.awards_proj: dict[str, dict[str, dict[str, Any]]] = {}
		self.award_by_key: dict[str, dict[str, Any]] = {}
		self.pending_deals: list[dict[str, Any]] = []
		self.seen_contacts: set[tuple[str, str]] = set()
		self.contact_orgs: dict[str, str | None] = {}  # tên Contact vừa nhập -> mã số thuế
		self._n = 0
		self.data: WorkbookData | None = None
		self._lead_meta = None

	# -- báo cáo nhỏ --

	def _warn(self, text: str) -> None:
		self.counters["warnings"] += 1
		if len(self.warnings) < MAX_ISSUES:
			self.warnings.append(text)

	def _add_confirm(self, kind: str, message: str, records: list[Any], hint: str = "") -> None:
		self.counters["confirm"] += 1
		if len(self.confirm) < MAX_ISSUES:
			self.confirm.append({"kind": kind, "message": message, "records": records, "hint": hint})

	def _error(self, sheet: str, row: SheetRow, reason: str) -> None:
		self.stats[sheet]["errors"] += 1
		self.errors[sheet].append((row.number, reason))

	# -- vòng lặp dòng --

	def _each(self, sheet: str, handler: Callable[[SheetRow], str]) -> None:
		assert self.data
		for row in self.data.sheets[sheet].rows:
			self.stats[sheet]["rows"] += 1
			sp = None
			if not self.dry_run:
				sp = f"omni_imp_{sheet}_{self.stats[sheet]['rows']}"
				frappe.db.savepoint(sp)
			try:
				outcome = handler(row)
			except RowError as e:
				self._rollback(sp)
				self._error(sheet, row, str(e))
				continue
			except Exception as e:
				self._rollback(sp)
				if isinstance(e, frappe.ValidationError | frappe.PermissionError | frappe.DoesNotExistError):
					reason = _reason(e)
				else:
					frappe.log_error(
						title="Nhập danh sách: lỗi dòng",
						message=f"{type(e).__name__} sheet={sheet} dòng={row.number}",
					)
					reason = _("Lỗi hệ thống khi xử lý dòng (xem Error Log)")
				self._error(sheet, row, reason)
				continue
			self.stats[sheet][outcome] += 1
			self._tick()

	def _rollback(self, sp: str | None) -> None:
		if sp:
			frappe.db.rollback(save_point=sp)
		frappe.clear_messages()

	def _tick(self) -> None:
		self._n += 1
		if self.commit and self._n % BATCH == 0:
			frappe.db.commit()

	def _skip(self, sheet: str, reason: str) -> str:
		self.skip_reasons[sheet][reason] += 1
		return "skipped"

	# -- chạy --

	def run(self) -> dict[str, Any]:
		started = time.monotonic()
		self.data = read_workbook(self.path)
		prev_defer, prev_import = frappe.flags.get("omni_defer_fit"), frappe.flags.get("in_import")
		frappe.flags.omni_defer_fit = (
			True  # tính điểm một lượt cho mỗi nhà thầu ở cuối, không tính lại sau từng gói
		)
		frappe.flags.in_import = True  # Contact.validate bỏ qua gravatar (gọi mạng)
		try:
			with omni_bot():
				self._prepare()
				self._each("NhaThau", self._org_row)
				self._each("GoiThau", self._award_row)
				self._each("NguoiLienHe", self._contact_row)
				self._convert_deals()
				fit_info = self._fit_pass()
				reconcile = self._reconcile()
				if self.commit:
					frappe.db.commit()
		finally:
			frappe.flags.omni_defer_fit = prev_defer
			frappe.flags.in_import = prev_import
		return self._report(time.monotonic() - started, fit_info, reconcile)

	# -- chuẩn bị --

	def _prepare(self) -> None:
		assert self.data
		self.warnings.extend(self.data.warnings)
		path_warning = _repo_warning(os.path.realpath(self.path))
		if path_warning:
			self._warn(path_warning)
		taxes: set[str] = set()
		by_tax: dict[str, list[tuple[SheetRow, str]]] = {}
		for sheet in SHEETS:
			for row in self.data.sheets[sheet].rows:
				try:
					tax = parsing.parse_tax_id(row.values.get("tax_id"))
				except RowError:
					continue
				taxes.add(tax)
				if sheet == "NhaThau":
					by_tax.setdefault(tax, []).append((row, parsing.clean_text(row.values.get("name"), 140)))
		for tax, rows in by_tax.items():  # cùng mã số thuế khác tên -> giữ cả hai, chờ xác nhận
			keys = {company_name_key(n) for _r, n in rows if n}
			if len(keys) > 1:
				self.held[tax] = {"reason": "mst_trung_khac_ten"}
				self._add_confirm(
					"mst_trung_khac_ten_trong_file",
					_("Hai dòng cùng mã số thuế {0} nhưng khác tên nhà thầu").format(tax),
					[{"dòng": r.number, "tên": n} for r, n in rows[:5]],
					_("Sửa tệp cho đúng một tên, rồi nhập lại. Không dòng nào của mã số thuế này được nhập."),
				)
		for chunk in _chunks(sorted(taxes), 500):
			for o in frappe.get_all(
				ORG,
				filters={"omni_tax_id": ["in", chunk]},
				fields=[
					"name",
					"organization_name",
					"omni_tax_id",
					"omni_import_snapshot",
					"omni_fit_computed_at",
					*ORG_FIELDS,
				],
				limit_page_length=0,
			):
				self.org[o.omni_tax_id] = dict(o)
		for o in frappe.get_all(
			ORG, fields=["name", "organization_name", "omni_tax_id"], limit_page_length=0
		):
			self.name_index.setdefault(company_name_key(o.organization_name or o.name), []).append(
				(o.name, o.omni_tax_id)
			)
		self.tax_by_name = {o["name"]: tax for tax, o in self.org.items()}
		names = list(self.tax_by_name)
		for chunk in _chunks(names, 500):
			for a in frappe.get_all(
				AWARD,
				filters={"organization": ["in", chunk]},
				fields=[
					"name",
					"organization",
					"tax_id",
					"import_key",
					"notice_no",
					"award_date",
					"end_date",
					"hospital_key",
					*AWARD_FIELDS,
				],
				limit_page_length=0,
			):
				self.award_by_key[a.import_key] = dict(a)
				self.awards_proj.setdefault(self.tax_by_name[a.organization], {})[a.import_key] = dict(a)

	# -- NhaThau --

	def _org_row(self, row: SheetRow) -> str:
		parsed = parsing.parse_organization(row.values)
		tax, name = parsed["tax_id"], parsed["name"]
		if tax in self.held:
			self.counters["held_rows"] += 1
			return self._skip("NhaThau", "Chờ xác nhận (xem mục Cần xác nhận)")
		if tax in self.file_orgs:
			first = self.file_orgs[tax]
			if first["fields"] != parsed["fields"]:
				self._warn(
					f"NhaThau dòng {row.number}: trùng mã số thuế {tax} với dòng {first['row']}; giữ dòng {first['row']}"
				)
			return self._skip("NhaThau", "Trùng mã số thuế trong tệp")
		existing = self.org.get(tax)
		if not existing:
			clash = self._name_clash(name, tax)
			if clash:
				self._hold_name_clash(row, tax, name, clash)
				return self._skip("NhaThau", "Chờ xác nhận (xem mục Cần xác nhận)")
		self.file_orgs[tax] = {**parsed, "row": row.number}
		backup = dict(existing) if existing else None
		try:
			return self._org_apply(row, parsed, existing)
		except Exception:
			# savepoint của dòng sẽ rollback DB; trả trạng thái trong bộ nhớ về như cũ để các dòng sau không tin vào bản ghi ảo
			self.file_orgs.pop(tax, None)
			if existing is None:
				created = self.org.pop(tax, None)
				if created:
					self.tax_by_name.pop(created["name"], None)
					key = company_name_key(created["name"])
					self.name_index[key] = [x for x in self.name_index.get(key, []) if x[1] != tax]
			elif backup is not None:
				existing.clear()
				existing.update(backup)
			raise

	def _org_apply(self, row: SheetRow, parsed: dict[str, Any], existing: dict[str, Any] | None) -> str:
		tax = parsed["tax_id"]
		outcome = self._upsert_org(row, parsed, existing)
		if outcome != "skipped" or self.org[tax].get("omni_fit_computed_at") is None:
			self.touched.add(
				self.org[tax]["name"]
			)  # chỉ tính lại điểm khi dữ liệu nhà thầu đổi hoặc chưa từng có điểm
		lead_changed = self._lead_for_org(self.org[tax], parsed)
		if outcome == "skipped":
			if lead_changed:
				return "updated"
			self.skip_reasons["NhaThau"]["Không đổi"] += 1
		return outcome

	def _name_clash(self, name: str, tax: str) -> tuple[str, str | None] | None:
		for other, other_tax in self.name_index.get(company_name_key(name), []):
			if other_tax != tax:
				return other, other_tax
		return None

	def _hold_name_clash(self, row: SheetRow, tax: str, name: str, clash: tuple[str, str | None]) -> None:
		other, other_tax = clash
		self.held[tax] = {"reason": "trung_ten"}
		have = self._org_summary(other)
		kind = "org_trung_ten_chua_co_mst" if not other_tax else "org_trung_ten_khac_mst"
		self._add_confirm(
			kind,
			_("Nhà thầu trong tệp trùng tên với Organization đã có {0}").format(
				_("(chưa có mã số thuế)") if not other_tax else _("(mã số thuế khác)")
			),
			[have, {"nguồn": f"tệp dòng {row.number}", "mã số thuế": tax, "tên": name}],
			_(
				"Không tự gán mã số thuế. Chọn: gộp hai nhà thầu (api merge_organizations) rồi nhập lại, hoặc đổi tên một bên."
			),
		)

	def _org_summary(self, org_name: str) -> dict[str, Any]:
		row = (
			frappe.db.get_value(
				ORG, org_name, ["name", "omni_tax_id", "omni_data_source", "creation"], as_dict=True
			)
			or {}
		)
		leads = frappe.db.count(LEAD, {"organization": org_name, "omni_merged_into": ["is", "not set"]})
		contacts = frappe.db.count(CONTACT, {"company_name": org_name})
		return {
			"nguồn": "CRM",
			"tên": org_name,
			"mã số thuế": row.get("omni_tax_id") or "(chưa có)",
			"nguồn dữ liệu": row.get("omni_data_source"),
			"ngày tạo": str(row.get("creation") or "")[:10],
			"số Lead": leads,
			"số Contact": contacts,
		}

	def _upsert_org(self, row: SheetRow, parsed: dict[str, Any], existing: dict[str, Any] | None) -> str:
		tax, name, fields = parsed["tax_id"], parsed["name"], dict(parsed["fields"])
		if existing is None:
			fields.setdefault("omni_data_source", _("Nhập danh sách nhà thầu ({0})").format(self.log or "-"))
			snap = {k: _jsonable(v) for k, v in fields.items()}
			record = {"name": name, "organization_name": name, "omni_tax_id": tax, **fields}
			record["omni_import_snapshot"] = json.dumps(snap, ensure_ascii=False)
			if not self.dry_run:
				doc = frappe.get_doc(
					{
						"doctype": ORG,
						"organization_name": name,
						"omni_tax_id": tax,
						"omni_verified": 0,
						"omni_import_snapshot": record["omni_import_snapshot"],
						**fields,
					}
				)
				doc.flags.ignore_permissions = True
				doc.insert()
				record["name"] = doc.name
			self.org[tax] = record
			self.tax_by_name[record["name"]] = tax
			self.name_index.setdefault(company_name_key(name), []).append((record["name"], tax))
			self.counters["orgs_created"] += 1
			return "created"
		if company_name_key(existing.get("organization_name") or existing["name"]) != company_name_key(name):
			self._add_confirm(
				"org_ten_khac_crm",
				_("Mã số thuế {0}: tên trong tệp khác tên trong CRM (giữ tên CRM, không đổi tên)").format(
					tax
				),
				[
					{"nguồn": "CRM", "tên": existing["name"]},
					{"nguồn": f"tệp dòng {row.number}", "tên": name},
				],
				_("Nếu tên CRM sai, đổi tên bằng chức năng Đổi tên của Frappe."),
			)
		snap = _snapshot(existing.get("omni_import_snapshot"))
		changes, conflicts = merge_fields(existing, fields, snap)
		for f in conflicts:
			self.counters["field_conflicts"] += 1
			self._warn(f"Nhà thầu {tax}: trường {f} đã được sửa trong CRM sau lần nhập trước, giữ nguyên")
		new_snapshot = json.dumps(snap, ensure_ascii=False)
		snap_changed = new_snapshot != (existing.get("omni_import_snapshot") or "")
		if not changes and not snap_changed:
			return "skipped"
		if not self.dry_run:
			if changes:
				doc = frappe.get_doc(ORG, existing["name"])
				doc.update(changes)
				doc.flags.ignore_permissions = True
				doc.save()
			frappe.db.set_value(
				ORG, existing["name"], "omni_import_snapshot", new_snapshot, update_modified=False
			)
		existing.update(changes)
		existing["omni_import_snapshot"] = new_snapshot
		if not changes:
			return "skipped"
		self.counters["orgs_updated"] += 1
		return "updated"

	# -- Lead --

	def _stage_status(self, stage: str | None) -> str:
		if stage in parsing.DEAL_STAGES:
			return "Đã trao đổi nhu cầu"
		return stage or "Tiềm năng"

	def _open_lead(self, org_name: str) -> tuple[str | None, str | None]:
		"""(Lead đang mở, Deal đang mở/đã thắng) của nhà thầu. Lead đã chuyển Deal tính theo Deal."""
		leads = frappe.db.sql(
			"""select l.name, l.converted, s.type from `tabCRM Lead` l
			left join `tabCRM Lead Status` s on s.name = l.status
			where l.organization = %s and ifnull(l.omni_merged_into, '') = '' order by l.creation""",
			(org_name,),
			as_dict=True,
		)
		for lead in leads:
			if not lead.converted and lead.type not in ("Won", "Lost"):
				return lead.name, None
		deals = frappe.db.sql(
			"""select d.name, s.type from `tabCRM Deal` d left join `tabCRM Deal Status` s on s.name = d.status
			where d.organization = %s order by d.creation""",
			(org_name,),
			as_dict=True,
		)
		for deal in deals:
			if deal.type != "Lost":
				return None, deal.name
		return None, None

	def _lead_for_org(self, org: dict[str, Any], parsed: dict[str, Any]) -> bool:
		"""Bảo đảm nhà thầu có đúng một Lead đang mở; trả True nếu có ghi/lập kế hoạch ghi gì đó."""
		stage, owner_email, notes = parsed["stage"], parsed["owner_email"], parsed["notes"]
		snap = _snapshot(org.get("omni_import_snapshot"))
		lead_name = None
		changed = False
		open_lead, open_deal = self._open_lead(org["name"])
		wanted = self._stage_status(stage)
		if open_lead:
			lead_name = open_lead
			self.counters["leads_linked"] += 1
			current = frappe.db.get_value(LEAD, open_lead, "status")
			if stage and current != wanted:
				self.counters["lead_stage_differs"] += 1
				self._warn(
					f"Nhà thầu {org['omni_tax_id']}: đã có Lead đang mở ở trạng thái «{current}», tệp ghi «{stage}»; giữ nguyên"
				)
		elif open_deal:
			self.counters["leads_skipped_open_deal"] += 1
		elif snap.get("lead") and frappe.db.exists(LEAD, snap["lead"]):
			lead_name = snap["lead"]
			self.counters["leads_kept_closed"] += 1
		else:
			self.counters["leads_created"] += 1
			changed = True
			if not self.dry_run:
				lead_name = self._create_lead(org, wanted, owner_email, stage)
				snap["lead"] = lead_name
			if stage in parsing.DEAL_STAGES:
				self.pending_deals.append({"org": org, "stage": stage, "lead": lead_name})
			if owner_email and not self._valid_owner(owner_email):
				self._warn(
					f"Nhà thầu {org['omni_tax_id']}: sale phụ trách «{owner_email}» không phải User hợp lệ"
				)
		if notes and lead_name and not self.dry_run:
			digest = hashlib.sha1(notes.encode()).hexdigest()
			if snap.get("notes_hash") != digest:
				self._add_note(lead_name, notes)
				snap["notes_hash"] = digest
				self.counters["notes_added"] += 1
				changed = True
		elif notes and self.dry_run and not open_deal:
			self.counters["notes_added"] += 1
		if not self.dry_run:
			new_snapshot = json.dumps(snap, ensure_ascii=False)
			if new_snapshot != (org.get("omni_import_snapshot") or ""):
				frappe.db.set_value(
					ORG, org["name"], "omni_import_snapshot", new_snapshot, update_modified=False
				)
				org["omni_import_snapshot"] = new_snapshot
		return changed

	def _valid_owner(self, email: str) -> str | None:
		if not email:
			return None
		user = frappe.db.get_value("User", {"name": email, "enabled": 1}, "name")
		return user

	def _lead_source(self) -> str | None:
		if not frappe.db.exists("CRM Lead Source", LEAD_SOURCE):
			frappe.get_doc({"doctype": "CRM Lead Source", "source_name": LEAD_SOURCE}).insert(
				ignore_permissions=True
			)
		return LEAD_SOURCE

	def _create_lead(
		self, org: dict[str, Any], status: str, owner_email: str | None, stage: str | None
	) -> str:
		if not frappe.db.exists("CRM Lead Status", status):
			raise RowError(
				f"Trạng thái «{status}» chưa có trong CRM (chạy bench migrate để tạo giai đoạn B2B)"
			)
		values: dict[str, Any] = {
			"doctype": LEAD,
			"first_name": org["name"][:140],  # CRM bắt buộc first_name; Lead của nhà thầu mang tên công ty
			"organization": org["name"],
			"status": status,
			"source": self._lead_source(),
		}
		owner = self._valid_owner(owner_email or "")
		if owner:
			values["lead_owner"] = owner  # chỉ áp cho Lead MỚI; Lead đang có giữ người phụ trách hiện tại
		if stage == parsing.NOT_FIT:
			values["lost_reason"] = "Other"
			values["lost_notes"] = IMPORT_LOST_NOTE
		meta = self._lead_meta or frappe.get_meta(LEAD)
		self._lead_meta = meta
		if meta.has_field("omni_ad_platform"):  # V2-05 điền ngược nếu chưa có trường
			field = meta.get_field("omni_ad_platform")
			if AD_PLATFORM in (field.options or "").split("\n"):
				values["omni_ad_platform"] = AD_PLATFORM
		doc = frappe.get_doc(values)
		doc.flags.ignore_permissions = True
		doc.insert()
		return doc.name

	def _add_note(self, lead: str, notes: str) -> None:
		body = "<p>" + frappe.utils.escape_html(notes).replace("\n", "<br>") + "</p>"
		frappe.get_doc(
			{
				"doctype": NOTE,
				"title": NOTE_TITLE,
				"content": body,
				"reference_doctype": LEAD,
				"reference_docname": lead,
			}
		).insert(ignore_permissions=True)

	# -- GoiThau --

	def _award_row(self, row: SheetRow) -> str:
		p = parsing.parse_award(row.values)
		tax = p["tax_id"]
		if tax in self.held:
			self.counters["held_rows"] += 1
			return self._skip("GoiThau", "Chờ xác nhận nhà thầu (xem mục Cần xác nhận)")
		org = self.org.get(tax)
		if not org:
			raise RowError(
				f"Mã số thuế {tax} không có trong sheet NhaThau và chưa có trong CRM (hoặc dòng nhà thầu bị lỗi)"
			)
		raw_key, hospital_key = hospital.resolve_keys(p["hospital"])
		if not raw_key:
			raise RowError("Thiếu tên bệnh viện / bên mời thầu")
		from omni_crm.omni_crm.doctype.omni_bid_award.omni_bid_award import build_import_key

		key = build_import_key(tax, p["notice_no"], raw_key, p["award_date"], p["award_value"])
		proj = self.awards_proj.setdefault(tax, {})
		if key in proj and proj[key].get("_from_file"):
			return self._skip("GoiThau", "Trùng gói thầu trong tệp")
		values = {f: p[f] for f in (*AWARD_FIELDS, "award_date") if p.get(f) is not None}
		end = (
			p["award_date"] + relativedelta(months=cint(p["duration_months"]))
			if cint(p["duration_months"]) > 0
			else None
		)
		existing = self.award_by_key.get(key)
		outcome = "created"
		if existing:
			changes = {f: v for f, v in values.items() if _norm(existing.get(f)) != _norm(v)}
			if changes:
				if not self.dry_run:
					doc = frappe.get_doc(AWARD, existing["name"])
					doc.update(changes)
					doc.flags.ignore_permissions = True
					doc.save()
				existing.update(changes)
				outcome = "updated"
			else:
				outcome = "skipped"
				self.skip_reasons["GoiThau"]["Không đổi"] += 1
		elif not self.dry_run:
			doc = frappe.get_doc(
				{
					"doctype": AWARD,
					"organization": org["name"],
					"tax_id": tax,
					"notice_no": p["notice_no"],
					**values,
				}
			)
			doc.flags.ignore_permissions = True
			doc.insert()
			self.award_by_key[key] = {"name": doc.name, **values, "notice_no": p["notice_no"]}
		else:
			self.award_by_key[key] = {"name": "(sẽ tạo)", **values, "notice_no": p["notice_no"]}
		proj[key] = {
			"category": p["category"],
			"award_date": p["award_date"],
			"end_date": end,
			"hospital_key": hospital_key,
			"item_count": p["item_count"] or 0,
			"award_value": p["award_value"] or 0,
			"_from_file": True,
		}
		if outcome != "skipped":
			self.touched.add(org["name"])
		return outcome

	# -- NguoiLienHe --

	def _contact_row(self, row: SheetRow) -> str:
		v = row.values
		# Người đã bị Xóa dữ liệu khách: kiểm TRƯỚC mọi thứ, không tạo lại, không nhắc tên/SĐT/email ở đâu cả.
		phone_probe = normalize_phone(parsing.clean_text(v.get("phone"), 40)) if v.get("phone") else None
		email_probe = normalize_email(parsing.clean_text(v.get("email"), 140)) if v.get("email") else None
		if (phone_probe or email_probe) and contact_lib.is_erased_person(phone_probe, email_probe):
			self.counters["contacts_erased_skipped"] += 1
			return self._skip("NguoiLienHe", "Người này đã yêu cầu Xóa dữ liệu khách: không tạo lại")
		p = parsing.parse_contact(v)
		tax = p["tax_id"]
		if tax in self.held:
			self.counters["held_rows"] += 1
			return self._skip("NguoiLienHe", "Chờ xác nhận nhà thầu (xem mục Cần xác nhận)")
		org = self.org.get(tax)
		if not org:
			raise RowError(
				f"Mã số thuế {tax} không có trong sheet NhaThau và chưa có trong CRM (hoặc dòng nhà thầu bị lỗi)"
			)
		phones = [p["phone"][1]] if p["phone"] else []
		emails = [p["email"]] if p["email"] else []
		key = phones[0] if phones else (emails[0] if emails else f"name:{company_name_key(p['full_name'])}")
		if (org["name"], key) in self.seen_contacts:
			return self._skip("NguoiLienHe", "Trùng người liên hệ trong tệp")
		match = self._match_contact(row, org, p, phones, emails)
		if match == "held":
			return self._skip("NguoiLienHe", "Chờ xác nhận (xem mục Cần xác nhận)")
		outcome = self._update_contact(match, org, p) if match else self._create_contact(org, p)
		self.seen_contacts.add((org["name"], key))  # chỉ ghi nhớ khi đã nhập được
		return outcome

	def _match_contact(
		self, row: SheetRow, org: dict[str, Any], p: dict[str, Any], phones: list[str], emails: list[str]
	) -> str | None:
		found = contact_lib.contacts_matching_any(phones, emails) if (phones or emails) else []
		if not found and not phones and not emails:
			found = frappe.get_all(
				CONTACT, filters={"company_name": org["name"], "full_name": p["full_name"]}, pluck="name"
			)
		if not found:
			return None
		info = {c: frappe.db.get_value(CONTACT, c, "company_name") for c in found}
		same = [c for c, comp in info.items() if comp == org["name"]]
		free = [c for c, comp in info.items() if not comp]
		if len(same) == 1:
			return same[0]
		if len(same) > 1 or len(free) > 1:
			self._add_confirm(
				"contact_nhieu_khop",
				_("Người liên hệ ở tệp dòng {0} khớp nhiều Contact có sẵn").format(row.number),
				[{"Contact": c, "nhà thầu": comp or "(chưa gắn)"} for c, comp in list(info.items())[:5]],
				_("Gộp các Contact trùng rồi nhập lại."),
			)
			return "held"
		if len(free) == 1:
			return free[0]
		self._add_confirm(
			"contact_khac_nha_thau",
			_("Người liên hệ ở tệp dòng {0} trùng SĐT/email với Contact của nhà thầu khác").format(
				row.number
			),
			[{"Contact": c, "nhà thầu": comp} for c, comp in list(info.items())[:3]]
			+ [{"nguồn": f"tệp dòng {row.number}", "nhà thầu": org["name"]}],
			_("Kiểm tra có phải cùng một người. Importer không chuyển Contact giữa các nhà thầu."),
		)
		return "held"

	def _contact_values(self, p: dict[str, Any]) -> dict[str, Any]:
		first, last = parsing.split_name(p["full_name"])
		desired = {
			"first_name": first,
			"last_name": last or None,
			"designation": p["title"],
			"omni_contact_role": p["contact_role"],
			"omni_has_zalo": p["has_zalo"],
			"omni_info_source": p["info_source"],
			"omni_consent": p["consent"],
			"omni_consent_date": p["consent_date"],
			"omni_consent_method": p["consent_method"],
		}
		return {k: v for k, v in desired.items() if v is not None}

	def _contact_comment(self, contact: str, notes: str, snap: dict[str, Any]) -> bool:
		digest = hashlib.sha1(notes.encode()).hexdigest()
		if not notes or snap.get("notes_hash") == digest:
			return False
		if not self.dry_run:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": CONTACT,
					"reference_name": contact,
					"content": frappe.utils.escape_html(notes).replace("\n", "<br>"),
				}
			).insert(ignore_permissions=True)
		snap["notes_hash"] = digest
		return True

	def _create_contact(self, org: dict[str, Any], p: dict[str, Any]) -> str:
		desired = self._contact_values(p)
		desired.setdefault("omni_consent", DEFAULT_CONSENT)
		snap = {k: _jsonable(v) for k, v in desired.items()}
		self.counters["contacts_created"] += 1
		if self.dry_run:
			if p["notes"]:
				self.counters["contact_notes_added"] += 1
			self.contact_orgs[f"(dry-run {self.counters['contacts_created']})"] = org["omni_tax_id"]
			return "created"
		doc = frappe.get_doc(
			{
				"doctype": CONTACT,
				"company_name": org["name"],
				**desired,
			}  # info_source đã bắt buộc ở parse_contact
		)
		if p["email"]:
			doc.append("email_ids", {"email_id": p["email"], "is_primary": 1})
		if p["phone"]:
			e164 = p["phone"][1]
			doc.append(
				"phone_nos",
				{
					"phone": p["phone"][0],
					"is_primary_mobile_no": 1 if is_mobile_phone(e164) else 0,
					"is_primary_phone": 0 if is_mobile_phone(e164) else 1,
				},
			)
		doc.omni_import_snapshot = json.dumps(snap, ensure_ascii=False)
		doc.flags.ignore_permissions = True
		doc.insert()
		if p["notes"] and self._contact_comment(doc.name, p["notes"], snap):
			doc.db_set("omni_import_snapshot", json.dumps(snap, ensure_ascii=False), update_modified=False)
			self.counters["contact_notes_added"] += 1
		self.contact_orgs[doc.name] = org["omni_tax_id"]
		return "created"

	def _update_contact(self, name: str, org: dict[str, Any], p: dict[str, Any]) -> str:
		doc = frappe.get_doc(CONTACT, name)
		desired = self._contact_values(p)
		snap = _snapshot(doc.get("omni_import_snapshot"))
		current = {f: doc.get(f) for f in desired}
		if doc.get("omni_consent") == contact_lib.REFUSED and desired.get("omni_consent") not in (
			None,
			contact_lib.REFUSED,
		):
			self.counters["consent_kept_refused"] += 1  # không bao giờ nới đồng ý từ "Từ chối" bằng nhập tệp
			self._add_confirm(
				"contact_tu_choi",
				_("Một người liên hệ đang «Từ chối» nhận liên hệ nhưng tệp ghi «{0}»; giữ «Từ chối»").format(
					desired["omni_consent"]
				),
				[{"Contact": name, "nhà thầu": org["name"]}],
				_("Chỉ đổi đồng ý khi có bằng chứng từ chính người đó, bằng tay."),
			)
			for f in ("omni_consent", "omni_consent_date", "omni_consent_method"):
				desired.pop(f, None)
				current.pop(f, None)
		changes, conflicts = merge_fields(current, desired, snap)
		for f in conflicts:
			self.counters["field_conflicts"] += 1
			self._warn(f"Contact của nhà thầu {org['omni_tax_id']}: trường {f} đã sửa trong CRM, giữ nguyên")
		adopted = not doc.company_name
		new_phone = p["phone"] and p["phone"][1] not in [
			normalize_phone(r.phone) for r in doc.get("phone_nos") or []
		]
		new_email = p["email"] and p["email"] not in [
			normalize_email(r.email_id) for r in doc.get("email_ids") or []
		]
		note_new = self._contact_comment(name, p["notes"], snap) if p["notes"] else False
		snap_text = json.dumps(snap, ensure_ascii=False)
		snap_changed = snap_text != (doc.get("omni_import_snapshot") or "")
		if not (changes or adopted or new_phone or new_email or note_new or snap_changed):
			return self._skip("NguoiLienHe", "Không đổi")
		if note_new:
			self.counters["contact_notes_added"] += 1
		if not (changes or adopted or new_phone or new_email):
			if not self.dry_run:
				doc.db_set("omni_import_snapshot", snap_text, update_modified=False)
			return self._skip("NguoiLienHe", "Không đổi")
		if not self.dry_run:
			doc.update(changes)
			if adopted:
				doc.company_name = org["name"]
			if new_phone:
				e164 = p["phone"][1]
				doc.append(
					"phone_nos",
					{
						"phone": p["phone"][0],
						"is_primary_mobile_no": 0,
						"is_primary_phone": 0,
					}
					if doc.get("phone_nos")
					else {
						"phone": p["phone"][0],
						"is_primary_mobile_no": 1 if is_mobile_phone(e164) else 0,
						"is_primary_phone": 0 if is_mobile_phone(e164) else 1,
					},
				)
			if new_email:
				doc.append(
					"email_ids", {"email_id": p["email"], "is_primary": 0 if doc.get("email_ids") else 1}
				)
			doc.omni_import_snapshot = snap_text
			doc.flags.ignore_permissions = True
			doc.save()
		self.counters["contacts_updated"] += 1
		self.contact_orgs[name] = org["omni_tax_id"]
		return "updated"

	# -- Chuyển Deal --

	def _convert_deals(self) -> None:
		"""Giai đoạn thuộc Deal (Đề xuất gói, Đàm phán, Ký hợp đồng): chuyển Lead -> Deal bằng hàm của CRM, cần người liên hệ."""
		for item in self.pending_deals:
			org, stage, lead = item["org"], item["stage"], item["lead"]
			contact = self._deal_contact(org)
			if not contact:
				self.counters["deals_waiting_contact"] += 1
				self._add_confirm(
					"deal_thieu_nguoi_lien_he",
					_(
						"Nhà thầu {0}: tệp ghi giai đoạn «{1}» nhưng chưa có người liên hệ để chuyển Deal"
					).format(org["omni_tax_id"], stage),
					[{"nhà thầu": org["name"], "Lead": lead or "(sẽ tạo)"}],
					_(
						"Thêm người liên hệ vào sheet NguoiLienHe rồi nhập lại; Lead tạm ở «Đã trao đổi nhu cầu»."
					),
				)
				continue
			self.counters["deals_converted"] += 1
			if self.dry_run:
				continue
			sp = f"omni_imp_deal_{self.counters['deals_converted']}"
			frappe.db.savepoint(sp)
			try:
				from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal

				lead_doc = frappe.get_doc(LEAD, lead)
				lead_doc.flags.ignore_permissions = True
				deal = convert_to_deal(
					lead=lead, doc=lead_doc, existing_contact=contact, existing_organization=org["name"]
				)
				frappe.db.set_value(DEAL, deal, "status", stage)
				self._tick()
			except Exception as e:
				self._rollback(sp)
				self.counters["deals_converted"] -= 1
				self.counters["deals_failed"] += 1
				self._warn(f"Nhà thầu {org['omni_tax_id']}: chưa chuyển được Deal ({_reason(e)})")

	def _deal_contact(self, org: dict[str, Any]) -> str | None:
		if self.dry_run:
			if any(t == org["omni_tax_id"] for t in self.contact_orgs.values()):
				return "(sẽ tạo)"
		else:
			for name, t in self.contact_orgs.items():
				if t == org["omni_tax_id"]:
					return name
		return frappe.db.get_value(CONTACT, {"company_name": org["name"]}, "name", order_by="creation asc")

	# -- Điểm phù hợp và đối chiếu --

	def _fit_pass(self) -> dict[str, Any]:
		if self.dry_run:
			return {"computed": 0, "skipped": "chạy thử: điểm được tính ở lần nhập thật"}
		if not fit.fit_enabled():
			return {"computed": 0, "skipped": "công tắc fit_scoring_enabled đang tắt: chưa tính điểm"}
		done = 0
		for org in sorted(self.touched):
			sp = f"omni_imp_fit_{done}"
			frappe.db.savepoint(sp)
			try:
				fit.apply_fit(org)
			except Exception as e:
				self._rollback(sp)
				self._warn(f"Không tính được điểm phù hợp của nhà thầu {org}: {_reason(e)}")
				continue
			done += 1
			if self.commit and done % BATCH == 0:
				frappe.db.commit()
		return {"computed": done, "skipped": None}

	def _fit_for(self, tax: str, today: date, params: dict[str, Any]) -> dict[str, Any] | None:
		org = self.org.get(tax)
		if not org:
			return None
		if self.dry_run:
			est = self.file_orgs.get(tax, {}).get("fields", {}).get("omni_est_item_count")
			if est is None:
				est = org.get("omni_est_item_count") or 0
			awards = list(self.awards_proj.get(tax, {}).values())
			return fit.fit_from_awards(awards, cint(est), params, today)
		return fit.compute_fit(org["name"], today)

	def _reconcile(self) -> dict[str, Any]:
		"""Đối chiếu điểm CRM với cột `calc_*` và sheet TongHop/ThamSo của tệp (docs/14, D1-14)."""
		assert self.data
		data = self.data
		params = fit.load_params()
		today = data.today or getdate()
		out: dict[str, Any] = {
			"date": str(today),
			"date_source": "ThamSo của tệp" if data.today else "hôm nay (tệp không có ngày tính)",
			"compared": 0,
			"without_calc": 0,
			"mismatches": [],
			"param_diffs": [],
			"tonghop": None,
			"top10_crm": [],
			"top10_file": [],
			"top10_match": None,
		}
		for key, file_value in data.params.items():
			mine = params.get(key)
			if mine is not None and abs(flt(mine) - flt(file_value)) > 1e-9:
				out["param_diffs"].append({"tham số": key, "tệp": file_value, "Omni Settings": mine})
		if data.scored_categories is not None and data.scored_categories != params["categories"]:
			out["param_diffs"].append(
				{
					"tham số": "fit_categories",
					"tệp": sorted(data.scored_categories),
					"Omni Settings": sorted(params["categories"]),
				}
			)
		rows: list[dict[str, Any]] = []
		for fo in self.file_orgs.values():
			calc = next((r.calc for r in data.sheets["NhaThau"].rows if r.number == fo["row"]), {})
			result = self._fit_for(fo["tax_id"], today, params)
			if result is None:
				continue
			entry = {
				"tax": fo["tax_id"],
				"name": fo["name"],
				"score": result["fit_score"],
				"tier": result["fit_tier"],
				"hospitals": result["hospital_count"],
				"meets": result["meets_criteria"],
				"file_score": _num(calc.get("calc_fit_score")),
				"file_tier": calc.get("calc_tier") or None,
				"file_hospitals": _num(calc.get("calc_hospitals")),
			}
			rows.append(entry)
			if entry["file_score"] is None and entry["file_hospitals"] is None and not entry["file_tier"]:
				out["without_calc"] += 1
				continue
			out["compared"] += 1
			for label, mine, theirs in (
				("calc_hospitals", entry["hospitals"], entry["file_hospitals"]),
				("calc_fit_score", entry["score"], entry["file_score"]),
				("calc_tier", entry["tier"], entry["file_tier"]),
			):
				if theirs is not None and _norm(mine) != _norm(theirs):
					out["mismatches"].append(
						{
							"tax": entry["tax"],
							"tên": entry["name"],
							"trường": label,
							"tệp": theirs,
							"CRM": mine,
							"lý do dự kiến": self._mismatch_reason(label, out["param_diffs"]),
						}
					)
		rows.sort(key=lambda r: (-r["score"], r["tax"]))
		out["top10_crm"] = [{k: r[k] for k in ("tax", "name", "score", "tier")} for r in rows[:10]]
		with_file = sorted(
			[r for r in rows if r["file_score"] is not None], key=lambda r: (-r["file_score"], r["tax"])
		)
		out["top10_file"] = [
			{"tax": r["tax"], "name": r["name"], "score": r["file_score"], "tier": r["file_tier"]}
			for r in with_file[:10]
		]
		if with_file:
			out["top10_match"] = [(r["tax"], r["score"], r["tier"]) for r in out["top10_crm"]] == [
				(r["tax"], r["score"], r["tier"]) for r in out["top10_file"]
			]
		if data.tonghop:
			crm = {
				"total": len(rows),
				"meets": sum(1 for r in rows if r["meets"]),
				"A": sum(1 for r in rows if r["tier"] == "A"),
				"B": sum(1 for r in rows if r["tier"] == "B"),
				"C": sum(1 for r in rows if r["tier"] == "C"),
			}
			out["tonghop"] = {
				"file": data.tonghop,
				"crm": crm,
				"match": all(crm.get(k) == v for k, v in data.tonghop.items()),
			}
		return out

	@staticmethod
	def _mismatch_reason(label: str, param_diffs: list[dict]) -> str:
		if label == "calc_hospitals":
			return (
				"CRM chuẩn hóa tên bệnh viện (bỏ dấu, viết tắt BV/BVĐK/TTYT, bảng Omni Hospital Alias) nên gộp hoặc tách "
				"khác Excel (Excel đếm theo chuỗi thô)"
			)
		if param_diffs:
			return "Tham số chấm điểm của tệp (ThamSo/DanhMuc) khác Omni Settings"
		return "Điểm/hạng lệch do số bệnh viện hoặc số mã hàng khác, hoặc ngày tính khác"

	# -- Báo cáo --

	def _report(self, seconds: float, fit_info: dict[str, Any], reconcile: dict[str, Any]) -> dict[str, Any]:
		assert self.data
		if reconcile["param_diffs"]:
			self._warn("Tham số chấm điểm trong tệp khác Omni Settings: điểm CRM có thể khác cột calc_*")
		held_deps = self.counters["held_rows"]
		if held_deps:
			self._warn(f"{held_deps} dòng bị giữ lại vì nhà thầu chờ xác nhận (không nhập, không phải lỗi)")
		sheets: dict[str, dict[str, Any]] = {}
		for sheet in SHEETS:
			s = self.stats[sheet]
			sheets[sheet] = {
				"rows": s["rows"],
				"created": s["created"],
				"updated": s["updated"],
				"skipped": s["skipped"],
				"errors": s["errors"],
				"skip_reasons": dict(self.skip_reasons[sheet]),
			}
		has_errors = any(self.errors[s] for s in SHEETS)
		error_file = (
			import_errors.build_error_workbook(self.data.sheets, self.errors, self.confirm)
			if has_errors or self.confirm
			else None
		)
		summary = {
			"dry_run": self.dry_run,
			"sheets": sheets,
			"counters": dict(self.counters),
			"fit": fit_info,
			"errors": {s: self.errors[s][:MAX_ISSUES] for s in SHEETS if self.errors[s]},
		}
		return {
			"dry_run": self.dry_run,
			"duration_seconds": round(seconds, 2),
			"sheets": sheets,
			"summary": summary,
			"confirm": self.confirm,
			"warnings": self.warnings,
			"reconcile": reconcile,
			"error_file": error_file,
			"errors": self.errors,
			"counters": dict(self.counters),
		}


def _num(value: Any) -> float | None:
	return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _chunks(items: list[Any], size: int):
	for i in range(0, len(items), size):
		yield items[i : i + size]
