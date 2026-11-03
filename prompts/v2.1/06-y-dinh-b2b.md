# V21-06 ★ · Ý định và nhóm câu hỏi theo bối cảnh nhà thầu

**Phụ thuộc:** tag `v2-08`. **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F6, docs/08-bot-ai.md mục 3–5 và 8, docs/14 mục 3.2 và 7, docs/02 mục 11 và 15, bot/prompts/ (analyze_v1, analyze_v2 nếu đã có từ V2-06), bot/guards.py, bot/scoring.py, eval/README.md.

Tạo nhánh v21/06-y-dinh-b2b từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F6.
1. Danh sách ý định mới (bảng F6 mục 1) làm hằng số ở một chỗ, kèm ánh xạ ý định → tín hiệu điểm → luôn chuyển người.
2. Migration chỉ thêm giá trị vào Select omni_intent (giữ giá trị cũ). Patch patches/v21/map_old_intents.py: dry run mặc định, in số Lead theo từng ánh xạ (Hỏi giá → Hỏi phí, Đặt hàng → Yêu cầu đề xuất, Tư vấn → Hỏi cách vận hành), chỉ Lead đang mở, chạy lại được, không động trường trong omni_manual_fields.
3. analyze_v2 (tạo mới hoặc sửa bản V2-06 chưa được bật): enum intent mới; bỏ products, quantity, timeframe_within_30d khỏi schema; tăng phiên bản schema; giữ cột cũ trên Lead. Câu lệnh đang dùng vẫn ghim v1 (không đổi analyze_prompt_version).
4. bot/scoring.py: tín hiệu lấy theo ánh xạ mới; vẫn hiểu giá trị cũ (Lead chưa chạy patch).
5. bot/guards.py: Hỏi phí, Yêu cầu đề xuất, Khiếu nại → luôn chuyển người bằng code, độc lập nhóm câu hỏi; giữ always_handoff theo nhóm.
6. Bộ đề: cập nhật expected_intent ca B2B (eval/cases/) theo danh sách mới; khi chấm ca v1, ánh xạ giá trị cũ sang mới. Chạy bộ đề bằng FakeAIClient để chắc công cụ chấm chạy với schema mới (không phải số đo cổng).
7. Hộp thư và Chi tiết nhà thầu hiển thị nhãn ý định mới.

Test bắt buộc: schema mới hợp lệ/không hợp lệ; mỗi ý định → đúng tín hiệu và đúng quyết định chuyển người; guard chặn dù AI trả action "reply"; patch dry run và chạy lại không đổi thêm; scoring với giá trị cũ và mới.

Hoàn thành khi: test xanh; báo cáo dry run patch trên dữ liệu tổng hợp; phiên bản câu lệnh đang dùng ghi rõ (vẫn v1).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
