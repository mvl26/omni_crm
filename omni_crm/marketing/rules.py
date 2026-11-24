"""Luật thuần của Marketing Hub (không chạm DB): mã UTM, link theo dõi, trạng thái bài tổng hợp từ các nền tảng."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime
from urllib.parse import urlencode

FACEBOOK, ZALO, TIKTOK = "Facebook", "Zalo OA", "TikTok"
PLATFORMS = (FACEBOOK, ZALO, TIKTOK)
UTM_SOURCE = {FACEBOOK: "facebook", ZALO: "zalo", TIKTOK: "tiktok"}
UTM_MEDIUM = {FACEBOOK: "social", ZALO: "oa", TIKTOK: "social"}
FORMATS = {
	FACEBOOK: ("fb_link", "fb_photo", "fb_multi_photo", "fb_video", "fb_pdf_link"),
	ZALO: ("zalo_article", "zalo_video", "zalo_pdf_link"),
	TIKTOK: ("tiktok_video",),
}

# trạng thái bài (Omni Content Item)
I_DRAFT, I_REVIEW, I_SCHEDULED, I_PROCESSING = "Nháp", "Chờ duyệt", "Đã lên lịch", "Đang xử lý"
I_PUBLISHED, I_PARTIAL, I_FAILED, I_CANCELLED = "Đã đăng", "Đăng một phần", "Thất bại", "Đã hủy"
# trạng thái từng nền tảng (Omni Content Target)
T_REVIEW, T_SCHEDULED, T_PROCESSING = "Chờ duyệt", "Đã lên lịch", "Đang xử lý"
T_SUCCESS, T_FAILED, T_CANCELLED = "Đăng thành công", "Thất bại", "Đã hủy"
T_LIVE = (T_SCHEDULED, T_PROCESSING, T_SUCCESS)  # đã duyệt, đang/đã đăng: khóa mã utm của chiến dịch

SLUG_MAX = 60


def slugify(text: str | None) -> str:
	"""Chuỗi không dấu, chữ thường, nối bằng '-': "Giới thiệu SPD Hà Nội" -> "gioi-thieu-spd-ha-noi"."""
	text = str(text or "").replace("đ", "d").replace("Đ", "D")
	text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
	return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def campaign_utm(title: str | None, when: date | datetime) -> str:
	"""utm_campaign tự sinh: slug tiêu đề (tối đa 60 ký tự) + '-' + mmyy của ngày bắt đầu."""
	slug = slugify(title)[:SLUG_MAX].strip("-") or "chien-dich"
	return f"{slug}-{when:%m%y}"


def next_content_code(existing: Iterable[str | None]) -> str:
	"""Mã bài kế tiếp trong chiến dịch: bai-01, bai-02, ... (bỏ qua mã không đúng mẫu)."""
	nums = [int(m.group(1)) for c in existing if c and (m := re.fullmatch(r"bai-(\d+)", c))]
	return f"bai-{(max(nums) + 1) if nums else 1:02d}"


def landing_path(path: str | None) -> str:
	"""Đường dẫn trang đích chuẩn: bắt đầu bằng '/', thư mục kết thúc bằng '/'.

	nginx trả 301 `/spd` -> `/spd/` và BỎ query string, nên link `/spd?utm_...` mất mã theo dõi: luôn dùng `/spd/`.
	"""
	path = str(path or "").strip() or "/"
	if re.match(r"^https?://", path, re.I):
		raise ValueError("Trang đích phải là đường dẫn trên địa chỉ gốc, ví dụ /spd/")
	path = "/" + path.lstrip("/")
	path = path.split("?")[0].split("#")[0]
	last = path.rsplit("/", 1)[-1]
	if last and "." not in last:
		path += "/"
	return path


def tracking_url(base: str, landing: str | None, platform: str, utm_campaign: str, utm_content: str) -> str:
	"""Link đích kèm UTM của một nền tảng: base + trang đích + ?utm_source&utm_medium&utm_campaign&utm_content."""
	if platform not in UTM_SOURCE:
		raise ValueError(f"Nền tảng không hỗ trợ: {platform}")
	query = urlencode(
		{
			"utm_source": UTM_SOURCE[platform],
			"utm_medium": UTM_MEDIUM[platform],
			"utm_campaign": utm_campaign,
			"utm_content": utm_content,
		}
	)
	return f"{str(base or '').rstrip('/')}{landing_path(landing)}?{query}"


def aggregate_status(target_statuses: Iterable[str]) -> str | None:
	"""Trạng thái bài tổng hợp từ các nền tảng đã duyệt; None nếu chưa có gì để tổng hợp (giữ trạng thái hiện tại).

	- tất cả thành công -> Đã đăng; có thành công và có thất bại/hủy -> Đăng một phần; tất cả thất bại -> Thất bại;
	- còn nền tảng đang chờ/đang xử lý -> Đang xử lý nếu đã có nền tảng xong hoặc đang chạy, không thì Đã lên lịch;
	- tất cả đã hủy -> Đã hủy.
	"""
	statuses = [s for s in target_statuses if s]
	if not statuses or any(s == T_REVIEW for s in statuses):
		return None
	live = [s for s in statuses if s != T_CANCELLED]
	if not live:
		return I_CANCELLED
	pending = [s for s in live if s in (T_SCHEDULED, T_PROCESSING)]
	if pending:
		started = any(s in (T_PROCESSING, T_SUCCESS, T_FAILED) for s in live)
		return I_PROCESSING if started else I_SCHEDULED
	success = sum(1 for s in live if s == T_SUCCESS)
	if success == len(statuses):
		return I_PUBLISHED
	if success:
		return I_PARTIAL
	return I_FAILED


def check_format(platform: str, post_format: str) -> bool:
	return post_format in FORMATS.get(platform, ())
