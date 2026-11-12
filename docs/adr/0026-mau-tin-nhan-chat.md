# 0026 · Mẫu tin nhắn chat cho sale (tab Messenger)

- Ngày: 2026-11-12
- Trạng thái: Đã chấp nhận (người phụ trách duyệt 12/11/2026: A1, B1, C1, D1)
- Prompt: không có prompt kit; yêu cầu trực tiếp của người phụ trách 12/11/2026 ("tôi cũng muốn các template gửi Khách hàng như của email")
- Số: 0024 và 0025 để dành cho hai ADR email đang chờ đổi số (do-tre-thap-cho-email, thu-gui-tu-crm-noi-luong-dung-hop-thu); thống nhất với phiên làm phần email 12/11/2026
- Dựa trên: ADR 0023 (tab Messenger), `crm_bridge/email_templates.py` (mẫu thư email dựng sẵn, V3), quy tắc trả lời khách của bot (`bot/prompts/reply_v2.md`)

## Bối cảnh

- Sale trả lời Messenger ngay trong tab Messenger của trang Lead/Deal (ADR 0023, chạy trên site `miyano` từ 12/11/2026). Gửi thư có nút chọn mẫu thư (Email Template của Frappe, `crm_bridge.email_templates`), còn chat chưa có: sale gõ tay các câu lặp lại (chào, xác nhận đã nhận yêu cầu báo giá, xin thông tin).
- Email Template của Frappe hợp với thư (tiêu đề, HTML) hơn chat (chữ thuần, ngắn). Giao diện CRM điền mẫu thư từ trường `response` và lọc theo `reference_doctype`.
- Jinja của Frappe (`frappe.utils.jinja.get_jenv`) là môi trường sandbox; biến chưa khai báo hiện nguyên chữ (`DebugUndefined`); `render_template` đoán chuỗi một dòng có đuôi `.txt`, `.md`... là đường dẫn tệp.
- Frappe lọc XSS các trường chữ khi giá trị trông như HTML (có thẻ): `A & <b>` bị đổi thành `&amp;`... trừ khi trường có `ignore_xss_filter`.

## Các lựa chọn

### A. Nơi lưu mẫu
1. **DocType riêng `Omni Chat Template`** (tên, đang dùng, kênh, thứ tự, dùng khi, nội dung chữ). Ưu: tách khỏi mẫu thư, không lẫn vào nút mẫu thư của Email; trường đúng nhu cầu chat. Nhược: thêm một DocType.
2. Dùng chung Email Template + ô "dùng cho chat". Ưu: một chỗ quản lý. Nhược: tiêu đề/HTML thừa, phải lọc hai chiều.

### B. Tab có nút chọn mẫu
1. **Chỉ tab Messenger.** Không sửa `ZaloTab.vue` (mã V3 của người phụ trách tab Zalo). Trường `channel_type` để sẵn cho Zalo dùng sau.
2. Cả tab Messenger và tab Zalo.

### C. Chọn mẫu thì làm gì
1. **Điền chữ đã thay biến vào ô soạn; sale sửa rồi tự bấm gửi.** Không bao giờ tự gửi.
2. Như 1, kèm tệp đính sẵn trong mẫu.

### D. Mẫu khởi đầu
1. **Soạn sẵn 5 mẫu nháp để người phụ trách duyệt chữ trước khi cài** trên máy thật.
2. Không soạn; quản lý tự nhập.

## Quyết định

A1, B1, C1, D1 (người phụ trách duyệt 12/11/2026).

