# V2-05 ★ · Lead từ Meta, Google, form website và báo cáo chiến dịch

**Phụ thuộc:** V2-04. **Thời lượng dự kiến:** 3 ngày.
**Cần anh cung cấp:** ứng dụng Meta (App ID, App Secret, Page access token có quyền `leads_retrieval`), quyền Google Ads tạo biểu mẫu, câu hỏi trên form quảng cáo (V2-Q15). Thiếu thứ nào thì agent làm phần còn lại và dùng payload mẫu cho phần thiếu.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/15-nguon-lead-quang-cao.md (toàn bộ), docs/14 mục 2, 5, 6, docs/v2/gap-report.md (các dòng mảng B), và prompt gốc prompts/dot-1/15-lead-quang-cao.md.

Nhiệm vụ: thực hiện TOÀN BỘ prompts/dot-1/15-lead-quang-cao.md (bước 0 và mục 1–10, kèm toàn bộ test ở docs/15 mục 10), với điều chỉnh:

1. Bước 0 (đọc tài liệu chính thức Meta/Google): nếu docs/12 mục C của repo đã có G15/G16 từ v1, chỉ kiểm tra lại link còn hiệu lực và ghi ngày kiểm tra; tài liệu đổi → cập nhật docs/12 và báo.
2. Kênh mới dùng lại hạ tầng v1: cổng tiếp nhận, `dedupe_key`, `identity_key`, hàng đợi `omni`, thử lại, tác vụ quét, giám sát (docs/11). Không viết đường đi riêng. Thêm cảnh báo giám sát: "Không có lead quảng cáo nào trong N giờ trong khi chiến dịch đang chạy" (N trong Omni Settings, mặc định 6, chỉ trong giờ làm việc).
3. Định danh lead quảng cáo đi qua resolver đã mở rộng ở V2-02: form có tên công ty/mã số thuế → gắn Organization theo docs/14 mục 2; người điền đã có Contact → không tạo Contact mới.
4. Lead quảng cáo của nhà thầu đã có Lead đang mở (từ danh sách V2-03 hoặc tin nhắn v1): KHÔNG tạo Lead mới; cập nhật `omni_last_touch_*`, giữ `omni_first_touch_*` và người phụ trách; ghi hoạt động "Điền biểu mẫu …"; báo người phụ trách.
5. Đồng ý nhận liên hệ từ form (`custom_disclaimer_responses` của Meta, câu đồng ý của Google/web form) → ghi `omni_consent`, `omni_consent_date`, `omni_consent_method` trên Contact. Không có đồng ý → không gửi email xác nhận tự động.
6. Mục 10 (điền ngược cho Lead nhập ở V2-03): chạy dry run rồi chạy thật trên {{SITE_STAGING}}; importer V2-03 từ nay tự điền hai trường chiến dịch.
7. Lead thử (`is_test` của Google, lead công cụ thử của Meta): ghi nhận và hiển thị có nhãn "Thử", không tính vào báo cáo, không giao sale, không gửi email.

Thử trên {{SITE_STAGING}}: gửi lead thử bằng Meta Lead Ads Testing Tool và nút gửi dữ liệu thử của Google Ads (nếu đã có quyền); không có quyền thì dùng payload mẫu ký đúng và ghi rõ "chưa thử với nền tảng thật".

Hoàn thành khi: mọi tiêu chí "Hoàn thành khi" của prompts/dot-1/15 đạt; mọi test ở docs/15 mục 10 xanh; công tắc tắt từng biểu mẫu và từng kênh không lỗi.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4, thêm bảng "Đã thử với nền tảng thật / chỉ payload mẫu" cho Meta, Google, Web form.
```

## Người duyệt kiểm tra

1. Gửi lead thử từ Meta và Google: Lead đúng chiến dịch, nguồn, câu trả lời; sale được giao nhận thông báo.
2. Gửi lại cùng lead: không có Lead thứ hai.
3. Điền form Meta bằng SĐT của một Contact đã nhập ở V2-03: không có Lead mới, Lead cũ có thêm hoạt động.
4. Để một Lead quá 15 phút trong giờ làm việc: hiện "Quá hạn phản hồi".
