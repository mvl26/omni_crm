# 0010 · Bảng điểm B2B: đề xuất đổi enum tín hiệu (làm ở V2-06)

- Ngày: 2026-11-02
- Trạng thái: Đề xuất (anh đã chốt ĐQ-5 về hướng; việc đổi làm ở V2-06)
- Prompt: V2-02 (gap-report 08.x, ĐQ-5)

## Bối cảnh
`Omni Score Rule.signal` là Select cố định (`has_phone, has_email, has_org, intent_price, intent_purchase, quantity_given, timeframe_within_30d, urgency_high, repeat_contact, complaint`), nối với `bot/scoring.py` (v1). Bảng B2B (`mau/bang-diem-lead-b2b-mau.csv`, docs/14 mục 3.2) dùng tín hiệu khác: phản hồi lần đầu, đã trao đổi nhu cầu, hỏi về gói dịch vụ, hỏi phí, yêu cầu đề xuất, có người quyết định tham gia, số lần liên hệ 30 ngày. Prompt V2-02 nói rõ `bot/scoring.py` của v1 KHÔNG đổi ở bước này; chỉ điểm phù hợp (fit) được thêm như trục riêng (`omni_fit_*`, không đụng `omni_score`).

## Các lựa chọn
1. Đổi Select `signal` + `scoring.py` ngay ở V2-02 — vượt phạm vi, đổi hành vi chấm điểm đang chạy.
2. Giữ v1; V2-06 thêm các giá trị enum B2B (giữ giá trị v1 để bảng cũ vẫn nhập được), thêm hàm tính tín hiệu mới và bản `analyze_v2.md`/`reply_v2.md` — đổi DocType con (Select options) nên cần duyệt.

## Quyết định (đề xuất)
Chọn 2, làm ở V2-06. Không có thay đổi mã nào ở V2-02 cho việc này. Hai trục độc lập: `omni_fit_score` (code thuần, docs/14 mục 3.1) và `omni_score` (quan tâm).

## Hệ quả
- V2-06 phải đổi `Omni Score Rule.signal` options và `bot/scoring.py` cùng test; `test_scoring::TestImportRules::test_sample_file_imports_with_warning` hiện dùng `mau/bang-diem-lead-mau.csv` (bản v1) nên vẫn xanh.
- Hạng/điểm phù hợp chưa tham gia ưu tiên liên hệ (docs/14 mục 3.3) cho tới khi V2-04 làm giao diện.
