# 0014 · Hàng dự phòng webhook: tệp JSON-lines trong thư mục private của site

- Ngày: 2026-11-03
- Trạng thái: Đã quyết
- Prompt: V21-04 (F4)

## Bối cảnh
Webhook trả 503 khi chờ khóa DB quá `omni_webhook_lock_wait_seconds`; Google, Zalo và Stringee có gửi lại khi 5xx hay không chưa xác nhận (docs/12 Z10, S8, G16), nên 503 có thể là mất tin.

**Nguyên nhân chờ khóa (đã đo):** tin vào đặt tên bằng `autoname = OIN-.YYYY.-.########`. Bộ đếm của Frappe (`getseries`) chạy `SELECT ... FOR UPDATE` trên dòng `tabSeries` rồi UPDATE, và giữ khóa ghi tới khi commit. Vì vậy MỌI webhook ghi tin xếp hàng sau bất kỳ giao dịch nào đang giữ dòng đó. Thử: kết nối khác giữ `tabSeries` `FOR UPDATE`, `save_inbound` ở bản `develop` chờ hết 3 giây (`QueryTimeoutError`), bản mới chèn xong trong 0,01 giây. Ngoài ra khóa duy nhất của `dedupe_key` (cùng sự kiện đang chèn dở) và khoảng khóa của `cleanup_raw_payloads` (UPDATE theo lô trên chỉ mục `creation`, đo được chặn INSERT mới) cũng có thể làm chờ.

## Các lựa chọn
1. **Danh sách Redis bật lưu đĩa (AOF).** Ưu: nhanh. Nhược: phụ thuộc cấu hình `appendonly` của Redis (trên bench dev là `--save ""`, không lưu đĩa), Redis queue của bench là dịch vụ khác có thể cũng chết cùng lúc, payload chứa dữ liệu cá nhân sẽ nằm chung bộ nhớ/đĩa với job; khó kiểm quyền và khó dọn.
2. **Tệp nối thêm trong `private/omni_spool` của site.** Ưu: độc lập với DB và Redis, bền (fsync), kiểm quyền 0700/0600 trực tiếp, dọn bằng xóa tệp, không thêm DocType (docs/02 giữ nguyên). Nhược: chỉ trên máy chạy web (nhiều máy web phải dùng chung đĩa hoặc mỗi máy tự quét); thao tác đĩa trong request (chỉ ở đường lỗi, vài mili giây).

## Quyết định
Chọn **tệp** (`ingest/spool.py`).
- **Vị trí/quyền:** `<site>/private/omni_spool/` (không nằm trong `public/` và không thuộc `private/files` nên không có URL); thư mục 0700 (siết lại mỗi lần ghi), tệp 0600.
- **Nội dung dòng:** `v`, `id`, `ch` (tên kênh), `ct` (loại kênh), `recv` (giờ nhận), `hdr` (CHỈ header chữ ký: `x-zevent-signature`, `x-hub-signature-256`, `x-stringee-signature`; không Authorization/Cookie/token), `raw` (base64 payload thô), `seal`. KHÔNG có App Secret, token, `google_key` (key này nằm trong payload của Google, đó là dữ liệu nhà cung cấp gửi; kiểm lại bằng khóa đã lưu của biểu mẫu), không có token `t` của tổng đài (nằm ở query, không lưu).
- **Kiểm lại khi chèn:** (1) `seal` = HMAC-SHA256 bằng khóa dẫn từ `encryption_key` của site (không lưu) trên (id, kênh, giờ nhận, SHA-256 payload, SHA-256 header): chứng minh dòng do app ghi sau khi đã xác thực và chưa bị sửa; (2) chữ ký nhà cung cấp kiểm lại được thì kiểm lại: Zalo (tính lại MAC, bỏ kiểm lệch giờ vì đã kiểm lúc nhận), Meta (HMAC App Secret), Google (`google_key`), Stringee (header chữ ký); Web Form (captcha dùng một lần) và Email chỉ dựa vào seal. Sai: xóa dòng, log chỉ có id/kênh/lý do.
- **Chỉ request đã qua xác thực** (và honeypot/giới hạn tần suất/trần/413 của Web Form) mới tới bước lưu nên mới vào hàng; kẻ tấn công không dùng được hàng để làm đầy đĩa. Giới hạn: `omni_spool_max_bytes` (50 MB) và `omni_spool_max_lines` (5.000); mỗi dòng <= 2 MB. Đầy: coi như hàng hỏng, cảnh báo và 503.
- **Chống ghi đồng thời:** `flock` độc quyền quanh kiểm tra giới hạn + `O_APPEND` + `fsync`. Tác vụ quét đổi tên `spool.jsonl` thành `spool.<ts>.work` dưới cùng khóa rồi xử lý, webhook tiếp tục ghi tệp mới; `drain.lock` không cho hai tác vụ quét chạy cùng lúc.
- **Dọn:** dòng chèn xong thì bị xóa (ghi lại tệp bằng tmp + rename); dòng hỏng/sai chữ ký/kênh đã xóa bị xóa ngay; dòng chưa chèn được thì GIỮ (không xóa tin) và cảnh báo khi quá 10 phút; chỉ khi quá `omni_spool_expire_days` (14 ngày) mới xóa kèm log nghiêm trọng + cảnh báo để không giữ dữ liệu cá nhân vô hạn.
- **Khi nào vào hàng:** sau khi xác thực, nếu parse/lưu gặp hết hạn chờ khóa, bế tắc, mất kết nối (`webhooks.is_db_unavailable`). Lỗi code (không phải DB) vẫn là 500 như trước. Lỗi DB TRƯỚC khi xác thực xong (tải kênh/bí mật từ DB) vẫn trả 5xx vì không xác thực được; đây là giới hạn đã biết.
- **Công tắc:** Omni Settings `webhook_spool_disabled` (Check, mặc định 0). Bật: không ghi hàng, trả 503 như trước; tác vụ quét vẫn chèn lại các dòng đã có.
- **`omni_webhook_lock_wait_seconds`:** giữ khóa cấu hình nhưng đổi nghĩa thành "chờ khóa bao lâu thì chuyển sang hàng dự phòng" (mặc định 3 giây thay vì 5). Site có giá trị cũ không lỗi.

## Hệ quả
- Phải có đĩa ghi được ở `private/omni_spool` và sao lưu/di chuyển site thì xóa thư mục này sau khi đã quét (nó chứa dữ liệu cá nhân chưa vào DB).
- Hệ thống nhiều máy web cần chạy `drain` ở máy có tệp (hoặc dùng chung đĩa); hiện tại một máy.
- "Xóa dữ liệu khách" không xóa được payload nằm trong hàng (chưa gắn Lead): API trả `spool_pending`; docs/12 G-V21-04-1.
- `spool_count()` / `oldest_age_seconds()` / `stats()` là hàm public để V21-08 (preflight).
