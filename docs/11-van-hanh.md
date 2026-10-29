# 11 · Vận hành

## 1. Màn hình giám sát (`/app/omni-monitor`, role Omni Admin)

| Khối | Nội dung | Ngưỡng cảnh báo |
| --- | --- | --- |
| Công tắc | Trạng thái mọi công tắc ở `docs/09` | — |
| Kênh | Mỗi kênh: lần nhận/gửi thành công gần nhất, lỗi gần nhất, hạn token Zalo | Không nhận tin nào > 4 giờ trong giờ làm việc; token còn < 1 giờ (tác vụ làm mới chạy khi còn < 2 giờ, nên còn < 1 giờ nghĩa là làm mới đã thất bại); làm mới token lỗi |
| Hàng đợi | Số tin New, Processing, Retry, Manual; tuổi tin New lâu nhất | New lâu nhất > 5 phút; Manual > 0 |
| Thời gian | p50/p95 từ nhận tin đến Done (24 giờ) | p95 > 2 phút |
| Chiều ra | Tin Queued/Failed/Blocked hôm nay | Failed > 5 trong 1 giờ |
| Nghi trùng | Số Candidate Open | > 20 |
| Bot (Đợt 2+) | Token dùng hôm nay / hạn mức, số Bot Run lỗi, tỉ lệ chuyển người | Dùng > 80% hạn mức; lỗi > 5% |
| Tổng đài (Đợt 2+) | Cuộc gọi hôm nay, gọi nhỡ, việc gọi lại quá hạn, số bù từ đối soát | Bù > 1%; việc quá hạn > 0 |

Nút thao tác: "Xử lý lại" tin Manual (đưa về New), "Quét ngay", "Làm mới token Zalo".

## 2. Cảnh báo

- Gửi email tới `alert_recipients` và tạo Notification Log cho Omni Admin.
- Mỗi loại cảnh báo tối đa 1 lần / 30 phút (khóa Redis), kèm số liệu và liên kết màn hình giám sát.
- Không đưa bí mật hay nội dung tin khách vào cảnh báo.

## 3. Sổ tay sự cố

| Triệu chứng | Kiểm tra | Xử lý |
| --- | --- | --- |
| Tin New tăng mãi | `bench doctor`, worker `omni` có chạy? Redis? | Khởi động worker; bấm "Quét ngay"; tăng `background_workers` nếu do tải |
| Nhiều tin Manual | Xem `last_error` nhóm theo lỗi | Sửa nguyên nhân, chọn tin, "Xử lý lại" |
| Zalo không gửi được | Hạn token, lỗi làm mới, quyền OA | "Làm mới token"; không được → cấp quyền lại qua luồng OAuth |
| Không nhận được tin Zalo | Cấu hình webhook phía Zalo, xác thực tên miền, chứng chỉ HTTPS, log 401 | Sửa cấu hình; nếu lỗi chữ ký hàng loạt → kiểm tra secret |
| Bot trả lời sai với khách | Tìm Bot Draft, Bot Run, nhóm câu hỏi | **Tắt `auto_send_enabled` của nhóm** ngay; sale gửi đính chính; sửa kho tri thức; chạy lại bộ đề |
| Chi phí AI tăng vọt | Bot Run theo giờ, kind | Giảm hạn mức; kiểm tra vòng lặp gọi lại |
| Lộ `webhook_token` tổng đài | — | Đổi token trên Omni Channel và phía nhà cung cấp (Stringee: `docs/huong-dan/cau-hinh-stringee.md` mục 6) |

## 4. Quay lui

| Mức | Làm gì | Ảnh hưởng |
| --- | --- | --- |
| 1 | Tắt công tắc phần lỗi (bot, nhóm câu hỏi, chiều ra, kênh) | Chỉ phần đó dừng; dữ liệu vẫn được lưu |
| 2 | Đặt `bot_reply_mode = Off` và `bot_analyze_enabled = 0` | Hệ thống thành "kênh vào CRM" như Đợt 1 |
| 3 | Deploy lại phiên bản app trước (git tag) + `bench migrate` | Cần quy trình deploy; migration phải tương thích ngược trong mỗi đợt |
| 4 | Tắt `enabled` của kênh | Tin vẫn được lưu, không xử lý; xử lý bù sau |

Quy tắc deploy: mỗi đợt gắn tag `dot-N-rcX`; migration chỉ **thêm** trường/DocType trong một đợt, không xóa hay đổi kiểu trường đang dùng.

## 5. Nhịp vận hành

| Tần suất | Việc | Người |
| --- | --- | --- |
| Hằng ngày | Xử lý tin Manual, nghi trùng, việc gọi lại quá hạn | Quản trị hệ thống |
| Hằng tuần | Xem nháp bị sửa/bỏ, cập nhật kho tri thức, chạy lại bộ đề | Phụ trách kho tri thức |
| Hằng tháng | Báo cáo kênh, chi phí AI, rà quyền | Chủ dự án |
| Hằng quý | Xem lại bảng điểm Lead theo tỉ lệ chốt thực tế | Trưởng sale |

