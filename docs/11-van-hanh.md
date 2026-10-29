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
| Lộ `webhook_token` tổng đài | — | Đổi token trên Omni Channel và phía nhà cung cấp |

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
