# 0027 · Tương tác Messenger: emoji, sticker, cảm xúc, trả lời tin cụ thể, ẩn tin (đợt 1); ghi âm, chuyển tiếp (đợt 2)

- Ngày: 2026-11-13
- Trạng thái: Đã chấp nhận (người phụ trách duyệt 13/11/2026: sticker từ kho của Meta, ghi âm đổi sang MP3 trên máy chủ, người gửi + quản lý được ẩn tin, chia hai đợt). Đợt 1 làm ở nhánh `feat/messenger-tuong-tac`; đợt 2 chưa làm.
- Prompt: không có prompt kit; yêu cầu trực tiếp 13/11/2026 ("chưa có nút ghi âm gửi voice, chưa có nút gửi tệp GIF, chưa có nút gửi icon"; "chưa thả react được, chưa có reply, chưa chuyển tiếp, chưa thu hồi 1 phía và 2 phía")
- Dựa trên: ADR 0022 (kênh Messenger qua file intake), 0023 (tab Messenger, gửi tệp), 0026 (mẫu tin nhắn)

## Bối cảnh (đã tra tài liệu Meta 13/11/2026)

- **Cảm xúc**: Send API có `sender_action = react` (payload `message_id`, `reaction` là emoji bất kỳ) và `unreact` **[Đã kiểm: trang "Sender Actions" của Meta]**. Khách thả cảm xúc: webhook `message_reactions` (`reaction.emoji`, `reaction.action` react/unreact, `reaction.mid`) **[Đã kiểm: trang tham chiếu webhook]**. Trang không nói Page tự thả có được báo lại không.
- **Trả lời tin cụ thể**: Send API nhận `reply_to: {mid}` (Meta mở 15/7/2025), trong khung 24 giờ; tin của khách trả lời một tin có `message.reply_to.mid` **[Đã kiểm: changelog và trang webhook `messages`]**.
- **Sticker**: Sticker API (Meta mở 01/6/2026): gửi bằng `message.sticker_id` trong khung tin thường; kho `/sticker_packs`, `/sticker_packs/{id}/stickers`, `/sticker_search` (q ≥ 2 ký tự, `locale`) dùng **App Access Token** `app_id|app_secret`; chỉ sticker miễn phí của Meta (~105 bộ), không GIF, không sticker tự tạo; 👍 có mã `369239263222822`, luôn gửi được **[Đã kiểm: trang Sticker API]**. Từ 30/8/2026 sticker khách gửi chỉ còn kiểu đính kèm `sticker` (có `sticker_id`, có thể không có `url`).
- **GIF**: đính kèm `image`; tệp .gif đã gửi được qua 📎 (ADR 0023).
- **Âm thanh**: Send API nhận âm thanh tới 25 MB; tài liệu chỉ nêu MP3 làm ví dụ **[Nguồn phụ]**. Trình duyệt ghi WebM/Opus (Chrome) hoặc MP4/AAC (Safari).
- **Thu hồi**: không tìm thấy API nào cho Page xóa/thu hồi tin đã gửi, cũng không có sự kiện báo khách thu hồi **[Đã tra, không thấy]**.
- **Chuyển tiếp**: Send API không có chuyển tiếp.
- Realtime chiều vào đã có sẵn (tiến trình `omni-intake-watch` + tín hiệu `omni_inbox_update` + tab tự tải lại 60 giây); việc "phải F5" trên yhct là vấn đề vận hành, kiểm riêng, không thuộc ADR này.

## Quyết định

### Đợt 1 (nhánh `feat/messenger-tuong-tac`)

