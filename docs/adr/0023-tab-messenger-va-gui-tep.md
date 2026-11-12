# 0023 · Tab Messenger trong trang Lead/Deal (ẩn khỏi Hoạt động) và gửi ảnh/tệp từ CRM qua Messenger

- Ngày: 2026-11-12
- Trạng thái: Đã chấp nhận (người phụ trách duyệt 12/11/2026: A1, B1, C1)
- Prompt: không có prompt kit; yêu cầu trực tiếp của người phụ trách 12/11/2026 ("giao diện messenger để tab ẩn như zalo", "gửi tệp từ CRM")
- Thay một phần: ADR 0022 mục C1 (tin Messenger hiện ở tab Hoạt động) và D2 (gửi tệp để đợt sau)
- Dựa trên: ADR 0016 (tab Zalo, patch giao diện crm), 0017 (ẩn tin Zalo khỏi Hoạt động), 0020 (hiển thị ảnh/tệp), 0021 (gửi ảnh/tệp Zalo)

## Bối cảnh

- Kênh Messenger chạy trên site `miyano` từ 12/11/2026 (ADR 0022, nhánh `feat/kenh-meta-messenger`): tin vào là `Communication` loại `Communication`, hiện ở tab Hoạt động/Email; trả lời bằng chữ ở Hộp thư. Sale muốn đọc và trả lời Messenger ngay trong trang Lead/Deal như tab Zalo, và gửi được ảnh/tệp.
- Mã nền: nhánh `feat/messenger-tab-gui-tep` cắt từ `tich-hop/miyano-server-email-messenger` = mã đang chạy trên máy chủ (`deploy/miyano-server`, V3-01…V3-09 của anh Việt) + nạp file intake/email + Messenger. Đã so: khác máy chủ chỉ ở thứ tự dòng trong `hooks.py`, bản sửa test B04 và tài liệu.
- Tab Zalo (ADR 0016): patch frontend crm v1.73.0 (`crm_frontend_patch/0001-zalo-tab.patch`, `apply.sh`, `build.sh`), logic trong tệp mới (`ZaloTab.vue`, `composables/zalo.js`, `utils/zaloThread.js` + vitest), vài dòng trong `Lead.vue`, `Deal.vue`, `Activities.vue`, `ActivityHeader.vue`; API `omni_crm.api.zalo_tab` bọc `inbox.thread_data` / `inbox.send_zalo`; công tắc `zalo_tab_disabled`.
- Ẩn khỏi Hoạt động (ADR 0017): `timeline.communication_type_for` ghi `Chat` cho Zalo OA; công tắc `zalo_in_activity_enabled`; lệnh đổi tin cũ `ops.hide_zalo_activity`.
- Gửi tệp Zalo (ADR 0021): tệp phải là File riêng tư gắn vào Lead/Deal (`media_out.attachment_for_lead`), `Omni Outbound Message` đã có `attachment`, `attachment_kind`, `attachment_name`; chữ kèm tệp thành hai tin; công tắc `zalo_media_send_enabled` + danh sách trắng.
- Messenger Send API gửi đính kèm `image`, `video`, `audio`, `file`; tệp riêng tư của CRM không có URL công khai nên phải tải lên kiểu multipart (`filedata`) cùng lời gọi `POST /{page}/messages`; giới hạn 25 MB; chữ và tệp là hai tin; vẫn luật 24 giờ **[Nguồn phụ: hiểu biết sẵn có về Messenger Platform, chưa đọc lại tài liệu; kiểm bằng thử thật trước khi bật]**.
- Máy IT dựng được giao diện: crm v1.73.0 đúng commit, Node 22, yarn, `node_modules` có sẵn ở bench thử.

## Các lựa chọn

### A. Tab
1. **Tab Messenger riêng** (`MessengerTab.vue`, `composables/messenger.js`, `MessengerIcon.vue`), dùng lại các hàm thuần của `utils/zaloThread.js` (gom bong bóng, kiểm URL ảnh, kiểm tệp chọn) mà không sửa `ZaloTab.vue`; API riêng `omni_crm.api.messenger_tab`. Ưu: không đụng mã tab Zalo đang chạy; gỡ riêng được. Nhược: lặp một phần giao diện.
2. Một tab "Chat" chung cho Zalo và Messenger (tổng quát hóa `ZaloTab.vue`). Ưu: ít lặp. Nhược: sửa sâu mã tab Zalo của V3-01…V3-09 (thẻ xin số, quỹ 48 giờ, mở chat OA chỉ có ở Zalo), rủi ro làm hỏng tab Zalo.

### B. Hạn chế người nhận khi thử gửi tệp
1. Chỉ công tắc `messenger_media_send_enabled` (mặc định tắt). Ứng dụng Meta đang ở chế độ development nên chỉ người có vai trò trong ứng dụng nhận được tin.
2. Công tắc + danh sách trắng PSID như Zalo (ADR 0021 mục 5).

