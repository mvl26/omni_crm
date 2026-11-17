"""Hằng số của bài đăng (ADR 0029)."""

POST = "Omni Content Post"
PILLAR = "Omni Content Pillar"
CAMPAIGN = "Omni Campaign"
LEAD = "CRM Lead"
DEAL = "CRM Deal"

DRAFT = "Nháp"
SCHEDULED = "Đã lên lịch"
PUBLISHING = "Đang đăng"
PUBLISHED = "Đã đăng"
FAILED = "Lỗi"
UNCERTAIN = "Cần kiểm"
MISSED = "Bỏ lỡ"
CANCELLED = "Đã huỷ"
STATUSES = (DRAFT, SCHEDULED, PUBLISHING, PUBLISHED, FAILED, UNCERTAIN, MISSED, CANCELLED)

MANUAL = "Đăng tay"
AUTO = "Tự động"

FACEBOOK = "Facebook"

# Bài đã gửi, đang gửi hoặc có thể đã lên nền tảng: không sửa nội dung, ảnh, giờ, link nữa vì link UTM đã ra ngoài.
LOCKED_STATUSES = (PUBLISHING, PUBLISHED, UNCERTAIN)
LOCKED_FIELDS = ("campaign", "platform", "publish_mode", "publish_at", "content", "image", "landing_url")
# Dán link bài vào ô "Link bài" chuyển các trạng thái này sang Đã đăng (bài đăng tay, hoặc người soạn tự đăng thay job).
MARK_PUBLISHED_FROM = (DRAFT, SCHEDULED, FAILED, UNCERTAIN, MISSED)
