# 0018 · Hiển thị ảnh / GIF / sticker / tệp khách gửi trong tab Zalo, danh sách host ảnh

- Ngày: 2026-11-10
- Trạng thái: Đề xuất (người phụ trách chưa duyệt; chưa kiểm trên site thật)
- Prompt: V3-05 (nhánh `v3/05-hien-anh-zalo`)

## Bối cảnh
Người dùng báo trong Lead không thấy ảnh khách gửi; trang chat Zalo OA có "[Tệp đính kèm]" (link `https://zgif-v2.zdn.vn/<hash>.gif`) và "[Ảnh]" (`https://photo-stal-27.zdn.vn/no/jpg/<hash>/<id>.jpg`). Mã cũ: `LABELS` thiếu gif/video/link/business_card (GIF thành "[Tệp đính kèm]"), tab chỉ vẽ mỗi tệp là một thẻ `<a>` mang tên bằng chính URL dài, không có ảnh.

Điều tra đường đi (gateway, hàng đợi, worker, pipeline, `Omni Inbound Message`, `Communication`, `thread_data`, tab): mọi `user_send_*` có `sender.id` đều thành `event_type = message`, chỉ sự kiện không có tiền tố `user_send_`/`oa_send_` mới thành `ignored`. Không có bước nào bỏ tin vì text rỗng hay có đính kèm (text luôn có nhãn), dedupe theo `msg_id` của Zalo. Vì vậy tin ảnh/GIF CÓ tới thì chắc chắn được lưu và hiện ở tab, nhưng chỉ là chữ và liên kết. Người dùng xác nhận các sự kiện ảnh/GIF/tệp/video/audio/sticker/link/location đã bật trong app Zalo, nên nguyên nhân chính là hiển thị. Hai khả năng còn lại cần dữ liệu thật: payload thật có hình dạng khác (đã làm phần parse phòng thủ, lỗi không còn biến cả sự kiện thành `unparsed`) và nhiều ảnh cùng `msg_id` bị dedupe (chưa xác nhận; xem docs/12). Lệnh `zalo_media_report` giúp chốt.

## Quyết định
1. Mỗi đính kèm Zalo chuẩn hóa thành `{type, url, name, mime, thumb?}`; thêm nhãn `gif`, `video`, `link`, `business_card`; ảnh đến với type `image` nhưng URL đuôi `.gif` được gán lại `gif`. Tin không chữ không đính kèm mà là `user_send_<loại>` vẫn có nhãn theo tên sự kiện.
2. `_safe_attachments` giữ `type` và `thumb`, chỉ nhận `http(s)`, `/files/`, `/private/files/` (như cũ), và đánh `inline = true` khi loại là ảnh/GIF/sticker **và** URL là `/files` nội bộ hoặc `https` tới host thuộc danh sách cho phép. Danh sách: `zdn.vn`, `zadn.vn`, `zaloapp.com`, `zalo.me` (đúng host hoặc tên miền con, không khớp hậu tố chuỗi; không userinfo). Chỉ `zdn.vn` được xác nhận từ dữ liệu thật; ba host còn lại theo hiểu biết về CDN Zalo, chưa có tài liệu chính thức. Host khác chỉ hiện thành liên kết có nhãn. Danh sách nằm ở `omni_crm/utils/media.py` và lặp lại ở `zaloThread.js` (phòng thủ hai lớp; sửa phải sửa cả hai).
3. Giao diện: ảnh thu nhỏ (`max-w 240px`), `loading=lazy`, `referrerpolicy=no-referrer`, bấm mở ảnh gốc tab mới `noopener noreferrer`; lỗi tải thì rơi về liên kết có nhãn; tệp hiện tên thân thiện; không `v-html`.
4. Chẩn đoán chỉ đọc `omni_crm.ops.zalo_media_report.run`: chỉ host, không URL đầy đủ, text cắt 40 ký tự. Sự kiện bị `ignored` được đếm theo tên (Redis 24 giờ, khóa `omni:zalo:reject:<kênh>:ignored:<tên>`) và báo cáo cũng đếm trong DB 7 ngày.

## Rủi ro
- Trình duyệt tải ảnh thẳng từ CDN Zalo; URL có thể hết hạn hoặc cần chữ ký: khi đó `onerror` rơi về liên kết. Host CDN thật có thể ngoài danh sách: ảnh chỉ hiện thành liên kết (an toàn); mở rộng danh sách cần sửa hai nơi.
- Tab chỉ có cho giao diện máy tính (như ADR 0016); phải áp lại patch và `build.sh` mới thấy.

## Hệ quả
Patch `0001-zalo-tab.patch` đổi (cùng 10 tệp). Không đổi DocType, không đổi công tắc. Test mới `test_v3_05_zalo_media.py` và vitest trong `zaloThread.test.js`.
