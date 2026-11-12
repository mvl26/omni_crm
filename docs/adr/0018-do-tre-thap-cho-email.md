# 0018 · Độ trễ thấp cho email: nạp ngay khi có file, gửi ngay khi sale bấm gửi

- Ngày: 2026-11-11
- Trạng thái: Đã chấp nhận (yêu cầu giảm độ trễ thấp nhất có thể, 11/11)
- Prompt: — (yêu cầu trực tiếp)

## Bối cảnh
Đo trên server (11/11): thư khách vào CRM trễ ~1 phút và phải F5; thư sale gửi từ CRM tới khách trễ ~2 phút. Nguyên nhân (đọc mã):
- Intake hỏi Gmail 60 s/lần; runner nạp file theo cron mỗi phút (Frappe không có lịch dưới 1 phút); worker chờ gộp tin 5 s (`debounce_seconds`) kể cả với email.
- Giao diện CRM không tự tải lại danh sách Lead hay khối hoạt động của trang Lead.
- Frappe gửi Email Queue bằng `frappe.email.queue.flush` ở sự kiện "all" (`scheduler_interval` mặc định 240 s) và `communication.email.make` của CRM không yêu cầu gửi ngay.

## Các lựa chọn
1. Intake gọi CRM khi ghi file (webhook nội bộ có khóa bí mật): nhanh nhất nhưng sửa hợp đồng intake, thêm điểm vào mạng và bí mật.
2. Tiến trình theo dõi spool phía CRM (`watcher`), chỉ `stat` thư mục mỗi giây rồi đẩy job nạp: không đổi intake, không thêm cổng mạng; thêm một chương trình supervisor.
3. Hạ `scheduler_interval` xuống 60 s cho việc gửi thư: đơn giản nhưng vẫn trễ tới 1 phút và làm mọi tác vụ "all" của mọi app chạy dày hơn.
4. Gmail Pub/Sub đẩy về intake: bớt thêm ≤ 5 s ở chặng đầu, cần topic Google Cloud và đường vào công khai cho intake.

## Quyết định
Chọn 2 cho chiều vào và một hook `Email Queue.after_insert` (chỉ thư tham chiếu CRM Lead/Deal) đẩy job gửi ngay cho chiều ra; email không chờ gộp tin; giao diện tự tải lại bằng bộ nghe `refetch_resource` có sẵn của CRM. Cron mỗi phút và flush của Frappe giữ nguyên làm lưới an toàn. Chưa làm 4 (để sau nếu cần dưới 5 s).

Chống nạp đôi khi cron và watcher chạy cùng lúc: khóa Redis trong `runner.run`, lượt đến sau để lại dấu "chạy lại". Chống gửi đôi: job và flush cùng khóa dòng Email Queue và `EmailQueue.send` chỉ gửi khi còn "Not Sent".

## Hệ quả
- Server thêm chương trình supervisor `omni-intake-watch` (docs/16, mục "Độ trễ thấp"); cập nhật mã phải khởi động lại nó.
- Hai chỗ `frappe.enqueue` mới được đăng ký trong `tests/test_job_users.py` và có test Guest (ADR 0006).
- Tin realtime ra trình duyệt chỉ chứa khóa đệm; tin về trang Lead/Deal chỉ vào phòng của tài liệu (kiểm quyền khi đăng ký).
