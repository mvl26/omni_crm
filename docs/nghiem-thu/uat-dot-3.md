# UAT Đợt 3: nháp của bot cho sale duyệt (D3-01)

Trạng thái: **CHƯA CHẠY.** Tệp này ghi điều kiện và mẫu bảng cho người phụ trách; không có số liệu nào ở đây được đo.

## 0. Bước 0: cổng `nhap` (CHƯA ĐO)

| Hạng mục | Kết quả |
| --- | --- |
| Lệnh | `bench --site omni.test execute omni_crm.bot.eval.run.run_cli --kwargs "{'gate': 'nhap'}"` (không client AI) |
| Lần chạy | `EVR-2026-00415`, 2026-10-30, báo cáo `eval/reports/2026-10-30-analyze-v1_reply-v1_judge-v1-EVR-2026-00415.md` |
| Bộ đề | 220 ca, tất cả `Synthetic`, chưa duyệt, 0 ca `Real` |
| Hành động đúng / Câu trả lời đạt / Lỗi nghiêm trọng | **chưa đo** / **chưa đo** / **chưa đo** (220/220 ca "chưa đo") |
| Qua cổng | Không (chưa đo = không qua) |

Số "100%" của client Fake (nếu có ở các báo cáo `-fake.md`) chỉ chứng minh hạ tầng chấm điểm chạy, không phải điểm của bot.

Quyết định đã chốt: làm đủ tính năng nhưng giữ `bot_reply_mode = Off`. **Chỉ bật Draft ở môi trường thật sau khi cổng `nhap` được đo bằng AI thật trên bộ đề đã duyệt và đạt** (hành động đúng >= 90%, câu trả lời đạt >= 85%, 0 lỗi nghiêm trọng; docs/08 mục 10). Điều kiện kèm theo: Q11 chốt, khóa Claude API, ca Real đã che thông tin cá nhân, người duyệt bộ đề.

## 1. Điều kiện trước ngày thử (người phụ trách hệ thống)

- [ ] Mục 0 đã đạt trên bộ đề đã duyệt, ghi mã `Omni Eval Run` đạt cổng.
- [ ] Kho tri thức có mục thật đã duyệt; nhóm câu hỏi "Hỏi giá", "Khiếu nại" có `always_handoff`.
- [ ] Staging: `sandbox_mode` BẬT lúc đầu (chỉ ghi log, không gửi thật), `developer_mode` TẮT, worker `omni` và scheduler chạy.
- [ ] `bot_reply_mode = Draft`; Lead thử có chủ là sale tham gia UAT.

## 2. Việc của sale

1. Nhắn OA một câu có trong kho tri thức: Hộp thư hiện thẻ "Nháp của bot — chưa gửi" dưới tin khách, có nhóm câu hỏi và mã mục kho tri thức (bấm để đọc nội dung).
2. Nhắn hỏi giá: thẻ "Bot đề nghị chuyển người" kèm lý do, **không có nút Gửi**.
3. Với 10 nháp: bấm Gửi / Sửa rồi gửi / Bỏ, rồi bấm Đúng / Sai / Thiếu. Ghi vào bảng.
4. Kiểm tra: nhắn thêm khi nháp còn chờ -> nháp cũ biến mất (hết hạn); chuông/thông báo "Có nháp bot chờ duyệt" tới đúng người phụ trách; khung có phí hiện hộp xác nhận.

## 3. Bảng 10 nháp (để trống, người dùng điền sau khi chạy trên staging)

| # | Nhóm câu hỏi | Quyết định (Gửi nguyên văn / Sửa rồi gửi / Bỏ) | Tỉ lệ sửa | Chấm (Đúng/Sai/Thiếu) | Ghi chú |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |
| 6 | | | | | |
| 7 | | | | | |
| 8 | | | | | |
| 9 | | | | | |
| 10 | | | | | |

Đối chiếu sau khi chạy: báo cáo "Bot nháp" (Desk > Báo cáo) theo nhóm và theo tuần phải khớp bảng trên.

## 4. Chưa kiểm được ở môi trường phát triển

- Giao diện Hộp thư trên trình duyệt (chỉ kiểm cú pháp JS và API bằng test).
- Hiển thị Notification Log thực tế, email thông báo.
- Gửi thật qua Zalo (mọi test dùng sandbox và mock).
- Chất lượng nháp của bot thật (không có khóa AI).
