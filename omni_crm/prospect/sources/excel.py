"""Nguồn Excel: bọc phần GoiThau của importer (cùng bộ phân tích dòng), không ghi DB."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from omni_crm.prospect.parsing import RowError, parse_award
from omni_crm.prospect.sources.base import AwardRecord, BidDataSource
from omni_crm.prospect.xlsx_reader import read_workbook


class ExcelSource(BidDataSource):
	"""Đọc sheet GoiThau của tệp danh sách nhà thầu; dòng không hợp lệ bị bỏ qua (importer mới là nơi báo lỗi)."""

	def __init__(self, path: str | Path) -> None:
		self.path = str(path)
		self.skipped: list[tuple[int, str]] = []

	def fetch_awards(self, since: date | None = None) -> list[AwardRecord]:
		"""Các gói thầu hợp lệ trong tệp (có ngày phê duyệt >= `since` nếu cho)."""
		self.skipped = []
		out: list[AwardRecord] = []
		for row in read_workbook(self.path).sheets["GoiThau"].rows:
			try:
				p = parse_award(row.values)
			except RowError as e:
				self.skipped.append((row.number, str(e)))
				continue
			if since and p["award_date"] < since:
				continue
			out.append(AwardRecord(**p))
		return out
