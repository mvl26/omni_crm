# D1-06 ★ · Cầu nối CRM: Lead, dòng thời gian, hộp thư

**Phụ thuộc:** D1-05. **Thời lượng dự kiến:** 2 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/06-kenh-zalo-oa.md mục 8, docs/05-kenh-email.md mục 4, docs/04 mục 3.2, docs/02 mục 2 (default_lead_source_map), mục 15, mục 16, docs/12 mục A (Q4, Q10).

Nhiệm vụ: hoàn thiện cách tin vào xuất hiện trong CRM: Lead có nguồn và người phụ trách, mọi tin hiện trên dòng thời gian, và một Hộp thư Omni để sale đọc hội thoại đa kênh (CHƯA có khung trả lời — D1-09 thêm).

Bước 0 — xác nhận G2, G11:
- Đọc {{BENCH}}/apps/crm/crm/api/activities.py (và mã frontend liên quan nếu cần) để biết CRM lấy hoạt động của Lead từ đâu: Communication (loại nào), Comment, Call Log, Note, Task, Version…
- Thử thực nghiệm trên {{SITE_TEST}}: tạo Communication với communication_medium khác "Email" (ví dụ "Chat") tham chiếu một Lead và kiểm tra API hoạt động của CRM có trả về không.
- Kiểm tra Assignment Rule và SLA của CRM có áp dụng cho Lead tạo bằng `insert()` từ job nền không (tạo Assignment Rule thử).
- Ghi kết quả vào docs/12 mục C.

Bước 1 — ADR "Cách hiển thị hội thoại Zalo" chọn giữa A/B/C ở docs/06 mục 8, dựa trên bằng chứng bước 0. Mặc định C + A nếu A hiển thị được, nếu không C + B. Ghi ADR "Đã chấp nhận" và làm tiếp (đây là quyết định trong phạm vi module).

Việc cần làm:
1. `crm_bridge/lead.py`: bổ sung `create_lead_for_identity` — đặt nguồn Lead theo `default_lead_source_map` (tạo CRM Lead Source nếu thiếu, qua fixtures hoặc patch), cập nhật `omni_last_inbound_at` mỗi lượt. Không tự gán sale bằng code nếu Assignment Rule của CRM làm được (G11); nếu không, viết `assign_lead(lead)` chia vòng tròn theo `Omni Settings.assignment_fallback_users`, ghi ADR.
2. `crm_bridge/lead.py::track_manual_edits(doc, method)` cho hook `CRM Lead.on_update`: khi người dùng (không phải job hệ thống, không phải `frappe.flags.in_omni_bot`) sửa một trường thuộc danh sách theo dõi (tên, SĐT, email, tổ chức, các trường `omni_*` bot ghi ở Đợt 2), thêm tên trường vào `omni_manual_fields`. Đặt `frappe.flags.in_omni_bot = True` khi code hệ thống ghi Lead. Bật hook trong hooks.py.
3. `crm_bridge/timeline.py::append(target, msgs)` (thay bản tạm): `target` là Lead hoặc Deal (Q10 — Lead đã chuyển thì ghi vào Deal). Theo ADR ở bước 1. Khóa chống ghi trùng theo `Omni Inbound Message.name`: tạo custom field `omni_inbound_message` / `omni_outbound_message` (docs/02 mục 15) trên DocType mà ADR chọn (Communication và/hoặc Comment) qua fixtures, ghi tham chiếu khi tạo, kiểm tra trước khi tạo. Email không tạo bản sao — chỉ cập nhật tham chiếu Communication có sẵn (phần email cụ thể làm ở D1-07; ở đây làm hàm chung có nhánh cho email).
   - Hàm tương ứng cho tin ra: `append_outbound(target, outbound)` (D1-09 sẽ gọi).
4. Hộp thư Omni: một trang Desk `omni-inbox` (Page trong module Omni CRM) hoặc trang tương đương theo ADR:
   - Cột trái: danh sách khách có tin gần đây (theo Omni Identity/Lead), lọc theo kênh, "chưa trả lời" (tin cuối là của khách), người phụ trách = tôi.
   - Cột phải: toàn bộ hội thoại của Lead trên mọi kênh, theo thời gian, ghi rõ kênh, tin vào/ra; tệp đính kèm mở được nếu có quyền; nút "Mở Lead trong CRM".
   - Cập nhật realtime khi có tin mới (`frappe.publish_realtime` từ worker sau khi commit, chỉ gửi tới user có quyền xem Lead đó hoặc tới room chung rồi lọc phía client — chọn cách an toàn, không lộ nội dung cho user không có quyền).
   - API: `api/inbox.py::list_conversations(filters, page)`, `get_thread(lead)` — kiểm tra `frappe.has_permission("CRM Lead", doc=lead)`; Sales User chỉ thấy Lead mình được xem.
   - Giao diện đơn giản, sạch, tiếng Việt. Không cần đẹp cầu kỳ; cần nhanh (≤ 1 giây với 50 hội thoại).
5. Liên kết ngược: trên CRM Lead, thêm cách mở Hộp thư tại đúng hội thoại mà KHÔNG sửa app `crm` (ví dụ một Comment hệ thống đầu tiên có liên kết, hoặc custom field kiểu Data hiển thị đường dẫn, hoặc cơ chế mở rộng mà CRM hỗ trợ — đọc mã CRM để chọn, ghi trong ADR).

Test:
- Lead mới có nguồn đúng theo kênh; Assignment Rule (nếu dùng) gán người.
- Tin vào hiện trong hoạt động của Lead qua chính API hoạt động của CRM (gọi hàm CRM trong test).
- Xử lý lại cùng lượt → không có bản ghi dòng thời gian thứ hai.
- Lead đã chuyển Deal → dòng thời gian ghi vào Deal.
- track_manual_edits: user sửa SĐT → có trong omni_manual_fields; job hệ thống sửa → không.
- `get_thread` với Sales User không có quyền trên Lead → bị từ chối.

Hoàn thành khi: test xanh; ảnh chụp (hoặc mô tả bước) Hộp thư hiển thị hội thoại mẫu có tin Zalo giả và email giả; ADR đã ghi.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Đăng nhập bằng một tài khoản sale: chỉ thấy hội thoại của Lead được phép xem.
2. Mở một Lead có tin giả: thấy tin trong hoạt động (hoặc theo cách ADR mô tả).
3. Sửa tay SĐT của Lead, rồi xem `omni_manual_fields` có tên trường.
