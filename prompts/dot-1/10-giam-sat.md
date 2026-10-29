# D1-10 · Giám sát, cảnh báo, lưu trữ

**Phụ thuộc:** D1-09. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/11-van-hanh.md (toàn bộ), docs/09, docs/03 mục 6.

Nhiệm vụ: màn hình giám sát, cảnh báo tự động, các tác vụ lưu trữ, và thao tác xóa dữ liệu khách.

Việc cần làm:
1. Trang Desk `omni-monitor` (chỉ Omni Admin) với các khối của docs/11 mục 1 thuộc Đợt 1 (Công tắc, Kênh, Hàng đợi, Thời gian, Chiều ra, Nghi trùng). Số liệu lấy qua `api/admin.py::monitor_snapshot()` — truy vấn gộp, chạy < 500 ms với 100.000 tin vào (tạo dữ liệu giả để đo). Tự làm mới mỗi 30 giây. Nút "Xử lý lại" (chọn tin Manual), "Quét ngay", "Làm mới token Zalo".
2. `monitoring/alerts.py::run_checks()`: các luật cảnh báo Đợt 1 theo ngưỡng docs/11 mục 1 (token Zalo: chỉ cảnh báo khi còn < 1 giờ hoặc lần làm mới gần nhất lỗi); bật lịch `*/5 * * * *` theo docs/01 mục 6; chống lặp 30 phút/loại bằng khóa Redis; gửi email tới `alert_recipients` + Notification Log cho Omni Admin; nội dung không chứa bí mật hay nội dung tin khách.
3. Tác vụ đêm `cleanup_raw_payloads`: xóa `raw_payload` của tin vào cũ hơn `raw_payload_retention_days` (theo lô, có giới hạn thời gian chạy). Bật lịch trong hooks.py.
4. Thao tác "Xóa dữ liệu khách" (docs/11 mục 6): API `api/admin.py::erase_customer_data(lead, reason)` chỉ Omni Admin, có hộp xác nhận; xóa nội dung (text, attachments, raw_payload, sender_profile) của mọi tin vào/ra liên quan Lead và các Identity của Lead; giữ bản ghi kỹ thuật (trạng thái, thời gian) với nội dung "[đã xóa]"; ghi Comment lý do và người làm. Không xóa CRM Lead (việc đó do CRM quản lý).
5. Thêm khối "Tình trạng hệ thống" ngắn gọn vào Hộp thư cho sale: chỉ hiện khi có sự cố kênh (ví dụ "Zalo đang mất kết nối, tin đến vẫn được lưu").

Test: snapshot đúng số liệu với dữ liệu giả; cảnh báo kích hoạt khi vượt ngưỡng và không lặp trong 30 phút; cleanup chỉ xóa đúng tin quá hạn; erase_customer_data bởi Sales User bị từ chối, bởi Omni Admin xóa đúng phạm vi và không đụng Lead khác.

Hoàn thành khi: test xanh; ảnh chụp/mô tả trang giám sát trên dev; số đo thời gian snapshot với 100.000 bản ghi.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Tắt worker 10 phút trên staging: nhận email cảnh báo "tin chờ lâu", chỉ một email.
2. Trang giám sát hiển thị đúng hạn token Zalo.
