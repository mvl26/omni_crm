# V2-01 ★ · Sửa chỗ lệch và nợ kỹ thuật của v1

**Phụ thuộc:** V2-00 đã được anh duyệt. **Thời lượng dự kiến:** tùy báo cáo khoảng cách (thường 1–2 ngày).

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, README-v2.md mục 4, docs/v2/gap-report.md (đặc biệt danh sách việc cho V2-01 và các quyết định anh đã chốt), các tài liệu docs/ mà từng dòng việc tham chiếu.

Nhiệm vụ: xử lý mọi dòng trong gap-report.md có "Prompt v2 xử lý = V2-01", tức phần ĐÃ CÓ từ v1 nhưng lệch đặc tả, thiếu test, hoặc là nợ kỹ thuật. KHÔNG làm tính năng mới của mảng A, B, C (nhà thầu, quảng cáo, dashboard) — các prompt sau làm.

Danh sách kiểm tra bắt buộc (dù gap-report có ghi hay không — xác nhận từng mục bằng test, sửa nếu thiếu):
1. `Omni Inbound Message.identity_key` được điền khi lưu; worker tìm tin theo trường này; chỉ mục (`identity_key`, `status`).
2. Tin ra có `next_attempt_at`, thử lại theo `retry_backoff_minutes` (phần tử n = chờ sau lỗi n), tác vụ quét đẩy lại tin ra kẹt.
3. Bot trả lời chạy SAU bot phân tích của cùng lượt (`after_analyze`), không chạy song song.
4. `OutboundRequest.bot_draft` và `Omni Outbound Message.bot_draft` tồn tại; tin bot gửi luôn có liên kết nháp.
5. Một cuộc gọi đúng một tin vào (`external_message_id = call_id`); sự kiện trung gian vào `Omni Call.raw_events`.
6. Mọi dữ liệu thật gửi sang AI tôn trọng Q11 (chưa chốt thì chỉ FakeAIClient/dữ liệu tổng hợp).
7. Chỉ số "Phản hồi lần đầu" (nếu đã có dashboard) đổi sang định nghĩa `omni_first_response_at` (docs/15 mục 7 bước 4); thêm trường này vào CRM Lead và điền ngược cho Lead cũ bằng patch chạy lại được.
8. `utils/business_hours.py` có `holidays` và `business_minutes_between`; mọi nơi tính hạn dùng chung module này.
9. Công tắc theo docs/09 đều tồn tại và tắt được tính năng tương ứng (test cho từng công tắc đã có).
10. Lỗi và cảnh báo còn mở trong docs/nghiem-thu/ của v1 (mức "Chặn" và "Nên sửa").

Quy trình cho mỗi mục: viết test tái hiện chỗ lệch trước → sửa nhỏ nhất → chạy toàn bộ suite. Một mục một commit `fix(v2-01): …`. Migration chỉ thêm; patch dữ liệu có dry run và chạy lại được. Mục nào sửa đúng đòi hỏi đổi hành vi với khách hoặc đổi DocType ngoài đặc tả → ghi ADR "Đề xuất" và để lại, không tự quyết.

Cập nhật gap-report.md: đổi hiện trạng các dòng đã xử lý thành "Có-đúng" kèm tên test.

Hoàn thành khi: mọi dòng V2-01 trong gap-report là "Có-đúng" hoặc có ADR chờ quyết định; toàn bộ test xanh.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở gap-report.md: không còn dòng V2-01 ở trạng thái "Có-lệch" mà không có ADR.
2. Số test tăng so với V2-00, không test nào bị bỏ qua.