## 6. Dữ liệu và lưu trữ

- `raw_payload` xóa sau `raw_payload_retention_days`; ghi âm xóa sau `recording_retention_days` (tác vụ đêm).
- Sao lưu DB hằng ngày (bench backup) và kiểm tra khôi phục mỗi quý.
- Yêu cầu xóa dữ liệu của khách: Omni Admin dùng thao tác "Xóa dữ liệu khách" trên Lead → xóa nội dung tin vào/ra, ghi âm, bản ghi bot của Lead, giữ bản ghi kỹ thuật ẩn danh. (Làm ở D1-10 ở mức tối thiểu.)

## Lưu ý D1-05: khóa tín hiệu `sig:`

Khóa Redis `omni:lock:sig:{SĐT/email}` (ADR 0003) có timeout 60 giây và KHÔNG được gia hạn; chờ tối đa 20 giây rồi ném `TransientError` (tin thử lại theo backoff). Nếu bước tìm + tạo Lead một lô kéo dài hơn 60 giây (ví dụ gọi mạng chậm, DB nghẽn) khóa hết hạn và lượt khác có thể chen vào, mất bảo đảm chống trùng. Triệu chứng: nhiều tin `Retry` với lỗi "Khóa tín hiệu định danh đang bận", hoặc Candidate cùng SĐT tăng bất thường. Khi gặp, kiểm tra độ trễ DB trước.

## Lưu ý D1-08: developer_mode

`zalo_skip_signature` (bỏ qua xác thực chữ ký webhook Zalo) chỉ có tác dụng khi `developer_mode` của site bật (đọc bằng `cint`, chuỗi "0" là tắt). **Cấm bật `developer_mode` ở production**: nó mở luôn đường bỏ qua chữ ký. Kiểm tra `bench --site <site> console` -> `frappe.conf.developer_mode` trước mỗi lần phát hành.

## Lưu ý D1-09: tắt chiều ra của kênh

Tắt `outbound_enabled` của một kênh thì mọi tin ra đang `Queued` của kênh đó chuyển `Blocked` với lý do "Kênh đã tắt chiều ra" khi worker/tác vụ quét chạm tới. Bật lại KHÔNG tự gửi lại những tin đã `Blocked`: sale phải gửi lại thủ công từ Hộp thư.

## Lưu ý D1-10: giám sát, cảnh báo, xóa dữ liệu khách

- **Màn hình** `/app/omni-monitor` (chỉ Omni Admin): công tắc, kênh (nhận/gửi gần nhất, lỗi, hạn token Zalo), hàng đợi, p50/p95 24 giờ, chiều ra hôm nay, nghi trùng, danh sách tin Manual để "Xử lý lại", "Quét ngay", "Làm mới token Zalo". Tự làm mới 30 giây. Số liệu p50/p95 = `modified - creation` của tin Done (worker đặt `modified` lúc Done); "Failed trong 1 giờ" tính theo `creation` của tin ra. Khối Bot/Tổng đài thuộc Đợt 2.
- **Cảnh báo** `monitoring.alerts.run_checks` chạy mỗi 5 phút cùng tác vụ quét. Luật: tin New cũ nhất > 5 phút; Manual > 0; p95 > 2 phút; tin ra Failed > 5 trong 1 giờ; Candidate Open > 20; kênh bật không nhận tin > 4 giờ làm việc (chỉ trong giờ làm việc); token Zalo còn < 1 giờ hoặc lần làm mới gần nhất lỗi (không áp khi sandbox bật). Chống lặp 30 phút mỗi loại (mỗi kênh với luật theo kênh) bằng khóa Redis `omni:alert:<loại>:<kênh>`; xóa khóa này (`bench --site <site> clear-cache` hoặc xóa key) để buộc gửi lại. Nếu Redis cache lỗi, cảnh báo vẫn gửi (có thể lặp). Muốn tắt thư: để trống `alert_recipients`.
- **Tắt worker 10 phút** để thử: sau ~5-10 phút nhận đúng một thư "Tin chờ xử lý quá lâu" (và thêm một thư sau mỗi 30 phút nếu vẫn kẹt).
- **Dọn raw_payload** 02:00 hằng đêm, theo lô 500, tối đa ~240 giây mỗi lần, chỉ tin đã xử lý xong; đặt `raw_payload_retention_days = 0` để tắt.
- **Xóa dữ liệu khách**: trang `omni-monitor` > "Xóa dữ liệu khách" (Lead + lý do + xác nhận) hoặc API `omni_crm.api.admin.erase_customer_data`. Xóa nội dung tin vào/ra theo Lead và các Identity của Lead, ghi Comment (lý do, người làm); không xóa Lead. LƯU Ý: chưa xóa Communication do timeline tạo, tên/SĐT/email trong Identity, `identity_key`, Comment liên kết (xem docs/12 mục D) nên chưa đủ cho yêu cầu pháp lý đầy đủ.
- **Email Account của Frappe** (kênh email, docs/05): trên Email Account dùng cho kênh phải để cờ tạo Lead của CRM = 0 và `append_to` trống, nếu không CRM tự tạo Lead song song với Omni (trùng Lead).
- **Supervisor/worker**: production phải có tiến trình `bench worker --queue omni`, tối thiểu 2 worker (debounce 5 giây giữ 1 worker mỗi khách; 1 worker xử lý ~20 khách khác nhau mất ~100 giây). Thiếu worker thì tin New tăng và cảnh báo "tin chờ lâu" nổ.
- **Redis queue** phải chạy: không có thì không enqueue được job nền, không tạo được Comment trên Lead (hook CRM enqueue).
- Giữ nguyên các lưu ý D1-05 (`sig:`), D1-08 (cấm `developer_mode` ở production) và D1-09 (tắt `outbound_enabled` => tin Blocked, không tự gửi lại) ở trên. Bảo vệ 1 MB cho webhook cần đặt thêm `client_max_body_size 1m;` ở nginx cho `/api/method/omni_crm.api.webhooks.*` (code đã chặn ở tầng ứng dụng nhưng nginx chặn sớm hơn).