1. **Emoji**: bảng emoji dựng sẵn trong giao diện (không thêm thư viện), chèn vào ô soạn; không cần máy chủ.
2. **Sticker**: kho sticker của Meta + nút 👍. API `sticker_packs`, `sticker_pack`, `sticker_search` (chỉ đọc, đệm 6 giờ, `locale = vi_VN`, cần `meta_app_id` + `meta_app_secret` của kênh; sandbox: không gọi Meta). Gửi bằng `send_sticker` qua cùng đường xếp hàng/guard với tin chữ (24 giờ, từ chối liên hệ, quyền ghi Lead); ảnh sticker chỉ lưu/hiện khi là https từ CDN của Meta (`fbcdn.net`, `fbsbx.com`, `facebook.com`). Không có công tắc riêng (không phải tệp).
3. **Cảm xúc**: sale thả/bỏ qua `react` (Send API `sender_action`, gọi trực tiếp, không xếp hàng, không tạo tin); ghi `reaction_page`, `reaction_page_by` lên tin. Khách thả/bỏ: miyano-intake ghi file `kind = "reaction"` (nhánh `feat/messenger-reaction`, cần đăng ký trường webhook `message_reactions`); runner ghi `reaction_customer` lên tin cùng `mid` của đúng khách đó, bỏ sự kiện cũ hơn lần đã áp; không tạo tin, không Communication.
4. **Trả lời tin cụ thể**: `send`, `send_attachment`, `send_sticker` nhận `reply_to_doctype` + `reply_to` (tin của chính Lead, kênh Messenger, đã có mã tin Meta); lưu `reply_to_mid` trên Omni Outbound Message, client gửi `reply_to.mid` (tin chữ bị tách thì chỉ đoạn đầu). Tin khách trả lời: lưu `reply_to_mid` trên Omni Inbound Message từ `raw.event.message.reply_to`. Tab hiện trích dẫn (≤ 80 ký tự) hoặc "tin gốc không còn hiển thị".
5. **Thu hồi 1 phía** (ẩn trong CRM): `hide_message`; người gửi ẩn/bỏ ẩn tin do mình gửi từ CRM, Sales Manager/Omni Admin/System Manager được mọi tin (kể cả tin khách). Ghi `hidden_at`, `hidden_by`; bản ghi gốc, Communication và lịch sử cho bot giữ nguyên. Người không phải quản lý không còn thấy tin đã ẩn (tab Messenger và Hộp thư); quản lý thấy, có nhãn và nút bỏ ẩn. **Thu hồi 2 phía: không làm được** (Meta không có API); giao diện nói rõ khách vẫn thấy tin.
6. **Trường mới** (cần migrate): Omni Inbound Message và Omni Outbound Message thêm `reply_to_mid`, `reaction_customer`, `reaction_customer_at`, `reaction_page`, `reaction_page_by`, `hidden_at`, `hidden_by`; Omni Outbound Message thêm `sticker_id`, `sticker_url`. `OutboundRequest.extras` mang `quote_mid`, `sticker_id`, `sticker_url`.
7. **Sticker khách gửi**: miyano-intake giữ `sticker_id` trong đính kèm; sticker không có `url` được ghi ngay (không treo hàng tải); tab hiện ảnh nếu đã tải, không thì "[Sticker]".

### Đợt 2 (chưa làm)

8. **Ghi âm**: ghi trên trình duyệt (MediaRecorder), tải lên CRM như tệp của Lead, máy chủ đổi sang MP3 bằng `ffmpeg` rồi gửi đính kèm `audio`; cần `ffmpeg` trên yhct và thử thật với Meta.
9. **Chuyển tiếp**: làm phía CRM: gửi lại nội dung/tệp của một tin cho khách Messenger khác (Lead khác), theo luật 24 giờ và mọi guard của khách nhận; ghi nguồn chuyển tiếp.

## Hệ quả

- Triển khai đợt 1: bản vá omni_crm + migrate + build lại giao diện; cập nhật `intake/channels/meta.py` của miyano-intake; tích trường `message_reactions` trong cấu hình webhook của ứng dụng Meta rồi chạy lại `meta-subscribe` (việc trên Meta do người phụ trách làm). Muốn có kho sticker: điền `meta_app_secret` cho kênh `messenger-mvl` (người phụ trách tự dán, không qua chat).
- Cảm xúc của Page không có thời hạn được tài liệu nêu; Meta có thể từ chối với tin quá cũ: lỗi hiện cho sale, không thử lại.
- Danh sách kiểm duyệt `test_v2_02_checks` thêm `send_sticker`, `react`, `hide_message`, `sticker_packs`, `sticker_pack`, `sticker_search`.
- Tài liệu: docs/02 (trường), docs/09 (mục 1.1), docs/16 (mục Messenger), README `crm_frontend_patch`; spd/docs 10, 12, 16, 17 (danh sách trường webhook).
