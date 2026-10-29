# D3-02 ★ · Chuyển người, tạm dừng bot, thông báo

**Phụ thuộc:** D3-01. **Cần anh chốt:** Q6 (giờ bot hoạt động, câu ngoài giờ) và Q8 (kênh báo sale) ở docs/12. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08-bot-ai.md mục 6; docs/09 mục 4; docs/12 mục A (Q6, Q8); `utils/business_hours.py` (D2-03).

Việc cần làm:
1. Trường Đợt 3 (kiểm tra trước, chỉ tạo cái chưa có): `Omni Identity.bot_paused_until` (thường đã tạo ở D2-07); Omni Settings: `handoff_pause_hours`, `handoff_message_in_hours`, `handoff_message_after_hours`, `handoff_notify_channels`, `bot_active_hours` (Q6).
2. `bot/handoff.py::handoff(draft)` đúng docs/08 mục 6:
   - Đặt `bot_paused_until`; `reply_batch` bỏ qua khách đang tạm dừng (đã có điều kiện ở D2-07 — kiểm lại).
   - Thông báo người phụ trách Lead theo `handoff_notify_channels`: "system" = Notification Log + realtime; "email" = gửi email nếu thông báo hệ thống chưa được mở sau 10 phút (job hẹn giờ kiểm tra). Không có người phụ trách → `callback_fallback_user`. Nội dung: tên khách, kênh, lý do, liên kết Hộp thư — không chép toàn văn tin khách vào email.
   - Câu giữ chân: CHỈ ở chế độ tự động của nhóm (D3-05 sẽ gọi); ở Draft không gửi gì. Viết hàm `holding_message(now) -> str` dùng mẫu trong/ngoài giờ, điền `{gio_lam_viec_ke_tiep}` từ business_hours; có test.
   - Ghi lý do vào dòng thời gian Lead.
3. Hộp thư: nhãn "Bot đang tạm dừng đến …" và nút "Cho bot tiếp tục" (xóa `bot_paused_until`, ghi ai bấm); nút "Tạm dừng bot với khách này" thủ công.
4. Khách tự đòi gặp người (`wants_human`) hoặc nhóm always_handoff → luôn qua `handoff`, kể cả khi bot đề xuất reply.

Test: handoff đặt tạm dừng đúng giờ; bot không soạn cho khách đang tạm dừng; hết hạn tạm dừng thì soạn lại; thông báo đúng người, email chỉ gửi khi chưa mở sau 10 phút (giả lập thời gian); holding_message trong giờ, ngoài giờ tối thứ Sáu, trưa thứ Bảy; nội dung email không chứa tin khách.

Hoàn thành khi: test xanh; trên staging hỏi giá qua Zalo → sale nhận thông báo trong vài giây; Hộp thư hiện nhãn tạm dừng.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Nhắn "cho tôi gặp nhân viên": người phụ trách nhận thông báo, bot không soạn nháp cho các tin tiếp theo trong thời gian tạm dừng.
2. Bấm "Cho bot tiếp tục": tin tiếp theo có nháp trở lại.
