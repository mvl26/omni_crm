# D2-05 ★ · Bot phân tích: trích thông tin vào Lead

**Phụ thuộc:** D2-04. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08-bot-ai.md mục 2, 3, 7, 8.1; docs/02 mục 15 (custom field Đợt 2); docs/04 mục 2–3 (tín hiệu trung bình).

Nhiệm vụ: sau mỗi lượt xử lý có tin khách mới, bot đọc hội thoại và điền thông tin vào Lead — an toàn, có căn cứ, không ghi đè điều sale đã sửa.

Việc cần làm:
1. Custom field Đợt 2 trên CRM Lead (fixtures): `omni_need`, `omni_products`, `omni_quantity`, `omni_timeframe`, `omni_urgency`, `omni_intent`, `omni_score`, `omni_score_breakdown`, `omni_bot_summary`. Thêm các trường này vào danh sách theo dõi của `track_manual_edits`.
2. `bot/analyze.py`:
   - Tool `record_lead_signals` với `input_schema` đúng docs/08 mục 3, bổ sung các trường B2B ở docs/14 mục 7 (`organization` đã có, `tax_id`, `hospitals_mentioned`, `current_pain`, `decision_role`) và cờ tín hiệu cho bảng điểm quan tâm B2B (docs/02 mục 11). Mã số thuế bot trích được: chuẩn hóa, chỉ gắn Organization khi khớp đúng một bản ghi; không khớp thì lưu vào `omni_tax_id_stated` để sale xác nhận. Tạo các custom field B2B trên CRM Lead ở docs/14 mục 7 (fixtures). Cập nhật `analyze_v1.md` thành `analyze_v2.md` cho bối cảnh nhà thầu (giữ mọi quy tắc của v1).
   - `analyze_batch(batch_id)`: lấy Lead từ batch, `build_conversation`, gọi `call_tool(kind="Analyze", model=analyze_model, system=<phiên bản analyze mới nhất — analyze_v2>, …)`.
   - Hậu xử lý bằng code đúng docs/08 mục 3 bước 1–6. Kiểm căn cứ: hàm `evidence_supported(evidence, customer_text) -> bool` (bỏ dấu, chữ thường, so khớp mờ ≥ 0.85 theo cửa sổ trượt), chỉ so với **lời khách**, không với lời Miyano.
   - Ghi Lead với `frappe.flags.in_omni_bot = True`; bỏ qua trường trong `omni_manual_fields`; tên chỉ điền khi đang là tên tạm (`is_placeholder_name`).
   - SĐT/email trích được → gọi `resolver` để xem có cần tạo Candidate nghi trùng không (tín hiệu trung bình) — không tự gộp.
   - Lưu tín hiệu dùng cho chấm điểm vào `Omni Bot Run.output_json` (D2-06 sẽ đọc).
3. Nối vào `post_batch_hooks`: nếu `bot_analyze_enabled` và lượt có tin khách loại message/user_info/call có transcript → enqueue `analyze_batch` (hàng đợi omni, job_id theo batch). Cuối `analyze_batch` (kể cả khi AI lỗi) gọi điểm nối `after_analyze(batch_id, lead)` — hiện để trống có docstring; D2-07 dùng nó để enqueue bot trả lời (docs/08 mục 5.1).
4. Hộp thư và Lead: hiển thị tóm tắt bot (`omni_bot_summary`) và nhãn ý định ở đầu hội thoại.
5. Dashboard CSKH (docs/13): hiện nhóm "Nhu cầu" trên Hồ sơ chăm sóc với nhãn nguồn Bot/Sale; `summary` của phiên chăm sóc đang mở dùng `summary` của bot (nếu sale chưa sửa); bật lý do "Khiếu nại" trong bảng Cần xử lý ngay.

Test với FakeAIClient:
- Hội thoại có tên, đơn vị, SĐT, số lượng → các trường được điền.
- Bot trả `evidence` không có trong lời khách (bịa) → trường đó bị bỏ.
- Bot trích từ lời của Miyano (không phải khách) → bị bỏ.
- Sale đã sửa `omni_need` → bot không ghi đè.
- Lead tên thật "Nguyễn Văn A" → bot không đổi tên; Lead tên tạm → bot điền tên.
- Bot Run lỗi → lượt xử lý vẫn Done, Lead không đổi.
- Công tắc tắt → không enqueue.

Thử thật (có khóa): CHỈ khi Q11 ở docs/12 đã được chốt cho phép; nếu chưa, dùng 10 hội thoại tổng hợp. Chạy trên staging (bot_analyze_enabled bật chỉ cho staging), dán bảng: trường bot điền / đúng-sai theo bạn đọc. Đây chỉ là kiểm tra nhanh; đánh giá chính thức ở D2-08.

Hoàn thành khi: test xanh; fixtures custom field đã xuất.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Nhắn OA thử: "Em là Hoa ở phòng khám X, cần 100 hộp găng tay tuần sau" → Lead có nhu cầu, số lượng, thời điểm, đơn vị.
2. Sửa tay "Nhu cầu" trên Lead, nhắn thêm một tin: trường đó giữ nguyên.
