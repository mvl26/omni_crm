# ADR 0018 — Trợ lý AI nội bộ trên trang Lead (V3-05)

- Trạng thái: Chấp nhận (2026-11-06)
- Người quyết định: anh Việt (brief "Bot trợ lý nội bộ Claude trong Omni CRM", 06/11/2026), hai điểm quyền chốt trong phiên làm việc cùng ngày.

## Bối cảnh

Nhân viên muốn gõ một câu tiếng Việt ("ghi log cuộc gọi ngày 6/11/2026 cho Bùi Hoàng Việt") để bot thao tác trên CRM thay vì tự bấm form. CRM hút nội dung từ ngoài (Zalo, email, form Meta), nên bot đọc dữ liệu có thể chứa câu lệnh chèn sẵn.

## Quyết định

1. Module `omni_crm/assistant/`: `api.py` (whitelist thường: `tab_state`, `start_session`, `chat`, `approve`, `reject`, `close_session`), `loop.py` (vòng tool-use đồng bộ, tối đa 10 vòng, 60 giây), `tools.py` (7 tool cố định trong code), `context.py` (ngữ cảnh + bọc dữ liệu khách).
2. **Hai DocType mới** (ngoài danh sách docs/02, được brief duyệt):
   - `Omni Assistant Session`: user, lead, allowed_leads (JSON), messages (JSON), status, write_count, turn_count, last_turn_at.
   - `Omni Assistant Action`: session, tool_name, command (câu lệnh gốc), arguments, result, status (Success/Denied/Error/Pending/Rejected/Limit), requires_approval, approved_by, approved_at, reported, target_doctype, target_name. Vừa là nhật ký kiểm toán vừa là hàng đợi duyệt.
3. Sửa DocType có sẵn đúng 2 chỗ: Omni Settings thêm `assistant_enabled` (mặc định 0), `assistant_model`, `assistant_daily_token_limit` (300000), `assistant_max_writes_per_turn` (5); Omni Bot Run thêm giá trị `Assistant` cho `kind`.
4. **Quyền (chốt 2026-11-06):**
   - Bản ghi kiểm toán (Session, Action, Bot Run) chỉ hệ thống ghi qua `tools._sys_insert` / `_sys_update` (chỉ nhận 2 doctype này). Sales User / Sales Manager chỉ ĐỌC bản ghi của chính mình (`if_owner`), nên không tự đánh dấu "đã duyệt" được qua `/api/resource`.
   - Ghi nhật ký chăm sóc theo đúng nút "Ghi nhật ký chăm sóc" có sẵn: điều kiện là người dùng có quyền SỬA Lead (`has_permission("CRM Lead", "write")` dưới phiên user), phần ghi dùng chung `crm_bridge.care_log` (Sales User chỉ có quyền đọc Omni Care Log ở mức DocType).
   - CRM Task và CRM Lead ghi bằng quyền của chính người dùng (không `ignore_permissions`).
   - Tool đọc dùng `frappe.get_list` / `has_permission(..., "read")`.
5. Chống chèn lệnh: system prompt là hằng số; ngữ cảnh Lead và mọi kết quả tool đọc dữ liệu CRM nằm trong `<du_lieu_khach_hang>`; chuỗi bên ngoài được NFKC, xóa mọi biến thể thẻ dữ liệu rồi thoát `< >`. Tool ghi chỉ nhận Lead trong `allowed_leads` (Lead đang mở đã kiểm quyền đọc, hoặc do `find_lead`/`search_leads` trả về trong phiên); ngoài tập thì Denied + Error Log.
6. Cổng Q11 giữ nguyên như bot phân tích: client thật chỉ gọi khi `omni_ai_allow_customer_data` bật; SĐT/email chỉ gửi nguyên vẹn khi `omni_ai_unmask_pii` bật (không thì chỉ 3 ký tự cuối).

7. **Bổ sung 2026-11-06 (anh Việt yêu cầu, thay mục "không lưu lịch sử" của brief):** lịch sử trò chuyện được lưu ở `Omni Assistant Session.transcript` (câu lệnh, câu trả lời, thao tác; tối đa 400 dòng). Mở lại tab Trợ lý trên một Lead thì nạp lại phiên gần nhất của CHÍNH người dùng trên Lead đó (người khác không thấy), trạng thái thẻ duyệt đọc lại từ Omni Assistant Action. Nút **Cuộc trò chuyện mới** mở phiên mới, phiên cũ vẫn giữ. Phiên mở lại giữ nguyên `allowed_leads` của chính phiên đó (quy tắc 6 không đổi). Khung chat rộng hơn, ô nhập tối thiểu 4 dòng, cao theo nội dung tới 40% màn hình.
8. **Bổ sung 2026-11-06 (anh Việt yêu cầu):** tool thứ 8 `create_call_log`: ghi cuộc gọi tay vào **CRM Call Log** (tab Cuộc gọi của Lead trong Frappe CRM, `telephony_medium = Manual`, như nút tạo cuộc gọi của CRM), có nội dung thì kèm **FCRM Note** gắn vào cuộc gọi; quyền tạo của chính người dùng, Lead phải trong phiên và sửa được. Vẫn KHÔNG ghi vào Omni Call (dữ liệu tổng đài). Link trong khung chat không mở giao diện quản trị `/app` nữa: Cuộc gọi, Ghi chú, Việc chuyển sang tab tương ứng của trang Lead; nhật ký chăm sóc (CRM không có tab) hiện nội dung ngay trong dòng.

## Rủi ro

- Mô hình thật có thể vẫn "nghe" câu lệnh trong dữ liệu; lớp chặn là backend (tập Lead của phiên, quyền user, duyệt tay khi sửa Lead), không phải lời nhắc.
- Tổng token của trợ lý cũng cộng vào bộ đếm chung `daily_token_budget` của bot (dùng nhiều trợ lý có thể làm bot phân tích dừng sớm trong ngày).
- Vòng lặp chạy trong request: nginx/gunicorn trên site miyano cho 120 giây; vòng lặp tự dừng ở 60 giây.

## Hệ quả

- Tắt hẳn: bỏ tích `assistant_enabled` (tab ẩn, API từ chối). Gỡ giao diện: ADR 0016 mục 13.
