# D3-06 ★ · Kiểm thử và nghiệm thu Đợt 3

**Phụ thuộc:** D3-05 (và D3-04 nếu đã làm). **Thời lượng dự kiến:** 1,5 ngày + 1 tuần chạy thử.

```
Bạn là agent kiểm thử cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/10-kiem-thu.md, docs/00 mục 2, docs/08 mục 10, eval/README.md mục 4, docs/nghiem-thu/dot-1.md và dot-2.md.

Nhiệm vụ: chứng minh Đợt 3 đủ điều kiện phát hành và hai đợt trước không bị hỏng. Sửa lỗi nhỏ theo prompts/chung/sua-loi.md; lỗi lớn ghi lại.

Việc cần làm:
1. Chạy lại toàn bộ test và kiểm thử tải Đợt 1 (1 lần), kịch bản Đợt 2.
2. Kịch bản Đợt 3 (viết test nếu thiếu):
   - D-01 Nháp hiện cho sale, gửi/sửa/bỏ, chấm.
   - D-02 Hỏi giá → Handoff, thông báo, tạm dừng bot, tiếp tục bot.
   - D-03 Khách chèn lệnh → không làm theo.
   - D-04 Nhóm Auto gửi đúng; ngoài khung Zalo → Draft; ngắt mạch.
   - D-05 Hết hạn mức AI khi đang Auto → không gửi gì, kênh vẫn chạy, cảnh báo.
   - D-06 (nếu có D3-04) Ghi âm → tóm tắt; quyền nghe ghi âm.
   - D-07 Quay lui mức 1 và 2 ở docs/11 mục 4: tắt từng công tắc, hệ thống dừng đúng phần đó.
3. Chạy bộ đề cổng `nhap` trên câu lệnh, kho tri thức, mô hình đang chạy; so với lần đạt trước (cổng `doi_cau_lenh`).
4. Viết `docs/nghiem-thu/uat-dot-3.md` và `docs/nghiem-thu/dot-3.md` (định dạng như các đợt trước), script `tests/acceptance/measure_dot3.py` đo: tỉ lệ nháp đúng (gửi nguyên văn hoặc sửa nhẹ), lỗi nghiêm trọng, tỉ lệ bot tự xử lý theo nhóm, thời gian phản hồi lần đầu.
5. Tổng hợp toàn dự án: bảng chỉ số mục tiêu docs/00 mục 2 với số đo hiện tại, việc còn mở, khuyến nghị cho 3 tháng tiếp theo (nhóm nào nên mở Auto tiếp, kho tri thức cần bổ sung gì dựa trên các ca Handoff "ngoài kho tri thức").

Hoàn thành khi: test xanh; cổng `nhap` đạt và không suy giảm; tài liệu nghiệm thu đã viết.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra (cổng phát hành Đợt 3)

1. Bộ đề: trả lời đạt ≥ 85%, lỗi nghiêm trọng = 0.
2. Tuần chạy thử: sale chấp nhận nháp, không có tin bot gửi sai cho khách thật.
3. Đọc mục khuyến nghị 3 tháng: chọn nhóm mở Auto tiếp theo và phần kho tri thức cần bổ sung.
