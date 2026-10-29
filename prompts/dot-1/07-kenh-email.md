# D1-07 · Kênh email

**Phụ thuộc:** D1-06. Có thể chạy song song với D1-08 (nhánh riêng). **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/05-kenh-email.md (toàn bộ), docs/04 mục 1.2–1.3, docs/03 mục 2.

Nhiệm vụ: đưa email khách vào chung đường ống, không để CRM tạo Lead trùng.

Bước 0 — G3: đọc mã `crm` và cấu hình Email Account để biết thư đến tạo CRM Lead bằng cơ chế nào (append_to, hook…). Thử thực nghiệm trên {{SITE_TEST}} với một Email Account giả (không cần kết nối máy chủ thư: tạo Communication "Received" bằng code giống luồng nhận thư thật của Frappe). Ghi vào docs/12 mục C và ghi ADR: tắt cơ chế của CRM cho hộp thư bán hàng bằng cấu hình nào; hoặc nếu không tắt được thì áp dụng phương án "Lead ứng viên" ở docs/05 mục 1.

Việc cần làm:
1. `channels/email/clean.py::clean_body` đúng docs/05 mục 3, kèm `split_signature(text) -> (body, signature)`.
2. `channels/email/adapter.py`:
   - `on_communication_insert(doc, method)` đúng docs/05 mục 2 (bật hook trong hooks.py), ghi trường `communication` của tin vào. Không làm việc nặng trong hook.
   - `EmailAdapter` theo `ChannelAdapter`: `verify_request` không dùng (trả True), `parse` không dùng cho webhook; `send` gửi qua `frappe.sendmail` (dùng cho bot ở Đợt 3; hôm nay cài đặt + test ở sandbox), `can_send_now` luôn True trừ khi kênh tắt chiều ra.
   - Bỏ qua thư tự động, thư hệ thống → lưu `status = Ignored`.
3. Bổ sung `resolver`: thư có `in_reply_to` trỏ Communication đã gắn Lead → tín hiệu chắc chắn (đọc trường tương ứng của Communication).
4. Nhánh email trong `crm_bridge/timeline.py`: gắn Communication có sẵn vào Lead đúng cách CRM hiển thị (kết quả G2), không tạo bản sao.
5. Fixtures test: ít nhất 10 thư mẫu trong `tests/fixtures/email/` (HTML và văn bản; Gmail, Outlook, gửi từ điện thoại; có chữ ký chứa SĐT; có trích dẫn tiếng Việt và tiếng Anh; một thư tự động; một thư báo lỗi gửi). Che mọi thông tin thật.

Test: toàn bộ ca ở docs/05 mục 6 + clean_body trên 10 thư mẫu (khẳng định nội dung giữ lại và phần bị cắt).

Hoàn thành khi: test xanh; trên {{SITE_TEST}} tạo 3 thư giả từ cùng một người với 3 tiêu đề khác nhau → đúng 1 Lead, 3 thư hiện trên Lead; ADR về G3.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Gửi một thư thật từ hộp thư cá nhân tới hộp thư bán hàng (trên staging): sau tối đa vài phút có Lead, thư nằm trên Lead, không có Lead thứ hai do CRM tạo.
2. Trả lời thư đó từ CRM: khách nhận được, chuỗi thư liền mạch.
