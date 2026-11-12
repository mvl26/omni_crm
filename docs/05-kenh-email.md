# 05 · Kênh email (Đợt 1)

Email là kênh dễ nhất vì Frappe đã có sẵn việc nhận thư (Email Account) và CRM đã có sẵn việc hiển thị, trả lời thư trên Lead. Việc của `omni_crm`: **chặn việc tạo Lead trùng**, đưa thư vào chung đường ống để chống trùng khách và cho bot đọc.

## 1. Cấu hình

- Một `Email Account` cho hộp thư bán hàng (ví dụ `sales@…`), bật nhận thư.
- Một `Omni Channel` loại Email trỏ tới Email Account đó.
- **Tắt cơ chế tự tạo Lead từ email của app `crm`** cho hộp thư này, nếu có (tài liệu Frappe CRM nói thư mới với tiêu đề mới sẽ tạo Lead). Agent phải đọc mã `crm` và cấu hình Email Account để xác định cơ chế (thường là `append_to` của Email Account) rồi ghi lại cách tắt. Nếu không tắt được bằng cấu hình: để CRM tạo, và `omni_crm` coi Lead do CRM tạo là **Lead ứng viên**, chạy định danh và gộp ngay nếu khớp chắc. Ghi quyết định vào ADR.

## 2. Luồng vào

Hook `Communication.after_insert` → `channels/email/adapter.py::on_communication_insert(doc)`:

1. Bỏ qua nếu `communication_medium != "Email"` hoặc `sent_or_received != "Received"` hoặc Email Account không thuộc `Omni Channel` nào đang bật.
2. Bỏ qua (lưu với `status = Ignored`) thư tự động: header `Auto-Submitted` khác `no`, `Precedence: bulk/list/junk`, có `List-Unsubscribe`, người gửi thuộc danh sách hệ thống (`docs/04` mục 1.2), thư báo lỗi gửi.
   **Khác đặc tả (V2-01, docs/12 mục D D1-07):** Frappe không lưu header thư vào Communication nên **không đọc được** `Auto-Submitted`, `Precedence`, `List-Unsubscribe`; code nhận diện thư tự động qua người gửi hệ thống và tiêu đề (`test_email_channel::test_all_ignored_fixtures_saved_ignored_without_lead`). Thư tự động lạ vẫn có thể thành Lead. Chấp nhận vì sửa cần đổi Frappe.
3. Dựng `InboundEvent`:
   - `external_message_id` = `Message-ID` của thư (trường `message_id` của Communication); không có thì băm.
   - `external_sender_id` = email người gửi đã chuẩn hóa.
   - `text` = nội dung đã làm sạch (mục 3).
   - `attachments` = các File gắn với Communication (chỉ lưu tham chiếu).
   - Trường `communication` của tin vào = `doc.name` (không chỉ để trong `raw`, vì `raw_payload` bị xóa theo hạn).
   - `raw` = `{"subject": ..., "in_reply_to": ..., "headers": <chỉ các header cần>}`.
4. `gateway.save_inbound(event)`. Không xử lý gì thêm trong hook (hook chạy trong luồng nhận thư của Frappe).

Thư trả lời trong cùng chuỗi: nếu `in_reply_to` trỏ tới một Communication đã gắn Lead → coi là tín hiệu **chắc chắn** về Lead (bổ sung vào `resolver`).

## 3. Làm sạch nội dung

`channels/email/clean.py::clean_body(html_or_text) -> str`

1. HTML → văn bản thuần (dùng tiện ích `html2text` có trong Frappe; xác nhận tên hàm v15).
2. Cắt phần trích dẫn: dòng bắt đầu bằng `>`; khối bắt đầu bằng các mẫu "On … wrote:", "Vào … đã viết:", "-----Original Message-----", "From: … Sent: …".
3. Cắt chữ ký: sau dòng `-- `; hoặc khối cuối bắt đầu bằng "Trân trọng", "Best regards", "Thanks & Regards"… **nhưng trước khi cắt, chạy `extract_contacts` trên chữ ký** để lấy SĐT/email (tín hiệu trung bình).
4. Gộp khoảng trắng; giới hạn 8.000 ký tự cho bot (giữ bản đầy đủ trong Communication).

