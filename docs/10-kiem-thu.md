# 10 · Kiểm thử

## 1. Tầng kiểm thử

| Tầng | Công cụ | Chạy khi | Ví dụ |
| --- | --- | --- | --- |
| Đơn vị | `FrappeTestCase` (`frappe.tests.utils`) hoặc `unittest` thuần cho hàm không cần DB | Mỗi commit (CI) | Chuẩn hóa SĐT, chữ ký Zalo, chốt chặn bot |
| Tích hợp | `FrappeTestCase` trên site test, Redis thật, gọi worker đồng bộ | Mỗi commit (CI) | Webhook → tin vào → Lead → dòng thời gian |
| Hợp đồng kênh | Payload mẫu trong `tests/fixtures/<kênh>/` | Mỗi commit | Mỗi loại sự kiện Zalo/Stringee |
| Tải | Script `tests/load/send_burst.py` (httpx, ký payload thật) | Trước mỗi đợt, trên staging | Gấp 3 lần ngày cao điểm |
| Sự cố | Kịch bản tay có script hỗ trợ | Trước mỗi đợt, trên staging | Tắt Redis, tắt AI, khởi động lại worker |
| Bot | Bộ đề `eval/` + `bot/eval/run.py` | Mỗi lần đổi câu lệnh, kho tri thức, mô hình | Độ chính xác, ca nghiêm trọng |
| Người dùng | Kịch bản UAT cho sale | Tuần chạy thử | Trả lời Zalo, gộp Lead, duyệt nháp |

## 2. Quy tắc

- Không gọi API ngoài thật trong CI. Dùng:
  - `FakeZaloClient`, `FakeProvider` (tổng đài), `FakeAIClient` (trả kết quả cố định theo kịch bản), `FakeSTT`.
  - Mock HTTP bằng `unittest.mock` hoặc thư viện `responses`; test khẳng định **không có request** khi `sandbox_mode` bật.
- Mỗi lỗi sửa xong phải có test tái hiện lỗi đó trước khi sửa.
- Test không phụ thuộc thứ tự chạy; mỗi test tự tạo và dọn dữ liệu (dùng rollback của `FrappeTestCase`).
- Worker trong test chạy đồng bộ: gọi thẳng `process_identity(identity_key)` với `debounce_seconds = 0`.
- Không sửa test để qua. Test sai thì ghi lý do trong commit và để người duyệt đồng ý.

## 3. Kịch bản tích hợp bắt buộc (Đợt 1)

| # | Kịch bản | Kỳ vọng |
| --- | --- | --- |
| I-01 | Khách Zalo mới gửi 3 tin liền | 1 Lead, 1 lượt xử lý (cùng `batch_id`), 3 dòng thời gian |
| I-02 | Zalo gửi lại cùng sự kiện 5 lần | 1 bản ghi tin vào |
| I-03 | Khách Zalo gửi form SĐT, sau đó email (chữ ký có SĐT đó) | 1 Lead |
| I-04 | Worker lỗi giả lập 2 lần rồi thành công | Done, `attempts = 2` |
| I-05 | Worker lỗi 3 lần | Manual, có cảnh báo |
| I-06 | Tin Processing kẹt 15 phút | Tác vụ quét đưa về Retry rồi Done |
| I-07 | Tắt kênh, gửi 10 tin, bật lại | 10 tin được lưu, xử lý đủ sau khi bật và chạy quét |
| I-08 | Sale trả lời Zalo từ hộp thư ở sandbox | Outbound Sent, `is_sandbox = 1`, không có HTTP |
| I-09 | Trả lời khách quá 7 ngày | Blocked, lý do rõ |
| I-10 | Webhook sai chữ ký | 401, không có bản ghi |
| I-11 | Thư tự động | Ignored, không Lead |
| I-12 | Hai Lead cùng SĐT, tin mới | Gắn Lead gần nhất + 1 nghi trùng |
| I-13 | Gộp hai Lead | Mọi bản ghi liên quan chuyển về Lead chính |

## 4. Kiểm thử tải (`tests/load/send_burst.py`)

- Tham số: `--site-url`, `--channel`, `--rate` (tin/giây), `--duration`, `--customers` (số khách giả), `--dup-ratio` (tỉ lệ gửi lại).
- Ký payload đúng như Zalo (dùng secret của kênh staging).
- Đo: p50/p95/p99 thời gian phản hồi webhook; thời gian đến khi số tin `New` + `Processing` về 0; số Lead tạo ra so với số khách giả (phải bằng nhau); số tin vào so với số gửi (trừ trùng).
- Đạt khi: p95 webhook < 1 giây; mọi tin xử lý xong trong 10 phút sau đợt cao điểm; không mất, không trùng.

## 5. Kiểm thử sự cố

| Sự cố | Cách tạo | Kỳ vọng |
| --- | --- | --- |
| Redis tắt 2 phút | `sudo systemctl stop redis…` trên staging | Webhook vẫn lưu tin (enqueue lỗi được bắt và ghi log); tác vụ quét đẩy lại khi Redis lên |
| Worker khởi động lại giữa lượt | `bench restart` | Tin kẹt được quét về Retry rồi Done |
| AI lỗi/timeout | `FakeAIClient` trả lỗi hoặc chặn mạng tới API | Lượt xử lý vẫn Done; Bot Run ghi Error; không có nháp |
| Token Zalo hết hạn | Đặt `zalo_token_expires_at` quá khứ, token sai | Làm mới; thất bại thì cảnh báo; tin vào vẫn được lưu |
| Kênh gửi lại tin | Gửi trùng payload | Không trùng |
| Cơ sở dữ liệu chậm | Khóa bảng giả lập | Webhook không quá 5 giây; tin vẫn được lưu hoặc kênh gửi lại |

## 6. Nghiệm thu theo đợt

Mỗi prompt `*-kiem-thu-dot-*.md` sinh ra `docs/nghiem-thu/dot-N.md` gồm: kết quả mọi test tự động, kết quả tải, kết quả sự cố, chỉ số trong tuần chạy thử (đối chiếu bảng mục tiêu ở `docs/00`), danh sách lỗi còn mở và mức độ, khuyến nghị phát hành hay không.
