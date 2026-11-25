# ADR 0032 — Bot kịch bản v3: kho ô kịch bản, tin hẹn giờ, người nhận theo dịch vụ

Ngày: 2026-11-17 · Trạng thái: Chấp nhận (người dùng duyệt 17/11) · Liên quan: `docs/bot_v3_contract.md`, `docs/bot_v3_plan.md` mục 3, 5–6, `docs/kich_ban_bot_miyano_v3.md`

## Bối cảnh

Kịch bản v3 là hội thoại có nút bấm (menu, nhánh dịch vụ, thu thông tin, đặt demo, nhắc hẹn). Bot hiện tại chỉ soạn
nháp/tự gửi câu Kho tri thức theo nhóm câu hỏi, không có chỗ lưu câu chữ theo ô kịch bản, không có nút, không có tin hẹn
giờ, không biết giao việc cho ai theo dịch vụ. Danh sách DocType chuẩn (`docs/02`) không đủ.

## Quyết định

Thêm 4 DocType (module Omni CRM):

1. `Omni Script Node` + bảng con `Omni Script Button`: mỗi ô kịch bản có mã (khớp mã nút `#CODE`), nội dung có biến,
   tối đa 5 nút (nhãn ≤ 20 ký tự → mã ô đích), trạng thái `Đang dùng / Cần duyệt / Tắt`. Truyền thông sửa câu chữ trên
   màn hình, không cần sửa code; code không chứa câu gửi khách.
2. `Omni Scheduled Message`: tin hẹn giờ (nhắc tài liệu, báo giá, demo, im lặng 3 ngày, hỏi thăm 3 tháng) có khóa chống
   trùng `kind:lead:mốc`, chạy bởi lịch 15 phút.
3. `Omni Service Recipient` (bảng con trong Omni Settings, trường `script_recipients`): người nhận chính/thay theo dịch vụ.

Trường mới trên Omni Settings, Omni Identity, Omni Outbound Message và CRM Lead thêm bằng Custom Field (fixtures), không
sửa JSON DocType gốc (tránh xung đột với thay đổi đang dở trên máy chủ).

An toàn: `script_bot_enabled` mặc định 0, bot kịch bản chỉ chạy cho `script_test_identities`; ô `Cần duyệt` chỉ gửi cho
danh sách thử; ô trống hoặc `Tắt` không gửi. Tin kịch bản đi qua `queue_outbound` (nguồn `Bot`, chọn loại tin Zalo như
mọi tin khác) và bị kiểm lại công tắc ngay trước khi gửi.

## Hệ quả

- Tách bạch kịch bản (câu cố định, nút) và Kho tri thức (câu trả lời kiến thức).
- Thêm một nhánh trong `bot/auto_send` cho tin Bot có `script_node` (không có nháp).
- Không có "spam" riêng: khách bị coi là spam khi CRM Lead ở trạng thái `Junk`.