Test với ít nhất 10 thư mẫu thật (che thông tin cá nhân) từ Gmail, Outlook, điện thoại.

## 4. Dòng thời gian

Thư đã là một `Communication`. Khi worker gắn được Lead: cập nhật tham chiếu của Communication tới Lead (`reference_doctype = "CRM Lead"`, `reference_name = lead`) hoặc thêm `timeline_links` — chọn đúng cách mà CRM dùng để hiện thư trong tab Email của Lead (đọc `crm/api/activities.py`). Không tạo bản sao.

## 5. Luồng ra

- **Sale trả lời**: dùng trình soạn thư có sẵn của CRM trên Lead. `omni_crm` ghi nhận thư đi (Communication `Sent`) để **so với nháp của bot ở chế độ ngầm** (Đợt 2), và trước khi Frappe dựng thư (ADR 0019, `crm_bridge.outgoing_email`): gắn `in_reply_to` vào thư khách gần nhất có Message-ID (thư đi có `In-Reply-To`, nằm đúng luồng ở hộp thư khách); ô FROM không phải hộp thư gửi được (CRM điền địa chỉ khách khi bấm Trả lời, hoặc email đăng nhập) thì đổi về hộp thư khách đã viết tới. Thư đi ngay sau khi bấm gửi (`crm_bridge.fast_email`, ADR 0018).
- **Cấu hình Email Account gửi thư cho khách**: tắt *Track Email Status* (ảnh theo dõi trỏ về tên miền CRM là dấu hiệu thư rác); đặt tên Email Account là tên thương hiệu (vd "Miyano Việt Nam", đổi bằng Rename) và bật **cả hai** *Always use this email address as sender address* và *Always use this name as sender name*. Không bật riêng ô thứ nhất: khi đó Frappe lấy lại tên người gửi qua `parse_addr`, mà `EMAIL_NAME_PATTERN` (frappe/utils/__init__.py) chỉ giữ ký tự U+00C0–U+024F nên xóa mọi chữ có dấu thanh tiếng Việt ("Trần Hồng Mai" thành "Trn Hng Mai"); ô thứ hai đặt tên từ tên Email Account, không đi qua bộ lọc đó.
- **Mẫu thư dựng sẵn cho sale** (`crm_bridge.email_templates`): 5 mẫu cho Lead, 3 cho Deal (Email Template, Rich Text, có chữ thay thế khi Lead thiếu tên/công ty, không nêu phí hay cam kết). Cài: `bench --site <site> execute omni_crm.crm_bridge.email_templates.install` (chỉ tạo mẫu chưa có, không ghi đè mẫu đã sửa); cho Sales Manager tạo/sửa mẫu: `...email_templates.grant_manager_access` (thêm quyền cho vai trò có sẵn, không tạo vai trò). Sale chọn mẫu ở nút mẫu thư khi soạn, xem lại rồi bấm Gửi.
- **Bot trả lời** (Đợt 3, chế độ tự động): `Omni Outbound Message` → adapter email gửi qua `frappe.sendmail` với `reference_doctype/name` là Lead, `in_reply_to` là thư khách gần nhất, cùng Email Account. Kiểm tra lại `sandbox_mode` trước khi gửi.

## 6. Test bắt buộc

- Thư mới từ khách mới → 1 Lead, thư hiện trong tab Email của Lead.
- Thư thứ hai cùng người, khác tiêu đề → cùng Lead (không có Lead do CRM tạo thêm).
- Thư trả lời trong chuỗi → cùng Lead.
- Thư tự động, thư báo lỗi → `Ignored`, không tạo Lead.
- Frappe kéo lại cùng thư (trùng Message-ID) → không có bản ghi thứ hai.
