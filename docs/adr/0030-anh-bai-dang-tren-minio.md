# 0030 · Ảnh bài đăng lưu trên kho S3 nội bộ (MinIO AIStor Free)

- Ngày: 2026-11-17
- Trạng thái: Đề xuất (chờ Việt review; chờ IT dựng MinIO)
- Prompt: không có prompt kit. Yêu cầu của người phụ trách ngày 17/11/2026: lưu bài đăng (nội dung, ảnh, ngày giờ) trên MinIO Free, cấu hình ở một nhánh con riêng. Đánh giá MinIO: `MinIO_AIStor_DanhGia_20261116_V1.0.pdf` (16/11/2026).
- Liên quan: ADR 0029 (bài đăng), `marketing/media.py`.

## Bối cảnh

- Nội dung, giờ đăng, trạng thái của bài là các trường của `Omni Content Post`, nằm trong MariaDB của CRM cùng mọi dữ liệu khác và được sao lưu cùng CSDL. Đưa các trường này sang MinIO thì mất lọc, lịch, báo cáo, quyền. Thứ nên ra kho đối tượng là tệp ảnh.
- Frappe lưu tệp đính kèm trên đĩa máy chủ (`sites/<site>/private/files`). Frappe chỉ có S3 cho bản sao lưu (S3 Backup Settings), không có S3 cho tệp đính kèm.
- Theo bản đánh giá ngày 16/11: AIStor Free dùng thương mại được nhưng chỉ một node, không mã hóa lúc lưu (SSE trả lỗi 405), không nhân bản. MinIO không thay việc sao lưu. Máy MinIO chưa dựng.
- `boto3` 1.34 đã có trong môi trường bench, vì Frappe dùng nó cho S3 Backup Settings.
- Thứ tự hook của Frappe: hook theo DocType chạy TRƯỚC hook `"*"`. `attach_files_to_document` (gắn tệp vừa tải lên vào bài) là hook `"*"` của `on_update`, còn `on_change` chạy sau toàn bộ `on_update`.

## Các lựa chọn

### A. Cách nối vào Frappe
1. **Chỉ ảnh của `Omni Content Post`**: sau khi lưu bài (`on_change`), đẩy ảnh lên S3, đổi `file_url` của File sang đường tải qua CRM, xóa bản trên đĩa. Phạm vi nhỏ, không đụng tệp của Lead, Deal.
2. Hook toàn cục `delete_file_data_content` cộng cài app bên thứ ba (kiểu `frappe_s3_attachment`): mọi tệp của site lên S3. Phạm vi lớn, đổi hành vi tệp của app crm, cần duyệt riêng.

### B. Trình duyệt lấy ảnh thế nào
1. **Qua CRM** (`/api/method/omni_crm.api.media.post_image?file=<File>`): kiểm quyền đọc bài rồi đọc object từ S3. MinIO chỉ cần mở cho máy chủ CRM.
2. Link ký sẵn (presigned) trỏ thẳng MinIO: nhẹ máy chủ hơn, nhưng MinIO phải mở cho trình duyệt của mọi người dùng và có TLS hợp lệ.

### C. MinIO lỗi khi đẩy ảnh
1. Chặn lưu bài: người soạn không làm được gì khi MinIO dừng (bản Free một node, không HA).
2. **Giữ ảnh trên đĩa, báo người soạn, job mỗi giờ đẩy lại.**

## Quyết định

Chọn A1, B1, C2.

- Omni Settings, mục "Kho ảnh S3 (MinIO, ADR 0030)": `s3_media_enabled` (mặc định 0), `s3_endpoint_url`, `s3_bucket`, `s3_region` (`us-east-1`), `s3_access_key`, `s3_secret_key` (Password), `s3_prefix` (`omni-content-post/`), `s3_verify_tls` (1). Nút "Kiểm kho ảnh S3" (Omni Admin) ghi, đọc, xóa một object thử.
- Client boto3: chữ ký s3v4, địa chỉ kiểu path (MinIO), timeout kết nối 5 giây, đọc 30 giây, thử lại 2 lần. Không gửi tham số mã hóa phía máy chủ (Free trả 405).
- Khóa object: `<prefix><mã bài>/<tên File><đuôi tệp>`. Custom Field `File.omni_s3_key` (chỉ đọc) giữ khóa.
- Sau khi đẩy được: `File.file_url` và `Omni Content Post.image` đổi sang đường tải qua CRM. Bản trên đĩa bị xóa nếu không File nào khác dùng chung. Xóa File thì xóa object (`after_delete`).
- Đường tải chỉ cho người đã đăng nhập và đọc được bài; File không gắn vào bài thì từ chối.
- Job đăng bài đọc ảnh qua `media.read_bytes`: File có `omni_s3_key` thì đọc từ S3.
- `image` của bài thêm `no_copy`: nhân bản bài thì không mang theo ảnh của bài gốc.

## Hệ quả

- Việc của IT trước khi bật `s3_media_enabled`:
  - dựng AIStor Free;
  - tạo bucket (ví dụ `crm-media`) và một access key riêng cho CRM, policy chỉ trên bucket đó (`s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`);
  - mở cổng S3 cho máy chủ yhct;
  - bấm "Kiểm kho ảnh S3".
- Bật xong, ảnh cũ trên đĩa được đẩy dần bởi job mỗi giờ (`marketing.s3_media.push_pending`).
- MinIO phải được sao lưu riêng (bản Free một node): máy MinIO hỏng thì mất ảnh đã chuyển đi, nội dung bài vẫn còn trong CSDL.
- Bản Free không mã hóa lúc lưu: ảnh bài đăng là ảnh công khai nên chấp nhận được. Không dùng kho này cho tệp có dữ liệu khách khi chưa có mã hóa ổ đĩa (bản đánh giá trang 12).
- Sửa File có `file_url` dạng `/api/method/...` trong Desk sẽ bị Frappe báo "URL must start with http". Không sửa File của ảnh đã chuyển: muốn đổi ảnh thì tải ảnh mới vào bài.
- Chưa thử với AIStor thật. Đã thử với máy chủ S3 giả lập (moto); khác biệt riêng của MinIO (path-style, TLS nội bộ) chỉ biết khi IT dựng máy.
