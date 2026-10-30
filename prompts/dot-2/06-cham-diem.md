# D2-06 · Chấm điểm Lead

**Phụ thuộc:** D2-05. **Cần anh cung cấp:** `mau/bang-diem-lead-mau.csv` đã được Trưởng sale duyệt. **Thời lượng dự kiến:** 0,5–1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/14-nha-thau-b2b.md mục 3 (điểm quan tâm là trục thứ hai bên cạnh điểm phù hợp đã có từ D1-13; V2-Q14), docs/08-bot-ai.md mục 4, docs/02 mục 2 (`score_rules`, `hot_lead_threshold`) và mục 11, mau/bang-diem-lead-mau.csv, docs/12 mục A (Q5 — bảng điểm đã chốt chưa).

Việc cần làm:
1. Bảng con `Omni Score Rule` và trường `score_rules`, `hot_lead_threshold` trên Omni Settings.
2. Lệnh nhập: `bench --site {{SITE_TEST}} execute omni_crm.bot.scoring.import_rules --kwargs "{'path': '...'}"` đọc CSV; kiểm tra tín hiệu hợp lệ; thay toàn bộ bảng; in kết quả. KHÔNG tự bịa điểm — nếu tệp còn giá trị mẫu chưa được duyệt, vẫn nhập nhưng in cảnh báo.
3. `bot/scoring.py::score_lead(lead) -> (score, breakdown)`: tính hoàn toàn bằng code từ tín hiệu (trường Lead, tín hiệu từ Bot Run gần nhất, số lần liên hệ 30 ngày từ tin vào). Mỗi tín hiệu cộng một lần. Ghi `omni_score` và lưu `breakdown` vào custom field ẩn `omni_score_breakdown` (đã có trong docs/02 mục 15; tạo bằng fixtures nếu D2-05 chưa tạo).
4. Gọi `score_lead` sau `analyze_batch`, và sau mỗi lượt có cuộc gọi nếu D2-01 đã có trong nhánh chính (nếu chưa, để điểm nối có docstring và ghi vào báo cáo).
5. Lead nóng = điểm quan tâm ≥ `hot_lead_threshold` **và** nhà thầu hạng A hoặc B (docs/14 mục 3.3); nhà thầu hạng C có điểm quan tâm cao hiện nhãn "Kiểm tra lại dữ liệu gói thầu" thay vì Lead nóng. Lần đầu đạt Lead nóng → thông báo người phụ trách (Notification Log + realtime) kèm lý do từ breakdown; không thông báo lại khi điểm dao động quanh ngưỡng (ghi dấu đã thông báo).
6. Hộp thư: hiển thị điểm và lý do khi rê chuột.
7. Dashboard CSKH (docs/13): bật ô "Lead nóng", lý do "Lead nóng" trong bảng Cần xử lý ngay, khối điểm và lý do trên Hồ sơ chăm sóc.

Test: tính điểm đúng cho 5 bộ tín hiệu mẫu; không cộng trùng; điểm âm với khiếu nại; thông báo đúng một lần; nhập CSV có tín hiệu sai → báo lỗi, không đổi bảng cũ.

Hoàn thành khi: test xanh; fixtures có `omni_score_breakdown`.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Lead có SĐT + hỏi giá + số lượng: điểm bằng tổng tay tính từ bảng điểm.
2. Rê chuột vào điểm trong Hộp thư: thấy vì sao.
