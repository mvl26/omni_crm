# 12 · Quyết định mở, giả định cần xác nhận, phát hiện

Tệp sống: agent cập nhật mỗi khi xác nhận một giả định hoặc phát hiện điều khác đặc tả. Người duyệt đọc tệp này trước mỗi cổng phát hành.

## A. Quyết định của chủ dự án

| # | Quyết định | Ảnh hưởng tới | Trạng thái |
| --- | --- | --- | --- |
| Q1 | Bot tự gửi hay chỉ soạn nháp — mặc định: nháp, mở tự động theo nhóm khi đạt chuẩn | D3-05 | Đề xuất |
| Q2 | Nhà cung cấp tổng đài — mặc định: adapter chung, bản đầu Stringee | D2-01, D2-02 | Đã chốt: adapter chung |
| Q3 | Mô hình AI và dịch vụ chuyển giọng nói | D2-04, D3-03 | Mô hình AI: Claude; STT: chờ D3-03 |
| Q4 | Quy tắc gán Lead cho sale (theo sản phẩm/khu vực/vòng tròn) | D1-06 (Assignment Rule của CRM) | Chưa chốt |
| Q5 | Bảng điểm Lead | D2-06 (`mau/bang-diem-lead-mau.csv`) | Chưa chốt |
| Q6 | Giờ bot hoạt động, câu trả lời ngoài giờ | D3-02 | Chưa chốt |
| Q7 | Thời hạn lưu dữ liệu, ghi âm; ai được xem | D1-02, D1-10, D3-04 | Chưa chốt — mặc định 180 ngày payload gốc, 365 ngày ghi âm |
| Q8 | Kênh báo cho sale khi chuyển người | D3-02 | Chưa chốt — mặc định thông báo hệ thống + email |
| Q9 | Có tạo Lead khi khách chỉ bấm quan tâm OA? | D1-08 | Mặc định: không |
| Q10 | Lead đã chuyển thành Deal: ghi tin mới vào Deal | D1-05, D1-06 | Mặc định: có |
| Q11 | Chính sách gửi dữ liệu hội thoại khách sang dịch vụ AI (Claude API) và dịch vụ chuyển giọng nói: được phép không, phạm vi, thông báo cho khách | D2-04, D2-05, D2-07, D2-08, D3-03 | **Chưa chốt — chặn mọi lần chạy AI trên dữ liệu khách thật** |

## B. Giả định cần xác nhận với hệ thống thật

Agent của prompt ghi trong cột "Ai xác nhận" phải kiểm tra rồi chuyển dòng sang mục C.

| # | Giả định | Nguồn cần đọc | Ai xác nhận |
| --- | --- | --- | --- |
| G1 | Tên và kiểu trường của `CRM Lead`, `CRM Call Log`, `CRM Task`, `CRM Note`, danh sách `CRM Lead Status` | `apps/crm/crm/fcrm/doctype/*/*.json` | D1-02 |
| G2 | Cách CRM hiển thị hoạt động trên Lead (email, bình luận, cuộc gọi…) và có hiện Communication không phải email không | `apps/crm/crm/api/activities.py` | D1-06 |
| G3 | Cách CRM tạo Lead từ email và cách tắt cho hộp thư bán hàng | Email Account, hooks của `crm` | D1-07 |
| G4 | `frappe.enqueue(job_id=..., deduplicate=True)` hoạt động trên bản v15 đang cài | `frappe/utils/background_jobs.py` | D1-03 |
| G5 | Cú pháp khóa Redis qua `frappe.cache` trên v15 | `frappe/utils/redis_wrapper.py` | D1-04 |
| G6 | Cấu hình hàng đợi riêng `workers` trong `common_site_config.json` | Tài liệu bench v15, `frappe/utils/background_jobs.py` | D1-04 |
| G7 | Công thức chữ ký webhook Zalo, tên sự kiện, cấu trúc payload | developers.zalo.me | D1-08 |
| G8 | Endpoint và quy tắc làm mới token Zalo (refresh token dùng một lần) | developers.zalo.me | D1-08 |
| G9 | Khung thời gian và phí tin tư vấn Zalo (48 giờ / 7 ngày), ZBS Template từ 2026 | developers.zalo.me, hợp đồng OA | D1-08 |
| G10 | Định dạng phản hồi tra cứu khách, payload sự kiện, chữ ký (nếu có) của Stringee | developer.stringee.com | D2-02 |
| G11 | Assignment Rule và SLA của CRM áp dụng được cho Lead tạo bằng code | Tài liệu và mã `crm` | D1-06 |
| G12 | Cách gọi Messages API, tool use, prompt caching, tên mô hình hiện hành | docs.claude.com | D2-04 |
| G13 | Thư viện `phonenumbers` có sẵn trong môi trường bench | `pip show phonenumbers` | D1-05 |

## C. Đã xác nhận

| # | Nội dung đã xác nhận | Bằng chứng (tệp/dòng, link tài liệu) | Ngày | Người/agent |
| --- | --- | --- | --- | --- |
| | | | | |

## D. Phát hiện ngoài phạm vi

| Ngày | Phát hiện | Mức độ | Đề xuất | Prompt phát hiện |
| --- | --- | --- | --- | --- |
| | | | | |
