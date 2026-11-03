# 0010 · Bảng điểm B2B: đề xuất đổi enum tín hiệu (làm ở V2-06)

- Ngày: 2026-11-02
- Trạng thái: **Đã chấp nhận** (2026-11-02, anh đồng ý ĐQ-5; thi hành ở V2-06)
- Prompt: V2-02 (gap-report 08.x, ĐQ-5)

## Bối cảnh
`Omni Score Rule.signal` là Select cố định (`has_phone, has_email, has_org, intent_price, intent_purchase, quantity_given, timeframe_within_30d, urgency_high, repeat_contact, complaint`), nối với `bot/scoring.py` (v1). Bảng B2B (`mau/bang-diem-lead-b2b-mau.csv`, docs/14 mục 3.2) dùng tín hiệu khác: phản hồi lần đầu, đã trao đổi nhu cầu, hỏi về gói dịch vụ, hỏi phí, yêu cầu đề xuất, có người quyết định tham gia, số lần liên hệ 30 ngày. Prompt V2-02 nói rõ `bot/scoring.py` của v1 KHÔNG đổi ở bước này; chỉ điểm phù hợp (fit) được thêm như trục riêng (`omni_fit_*`, không đụng `omni_score`).

## Các lựa chọn
1. Đổi Select `signal` + `scoring.py` ngay ở V2-02 — vượt phạm vi, đổi hành vi chấm điểm đang chạy.
2. Giữ v1; V2-06 thêm các giá trị enum B2B (giữ giá trị v1 để bảng cũ vẫn nhập được), thêm hàm tính tín hiệu mới và bản `analyze_v2.md`/`reply_v2.md` — đổi DocType con (Select options) nên cần duyệt.

## Quyết định (đã chấp nhận)
Chọn 2, làm ở V2-06. Không có thay đổi mã nào ở V2-02 cho việc này. Hai trục độc lập: `omni_fit_score` (code thuần, docs/14 mục 3.1) và `omni_score` (quan tâm).

## Hệ quả
- V2-06 phải đổi `Omni Score Rule.signal` options và `bot/scoring.py` cùng test; `test_scoring::TestImportRules::test_sample_file_imports_with_warning` hiện dùng `mau/bang-diem-lead-mau.csv` (bản v1) nên vẫn xanh.
- Hạng/điểm phù hợp chưa tham gia ưu tiên liên hệ (docs/14 mục 3.3) cho tới khi V2-04 làm giao diện.

## Thi hành ở V2-06 (tương thích ngược)
- `Omni Score Rule.signal`: GIỮ 10 giá trị v1, THÊM `known_org, replied, needs_discussed, intent_service_info, intent_fee, intent_proposal, decision_maker_engaged, pain_point_stated`. Thêm Check `active` (mặc định 1) cho dòng bảng điểm.
- `bot/scoring.py::import_rules(path, merge=True)`: cập nhật dòng cùng tín hiệu, thêm dòng mới, TẮT (`active = 0`, không xóa) dòng của tín hiệu không còn trong CSV; chạy lại không đổi gì. Mặc định `merge=False` giữ hành vi v1 (thay cả bảng); `mau/bang-diem-lead-mau.csv` (v1) vẫn nhập được; `SAMPLE_FILE` giữ trỏ bản v1, thêm `SAMPLE_FILE_B2B`.
- Định nghĩa tín hiệu: `known_org` = `organization` là bản ghi CRM Organization có thật; `replied` = khách có tin/cuộc gọi vào SAU `omni_first_response_at`; `needs_discussed` = có `omni_need` và Miyano đã phản hồi; `intent_service_info/fee` = từng có ý định Tư vấn/Hỏi giá trong 20 lượt phân tích gần nhất hoặc ở trường hiện tại; `intent_proposal` = bot ghi `asks_proposal` (có căn cứ); `decision_maker_engaged` = `omni_decision_role` là Người quyết định; `pain_point_stated` = `omni_current_pain` có giá trị; `repeat_contact`, `complaint`, `has_phone`, `has_email` như cũ.
- Lead nóng (`scoring.is_hot`, một chỗ duy nhất cho báo Lead nóng, nhãn ưu tiên, ô Lead nóng, Chi tiết khách): điểm >= ngưỡng VÀ hạng phù hợp không phải C. Lead không có nhà thầu, nhà thầu chưa có hạng, hoặc `fit_scoring_enabled` tắt: quy tắc cũ (chỉ điểm >= ngưỡng).
- Chưa quyết (anh): điểm và ngưỡng 60 vẫn là mẫu chưa duyệt (Q5, V2-Q14).
- V21-06 (F6): `intent_service_info/fee/proposal` nay lấy qua `bot/intents.py` (chuẩn hóa tên cũ -> mới, ví dụ Tư vấn/Hỏi cách vận hành/Hỏi gói dịch vụ -> `intent_service_info`; Hỏi giá/Hỏi phí -> `intent_fee`; Đặt hàng/Yêu cầu đề xuất -> `intent_proposal`); định nghĩa ở trên đọc theo bảng đó.