### C. Nút đính kèm ở Hộp thư
1. Chỉ ở tab Messenger (như Zalo, ADR 0021: Hộp thư chưa có nút đính kèm, chỉ hiện ảnh/tệp đã gửi).
2. Thêm nút đính kèm ở Hộp thư cho Messenger.

## Quyết định

A1, B1, C1 (người phụ trách duyệt 12/11/2026).

1. **Ẩn tin Messenger khỏi Hoạt động/Email** như Zalo: `timeline.communication_type_for` trả `Chat` cho `Meta Messenger`, trừ khi bật `Omni Settings.messenger_in_activity_enabled` (Check, mặc định 0 = ẩn). Tin cũ: lệnh chạy tay đổi loại, mặc định dry-run, đảo được (tổng quát `ops.hide_zalo_activity` bằng tham số loại kênh, hoặc lệnh riêng nếu sửa lệnh Zalo có rủi ro).
2. **Tab Messenger** (A1): thêm vào patch giao diện các tệp mới ở trên + test vitest; vài dòng trong 4 tệp đã thuộc patch (`Lead.vue`, `Deal.vue`: thêm mục tab; `Activities.vue`: hiện tab; `ActivityHeader.vue`: ẩn nút "New"). Không thêm tệp crm bị sửa mới. Đổi tên patch không cần; `0001-zalo-tab.patch` được tạo lại từ mã nguồn crm đã gộp (như cách V3-05/V3-09 làm). Tab hiện trên mọi Lead/Deal khi `Omni Settings.messenger_tab_disabled` = 0, giống tab Zalo (`tab_state()` không nhận tài liệu); Lead/Deal không có định danh Messenger thì tab trống và khung soạn khóa kèm lý do. (Sửa câu ban đầu "chỉ hiện khi có định danh Messenger" cho khớp cách làm thực tế, 12/11/2026.)
3. **API `omni_crm.api.messenger_tab`**: `tab_state`, `get_conversation` (lọc `channel_type = "Meta Messenger"` từ `inbox.thread_data`), `send` (bọc `inbox.send_messenger`), `send_attachment`. Quyền như tab Zalo (ADR 0016 mục 5): quyền trên tài liệu đang mở VÀ trên Lead gốc. Hiển thị như ADR 0016 mục 8 và 0020: nội suy văn bản, không `v-html`; ảnh nội tuyến chỉ từ `/files`, `/private/files`.
4. **Gửi ảnh/tệp** (C1, chỉ ở tab): tệp tải lên CRM trước (File riêng tư gắn Lead/Deal, kiểm thuộc Lead bằng `media_out.attachment_for_lead`, dùng lại, không sửa); `MessengerClient.send_attachment` tải lên multipart cùng lời gọi gửi; loại: `image` (jpg, png, gif), `video` (mp4), `audio` (mp3, m4a), `file` (pdf, doc, docx, xls, xlsx, csv, txt, zip); tối đa 25 MB; chữ kèm tệp thành hai tin (chữ trước); ghi `attachment`, `attachment_kind`, `attachment_name` của `Omni Outbound Message` (đã có từ V3-09); echo của tin tệp vẫn khớp mã tin nên không hiện hai lần. Ngoài 24 giờ chặn như tin chữ.
5. **Công tắc** (Omni Settings, Check; thêm 3 trường, cần `migrate`): `messenger_tab_disabled` (mặc định 0 = hiện tab), `messenger_in_activity_enabled` (mặc định 0 = ẩn khỏi Hoạt động), `messenger_media_send_enabled` (mặc định 0 = chưa gửi tệp; bật khi thử).

## Người phụ trách đã duyệt (12/11/2026)

1. A1: tab Messenger riêng.
2. B1: chỉ công tắc `messenger_media_send_enabled`.
3. C1: nút đính kèm chỉ ở tab Messenger.

## Hệ quả

- Triển khai lên máy chủ: bản vá Python + `migrate` (3 trường Omni Settings), lệnh đổi loại tin cũ, rồi `apply.sh` (patch giao diện mới) + `build.sh` NGOÀI giờ làm việc (vite xóa giao diện cũ trước khi ghi; `build.sh` tự phục hồi khi lỗi). Tải lại trang bằng Ctrl+Shift+R.
- Tab chỉ có trên giao diện máy tính (như tab Zalo).
- Rủi ro chung của patch giao diện crm (ADR 0016 mục Rủi ro) vẫn áp dụng; ngoại lệ "sửa frontend crm" mở rộng thêm phạm vi Messenger theo ADR này.
- Tài liệu: ADR 0022 (ghi chú thay C1, D2), docs/16 mục Messenger, docs/09 (3 công tắc), README của `crm_frontend_patch`.
