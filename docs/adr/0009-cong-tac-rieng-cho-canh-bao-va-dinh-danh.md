# 0009 · Công tắc riêng cho cảnh báo, theo dõi sửa tay, Hộp thư, định danh

- Ngày: 2026-11-02
- Trạng thái: Đề xuất
- Prompt: V2-01 (gap-report 09.06; docs/nghiem-thu/dot-1.md 9.1 mục 15)

## Bối cảnh
docs/09 liệt kê công tắc cho kênh, chiều ra, bot, chấm điểm, tự gửi. Bốn phần không có công tắc riêng: cảnh báo (`monitoring/alerts.py`), theo dõi sửa tay (`crm_bridge/lead.track_manual_edits`), trang Hộp thư, và bộ định danh. Ngoại lệ này đã được ghi từ D1-06/D1-10 và chấp nhận ở nghiệm thu Đợt 1.
Cách tắt hiện có mà không cần trường mới: cảnh báo — để trống `alert_recipients` (Notification Log vẫn tạo); Hộp thư — gỡ vai trò khỏi trang; định danh — tắt kênh (`enabled`), tin vẫn được lưu.

## Các lựa chọn
1. Giữ nguyên, ghi rõ cách tắt thay thế ở docs/09 (không đổi DocType).
2. Thêm trường Check vào Omni Settings cho từng phần — đổi DocType ngoài danh sách đặc tả, cần duyệt.

## Quyết định (đề xuất)
Chọn 1. Không thêm trường. Dòng 09.06 ở gap-report đóng bằng ADR này; docs/09 thêm mục "Phần không có công tắc riêng và cách tắt thay thế".

## Hệ quả
- Nếu v2 cần tắt riêng (ví dụ cảnh báo trong chiến dịch), mở lại bằng ADR mới kèm đổi DocType.
