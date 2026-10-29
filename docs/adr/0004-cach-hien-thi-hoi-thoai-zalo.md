# 0004 · Cách hiển thị hội thoại Zalo (và đa kênh) cho sale

- Ngày: 2026-10-29
- Trạng thái: Đã chấp nhận
- Prompt: D1-06

## Bối cảnh
Zalo (và sau này tổng đài) không có chỗ riêng trong giao diện CRM. `docs/06` mục 8 nêu ba cách: A (mỗi tin là `Communication` loại khác Email), B (mỗi tin là `Comment`), C (trang Hộp thư riêng, kèm A hoặc B).

Đã kiểm tra mã và thực nghiệm trên `omni.test` (docs/12 mục C, G2):
- `crm/api/activities.py::get_lead_activities` / `get_deal_activities` lấy Communication qua `frappe.desk.form.load.get_docinfo` -> `get_communication_data`. Hàm này chỉ lọc `communication_type IN ('Communication','Feedback','Automated Message')` và `reference_doctype/reference_name` (hoặc bảng `Communication Link`); **không lọc theo `communication_medium`**.
- Thực nghiệm: một `Communication` có `communication_medium = "Chat"` tham chiếu một CRM Lead được `get_activities(lead)` trả về với `activity_type = "communication"`, có `subject`, `content`, `sender_full_name`. Hook của CRM trên Communication (`on_communication_insert/update`) không gây lỗi; `on_update` cập nhật `communication_status` của Lead thành `Open` (đúng ý: có khách nhắn).
- Giới hạn của CRM/Frappe: `get_docinfo` chỉ nạp 21 Communication gần nhất (`_get_communications(limit=21)`). Hội thoại dài chỉ hiện đoạn cuối trong tab hoạt động của CRM; toàn bộ hội thoại xem ở Hộp thư Omni.

## Các lựa chọn
1. A: Communication `Chat` — hiện được trong hoạt động của CRM (đã chứng minh), phân biệt được tin vào/ra (`sent_or_received`), không lẫn bình luận nội bộ.
2. B: Comment — chắc chắn hiện nhưng lẫn bình luận nội bộ của sale, không có hướng vào/ra, và `on_comment_insert` của CRM có thể enqueue cập nhật `modified`.
3. C: Hộp thư Omni — cần thêm (đọc cả hội thoại đa kênh, lọc, realtime, chỗ cho khung trả lời D1-09).

## Quyết định
**C + A.**
- Mỗi tin Zalo vào/ra là một `Communication` (`communication_medium = "Chat"`, `communication_type = "Communication"`), tham chiếu Lead (hoặc Deal nếu Lead đã chuyển, Q10). Khóa chống ghi trùng: Custom Field `omni_inbound_message` / `omni_outbound_message` (Link, ẩn, có chỉ mục) **chỉ trên Communication** (không tạo trên Comment vì không dùng Comment cho tin nhắn).
- Email không tạo bản sao: tin email đã là Communication, chỉ trỏ lại `reference_doctype/name` về Lead/Deal và ghi `omni_inbound_message`.
- Sự kiện hiện trên dòng thời gian: `message` và `oa_echo` (nhân viên trả lời ngoài hệ thống, ghi là tin ra). `follow`, `unfollow`, `user_info`... không hiện.
- Nội dung được thoát HTML; đính kèm chỉ thành liên kết khi URL bắt đầu `http://`/`https://`.
- Liên kết ngược Lead -> Hộp thư: **một Comment hệ thống** (tạo một lần khi tạo Lead) chứa liên kết `/app/omni-inbox?lead=<tên Lead>`. Đã cân nhắc custom field Data hiển thị đường dẫn: giao diện CRM (SPA) chỉ hiện trường nằm trong Fields Layout của CRM nên không chắc hiển thị, trong khi Comment luôn hiện trong tab hoạt động. Lead tạo tay (không qua Omni) không có liên kết này.
- Hộp thư là Page Desk `omni-inbox` (module Omni CRM), vai trò Sales User/Sales Manager/Omni Admin/System Manager. Chưa có khung trả lời (D1-09).
- Realtime: sự kiện `omni_inbox_update` **không chứa nội dung tin**, chỉ `{lead, channels}`; gửi tới phòng của Lead (Frappe chỉ cho người đọc được Lead đăng ký phòng) và tới người phụ trách/được giao. Client nhận rồi gọi API có kiểm quyền để tải lại. Có thêm thăm dò 60 giây khi mất realtime.

## Hệ quả
- `omni_crm.crm_bridge.timeline.append(target, msgs)` nhận tên Lead **hoặc** Deal; worker truyền `res.deal or res.lead`. `append_outbound(target, outbound)` cho D1-09 gọi (kênh Email: không làm gì).
- Tin cũ ở lại Lead khi Lead chuyển thành Deal; chỉ tin mới ghi vào Deal. Tab hoạt động của Deal không hiện tin cũ của Lead (người dùng xem toàn bộ ở Hộp thư).
- Ghi Communication bằng `insert()` nên hook CRM chạy (`communication_status`); nhánh email dùng `db.set_value` nên không chạy hook (D1-07 cân nhắc).
- Gán người phụ trách (G11): Assignment Rule của Frappe áp dụng được cho Lead tạo từ job nền nên **không cần** `assign_lead` chia vòng tròn tự viết; `Omni Settings.assignment_fallback_users` hiện không dùng. Cần chốt Q4 và tạo Assignment Rule trên CRM Lead (Desk > Assignment Rule) trước khi bật cho khách thật.
