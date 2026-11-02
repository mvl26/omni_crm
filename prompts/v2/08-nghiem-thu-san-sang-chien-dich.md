# V2-08 ★ · Hồi quy, kiểm thử tải và nghiệm thu sẵn sàng chiến dịch

**Phụ thuộc:** V2-05, V2-06 (và V2-07 nếu đã chạy). **Thời lượng dự kiến:** 1,5–2 ngày + tuần chạy thử.

```
Bạn là agent kiểm thử cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/10-kiem-thu.md, docs/11-van-hanh.md, docs/15 mục 7, 10, docs/14 mục 6, docs/09, docs/12, docs/v2/gap-report.md, các báo cáo nghiệm thu v1 trong docs/nghiem-thu/, prompt gốc prompts/dot-1/11-kiem-thu-dot-1.md.

Nhiệm vụ: chứng minh hệ thống sẵn sàng nhận lead của chiến dịch đầu tiên, không làm hỏng những gì v1 đã chạy.

1. Hồi quy: chạy toàn bộ test; so số test với V2-00 (không test nào bị xóa hay bỏ qua — liệt kê nếu có và lý do). gap-report.md: mọi dòng phải là "Có-đúng", "Không áp dụng", hoặc có ADR/quyết định treo; dòng còn lại → liệt kê là lỗi còn mở.
2. Đối chiếu kịch bản: bảng I-01…I-13 (docs/10 mục 3) + test của docs/13, docs/14, docs/15 mục 10 → bảng ánh xạ ca → tên test. Thiếu thì viết.
3. Kiểm thử tải trên {{SITE_STAGING}} (dùng lại `tests/load/send_burst.py` của v1, mở rộng thêm kênh quảng cáo):
   - 200 lead quảng cáo (Meta + Google + web form trộn) trong 10 phút, 10% gửi lại, 20% trùng Contact đã có từ V2-03.
   - Cùng lúc: lưu lượng tin nhắn ngày cao điểm như D1-11.
   - Đo: p95 phản hồi webhook (< 1 giây), không mất, không Lead trùng, thời gian lead→Lead có người phụ trách (p95), số thông báo gửi sale, hàng đợi `omni` trở về rỗng sau bao lâu.
4. Kịch bản sự cố (docs/10 mục 5) thêm: Graph API Meta lỗi 30 phút → đối soát bù đủ; Google gửi lại sau 5xx → không trùng; Redis khởi động lại giữa đợt.
5. Bảo mật nhanh: webhook Meta/Google/web form không chữ ký hoặc sai khóa; API dashboard và chi tiết nhà thầu với Guest và Sales User không có quyền; không có chức năng gửi hàng loạt (V2-Q12); Contact "Từ chối" không liên hệ được qua giao diện.
6. Tài liệu:
   - `docs/nghiem-thu/v2.md`: kết quả 1–5, bảng chỉ số mục tiêu chiến dịch (Lead mới/ngày, % phản hồi trong hạn 15 phút giờ làm việc, thời gian phản hồi lần đầu trung vị, Lead trùng, lead mất, chi phí mỗi Lead hợp lệ) với cột "Đo được" để trống phần cần tuần chạy, kèm script đo `tests/acceptance/measure_campaign.py`.
   - `docs/nghiem-thu/uat-v2.md`: kịch bản cho 2–3 sale chạy thử: nhận lead quảng cáo, gọi/nhắn, ghi nhật ký chăm sóc, chuyển giai đoạn, xem Chi tiết nhà thầu.
   - `docs/huong-dan/truoc-ngay-chay-chien-dich.md`: danh sách kiểm tra (xem dưới), mỗi mục có cách kiểm tra và người chịu trách nhiệm.
7. Danh sách kiểm tra trước ngày chạy chiến dịch (đưa vào tài liệu trên, agent tự kiểm tra được mục nào thì đánh dấu và ghi bằng chứng):
   - Webhook Meta đã đăng ký trường `leadgen` cho đúng Page; ứng dụng ở chế độ Live; token Page còn hạn và có cảnh báo hết hạn.
   - Google Ads: URL webhook và `google_key` đã nhập vào từng biểu mẫu; đã gửi dữ liệu thử thành công.
   - Mỗi biểu mẫu có Omni Lead Form `enabled = 1`, `field_map` đủ, gắn đúng Omni Campaign.
   - Form website gửi về endpoint; UTM lưu đúng (thử với một link có UTM).
   - Quy tắc phân công có đủ sale đang làm việc; giờ làm việc và ngày lễ đúng lịch chiến dịch.
   - Giám sát: cảnh báo "không có lead N giờ", hàng đợi kẹt, webhook lỗi — đã thử kích hoạt một lần.
   - Zalo OA, email, tổng đài (nếu đã có nhà cung cấp) hoạt động trên production; nếu chưa có tổng đài: sale ghi cuộc gọi bằng nhật ký chăm sóc.
   - `sandbox_mode` trên production đã **tắt có chủ đích** (ghi ai tắt, lúc nào); trên staging vẫn bật.
   - V2-Q12 (liên hệ chủ động) và V2-Q15 (ngày chạy, câu hỏi form) đã chốt hoặc ghi rõ cách làm tạm.
   - Bot: chế độ từng nhóm câu hỏi đúng kết luận V2-07 (chưa đo → Shadow).
   - Sao lưu production và cách quay lui về `v1-final` đã thử (docs/11).

Hoàn thành khi: mọi test xanh; số đo tải và sự cố thật từ staging; ba tài liệu đã viết; danh sách lỗi còn mở có mức độ. Kết luận: "Sẵn sàng chạy chiến dịch" / "Sẵn sàng sau khi sửa …" / "Chưa sẵn sàng".

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra (cổng 3)

1. Đọc `docs/nghiem-thu/v2.md`: p95 webhook < 1 giây, không mất, không trùng trong đợt 200 lead.
2. Đi hết danh sách `docs/huong-dan/truoc-ngay-chay-chien-dich.md` cùng người chạy quảng cáo.
3. Gửi một lead thử thật từ Meta và Google trên production: vào đúng chiến dịch, đúng sale, sale nhận thông báo trong 1 phút.
4. Sau tuần chạy thử, chạy `measure_campaign.py` và điền cột "Đo được".
