# D1-15 ★ · Lead từ Meta, Google Ads, form website và báo cáo chiến dịch

**Phụ thuộc:** D1-12 (khung Tổng quan để thêm khối chiến dịch), D1-13 (gắn nhà thầu). Chạy trước D1-11. **Thời lượng dự kiến:** 3 ngày.
**Cần anh cung cấp trước:** quyền quản trị ứng dụng Meta và Page công ty, tài khoản Google Ads có biểu mẫu khách hàng tiềm năng, danh sách biểu mẫu và câu hỏi dự kiến của chiến dịch (nhập vào site, không gửi khóa trong chat).

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/15-nguon-lead-quang-cao.md (toàn bộ — đặc tả chính), docs/03 mục 1–3, docs/14 mục 2, 6, docs/13 mục 1.

Nhiệm vụ: nhận Lead từ Meta Lead Ads, Google Ads lead form, form website; quy kết chiến dịch; phân loại, giao sale, đo thời gian phản hồi; khối báo cáo chiến dịch.

Bước 0 — G15, G16: đọc tài liệu chính thức hiện hành (WebFetch): Meta Webhooks (handshake, X-Hub-Signature-256), Lead Ads (webhooks, retrieving, quyền), Google Ads lead form webhook (định dạng, google_key, mã phản hồi) và khả năng tải lại lead qua Google Ads API. Đọc mã Frappe v15/app crm xem có DocType chiến dịch/UTM sẵn không. Ghi tất cả vào docs/12 mục C kèm link; khác biệt với docs/15 → sửa theo tài liệu chính thức, ghi ADR nếu đổi hành vi.

Việc cần làm:
1. DocType `Omni Campaign`, `Omni Lead Form` (+ bảng con `Omni Lead Form Field`); custom field CRM Lead ở docs/15 mục 6 (fixtures); CRM Lead Source cho từng nền tảng.
2. Adapter `channels/meta_leads/`, `channels/google_leads/`, `channels/web_form/` theo `ChannelAdapter` (không có chiều ra) + endpoint trong `api/webhooks.py`: `meta_leads` (GET handshake, POST sự kiện), `google_leads`, `web_form`. Cổng chỉ lưu; worker xử lý. Google: trả `{}` mã 200 khi đã lưu, 4xx khi sai khóa, 5xx khi lỗi tạm thời.
3. Worker `form_lead`: Meta → gọi Graph API lấy `field_data` (+ `custom_disclaimer_responses`) và thông tin quảng cáo (cache theo ngày); áp `field_map`; định danh theo docs/15 mục 7 bước 1; tạo/cập nhật CRM Lead với trường chiến dịch (first/last touch); ghi dòng thời gian "Điền biểu mẫu <tên> từ <nền tảng>" kèm mọi câu trả lời.
4. Phân loại và giao sale (docs/15 mục 7, bước 2–3); `omni_first_response_at` cập nhật từ tin ra đầu tiên, cuộc gọi đi nghe máy, nhật ký chăm sóc đầu tiên; nhắc quá hạn theo mục 7 bước 4 (dùng `utils/business_hours.py` đã có từ D1-12).
5. Đối soát Meta mỗi giờ (docs/15 mục 2); lệnh nhập CSV lead Google tải từ Google Ads (chống trùng theo lead_id).
6. Form website: endpoint + chống spam (docs/15 mục 4); tệp `public/js/omni_utm.js` (lưu UTM/click id từ lần truy cập đầu, gắn vào form) và hướng dẫn `docs/huong-dan/gan-form-website.md` để đội web sửa LeadForm gửi về endpoint.
7. Email xác nhận tự động theo biểu mẫu (tắt mặc định; chỉ khi có email và đồng ý; tôn trọng sandbox_mode).
8. Dashboard (docs/15 mục 9): khối "Theo chiến dịch" trên Tổng quan; trường `spend` nhập tay trên Omni Campaign; lọc theo kỳ; Lead thử nghiệm không tính.
9. Công tắc: mỗi Omni Lead Form có `enabled`; mỗi kênh mới có Omni Channel như các kênh khác (thêm Meta Lead Ads / Google Lead Form / Web Form vào `Omni Channel.channel_type` và `default_lead_source_map`).
10. Bổ sung cho Lead đã nhập ở D1-14 (trước khi có trường chiến dịch): patch chạy lại được đặt `omni_ad_platform = "Danh sách mua sắm công"` và gắn Omni Campaign "Liên hệ chủ động – <tháng nhập>" (docs/15 mục 8); importer D1-14 từ nay tự điền hai trường này.

Test: toàn bộ ca ở docs/15 mục 10 (dùng payload mẫu theo tài liệu chính thức trong `tests/fixtures/meta_leads/`, `google_leads/`; mock Graph API, không gọi thật).

Thử thật trên staging (nếu đã có quyền): dùng công cụ thử Lead Ads của Meta và nút gửi dữ liệu thử của Google Ads → Lead xuất hiện đúng chiến dịch trong vòng 1 phút. Dán bằng chứng hoặc ghi "chưa thử thật".

Hoàn thành khi: test xanh; docs/12 có G15, G16; hướng dẫn gắn form website đã viết.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Gửi lead thử từ Meta và Google: Lead có đúng chiến dịch, nguồn, các câu trả lời, người được giao nhận thông báo.
2. Gửi lại cùng lead thử lần hai: không có Lead thứ hai.
3. Để một Lead quá 15 phút trong giờ làm việc: hiện "Quá hạn phản hồi" trên Tổng quan.
