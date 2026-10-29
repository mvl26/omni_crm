# D2-09 ★ · Kiểm thử và nghiệm thu Đợt 2

**Phụ thuộc:** D2-03, D2-06, D2-08. **Thời lượng dự kiến:** 1,5 ngày + 1 tuần chạy thử.

```
Bạn là agent kiểm thử cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/10-kiem-thu.md, docs/00 mục 2 và 4, eval/README.md mục 4, docs/nghiem-thu/dot-1.md (để giữ cùng định dạng).

Nhiệm vụ: chứng minh Đợt 2 đủ điều kiện phát hành và Đợt 1 không bị hỏng. Sửa lỗi nhỏ theo prompts/chung/sua-loi.md; lỗi lớn ghi lại.

Việc cần làm:
1. Chạy lại toàn bộ test tích hợp Đợt 1 + kiểm thử tải Đợt 1 (giảm còn 1 lần chạy) để chắc không suy giảm.
2. Kịch bản tích hợp Đợt 2 (viết test nếu thiếu, ghi bảng ánh xạ):
   - C-01 Gọi đến nghe máy → Lead + Call Log + hiện trong Hộp thư.
   - C-02 Gọi nhỡ 3 lần → 1 việc gọi lại, đúng hạn giờ làm việc.
   - C-03 Đối soát bù cuộc gọi thiếu.
   - C-04 Khách gọi trước, sau đó nhắn Zalo gửi SĐT → cùng Lead.
   - B-01 Tin khách → Bot phân tích điền trường có căn cứ; sale đã sửa thì không ghi đè.
   - B-02 Điểm Lead đúng bảng điểm; báo Lead nóng một lần.
   - B-03 Bot trả lời chỉ ở Shadow; không có Outbound source=Bot.
   - B-04 Hết hạn mức token → bot dừng, kênh vẫn chạy bình thường.
   - B-05 AI lỗi hàng loạt (FakeAIClient lỗi 100%) → lượt xử lý vẫn Done, cảnh báo bật.
3. Kiểm thử sự cố bổ sung: webhook tổng đài token sai; Stringee gửi lại sự kiện; tra cứu khi DB chậm.
4. Chạy bộ đề với cổng `phan_tich` (sau khi người duyệt đã chỉnh ca thật). Ghi kết quả.
5. Viết `docs/nghiem-thu/uat-dot-2.md` (sale dùng việc gọi lại, xem điểm, tóm tắt bot; cách báo trường bot điền sai) và `docs/nghiem-thu/dot-2.md` (kết quả test, bộ đề, chỉ số mục tiêu liên quan Đợt 2: gọi nhỡ được gọi lại đúng hạn, trường trích xuất đúng; script đo `tests/acceptance/measure_dot2.py`).
6. Kết luận: phát hành Đợt 2 với `bot_analyze_enabled = 1` hay chưa; `bot_reply_mode` giữ Shadow.

Hoàn thành khi: test xanh; cổng `phan_tich` đạt (hoặc nêu rõ thiếu bao nhiêu và đề xuất sửa kho tri thức/câu lệnh); tài liệu nghiệm thu đã viết.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra (cổng phát hành Đợt 2)

1. Trường trích xuất đúng ≥ 90% trên bộ đề đã duyệt.
2. Một tuần chạy thử: sale xác nhận việc gọi lại hữu ích, điểm Lead hợp lý.
3. Báo cáo "Bot ngầm" sau 1 tuần: tỉ lệ nháp gần giống câu sale theo từng nhóm — dùng để chọn nhóm mở nháp ở Đợt 3.