## Lưu ý D1-11: kiểm thử tải, sự cố, nghiệm thu Đợt 1

Số liệu và kết luận đầy đủ ở `docs/nghiem-thu/dot-1.md`. Điều vận hành rút ra:

- **Số worker `omni`.** Mỗi lượt xử lý giữ một worker khoảng `debounce_seconds` (mặc định 5 giây) cộng thời gian xử lý (~1 giây), kể cả khi lượt đó chỉ có một tin. Với nhiều khách khác nhau nhắn rải đều, công suất xấp xỉ `số_worker / 6` khách mỗi giây. Đo thật (máy dev 16 nhân): 4 worker xử lý ~0,75 tin/giây khi mỗi khách chỉ nhắn một tin mỗi lượt, backlog tăng khi tin đến 1,67 tin/giây; 12 worker giữ backlog dưới ~200 tin và cạn trong vài phút. Quy tắc: `số_worker >= tin_mỗi_giây_ở_giờ_cao_điểm x 6 x 1,5`. Khi khách nhắn dồn (nhiều tin cùng khách) các tin gộp thành một lượt nên công suất cao hơn nhiều (4 worker xử lý 6.000 tin dồn của 300 khách trong ~7 phút). Cảnh báo "Tin chờ xử lý quá lâu" (> 5 phút) là tín hiệu để thêm worker.
- **URL webhook.** Đặt `?ch=<tên kênh>` trên URL (đặt tên kênh không có khoảng trắng cho dễ cấu hình). Từ D1-11 mã đọc `ch` từ query string của request; trước đó Frappe bỏ query khi thân JSON nên webhook thật trả 404.
- **Khóa DB kéo dài.** Webhook không tự giới hạn thời gian chờ khóa của DB: khi bảng tin vào bị khóa, webhook chờ tới `innodb_lock_wait_timeout` (50 giây mặc định) hoặc `gunicorn -t` (120 giây) thay vì trả lời sau 5 giây; Zalo gửi lại thì khử trùng lặp xử lý (xem docs/12 mục D). Kiểm tra độ trễ DB khi thấy p95 webhook tăng.
- **Redis queue chết.** Webhook vẫn lưu tin và trả 200 (lỗi enqueue bị bắt); tin ở trạng thái `New`. Khi Redis lên lại, job đã mất, tác vụ quét (`requeue_stuck`, mỗi 5 phút, tin New > 2 phút) đẩy lại. Chắc chắn scheduler của site chạy (`bench --site <site> scheduler status`), nếu không thì bấm "Quét ngay" ở `/app/omni-monitor`.
- **Worker bị kill (`kill -9`, mất điện).** Không mất tin, nhưng phục hồi chậm: đo thật ~15 phút. Tin của các khách đang xử lý vẫn `New`; job bị bỏ lại trong StartedJobRegistry của RQ làm lần đẩy lại của tác vụ quét (dùng `deduplicate`) không có tác dụng cho tới khi RQ dọn kho đăng ký (timeout job 300 giây + chu kỳ bảo trì), sau đó lần quét kế tiếp đẩy được. Triệu chứng: cảnh báo "Tin chờ xử lý quá lâu" ngay sau khi khởi động lại worker. Cứ để tự phục hồi, hoặc bấm "Quét ngay" sau khi kho đã dọn. Tin kẹt `Processing` quá 15 phút được chuyển `Retry` như docs/03 mục 6. Khóa theo khách hết hạn sau tối đa 60 giây. Xem docs/12 mục D.
- **Đo chỉ số nghiệm thu.** `bench --site <site> execute omni_crm.tests.acceptance.measure_dot1.run --kwargs '{"start": "...", "end": "..."}'`. Giờ `start`/`end` theo múi giờ của site (kiểm tra `System Settings.time_zone`; trên omni.test lệch so với giờ máy).
