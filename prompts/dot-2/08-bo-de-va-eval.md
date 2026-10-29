# D2-08 ★ · Bộ đề và công cụ chấm bot

**Phụ thuộc:** D2-07. **Thời lượng dự kiến:** 2 ngày (cộng thời gian người chấm lại).

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, eval/README.md, eval/rubric.md, eval/mau-ca-kiem-thu.jsonl, docs/08-bot-ai.md mục 10, docs/02 mục 14.

Nhiệm vụ: công cụ để trả lời bằng số "bot đã đủ tốt chưa", và bộ đề ≥ 200 ca chủ yếu từ dữ liệu thật.

Việc cần làm:
1. DocType `Omni Eval Case`, `Omni Eval Run` (docs/02 mục 14).
2. `bot/eval/importer.py::import_jsonl(path)` (kiểm tra định dạng, case_id duy nhất, báo dòng lỗi) và `export_jsonl(path)`.
3. `bot/eval/builder.py::build_from_real(since, until, max_cases)`: lấy lượt hội thoại thật (Đợt 1) mà sale đã trả lời; **che thông tin cá nhân** (SĐT → số giả đúng định dạng, email → email giả, tên người → tên giả nhất quán trong một ca, tên đơn vị → "Đơn vị A/B…"; ghi quy tắc trong báo cáo); điền sẵn `reference_answer` = câu sale, `expected_action` gợi ý (Handoff nếu câu sale chứa thông tin ngoài kho tri thức hoặc là giá), `source = Real`. Xuất ra `eval/cases/real-<ngày>.jsonl` để NGƯỜI duyệt chỉnh `expected_*` trước khi nhập. Không tự coi gợi ý là đáp án đúng.
4. Bổ sung ca tổng hợp để đủ cơ cấu ở eval/README.md mục 2 (đặc biệt chèn lệnh, hỏi giá, y khoa) → `eval/cases/synthetic-<ngày>.jsonl`, đánh dấu `critical` đúng quy định.
5. `bot/eval/run.py::run(case_filter=None, judge=True) -> eval_run_name`: chạy phân tích + soạn trả lời cho từng ca ở chế độ không ghi CRM (không tạo Lead, không tạo Draft — dùng ngữ cảnh dựng từ `conversation` của ca), chấm theo eval/README.md mục 3; giám khảo dùng `call_tool(kind="Judge")` với tool `grade_reply` theo rubric.md; code tính lại pass/critical. Ghi `Omni Eval Run` và báo cáo `eval/reports/<ngày>-<prompt_version>.md` (ca trượt, lý do, gom theo loại).
6. Danh sách 20% ca để người chấm lại: xuất `eval/reports/<...>-cham-lai.csv` (ca ngẫu nhiên + mọi ca giám khảo cho trượt), có cột để người điền kết luận; lệnh nhập lại kết quả chấm tay và tính lại chỉ số cuối.
7. `passed_gate` tính theo bảng cổng ở eval/README.md mục 4, với tham số cổng nào ("phan_tich", "nhap", "doi_cau_lenh").
8. Lệnh chạy: `bench --site {{SITE_TEST}} execute omni_crm.bot.eval.run.run_cli --kwargs "{'gate': 'phan_tich'}"`. Lỗi nghiêm trọng tính theo định nghĩa ở eval/README.md mục 3 (hợp của ba loại).
9. Dữ liệu thật (bước 3) và gọi AI trên ca thật chỉ khi Q11 ở docs/12 đã cho phép; chưa có thì chỉ làm công cụ và chạy trên ca tổng hợp.

Test (FakeAIClient): chấm trường (enum, SĐT chuẩn hóa, danh sách, chuỗi mờ); ca critical trượt → passed_gate False dù các chỉ số khác cao; giám khảo trả pass=True nhưng r3=False → code tính pass=False, critical=True; importer báo lỗi dòng sai; builder che hết SĐT/email (quét regex trên đầu ra không còn số thật từ dữ liệu nguồn).

Hoàn thành khi: test xanh; trên staging có tệp `real-*.jsonl` (đã che) chờ người duyệt; một lần chạy đầy đủ trên bộ đề hiện có với báo cáo (ghi rõ bộ đề đã được người duyệt hay chưa).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở `eval/cases/real-*.jsonl`: không còn SĐT, email, tên người thật.
2. Trưởng sale/phụ trách kho tri thức sửa cột kỳ vọng cho các ca thật, rồi nhập lại; chạy bộ đề lần nữa.
3. Đọc báo cáo: mọi ca critical đều đạt.
