# 0013 · Lead quảng cáo: không email xác nhận tự động và các quyết định nhỏ

- Ngày: 2026-11-02
- Trạng thái: Đề xuất
- Prompt: V2-05

## Bối cảnh
`prompts/dot-1/15-lead-quang-cao.md` mục 7 yêu cầu email cảm ơn tự động cho người điền biểu mẫu (tùy chọn theo biểu mẫu, tắt mặc định). Quy tắc điều phối V2-05: KHÔNG tự gửi tin cho người điền form; chỉ ghi Lead và cảnh báo SLA nội bộ. Đường email xác nhận cũng đi ra ngoài `OUTBOUND_CREATORS` (`tests/test_v2_02_checks.py`) và cần nhà cung cấp gửi thật, đồng ý rõ, sandbox.

## Quyết định (đề xuất)
1. **Không xây** email xác nhận tự động. `Omni Lead Form` không có trường `auto_confirm_email`; ba kênh mới `send()` ném `NotImplementedError`, `can_send_now()` luôn False. Test quét mã nguồn (`test_ads_pipeline::TestGuestAndBotIsolation::test_new_modules_have_no_send_path`) giữ điều này. Bật sau: ADR mới + công tắc + sandbox + chỉ khi có email và `consent_contact`.
2. **Đồng ý chỉ ghi lên Contact đã có** (không tạo Contact mới) để không trùng với `convert_to_deal` của crm; Lead chưa có Contact thì đồng ý nằm trên dòng thời gian.
3. **Tên trường first/last touch**: dùng đúng docs/15 mục 6 (`omni_campaign`, `omni_last_campaign`); không tạo `omni_first_touch_*`.
4. **Không thêm DocType ngoài docs/02**: chỉ `Omni Campaign`, `Omni Lead Form`, `Omni Lead Form Field`; thêm trường vào Omni Channel (khóa Meta, chống bot Web Form), Omni Lead Form (`last_reconciled_at`) và Omni Settings (`ads_silence_hours`, `webform_rate_limit`, `webform_rate_window_minutes`).
5. **Nhắc quá hạn** gửi cho người được giao rồi mọi người có vai trò trưởng nhóm; không dùng SLA của CRM (`sla`), vì đồng hồ giờ làm việc của app đã có.

## Hệ quả
- Anh quyết nếu muốn email xác nhận (kèm nhà cung cấp gửi thư và lời văn); chưa có việc nào trong code phụ thuộc.
- Nếu Miyano có nhiều đội, cần danh sách trưởng nhóm theo đội cho bước 5.
6. **(Review độc lập, siết)** Web Form: MST là tín hiệu yếu, không gắn Lead/Organization có sẵn; không nâng đồng ý của Contact; mã biểu mẫu chưa cấu hình bị bỏ qua (công tắc `webform_accept_unknown_form`); trần toàn kênh theo giờ + cảnh báo. Kênh Meta/Google tắt thì không lưu.

