# D1-11 ★ · Kiểm thử và nghiệm thu Đợt 1

**Phụ thuộc:** D1-15, staging sẵn sàng. **Thời lượng dự kiến:** 2 ngày + 1 tuần chạy thử với sale.

```
Bạn là agent kiểm thử cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/10-kiem-thu.md (toàn bộ), docs/00 mục 2, docs/11 mục 3–4.

Nhiệm vụ: chứng minh Đợt 1 đủ điều kiện phát hành. Bạn được viết test, script kiểm thử và sửa lỗi nhỏ phát hiện được (theo quy trình prompts/chung/sua-loi.md, mỗi lỗi một commit có test tái hiện). Lỗi lớn hoặc cần đổi thiết kế: ghi lại, không sửa.

Việc cần làm:
1. Đối chiếu bộ test tích hợp với bảng I-01…I-13 ở docs/10 mục 3, cộng các test của D1-12 (phiên chăm sóc, chỉ số dashboard, quyền), D1-13/D1-14 (nhà thầu, điểm phù hợp khớp mẫu Excel, nhập lại không trùng) và D1-15 (Meta, Google, form website, SLA). Kiểm thử tải bổ sung một đợt 200 lead quảng cáo trong 10 phút (mô phỏng lúc chiến dịch chạy mạnh). Ca nào thiếu thì viết. Ghi bảng ánh xạ ca → tên test.
2. Viết `tests/load/send_burst.py` đúng docs/10 mục 4 (chạy ngoài bench, dùng httpx, ký payload bằng secret kênh staging đọc từ biến môi trường). Chạy trên {{SITE_STAGING}}: 3 lần lưu lượng ngày cao điểm dự kiến (hỏi giá trị ngày cao điểm trong báo cáo nếu chưa có; mặc định 2.000 tin/ngày → thử 6.000 tin dồn trong 1 giờ, cộng một đợt 50 tin/giây trong 2 phút), 300 khách giả, 10% gửi lại. Ghi số đo.
3. Chạy các kịch bản sự cố ở docs/10 mục 5 trên {{SITE_STAGING}} (cần quyền sudo cho Redis: nếu không có, ghi rõ và hướng dẫn người duyệt tự làm). Ghi kết quả từng kịch bản.
4. Kiểm tra bảo mật nhanh: webhook không chữ ký, `zalo_skip_signature` trên production, API inbox/admin với Guest và Sales User không có quyền.
5. Viết `docs/nghiem-thu/uat-dot-1.md`: kịch bản cho 1–2 sale chạy thử 1 tuần (việc làm hằng ngày, cách báo lỗi, mẫu ghi nhận).
6. Viết `docs/nghiem-thu/dot-1.md` theo docs/10 mục 6, gồm bảng chỉ số mục tiêu Đợt 1 (docs/00 mục 2: tin mất, Lead trùng, thời gian tin→Lead, phản hồi lần đầu, tin xử lý tay, cổng hoạt động) với cột "Đo được" để trống phần cần tuần chạy thử, và truy vấn SQL/script để đo từng chỉ số.
7. Tạo script `tests/acceptance/measure_dot1.py` đo các chỉ số trên từ dữ liệu thật trong một khoảng thời gian, để chạy cuối tuần chạy thử.

Hoàn thành khi: mọi test xanh; báo cáo tải và sự cố có số liệu thật từ staging; hai tài liệu nghiệm thu đã viết; danh sách lỗi còn mở có mức độ. Kết luận khuyến nghị: "Phát hành" / "Phát hành sau khi sửa …" / "Chưa phát hành".

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra (cổng phát hành Đợt 1)

1. Đọc `docs/nghiem-thu/dot-1.md`: p95 webhook < 1 giây, không mất, không trùng trong kiểm thử tải.
2. Sau tuần chạy thử, chạy `measure_dot1.py` và điền cột "Đo được". Đạt ngưỡng nghiệm thu ở `docs/00` mục 2 → mở cho toàn đội.
3. `sandbox_mode` trên production đã **tắt có chủ đích** trước khi mở.
