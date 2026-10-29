# D1-09 ★ · Chiều ra: sale trả lời từ hệ thống

**Phụ thuộc:** D1-07, D1-08. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/03 mục 8, docs/06 mục 6–7, docs/05 mục 5, docs/02 mục 6, docs/09.

Nhiệm vụ: sale trả lời khách Zalo ngay trong Hộp thư; mọi tin ra được ghi nhận và hiện trên dòng thời gian. Email vẫn trả lời bằng trình soạn thư của CRM (không làm lại).

Việc cần làm:
1. `pipeline/outbound.py`:
   - `queue_outbound(req: OutboundRequest, user) -> str` tạo `Omni Outbound Message(status=Queued)` và enqueue `send_outbound` với `job_id=f"omni-out::{name}"`.
   - `send_outbound(name)` đúng docs/03 mục 8 (công tắc, `can_send_now`, sandbox, gửi, phân loại lỗi, thử lại bằng `mark_retry` + `next_attempt_at`, ghi dòng thời gian bằng `timeline.append_outbound`).
   - Bổ sung mục 4 của `requeue_stuck` (docs/03 mục 6) cho tin ra.
   - Cập nhật realtime cho Hộp thư khi trạng thái đổi.
2. `api/inbox.py`:
   - `send_reply(lead, channel, text, confirm_paid=False)`: kiểm tra quyền ghi Lead; tìm Identity của Lead trên kênh đó; nếu `can_send_now` báo khoảng "có phí" và `confirm_paid` False → trả về yêu cầu xác nhận (không gửi). Văn bản rỗng hoặc > giới hạn → lỗi rõ ràng.
   - `request_phone(lead)`: gửi tin yêu cầu thông tin người dùng qua Zalo theo quy tắc docs/06 mục 6 (chưa có SĐT, `phone_requested_at` trống hoặc quá 7 ngày; gửi xong ghi `phone_requested_at`).
3. Hộp thư: thêm khung soạn tin cho hội thoại có Zalo; hiển thị trạng thái từng tin ra (Đang gửi/Đã gửi/Lỗi + lý do/Bị chặn + lý do); hộp xác nhận khi tin có thể tính phí; nút "Xin SĐT qua Zalo" (ẩn khi Lead đã có SĐT); với hội thoại email, nút "Trả lời email trong CRM" mở Lead.
4. Ghi nhận thư đi của sale từ CRM (Communication Sent gắn Lead): không cần làm gì thêm nếu CRM đã gắn; nếu chưa gắn thì gắn theo cách ở D1-07. Đảm bảo `bot/context` sau này đọc được cả tin ra Zalo, thư đi và oa_echo theo thời gian — viết hàm `conversation_turns(lead, until=None) -> list[dict]` trong `crm_bridge/timeline.py` trả mọi lượt vào/ra mọi kênh, có test. (Đây là dữ liệu cho bot chạy ngầm ở Đợt 2.)

Test: ca I-08, I-09 ở docs/10 mục 3; gửi trùng (enqueue 2 lần cùng outbound) → gửi 1 lần; khách chặn OA → Failed + lý do; token lỗi → làm mới → Sent; quá 48 giờ không xác nhận → không gửi; Sales User không có quyền Lead → bị từ chối; `conversation_turns` trả đúng thứ tự cho hội thoại trộn email + Zalo + oa_echo.

Hoàn thành khi: test xanh; trên staging ở sandbox_mode, sale gửi 5 tin → 5 Outbound `Sent` + `is_sandbox`, không có tin thật nào tới khách; sau đó (nếu anh cho phép, với tài khoản Zalo thử do anh chỉ định) tắt sandbox trên staging, gửi 1 tin thật tới tài khoản thử và dán bằng chứng.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Trả lời một khách Zalo thử từ Hộp thư: điện thoại nhận tin, Hộp thư hiện "Đã gửi".
2. Thử trả lời một khách đã im lặng hơn 7 ngày (đổi `last_inbound_at` trên staging): bị chặn kèm lý do dễ hiểu.
