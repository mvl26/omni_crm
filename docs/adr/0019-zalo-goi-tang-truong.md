# ADR 0019 — Zalo gói Tăng trưởng: xin số điện thoại, chọn loại tin, quỹ tin ngoài 48 giờ (V3-07)

- Trạng thái: Chấp nhận (2026-11-10)
- Người quyết định: anh Việt (brief "Xin số điện thoại, định tuyến tin Zalo, chatbot kịch bản", 10/11/2026). Phạm vi danh sách trắng chốt trong phiên cùng ngày: chỉ chặn tin MỚI (thẻ xin số, ZBS); tin sale trả lời khách giữ nguyên.

## Quyết định

1. **Không thêm DocType.** Thêm trường đúng như brief:
   - Omni Settings: `phone_request_enabled` (công tắc tổng, mặc định 0), `phone_request_title`, `phone_request_subtitle`, `phone_request_image_url`, `zalo_quota_total` (500), `zalo_quota_cycle_end` (2027-11-09).
   - Omni Outbound Message: `message_kind` (CS 48h / CS ngoai 48h / ZBS Template), `purpose` (reply / followup / transaction / request_info), `zbs_template_id`, `quota_consumed`, `approved_by`.
2. **Xin số** (`channels/zalo/phone_request.py`), làm tiếp luồng có sẵn (nút xin số của Hộp thư, sự kiện `user_submit_info`), không viết luồng song song.
   - Tự động 60 giây sau tin của khách. Hẹn trong Redis (sorted set), job mỗi phút `run_due` lấy ra bằng ZREM rồi kiểm lại mọi điều kiện trước khi gửi.
   - Nút tay trên tab Zalo.
   - Điều kiện: định danh Zalo có Lead, `phone_e164` trống, không xin trong 30 ngày (nút tay: không xin trong phiên 48 giờ), khách nhắn trong 48 giờ, bot không tạm dừng (chỉ tự động), không từ chối liên hệ.
3. **Danh sách trắng** (`WHITELIST_USER_IDS`, chỉ tài khoản Zalo thử nghiệm `277477470109110101`):
   - Kiểm trong `ZaloClient.send_request_user_info`, trước cả nhánh sandbox, khi `phone_request_enabled` tắt.
   - Không áp cho tin sale trả lời (quyết định của anh Việt).
4. **Nhận số.** `user_submit_info` đọc cả `info.full_address{...}` (ví dụ trong tài liệu) lẫn trường phẳng (bảng mô tả).
   - Số được chuẩn hóa E.164 ngay ở adapter. Số hỏng thì bỏ và ghi Error Log đã che (`+8498***6848`).
   - Bộ khớp định danh có sẵn ghi `phone_e164` và điền SĐT Lead còn trống.
   - Lead đã có số khác thì giữ nguyên số đó và thêm MỘT bình luận.
   - Nghi trùng tạo ngay, lý do ghi "Trùng số điện thoại từ Zalo".
5. **Chọn loại tin** (`channels/zalo/routing.pick_message_kind`).
   - `pipeline.outbound.queue_outbound` gọi bộ chọn cho mọi tin Zalo không có `route` sẵn; Hộp thư/tab Zalo gọi trước để hỏi sale. Vì vậy không có đường xếp hàng tin Zalo nào bỏ qua bộ chọn.
   - Ngoài 48 giờ thì ném `NeedsApproval`: sale phải bấm đồng ý, `approved_by` ghi người bấm.
   - `transaction` không bao giờ đi bằng tin tư vấn.
   - `send_outbound` kiểm lại loại tin ngay trước khi gửi.
   - Zalo trả `data.quota.quota_type` sau mỗi lần gửi: `reply` = trong khung 48 giờ; `sub_quota`, `purchase_quota`, `reward_quota` = ăn vào quỹ. Giá trị này ghi đè `quota_consumed`.
6. **Quỹ** (`channels/zalo/quota.py`, `quota_watch.py`).
   - Đếm `quota_consumed = 1`, `Sent`, không sandbox, trong chu kỳ 1 năm kết thúc ở `zalo_quota_cycle_end`.
   - Ngưỡng 80/95/100%: cảnh báo một lần mỗi mức mỗi chu kỳ.
   - Job 07:15 hằng ngày đối chiếu với `POST /v3.0/oa/quota/message` (sub_quota) và tìm tin Zalo không có `message_kind`.
   - Patch `v3.backfill_zalo_message_kind` gắn loại tin cho tin cũ trong chu kỳ.
7. **Móc nối chatbot.**
   - Tin Sale gửi đi, kể cả `oa_echo` có `sender.admin_id`, đặt `bot_paused_until` = +24 giờ (không rút ngắn).
   - `oa_echo` không có `admin_id` (chatbot/kịch bản/ứng dụng khác) ghi nguồn `Bot` và không tính là sale phản hồi.
   - `sent_by` của echo lấy theo bảng Omni Agent Map của kênh (`agent_ref` = `admin_id`).

## Lệch so với brief (có chủ đích)

- **Tên site.** Site là `miyano`, không phải `crm.miyano.com.vn`: bench riêng đó đã gỡ.
- **`phone_requested_at` được giữ chỗ trước khi gọi Zalo.**
  - Mốc trả về giá trị cũ khi Zalo từ chối chắc chắn. Acceptance "gửi thất bại → không ghi mốc" vẫn đúng.
  - Lỗi mạng mơ hồ thì GIỮ mốc, để không lặp thẻ cho khách thật.
- **Cặp Lead đã có Duplicate Candidate ở bất kỳ trạng thái nào thì không tạo thêm.** Brief chỉ chặn trạng thái Open; hành vi có sẵn chặt hơn.
- **Tiêu đề mặc định của thẻ.** Brief ghi "Miền xin phép…", em sửa thành "Miyano xin phép nhận thông tin liên hệ" (đoán là lỗi gõ). Sửa được trong Omni Settings.
- **Không tự điền Omni Agent Map trên kênh `zalo-mvl`.**
  - Đó là bảng con của cấu hình kênh, brief cấm đụng.
  - Mã `admin_id` chỉ biết sau khi có tin echo thật.
  - Người vận hành tự điền.
- **Chưa gửi ZBS Template.** Chưa xác minh được liên kết OA ↔ App ↔ tài khoản ZBS (brief mục 9.4). `transaction` luôn báo lỗi rõ.
- **Bảng giá Zalo ghi gói Tăng trưởng có 500 tin ngoài 48 giờ MỖI THÁNG, brief ghi mỗi năm.**
  - Hệ thống đếm theo năm như brief, tổng và hạn chu kỳ sửa được trong Omni Settings.
  - Job đối chiếu báo số Zalo thực tế.

## Hệ quả

- Tắt luồng xin số: bỏ tích `phone_request_enabled`. Khi đó hệ thống không tự gửi thẻ cho khách, chỉ tài khoản thử nghiệm nhận được.
- Chọn loại tin và chốt quỹ luôn chạy, không có công tắc. Đây là chốt chặn chi phí, không phải tính năng.
