# ADR 0033 · Ảnh bài marketing trên kho S3 (MinIO AIStor Free), CRM phát lại ảnh cho Facebook/Zalo

Ngày: 2026-11-18 · Trạng thái: Đề xuất (chờ Việt review) · Liên quan: ADR 0028 (Marketing Hub), `docs/17-marketing-hub.md`, hướng dẫn dựng MinIO `spd/docs/26`

## Bối cảnh

- Người phụ trách (18/11/2026): ảnh của từng bài lưu trên MinIO bản Free. Bộ Marketing Hub trên máy chủ (ADR 0028) đã đủ
  bài, lịch, đăng Facebook/Zalo, insights, báo cáo; chỉ chưa có kho ảnh ngoài đĩa máy chủ CRM.
- Ảnh bài (`Omni Content Item.media`, `media_items[].image`) là tệp công khai `/files/...`. Bộ đăng đưa **địa chỉ** ảnh
  cho nền tảng tự tải (`publisher/content.py::public_image_url`): Facebook `POST /{page}/photos` với `url`, Zalo bài viết
  OA với `cover.photo_url` và `body[].url`. Zalo không có đường tải tệp lên cho bài viết.
- MinIO đặt sau tường lửa, chỉ yhct gọi được (`spd/docs/26`), nên Facebook/Zalo không tải thẳng từ MinIO được.
- Giao diện `/crm` tải ảnh lên ở chế độ công khai, chưa gắn vào bài; Frappe chỉ tự gắn File cho ô Attach của bảng cha.

## Các lựa chọn

1. **CRM phát lại ảnh ở địa chỉ công khai có chữ ký** (`/omni-media/<File>-<HMAC>.<đuôi>`), đọc từ MinIO. MinIO vẫn kín;
   địa chỉ không đoán được; có đuôi ảnh, không có `?` (không phụ thuộc cách Zalo đọc query). Đổi lại mỗi lần nền tảng
   tải ảnh đi qua gunicorn thay vì nginx.
2. Mở bucket công khai hoặc URL ký sẵn của MinIO: phải mở MinIO ra Internet, URL ký sẵn có hạn (tối đa 7 ngày) trong khi
   bài lên lịch có thể xa hơn; trái với thiết kế mạng của tài liệu 26.
3. Giữ ảnh trên đĩa đến khi đăng xong rồi mới chuyển: ảnh chờ đăng vẫn ở đĩa, logic hai pha; không đạt "lưu trên MinIO".

## Quyết định

Chọn 1.

- DocType Single mới **`Omni Media Storage`** (Omni Admin, System Manager): công tắc, endpoint, bucket, thư mục, region,
  access key, secret key (Password), kiểm TLS (mặc định bật), tệp CA nội bộ (boto3 không đọc kho CA của hệ điều hành).
  Không thêm ô vào `Omni Marketing Settings` để không đụng tệp đang thay đổi nhiều. Nút **Kiểm kho ảnh S3** ghi, đọc,
  xóa một object thử (`api/media.py::check_storage`).
- Custom Field `File.omni_s3_key` (fixture): khóa object.
- `marketing/s3_media.py`:
  - `on_item_change` (hook `on_change` của Omni Content Item) chuyển ngay ảnh `/files/` của bài khi lưu; MinIO lỗi thì
    báo người soạn, ảnh ở lại đĩa. Bỏ qua bài có nền tảng đang "Đang xử lý".
  - `push_pending` (cron `47 * * * *`): chuyển ảnh còn trên đĩa; bỏ qua bài đang đăng hoặc sắp đăng trong 10 phút; MinIO
    lỗi thì dừng lượt.
  - Khi chuyển: ghi object `<thư mục><bài>/<File><đuôi>`, đổi `File.file_url` và ô ảnh sang `/omni-media/...`, bỏ
    `content_hash` (Frappe gộp tệp trùng hash bằng cách đọc tệp cũ trên đĩa), xóa tệp trên đĩa sau commit **chỉ khi**
    không còn dòng File hoặc ô ảnh nào dùng địa chỉ cũ. Đổi bằng `db.set_value`: không chạy validate nên không làm điểm
    chất lượng về 0, không vướng khóa sửa bài đã duyệt.
  - Không chuyển ảnh riêng tư (`/private/files/`) và ảnh thêm thuộc tài liệu khác.
  - Chỉ chuyển **JPG, PNG, GIF, WebP** (`IMAGE_TYPES`, các loại Facebook/Zalo nhận). Loại khác (SVG, PDF, video…) ở lại
    `/files/` như cũ, `pending_count` đếm riêng ở `kept_other_type`. **Không có SVG**: SVG chứa được script; phát inline
    trên tên miền CRM là XSS. nginx của Frappe ép tải về `.svg/.html/.xml` trong `/files/`, `/omni-media/` không có luật đó.
  - `MediaRenderer` (hook `page_renderer`): phát `/omni-media/...` cho khách không đăng nhập, kiểm chữ ký HMAC-SHA256
    theo khóa mã hóa của site, chỉ trả đúng 4 loại trong `IMAGE_TYPES` (object loại khác trả 404 dù chữ ký đúng), `Cache-Control: public, max-age=604800`; chữ ký sai 404, MinIO lỗi 503.
  - `pull_back` (quay lui, mặc định chỉ đếm, từ chối khi công tắc còn bật): kéo object về `/files/` công khai, trả ô ảnh
    về địa chỉ trên đĩa; giữ object trên MinIO.
  - `on_file_delete` (hook `after_delete` của File): xóa object khi không File nào khác dùng khóa đó.
