"""Nguồn dữ liệu gói thầu (docs/14 mục 5.3). Chỉ có cài đặt từ tệp Excel; KHÔNG viết công cụ thu thập tự động trang
đấu thầu cho tới khi pháp chế xác nhận điều khoản sử dụng (V2-Q13)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AwardRecord:
	"""Một gói thầu đã trúng, đã chuẩn hóa (cùng trường với Omni Bid Award)."""

	tax_id: str
	hospital: str
	category: str
	award_date: date
	notice_no: str = ""
	package_name: str | None = None
	hospital_province: str | None = None
	award_value: float | None = None
	item_count: int | None = None
	duration_months: int | None = None
	role: str | None = None
	source_url: str | None = None


class BidDataSource(ABC):
	"""Giao diện nguồn dữ liệu gói thầu; thêm `DauThauApiSource` khi công ty ký gói API."""

	@abstractmethod
	def fetch_awards(self, since: date | None = None) -> list[AwardRecord]:
		"""Các gói thầu có ngày phê duyệt từ `since` trở đi (None = tất cả)."""
