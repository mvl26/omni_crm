# D1-08 ★ · Kênh Zalo OA

**Phụ thuộc:** D1-06. Có thể song song với D1-07. **Thời lượng dự kiến:** 2,5 ngày.
**Cần anh cung cấp trước:** App ID, OA ID, khóa bí mật của ứng dụng và OA trên Zalo for Developers (nhập vào Omni Channel trên site dev/staging, không gửi trong chat); tên miền HTTPS đã xác thực với Zalo để thử thật ở cuối.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/06-kenh-zalo-oa.md (toàn bộ — đọc phần cảnh báo đầu tệp), docs/03 mục 1–2, 8, docs/02 mục 3, 4.

Nhiệm vụ: kênh Zalo OA đầy đủ chiều vào, quản lý token, client gửi tin (hàm gửi dùng ở D1-09).

Bước 0 — G7, G8, G9: dùng WebFetch/WebSearch đọc tài liệu chính thức developers.zalo.me về: xác thực sự kiện webhook (header, công thức, ví dụ), danh sách sự kiện và cấu trúc payload, OAuth v4 cho OA (cấp quyền, đổi code, làm mới token, PKCE), API gửi tin tư vấn và tin yêu cầu thông tin người dùng, API lấy thông tin người dùng, mã lỗi, quy định khung thời gian và phí tin tư vấn. Ghi từng điểm vào docs/12 mục C kèm đường link. Mọi điểm khác docs/06 → ghi docs/12 và sửa theo tài liệu chính thức (nếu thay đổi hành vi với khách thì ghi ADR "Đề xuất" và dừng hỏi).

Việc cần làm:
1. `channels/zalo/signature.py::verify(raw_body, headers, app_id, oa_secret_key, now)` + kiểm tra timestamp lệch ≤ 10 phút; `compare_digest`. Tôn trọng `zalo_skip_signature` CHỈ khi site không phải production (`frappe.conf.developer_mode` hoặc cờ môi trường bạn xác định rõ, ghi trong báo cáo); production bật cờ đó → từ chối và cảnh báo.
2. `channels/zalo/adapter.py::ZaloAdapter`: `verify_request`, `parse` (ánh xạ sự kiện theo docs/06 mục 4 và kết quả bước 0; `oa_echo` lấy khách từ recipient), `send`, `can_send_now` (docs/06 mục 7, trả lý do bằng tiếng Việt cho người dùng). Đăng ký trong registry.
3. `channels/zalo/client.py::ZaloClient`: gọi API qua `requests` với timeout 10 giây; tự làm mới token một lần khi gặp lỗi token rồi thử lại; không log token. Hàm: `send_text(user_id, text)` (tách tin quá dài, giữ thứ tự), `send_request_user_info(user_id, title, subtitle, image_url)`, `get_user_profile(user_id)`.
4. `channels/zalo/errors.py`: bảng ánh xạ mã lỗi Zalo → (loại: tạm thời/vĩnh viễn/token/khung gửi, thông điệp tiếng Việt), có test.
5. `channels/zalo/token.py`: `refresh_if_needed()` theo docs/06 mục 2 (khóa Redis, lưu cả hai token trong một transaction, cảnh báo khi lỗi); bật lịch `*/30 * * * *`. Luồng cấp quyền ban đầu: `api/webhooks.py::zalo_oauth_start` (chỉ Omni Admin, sinh `state`, chuyển hướng tới trang cấp quyền) và `zalo_oauth_callback` (kiểm `state`, đổi code, lưu token). Thêm nút "Kết nối Zalo OA" trên form Omni Channel.
6. Worker: lần đầu gặp một Zalo user id (Identity mới) → gọi `get_user_profile` trong worker, lưu display_name; lỗi không làm hỏng lượt. Sự kiện `user_info` → cập nhật SĐT/email của Identity là tín hiệu mạnh. `follow/unfollow` → `is_follower`; không tạo Lead trừ khi `zalo_create_lead_on_follow`. Mọi tin vào cập nhật `last_inbound_at` (trừ oa_echo).
7. `oa_echo`: ghi vào dòng thời gian như tin ra từ "Sale (ngoài hệ thống)" và tạo `Omni Outbound Message(source=Sale, status=Sent, external_message_id=…)` để không gửi lại và để dùng làm dữ liệu so sánh bot ở Đợt 2.
8. Payload mẫu cho mọi loại sự kiện trong `tests/fixtures/zalo/` (lấy cấu trúc từ tài liệu chính thức, giá trị giả).

Test: toàn bộ ca ở docs/06 mục 9; refresh token đồng thời (2 thread) chỉ 1 lần gọi API; token hết hạn khi gửi → làm mới → gửi lại thành công (mock); `send_text` ở sandbox_mode không có HTTP.

Thử thật (nếu anh đã cung cấp tài khoản và tên miền): cấp quyền OA trên staging, gửi tin từ một tài khoản Zalo thử tới OA → Lead xuất hiện, tin hiện trong Hộp thư. Dán bằng chứng (mã Lead, thời gian). Không có điều kiện thử thật thì ghi rõ "chưa thử thật".

Hoàn thành khi: test xanh; docs/12 có G7–G9 với link; nút "Kết nối Zalo OA" hoạt động trên dev (ít nhất tới bước chuyển hướng).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Đọc docs/12 mục C phần Zalo: công thức chữ ký và khung gửi tin có link tài liệu chính thức.
2. Từ điện thoại, nhắn OA: tin vào Hộp thư trong vòng ~10 giây.
3. Nhắn OA từ ứng dụng Zalo OA (nhân viên trả lời ngoài hệ thống): tin đó cũng hiện trong Hộp thư là tin ra.
