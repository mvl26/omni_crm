# 0019 · Thư gửi từ CRM: nối vào luồng thư của khách và gửi từ đúng hộp thư

- Ngày: 2026-11-11
- Trạng thái: Đã chấp nhận (thư gửi từ CRM vào Spam của khách, yêu cầu xử lý 11/11)
- Prompt: — (yêu cầu trực tiếp)

## Bối cảnh
Thư sale gửi từ CRM tới khách rơi vào thư rác. Đọc mã (crm 3942bf6, frappe 7341623) và dựng thử thư trên `omni.test`:
- `CommunicationArea.vue` gọi `communication.email.make` không có `in_reply_to`; Frappe chỉ đặt header In-Reply-To khi Communication có `in_reply_to` (`email_queue.py`, `set_in_reply_to`). Thư "Re: ..." đi ra không nối vào thư nào: hộp thư khách không xếp vào cuộc hội thoại, bộ lọc coi là "Re:" giả.
- Bấm Trả lời một thư khách gửi: `EmailArea.vue` gán ô FROM = `email.sender`, tức địa chỉ của khách; chưa liên kết hộp thư (User Emails) thì FROM là email đăng nhập. Địa chỉ gửi không phải hộp thư thật: Gmail viết lại, hoặc Frappe báo thiếu Email Account.
- Email Account mới có `track_email_status = 1`: Frappe chèn ảnh theo dõi trỏ về tên miền CRM vào mọi thư.

## Các lựa chọn
1. Sửa giao diện app `crm`: cấm (chỉ dùng hook).
2. Hook `Communication.before_insert` của `omni_crm` cho thư `Sent` trên CRM Lead/Deal: gắn `in_reply_to` vào thư khách gần nhất có Message-ID thật (ưu tiên thư của người đang được trả lời, xét cả Lead và Deal đi cặp); FROM không phải Email Account gửi được thì đổi về hộp thư của thư được trả lời, không có thì hộp thư liên kết với người dùng.
3. Chỉ dựa vào cấu hình "Always use this email address as sender address": sửa được From nhưng không nối luồng, và không cứu trường hợp Frappe báo thiếu Email Account.

## Quyết định
Chọn 2, cộng cấu hình Email Account: tắt Track Email Status; tên Email Account là tên thương hiệu, bật cả Always use this email address as sender address và Always use this name as sender name (docs/05 mục 5: bật riêng ô thứ nhất làm mất dấu tiếng Việt trong tên người gửi do `parse_addr` của Frappe). Lỗi trong hook chỉ ghi log, không chặn gửi.

## Hệ quả
- Người nhận thấy thư đến từ đúng hộp thư mà họ đã viết tới, nằm trong cùng cuộc hội thoại.
- Uy tín hộp thư gửi vẫn quyết định một phần: hộp thư thử mới tạo dễ bị lọc; dùng thật nên gửi từ hộp thư tên miền công ty có SPF/DKIM/DMARC (việc `info@` còn mở).
- Test: `tests/test_outgoing_email.py` (dựng thư qua `make` như CRM, kiểm header In-Reply-To và From).