1. **DocType `Omni Chat Template`** (module Omni CRM, autoname theo `template_name`): `template_name` (duy nhất), `enabled`, `channel_type` (trống = mọi kênh chat; `Meta Messenger`; `Zalo OA`), `sort_order`, `usage_note`, `content` (Text, `ignore_xss_filter` vì là chữ thuần gửi qua chat, không hiển thị dạng HTML ở đâu). Quyền: Omni Admin và Sales Manager đọc/tạo/sửa/xóa; Sales User chỉ đọc. Không tạo vai trò mới.
2. **Dựng mẫu** (`crm_bridge/chat_templates.py`): Jinja sandbox của Frappe gọi thẳng `from_string` (không qua `render_template` để tránh đoán nhầm là đường dẫn tệp), chặn `.__`. Biến điền sẵn: `ten_goi` (chữ cuối của họ tên, ví dụ "Minh"), `ten_khach` (họ tên; rỗng nếu là tên tạm "Khách …" hoặc email, như mẫu thư), `cong_ty` (`organization` của Lead), `ten_sale`, `sdt_sale` (người đang soạn), `lead` (mọi trường Lead, trống thành ""). Biến lạ (gõ sai) bị từ chối. Sau khi dựng: bỏ khoảng trắng thừa và khoảng trắng trước dấu câu do biến trống để lại.
3. **Kiểm lúc lưu** (controller): rỗng, dài quá 2000 ký tự (một tin Messenger), sai cú pháp, biến lạ, lỗi khi điền với dữ liệu mẫu đầy đủ và dữ liệu rỗng thì không cho lưu, báo rõ lý do.
4. **API** `omni_crm.api.messenger_tab.get_templates(doctype, name)` (chỉ đọc): quyền đọc trang đang mở VÀ Lead gốc; tab tắt hoặc không có Lead gốc trả danh sách rỗng. Trả các mẫu đang dùng của kênh Messenger và mẫu để trống kênh, theo `sort_order` rồi tên, đã điền cho Lead và người đang soạn. Mẫu lỗi (ví dụ sửa thẳng DB) vẫn có trong danh sách với `error`, chữ rỗng, không làm hỏng các mẫu khác và không bật hộp thông báo.
5. **Giao diện** (patch crm, chỉ 4 tệp Messenger đã có): nút **Mẫu tin** cạnh nút 📎 khi gửi được; danh sách tải khi mở lần đầu; tìm theo tên/gợi ý/nội dung (không phân biệt dấu) khi nhiều hơn 5 mẫu; mẫu lỗi hiện mờ kèm lý do; chọn mẫu điền vào ô soạn (ô đang có chữ thì hỏi trước khi thay). Không `v-html`.
6. **Mẫu khởi đầu** (`chat_templates.install`, chạy tay, chỉ tạo mẫu chưa có theo tên): Chào hỏi, Đã nhận yêu cầu báo giá, Xin thông tin để báo giá, Hẹn gọi lại, Cảm ơn. Cùng quy tắc với mẫu thư và bot: không nêu giá, chiết khấu, điều khoản, cam kết thời hạn; gọi khách "anh/chị"; không biểu tượng cảm xúc; sale xưng "em" vì tin đi dưới tên người đang soạn. **Chữ là bản nháp, người phụ trách duyệt trước khi chạy `install` trên máy thật.**

## Hệ quả

- Cần `migrate` (DocType mới) và build lại giao diện (patch crm đổi). Không có công tắc mới: ẩn cả nút bằng `messenger_tab_disabled` (ẩn cả tab), tắt từng mẫu bằng ô "Đang dùng".
- Gửi tin vẫn qua `send` của tab với mọi guard cũ (24 giờ, từ chối liên hệ, sandbox, quyền ghi Lead); mẫu không mở thêm đường gửi nào.
- Chữ có dạng thẻ HTML (ví dụ `<tên>`) trong tin sale gửi vẫn bị bộ lọc XSS của Frappe đổi khi lưu `Omni Outbound Message` (hành vi có sẵn, không do ADR này); mẫu nên tránh dấu `<…>`.
- Tab Zalo dùng lại được danh mục này sau (trường `channel_type` có `Zalo OA`), khi người phụ trách tab Zalo đồng ý sửa `ZaloTab.vue`.
- Tài liệu: docs/02 (DocType, quyền), docs/09 (mục 1.1), docs/16 (mục Messenger), README của `crm_frontend_patch`.
