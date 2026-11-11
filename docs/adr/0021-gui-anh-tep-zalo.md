# 0021 · Sale gửi ảnh / tệp qua Zalo OA từ tab Zalo

Ngày: 2026-11-11 · Trạng thái: Đã chọn (V3-09) · Liên quan: ADR 0016 (tab Zalo), 0019 (gói Tăng trưởng, chọn loại tin), 0020 (hiển thị ảnh)

## Bối cảnh

Người phụ trách yêu cầu sale gửi được ảnh và tệp PDF/Word từ CRM tới khách Zalo. Tài liệu chính thức Zalo (stc-developers.zdn.vn, đọc 2026-11-11):

- Tải lên trước, nhận mã: `POST /v2.0/oa/upload/image` (jpg, png, tối đa 1 MB, trả `attachment_id`), `/v2.0/oa/upload/gif` (tối đa 5 MB), `/v2.0/oa/upload/file` (PDF/DOC/DOCX/CSV, tối đa 5 MB, trả `token`). Multipart, trường `file`. Hạn mức mỗi API **5.000 lượt/tháng**. Zalo giữ tệp tối đa **7 ngày**: khách mở lại sau đó thì Zalo báo tệp không còn.
- Gửi bằng `POST /v3.0/oa/message/cs` (tin tư vấn, cùng luật khung 48 giờ và nguồn quota như tin chữ):
  - ảnh/GIF: `attachment.type = template`, `template_type = media`, một phần tử `{media_type: image|gif, attachment_id}`; GIF bắt buộc `width`, `height`; `message.text` là chú thích, tối đa 2.000 ký tự;
  - tệp: `attachment.type = file`, `payload.token`; KHÔNG có trường chữ.

## Quyết định

1. **Tệp nằm trong CRM trước.** Giao diện tải tệp bằng `upload_file` của Frappe, gắn vào Lead/Deal đang mở, `is_private = 1` (tệp hiện ở tab Đính kèm của Lead). API `zalo_tab.send_attachment` chỉ nhận `file_url` của File gắn vào chính Lead đó hoặc Deal của Lead (`media_out.attachment_for_lead`): không gửi được URL ngoài hay tệp của Lead khác.
2. **Tải lên Zalo lúc gửi, không lúc xếp hàng.** `send_outbound` → `ZaloAdapter.send` → `media_out` đọc tệp, nén ảnh JPG/PNG lớn hơn 1 MB (thu nhỏ dần, xuất JPEG; ảnh gốc tối đa 15 MB), lấy kích thước GIF, rồi `ZaloClient.send_media` tải lên và gửi. Lỗi tệp (hỏng, mất) là lỗi vĩnh viễn, không thử lại. Tải lên không tự thử lại; lần thử sau của hàng đợi tải lại từ đầu.
3. **Chỉ thêm trường** vào `Omni Outbound Message`: `attachment` (Attach), `attachment_kind` (image/gif/file), `attachment_name`. Không DocType mới. Mỗi tin Zalo vẫn là đúng một dòng (một `external_message_id`), để echo của Zalo khớp và không sinh dòng Bot giả.
4. **Tệp kèm chữ thành hai tin.** Zalo không cho chữ đi cùng tệp, nên `send_attachment` xếp hàng tin chữ TRƯỚC, rồi tin tệp; mỗi tin qua `routing.pick_message_kind` riêng (ngoài 48 giờ: mỗi tin tiêu 1 tin quỹ, sale xác nhận một lần cho cả hai). Thứ tự tới khách thường đúng (tin chữ gửi nhanh hơn tin phải tải tệp) nhưng không bảo đảm tuyệt đối khi hai worker chạy song song.
5. **Công tắc và danh sách trắng.** `Omni Settings.zalo_media_send_enabled` mặc định 0: chỉ tài khoản Zalo thử nghiệm (cùng danh sách `phone_request.WHITELIST_USER_IDS` của V3-07) nhận được, chặn trong `ZaloAdapter.send` và lần nữa trong `ZaloClient.send_media`, trước cả nhánh sandbox. Nút 📎 chỉ hiện khi máy chủ trả `compose.can_send_media`.
6. **Hạn mức tải lên** đếm ở Redis theo tháng và loại (`omni:zalo:upload_count:<YYYY-MM>:<loại>`), từ chối khi vượt 5.000. Mất cache thì đếm lại từ 0 (chấp nhận: Zalo vẫn chặn ở phía họ).
7. **Hiển thị**: tin ra có đính kèm hiện trong tab Zalo và Hộp thư qua cùng bộ lọc URL của ADR 0020 (`/private/files` được vẽ ảnh nội tuyến; người xem đã đăng nhập và có quyền Lead). `zalo_tab._http_only` nay nhận cả đường dẫn /files của CRM (trước chỉ http(s)).

## Hệ quả

- Bản giao diện patch (ADR 0016) thêm nút 📎, thẻ tệp đã chọn, ô chú thích; hàm thuần `checkMediaFile`, `canSubmitWithFile` trong `zaloThread.js` (vitest).
- Hộp thư (`/omni-inbox`) chưa có nút đính kèm (ngoài phạm vi), nhưng hiện được ảnh/tệp đã gửi.
- Chưa xác minh với API thật: tên trường `token` hay `attachment_id` trong phản hồi tải GIF (tài liệu ví dụ trả `attachment_id`, bảng mô tả ghi `token`; code đọc cả hai); tải lên có tính vào giới hạn 100 lượt/phút của OA không.
