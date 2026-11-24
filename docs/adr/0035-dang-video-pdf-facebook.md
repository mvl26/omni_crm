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
- **Chỉ số** (người phụ trách yêu cầu thêm chỉ số video, 23/11/2026; đã thử trên Page thật ở bước T3 cùng ngày,
  `spd/docs/35` mục Kết quả). Graph v26 đã gỡ `post_impressions*` (Meta thay impressions bằng views). Token
  `messenger-mvl` có `read_insights`, `pages_read_engagement`, chưa có `pages_read_user_content`. T3 cho thấy hai lỗi
  của mã cũ: đọc `reactions`/`comments` của bài bị chặn (mã 10); nút video không có `reactions` (mã 100). Vì vậy mã
  cũ không lấy được chỉ số của bài nào.
  - Tiếp cận: `post_total_media_view_unique`. Cột "Lượt hiển thị" (`impressions`): `post_media_view` (lượt xem).
    Lượt bấm: `post_clicks`. Mỗi metric một lần gọi; metric bị gỡ hoặc thiếu quyền chỉ để trống số đó.
  - Cảm xúc, bình luận, chia sẻ:
    - Token có `pages_read_user_content`: đọc trường của bài (số tức thời).
    - Không có (hiện tại): `post_reactions_by_type_total` (cộng các loại) và `post_activity_by_action_type` (khóa
      `comment`, `share`). Trước đó gọi `GET <bài>?fields=id`: bài đã xóa hoặc token hỏng thì vẫn ném lỗi để job
      đồng bộ ghi lại.
  - Video: Facebook hiện video đăng qua `/{page}/videos` thành **Reel**.
    - Cột mới **Lượt xem video** `video_views`: `total_video_views`, Reel thì `fb_reels_total_plays` (T3:
      `total_video_views` rỗng với Reel).
    - Thích, bình luận: trường `likes`, `comments` trên nút video, vì tương tác Reel không vào metric của bài Trang.
    - Tiếp cận, lượt xem, lượt bấm, chia sẻ: trên bài Trang chứa video. Trường `post_id` của nút video trả số trần
      (không có trong tài liệu Meta), mã bài là `<page>_<post_id>`; không có thì `<page>_<video>`. Dùng mã Graph trả
      về.
    - Không dùng `total_video_impressions*`, `total_video_views_unique`, `post_impressions_unique` của video (T3: lỗi
      mã 100).
  - Báo cáo marketing (`report.py`, trang `/crm`, Excel) có thêm thẻ và cột "Lượt xem video".
  - Nguồn: tài liệu Insights và Video Insights (bảng Reels Metrics) của Meta, blog Page Insights API Updates
    (15/8/2025), blog Graph API v25 (18/2/2026), và kết quả T3.
- **Bài có link tới PDF chờ 60 giây** (`LINK_TIMEOUT`): Facebook tải PDF dựng thẻ xem trước trong lúc trả lời. T3:
  quá 10 giây, bài vẫn lên, lượt thử sau đối chiếu thấy, không trùng.
- **Kho S3 (ADR 0033):** hai ô mới chuyển lên MinIO khi lưu bài như ô ảnh. Loại và cỡ theo `ATTACH_TYPES`, tối đa
  `ATTACH_MAX_BYTES`. Video lớn hơn ở lại `/files/`, và nginx phát trực tiếp nên Facebook vẫn tải được. `_replace_refs`,
  `_still_used`, `push_pending`, `pending_count`, `pull_back` và báo cáo "Kho ảnh MinIO" đều tính hai ô này. Đổi video
  hoặc PDF của bài đã chấm điểm thì điểm về 0, như đổi ảnh.

## Hệ quả

- **Migrate:** thêm hai cột vào `tabOmni Content Item`, hai giá trị vào ô chọn định dạng; cột `video_views` của `tabOmni Content Target`.
- **Giao diện `/crm`** (bản vá giao diện `0002`, trang bài Marketing; người phụ trách yêu cầu 23/11/2026):
  - Hộp chọn định dạng Facebook có thêm "Video" và "Bài có link tới PDF". Máy chủ chưa có hai ô mới thì hai định dạng
    này tự ẩn.
  - Mục mới **Video và tài liệu PDF**:
    - Nút tải lên. Tệp công khai, gắn vào bài đúng ô (`attached_to_field`), nên không bị hiểu là tệp của thanh Đính
      kèm (mức 2). Trình duyệt kiểm đuôi MP4, MOV, PDF trước khi tải.
    - Ô **Chọn tệp đã đính kèm**: tệp công khai đúng loại đã gắn vào bài qua thanh Đính kèm trên Desk. Đây là cách
      đưa tệp đính kèm vào bài đăng.
    - Trình phát video, liên kết PDF theo tên gốc, nút gỡ (tệp vẫn còn trong CRM).
  - Nền tảng chọn định dạng cần tệp mà bài chưa có thì hiện nhắc màu đỏ. Ô xem trước vẽ video hoặc thẻ PDF theo nền
    tảng đầu tiên.
  - Máy chủ: `views.item_detail` trả thêm `files`, là các tệp gắn vào bài (tên gốc, địa chỉ hiện tại, riêng tư hay không,
    ô gắn). Hàm thuần ở `utils/marketingMedia.js`, có vitest. Chỉ nhận địa chỉ `/files/`, `/omni-media/` hoặc https.
  - Chọn tệp đính kèm còn ở `/files/`: Frappe (`attach_files_to_document`) tạo thêm một dòng File cho ô. Dòng này giữ
    tệp trên đĩa sau khi tệp chuyển lên MinIO (`_still_used`). Không hỏng liên kết, chỉ chưa giải phóng đĩa. Tệp đã ở
    `/omni-media/` thì Frappe bỏ qua.
  - Hộp thoại **Thêm bài** của trang chiến dịch chỉ chọn định dạng. Tải tệp ở trang bài.
- Trợ lý soạn bài (AI) không đề xuất hai định dạng này: lời nhắc không nhắc tới. `check_format` vẫn chấp nhận.
- Video qua `/omni-media/` đi qua gunicorn (trần 50 MB). Video lớn hơn ở `/files/` qua nginx. Muốn mọi video trên
  MinIO thì phải có `X-Accel-Redirect` (sửa nginx yhct), hoặc tải lên từng phần (Resumable Upload) đọc từ MinIO.
  Chưa làm.
- **Đã thử trên Page thật (bước T3, 23/11/2026, `spd/docs/35` mục Kết quả):**
  - Một bài `fb_video`: Facebook tự tải từ `/omni-media/` của CRM, đăng thành Reel.
  - Một bài `fb_pdf_link`: thẻ link do Facebook dựng, tiêu đề là tên miền CRM.
  - Đạt các chỗ VERIFY về video: mô tả có mã theo dõi, permalink tương đối, `created_time`, đối chiếu.
  - Tên metric và đường đọc chỉ số đã chốt theo kết quả thật (mục Quyết định). Hai chỗ `VERIFY` còn lại trong
    `facebook.py` là tên metric do Meta đổi định kỳ; kiểm lại bằng `thu_t3.py --kiem-bai` khi Meta báo đổi.
- Test: `tests/test_v3_35_video_pdf.py` (23 test, giả lập HTTP và kho S3), `frontend/tests/unit/marketingMedia.test.js` (17 test).
