# V2-06 ★ · Bot phân tích và trả lời theo bối cảnh nhà thầu

**Phụ thuộc:** V2-02 (có thể chạy song song với V2-03 → V2-05 trên nhánh riêng). **Thời lượng dự kiến:** 2 ngày.
**Cần anh cung cấp:** kho tri thức B2B đã duyệt nội dung (bắt đầu từ `mau/kho-tri-thuc-mau.csv`, phòng kinh doanh sửa và duyệt từng mục).

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08-bot-ai.md (toàn bộ, đặc biệt mục 3, 4, 5.4, 7, 8, 10), docs/14 mục 3.2, 3.3, 7, docs/02 mục 11, eval/README.md, eval/rubric.md, mau/nhom-cau-hoi-mau.csv, mau/kho-tri-thuc-mau.csv, mau/bang-diem-lead-mau.csv, eval/mau-ca-kiem-thu.jsonl, docs/v2/gap-report.md (các dòng bot).

Nhiệm vụ: chuyển bot của v1 sang bối cảnh nhà thầu mà không làm hỏng luồng đang chạy.

1. Câu lệnh mới — `bot/prompts/analyze_v2.md`, `bot/prompts/reply_v2.md`: nội dung theo docs/08 mục 8.1, 8.2 (bản trong kit v2), dòng đầu `version: analyze_v2` / `reply_v2`. GIỮ NGUYÊN tệp v1. `loader` chưa đổi phiên bản đang dùng — chỉ đổi sau khi bản v2 đạt cổng `doi_cau_lenh` (bước 7).
2. Trường B2B trong tool `record_lead_signals` (docs/14 mục 7): `tax_id`, `hospitals_mentioned`, `current_pain`, `decision_role`; schema JSON cập nhật, phiên bản schema tăng. Custom field CRM Lead `omni_tax_id_stated`, `omni_hospitals_mentioned`, `omni_current_pain`, `omni_decision_role` (fixtures, chỉ thêm). Kiểm tra trích dẫn (evidence) áp cho trường mới như trường cũ. Mã số thuế bot trích: chuẩn hóa, khớp đúng một Organization → gắn (tín hiệu trung bình, docs/14 mục 2); không khớp hoặc nhiều → chỉ ghi `omni_tax_id_stated`. Không bao giờ ghi đè trường trong `omni_manual_fields`.
3. Nhóm câu hỏi và kho tri thức: lệnh nhập lại từ CSV (đã có ở v1 hay chưa → kiểm tra; chưa thì viết) theo `item_id`, chạy lại được. Nhóm mới theo docs/14 mục 7; "Phí dịch vụ" và "Đàm phán hợp đồng" có `always_handoff = 1` — chốt chặn trong `bot/guards.py` kiểm tra bằng code, không chỉ dựa vào câu lệnh. Nhóm cũ không còn trong CSV → đặt không hoạt động, không xóa (giữ lịch sử Bot Run). Chế độ mỗi nhóm sau khi nhập: Shadow (không tự bật Draft/Auto).
4. Bảng điểm quan tâm: nhập `mau/bang-diem-lead-b2b-mau.csv` vào Omni Score Rule; tín hiệu B2B mới (hỏi về gói dịch vụ, hỏi phí, yêu cầu đề xuất, có người quyết định tham gia, số lần liên hệ 30 ngày) tính trong `bot/scoring.py`. Quy tắc cũ không còn trong CSV → tắt, không xóa. "Lead nóng" = điểm quan tâm ≥ ngưỡng **và** hạng phù hợp A/B (docs/14 mục 3.3); Lead không có Organization → giữ quy tắc nóng cũ. Thêm patch chấm lại điểm cho Lead đang mở (dry run, chạy lại được).
5. Bộ đề: thêm ca B2B vào `eval/cases/` từ `eval/mau-ca-kiem-thu.jsonl` và tự viết thêm ca tổng hợp cho đủ cơ cấu ở eval/README.md mục 2 với nhóm mới (đặc biệt: hỏi phí → handoff, đàm phán điều khoản → handoff, khách nêu mã số thuế, khách nêu nhiều bệnh viện, chèn lệnh trong email). Đánh dấu `critical` đúng quy định. Ca thật cũ của v1 giữ nguyên.
6. Chạy bộ đề bằng FakeAIClient / ca tổng hợp để kiểm tra công cụ chấm chạy đúng với schema mới (đây KHÔNG phải số đo cổng). Nếu Q11 đã cho phép và có khóa API: chạy thật cả `analyze_v1`/`reply_v1` và `analyze_v2`/`reply_v2` trên cùng bộ đề, ghi hai báo cáo vào eval/reports/. Chưa cho phép → ghi "cổng chưa đo" và dừng ở đây (V2-07 đo sau).
7. Chỉ khi bước 6 có số đo thật và bản v2 đạt cổng `doi_cau_lenh` so với lần chạy được chấp nhận gần nhất (nếu có) hoặc đạt cổng `phan_tich`: đổi `analyze_prompt_version` / `reply_prompt_version` sang v2. Không đạt → giữ v1, báo cáo ca trượt.

Test bắt buộc: schema mới hợp lệ/không hợp lệ; evidence thiếu → trường bị bỏ; mã số thuế khớp một/nhiều/không khớp; nhóm `always_handoff` chặn bằng code dù AI trả action "reply"; nhập lại CSV không trùng; Lead nóng theo ma trận docs/14 mục 3.3 (4 ô + trường hợp không có Organization); công tắc tắt bot vẫn hoạt động như v1.

Hoàn thành khi: test xanh; bộ đề mới nhập vào DB; báo cáo eval (giả lập, và thật nếu Q11 cho phép); phiên bản câu lệnh đang dùng ghi rõ trong báo cáo.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Phòng kinh doanh đọc lại kho tri thức đã nhập: không có mục nào nêu phí hay cam kết chưa duyệt.
2. Nhắn thử "phí dịch vụ bao nhiêu" trên staging (kênh sandbox): bot phân tích ghi nhận, bot trả lời là "Chuyển người".
3. Mọi nhóm câu hỏi đang ở Shadow.
