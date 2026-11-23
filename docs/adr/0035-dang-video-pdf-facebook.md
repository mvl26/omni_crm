# ADR 0035 · Bài Facebook có video (fb_video) và bài có link tới PDF (fb_pdf_link)

Ngày: 2026-11-23 · Trạng thái: Đề xuất (chờ Việt review) · Liên quan: ADR 0028 (Marketing Hub), ADR 0033 (kho S3, mức 2), `spd/docs/35`

## Bối cảnh

- Người phụ trách (23/11/2026) muốn đăng video và PDF lên Facebook, Website, Zalo, Gmail, theo thứ tự đó. Phần này là
  **Facebook**. Bộ đăng bài (ADR 0028) mới có bài link, bài một ảnh, bài nhiều ảnh.
- Thử trên Page thật ngày 23/11/2026 (`spd/docs/35` mục 7):
  - Token System User `miyano-intake` tải và xóa được video ẩn trên Page ở mức quyền hiện tại, không cần App Review.
  - Điều kiện duy nhất: System User có quyền **Content** trên Page.
  - Token đang chạy của kênh `messenger-mvl` đã có `pages_manage_posts`.
- Pages API không đính kèm được PDF vào bài. Chỉ có `message` và `link`; ảnh và video có endpoint riêng.

## Quyết định

- **Hai ô mới trên `Omni Content Item`:** `video` và `document` (Attach). Không dùng thanh Đính kèm, vì thanh đó không
  cho biết tệp nào là tệp để đăng. Không làm bảng con: một bài Facebook chỉ đăng một video hoặc một PDF.
- **Hai định dạng mới cho Facebook** (`rules.FORMATS`, `Omni Content Target.post_format`):
  - `fb_video`: `POST /{page}/videos` ở `graph-video.facebook.com` với `file_url` (Facebook tự tải video về từ địa chỉ
    công khai của CRM), `description` = caption cùng link theo dõi, `published=true`. Mã lưu lại là **video id**
    (không có `_`). Thời gian chờ 120 giây, vì Facebook tải video về trong lúc trả lời.
  - `fb_pdf_link`: `POST /{page}/feed` với `message` và `link` = địa chỉ công khai của PDF.
- **Kiểm:**
  - Duyệt bài (`approval._check_media`) đòi ô tương ứng có tệp.
  - Dựng nội dung (`content.public_file_url`) đòi đúng đuôi (MP4, MOV; PDF), tệp công khai, còn trên đĩa hoặc trên kho S3.
  - Kiểm sau cùng (`content.validate`) đòi có video hoặc PDF.
- **Đối chiếu khi hết giờ chờ** (`reconcile`): với video, đọc `/{page}/videos` (mô tả, permalink, created_time), khớp
  mã theo dõi, chỉ lấy video tạo sau mốc đối chiếu.
- **permalink** của video là đường tương đối: thêm `https://www.facebook.com`.
- **Chỉ số video:** chỉ cảm xúc và bình luận. Chia sẻ, tiếp cận, hiển thị, lượt bấm để trống. Lượt xem video (metric
  riêng của Meta) chưa đọc.
- **Kho S3 (ADR 0033):** hai ô mới chuyển lên MinIO khi lưu bài như ô ảnh. Loại và cỡ theo `ATTACH_TYPES`, tối đa
  `ATTACH_MAX_BYTES`. Video lớn hơn ở lại `/files/`, và nginx phát trực tiếp nên Facebook vẫn tải được. `_replace_refs`,
  `_still_used`, `push_pending`, `pending_count`, `pull_back` và báo cáo "Kho ảnh MinIO" đều tính hai ô này. Đổi video
  hoặc PDF của bài đã chấm điểm thì điểm về 0, như đổi ảnh.

## Hệ quả

- **Migrate:** thêm hai cột vào `tabOmni Content Item` và hai giá trị vào ô chọn định dạng.
- **Giao diện `/crm`** (trang Marketing, bản vá giao diện `0002`) **chưa** có định dạng và ô mới; lúc này chọn trên
  Desk. Muốn thêm vào `/crm` phải sửa giao diện và build lại (việc V6), làm cùng Việt.
- Trợ lý soạn bài (AI) không đề xuất hai định dạng này: lời nhắc không nhắc tới. `check_format` vẫn chấp nhận.
- Video qua `/omni-media/` đi qua gunicorn (trần 50 MB). Video lớn hơn ở `/files/` qua nginx. Muốn mọi video trên
  MinIO thì phải có `X-Accel-Redirect` (sửa nginx yhct), hoặc tải lên từng phần (Resumable Upload) đọc từ MinIO.
  Chưa làm.
- Mã chưa thử với Facebook thật, ngoài lần thử quyền bằng script.
  - Việc cần kiểm trên Page thật: một bài `fb_video` và một bài `fb_pdf_link` (Facebook tự tải từ `file_url` công khai
    của CRM). Link preview của PDF do Facebook tự dựng.
  - Các chỗ đánh dấu `VERIFY` trong `facebook.py` (trường của video) phải kiểm khi chạy thật.
- Test: `tests/test_v3_35_video_pdf.py` (giả lập HTTP và kho S3).
