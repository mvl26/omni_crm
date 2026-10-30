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

## 4. Chuyển người và tạm dừng bot (D3-02), việc còn lại cho người dùng

Chưa chạy được ở môi trường phát triển (không staging, không AI thật, không trình duyệt). Người phụ trách làm trên staging sau khi cổng `nhap` đạt và bật `bot_reply_mode = Draft`, `sandbox_mode` bật hoặc có người nhận thử chỉ định.

| # | Việc | Kỳ vọng | Kết quả (điền sau) |
| --- | --- | --- | --- |
| 1 | Khách hỏi giá qua Zalo | Sale phụ trách nhận thông báo trong vài giây; chỉ MỘT thông báo | |
| 2 | Khách nhắn "cho tôi gặp nhân viên" | Như trên; bot không soạn nháp cho các tin tiếp theo trong thời gian tạm dừng | |
| 3 | Mở Hộp thư của khách đó | Hiện nhãn "Bot đang tạm dừng đến …" và nút "Cho bot tiếp tục" | |
| 4 | Bấm "Cho bot tiếp tục", khách nhắn tiếp | Tin tiếp theo lại có nháp; dòng thời gian Lead ghi ai bấm | |
| 5 | Bấm "Tạm dừng bot với khách này" | Nhãn hiện; tin tiếp theo không có nháp | |
| 6 | Bật `handoff_notify_channels = system,email`, không mở thông báo 10 phút | Nhận email nhắc có tên khách, kênh, lý do, liên kết; không có lời khách | |
| 7 | Tài khoản không có quyền ghi Lead thử bấm hai nút | Bị từ chối | |

Cần chủ dự án chốt Q6 (giờ bot, câu ngoài giờ) và Q8 (kênh báo) trước khi xem các mặc định tạm là chính thức.

## 5. Chưa kiểm được ở môi trường phát triển

- Giao diện Hộp thư trên trình duyệt (chỉ kiểm cú pháp JS và API bằng test).
- Hiển thị Notification Log thực tế, email thông báo.
- Gửi thật qua Zalo (mọi test dùng sandbox và mock).
- Chất lượng nháp của bot thật (không có khóa AI).

## 6. Thử nghiệm chuyển giọng nói D3-03 (cổng quyết định cho D3-04) — CHƯA ĐO

Chưa có nhà cung cấp, khóa dùng thử, ghi âm thật, bản chuẩn gõ tay, và Q11 chưa chốt, nên **không có số WER hay tỉ lệ trường đúng nào**. Khung và cách chạy: `docs/nghiem-thu/stt-thu-nghiem.md`. Việc của người phụ trách:

| # | Việc | Kết quả (điền sau) |
| --- | --- | --- |
| 1 | Chốt Q11 (cho phép gửi ghi âm tới dịch vụ chuyển giọng nói) và Q7 (điều khoản dữ liệu nhà cung cấp) | |
| 2 | Chọn 2–3 nhà cung cấp (Q13); thêm adapter theo mục 5 của tài liệu thử nghiệm | |
| 3 | Chuẩn bị 20 ghi âm được phép dùng nội bộ, ít nhất 5 bản chuẩn gõ tay, bảng giá | |
| 4 | Chạy `run_trial` trên máy có khóa, người duyệt điền cột `correct` trong `bang-danh-dau.csv`, chạy `--summarize-review` | |
| 5 | Ghi nhánh theo ngưỡng: có nhà cung cấp ĐẠT thì làm D3-04, nếu không thì D3-04 hoãn (Đợt 3 chỉ gồm bot nháp và mở tự động) | |

## 7. Mở bot tự gửi theo nhóm (D3-05) — CHƯA CHẠY, mặc định TẮT

Trạng thái: tính năng đã xây và có test tự động (sandbox, không HTTP, khóa hạ tầng chỉ bật trong phạm vi test). **Chưa kiểm trên trình duyệt** (nút "Bật tự động gửi" trên biểu mẫu Nhóm câu hỏi, nhãn "Bot đã gửi" / chấm / nút "Tắt tự động nhóm này" trong Hộp thư, báo cáo "Bot tự gửi"); chưa có staging, AI thật hay phép gửi thật. Tuyệt đối KHÔNG đặt `omni_auto_send_allowed` và KHÔNG tắt `sandbox_mode` cho tới khi người phụ trách cho phép rõ ràng.

Điều kiện trước: Q1 chốt; cổng `nhap` đạt trên AI thật; Q11 chốt; ít nhất 2 tuần nháp thật (nhóm cần >= 30 nháp đã quyết định trong 14 ngày, >= 95% gửi nguyên văn hoặc sửa nhẹ, 0 nghiêm trọng).

| # | Việc trên staging (làm theo thứ tự, `sandbox_mode` BẬT) | Kỳ vọng | Kết quả (điền sau) |
| --- | --- | --- | --- |
| 1 | Người vận hành đặt `omni_auto_send_allowed: 1` trong site_config staging, khởi động lại | Chưa nhóm nào tự gửi (mọi nhóm vẫn tắt) | |
| 2 | Omni Admin mở nhóm an toàn (ví dụ "Giờ làm việc") -> "Bật tự động gửi" | Hộp xác nhận nêu số mẫu / tỉ lệ / nghiêm trọng; nhóm chưa đủ điều kiện thì không bật được (chỉ "bật cưỡng bức" kèm lý do) | |
| 3 | Thử bật nhóm "Hỏi giá" hoặc "Khiếu nại" (`always_handoff`) | Bị từ chối, kể cả cưỡng bức | |
| 4 | Khách thử nhắn câu thuộc nhóm đã bật, trong giờ bot, khung miễn phí Zalo | Có tin ra `source=Bot`, `is_sandbox = 1`, Hộp thư hiện nhãn "Bot đã gửi"; không gọi Zalo | |
| 5 | Nhắn lúc ngoài `bot_active_hours`, hoặc khách quá 48 giờ, hoặc câu có giá | Rơi về nháp cho sale (hoặc Handoff + một câu giữ chân khi nhóm đang Auto) | |
| 6 | Sale chấm "Sai" một tin bot | Nhóm tự tắt; Omni Admin và Sales Manager nhận thông báo; báo cáo "Bot tự gửi" đếm 1 lần tự tắt | |
| 7 | Khách nhắn khiếu nại trong 24 giờ sau tin bot (bật lại nhóm trước) | Nhóm tự tắt + cảnh báo | |
| 8 | Chỉ khi được cho phép rõ ràng: tắt sandbox, chỉ định MỘT tài khoản Zalo thử, gửi MỘT tin thật | Tin tới đúng tài khoản đó; bật lại sandbox ngay | |
| 9 | Xóa `omni_auto_send_allowed` khỏi site_config | Tin Bot đang chờ `Blocked`, không nhóm nào tự gửi | |

Chưa kiểm được ở môi trường phát triển: mọi bước trên (cần staging, AI thật, phép gửi thật, trình duyệt).