- `publisher/content.py::public_image_url` nhận thêm địa chỉ `/omni-media/` (trả URL tuyệt đối qua `get_url`), địa chỉ
  hỏng thì `PublishValidationError` như ảnh thiếu.
- Hook thêm bằng script `them_hooks_minio.py` (nối một khối cuối `hooks.py`, cùng cách K3), không nằm trong bản vá vì
  `hooks.py` trên máy chủ có thay đổi chưa commit.

## Hệ quả

- Ảnh chỉ còn trên MinIO: MinIO ngừng thì ảnh bài không hiện và bài đến giờ đăng lỗi "Không tìm thấy ảnh" hoặc nền tảng
  không tải được ảnh (503); bộ đăng thử lại theo lịch 1/5/15 phút. Giấy phép AIStor Free hết hạn quá 30 ngày thì chỉ
  đọc (ảnh mới ở lại đĩa), quá 90 ngày thì chặn S3: theo dõi hằng tháng (tài liệu 26 bước 11).
- Đường `/omni-media/` phải đi tới gunicorn qua nginx (mặc định của Frappe: mọi đường không phải tệp tĩnh). Kiểm bằng
  `curl` từ ngoài Internet trước khi bật (tài liệu 27).
- Đổi khóa mã hóa của site thì mọi địa chỉ `/omni-media/` đổi chữ ký: phải tính lại (`public_path`) cho File có
  `omni_s3_key`. Chưa có lệnh tự động.
- `tests/test_v3_33_media_storage.py`: kho S3 giả và boto3 thật (moto qua HTTP và HTTPS với CA thử) khi đặt
  `OMNI_S3_TEST_ENDPOINT`. `tests/test_v2_02_checks.py` thêm `api/media.py`.

## Bổ sung 20/11/2026: báo cáo "Kho ảnh MinIO"

Người phụ trách cần một chỗ xem kho đang có ảnh gì, của bài nào, xem được ảnh, mà không cần SSH (`spd/docs/30`, phương
án B; phương án A là Console của AIStor qua đường SSH, cho IT).

- Script Report **`Kho ảnh MinIO`** (module Omni CRM, `ref_doctype` Omni Content Item vì Frappe đòi quyền `report` trên
  DocType đó; Single `Omni Media Storage` không có quyền `report`). Vai trò: Omni Admin, System Manager; `execute` kiểm
  lại vai trò. Nút **Xem kho ảnh** trên form Omni Media Storage.
- `s3_media.list_objects` (ListObjectsV2 phân trang, dưới thư mục ảnh bài, dừng ở 10 000 object) dùng tài khoản
  `crm-yhct` đã cấu hình (đã có `s3:ListBucket`). Lỗi kho = thông báo lỗi, không hiện bảng rỗng.
- Mỗi dòng một khóa: **Khớp** (có object và File), **Thiếu trên MinIO** (File có `omni_s3_key` mà object không còn),
  **Mồ côi** (object không File nào dùng). Kèm bài, tiêu đề, File, số ô ảnh đang dùng, kích thước, giờ ghi, liên kết
  `/omni-media/...` để xem (chỉ khi object còn). Ô tổng hợp tính trên cả kho (cộng `pending_count`), không theo bộ lọc.
- **Chỉ đọc**: không có nút xóa hay sửa. Dọn object mồ côi vẫn làm bằng `mc rm` sau khi IT xem.
- Chỉ bucket của CRM. Bucket giai đoạn 2 (ERP, bot) mã hóa phía ứng dụng (`spd/docs/29`), mỗi ứng dụng tự có màn hình.
- `tests/test_v3_33_media_report.py`: kho S3 giả, phân trang giả, boto3 thật qua moto khi đặt `OMNI_S3_TEST_ENDPOINT`.
