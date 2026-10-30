# D3-01 ★ · Nháp của bot cho sale duyệt

**Phụ thuộc:** Đợt 2 đã phát hành; bộ đề đạt cổng `nhap` (chạy D2-08 với `gate='nhap'`). **Thời lượng dự kiến:** 2 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08-bot-ai.md mục 5.5, 10; docs/02 mục 13; docs/06 mục 7; giao diện Hộp thư hiện tại (D1-06, D1-09).

Nhiệm vụ: bật chế độ Draft: nháp của bot hiện trong Hộp thư, sale chọn Gửi / Sửa rồi gửi / Bỏ, và chấm nháp. Bot vẫn KHÔNG tự gửi (tự động làm ở D3-05).

Bước 0: chạy bộ đề với cổng `nhap` trên prompt và kho tri thức hiện hành. Không đạt → DỪNG, báo cáo các chỉ số và ca trượt; không làm tiếp.

Việc cần làm:
1. `bot_reply_mode` thêm giá trị "Draft" (đã có trong Select; kiểm tra luồng): ở Draft, `reply_batch` tạo nháp `mode=Draft`. Nháp cũ của cùng Lead còn Pending khi có tin khách mới → `Expired`.
2. Hộp thư:
   - Hiển thị nháp ngay dưới tin khách cuối, phân biệt rõ là "Nháp của bot — chưa gửi", kèm nhóm câu hỏi và các mục kho tri thức đã dùng (bấm để xem nội dung mục).
   - Nút "Gửi", "Sửa rồi gửi" (mở khung soạn có sẵn nội dung), "Bỏ". Nháp `action = Handoff` hiển thị lý do chuyển người, không có nút Gửi.
   - Sau khi gửi/bỏ: hỏi nhanh "Nháp này: Đúng / Sai / Thiếu" (không bắt buộc), lưu `rating`.
   - Gửi qua đúng đường `queue_outbound` với `source=Sale`, `bot_draft` liên kết; quy tắc khung gửi Zalo và xác nhận tin có phí giữ nguyên.
3. Lưu `decision` (Sent As Is / Sent Edited / Discarded), `final_text`, `edit_distance_ratio`, `decided_by`, `decided_at`.
4. Trên Lead (không sửa app crm): cách báo "Có nháp bot chờ duyệt" theo cơ chế đã chọn ở ADR D1-06 (ví dụ thông báo cho người phụ trách kèm liên kết Hộp thư).
5. Báo cáo "Bot nháp" (mở rộng báo cáo "Bot ngầm"): theo nhóm — số nháp, tỉ lệ gửi nguyên văn, tỉ lệ sửa, tỉ lệ bỏ, chấm Đúng/Sai/Thiếu, theo tuần.
6. Dashboard CSKH (docs/13): bật ô "Nháp bot chờ duyệt", khối "Bot hôm nay", lý do "Có nháp bot chờ duyệt" trong bảng Cần xử lý ngay, bộ lọc "Có nháp bot" trong Hộp thư, cột "Nháp đã duyệt" theo nhân viên; khối nháp trong dòng thời gian Hộp thư đúng mô tả docs/13 mục 2.
7. Quyền: chỉ người xem được Lead mới thấy nháp; Omni Knowledge Editor xem báo cáo, không xem hội thoại nếu không có quyền Lead.

Test: Draft tạo nháp, Shadow không hiện; gửi nguyên văn → Outbound source=Sale có bot_draft, decision Sent As Is, ratio 0; sửa → Sent Edited, ratio > 0; bỏ → Discarded; tin khách mới → nháp cũ Expired; nháp Handoff không gửi được qua API (gọi API trực tiếp cũng bị từ chối); vẫn không có Outbound source=Bot.

Hoàn thành khi: test xanh; trên staging một sale thử duyệt 10 nháp, dán bảng decision/rating.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Nhắn OA một câu có trong kho tri thức: Hộp thư hiện nháp đúng, có nguồn.
2. Nhắn hỏi giá: nháp là "Chuyển người", không có nút gửi.
