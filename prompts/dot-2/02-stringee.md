# D2-02 ★ · Adapter Stringee

**Phụ thuộc:** D2-01. **Thời lượng dự kiến:** 1,5 ngày.
**Cần anh cung cấp:** tài khoản Stringee (API key SID/secret, đầu số, dự án) — nhập vào Omni Channel trên staging. Nếu công ty chọn nhà cung cấp khác, dùng prompt này làm mẫu và thay tên nhà cung cấp.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/07-kenh-tong-dai.md (mục 3, 4 và interface ở mục 1).

Nhiệm vụ: cài đặt `StringeeProvider` theo interface `TelephonyProvider`. Không đổi interface; nếu thấy interface thiếu, ghi ADR "Đề xuất" và dừng hỏi.

Bước 0 — G10: dùng WebFetch đọc tài liệu chính thức developer.stringee.com: xác thực REST API (JWT), cấu hình get_customer_info_url (tham số, định dạng phản hồi chờ đợi, timeout), event_url (các loại sự kiện, cấu trúc payload, trạng thái), ghi âm (record action, eventUrl, recording_url, tải file, thời hạn lưu), Call log API (tham số, phân trang), và Stringee có ký webhook không. Ghi từng điểm vào docs/12 mục C kèm link.

Việc cần làm:
1. `channels/telephony/stringee.py::StringeeProvider`:
   - `_jwt(channel)`: JWT HS256 theo docs/07 mục 3 (dùng thư viện `PyJWT` nếu đã có trong bench, nếu không thì `hmac` + base64url tự viết có test), hạn ngắn (ví dụ 1 giờ), cache đến gần hết hạn.
   - `verify_request`: theo kết quả G10 (chữ ký nếu có; nếu không, dựa vào webhook_token đã kiểm ở khung chung và trả True).
   - `parse_event`, `parse_lookup_request`, `lookup_response` đúng định dạng tài liệu.
   - `fetch_recording(recording_ref)`: GET kèm JWT, trả (bytes, mime). Giới hạn kích thước (ví dụ 100 MB), timeout 60 giây.
   - `list_calls(since, until)`: phân trang tới hết.
   - `click_to_call`: `NotImplementedError` kèm thông điệp rõ.
   - Ánh xạ trạng thái Stringee → trạng thái chuẩn (Answered/Missed/Busy/Failed/Voicemail) thành bảng hằng số có test.
2. Nếu Stringee không ký webhook: trong worker, trước khi tạo việc gọi lại cho cuộc gọi nhỡ, xác minh `call_id` qua Call log API; API lỗi → xử lý nhưng đặt `Omni Call.unverified = 1` và tăng bộ đếm cảnh báo (docs/07 mục 3, đoạn cuối).
3. Payload mẫu trong `tests/fixtures/stringee/` theo tài liệu chính thức (giá trị giả).
4. Viết `docs/huong-dan/cau-hinh-stringee.md`: các bước cấu hình trên trang quản trị Stringee (URL tra cứu, URL sự kiện có `ch` và `t`, bật ghi âm, timeout), và cách đổi `webhook_token` khi lộ.

Test: JWT đúng cấu trúc (giải mã kiểm header/payload/chữ ký); parse mọi payload mẫu; lookup_response đúng định dạng; ánh xạ trạng thái; list_calls phân trang (mock HTTP); fetch_recording quá lớn bị từ chối.

Thử thật (nếu có tài khoản): cấu hình URL trên Stringee trỏ staging, gọi vào đầu số từ một điện thoại: chuông reo có tra cứu, gác máy có Omni Call và CRM Call Log; để nhỡ một cuộc. Dán bằng chứng hoặc ghi "chưa thử thật".

Hoàn thành khi: test xanh; docs/12 có G10 với link; tài liệu cấu hình Stringee đã viết.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Gọi vào đầu số trên staging: Lead/Call Log xuất hiện sau khi gác máy.
2. Đọc `docs/huong-dan/cau-hinh-stringee.md`: một người IT khác làm theo được.
