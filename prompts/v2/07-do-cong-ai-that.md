# V2-07 · Đo cổng chất lượng bot bằng AI thật

**Phụ thuộc:** V2-06; **Q11 đã chốt cho phép** gửi hội thoại sang dịch vụ AI; có khóa Claude API trên staging; bộ đề đã được người duyệt (trưởng sale/phụ trách kho tri thức). **Thời lượng dự kiến:** 1 ngày máy + thời gian người chấm lại.
Q11 chưa chốt hoặc chốt "không cho phép" → **không chạy prompt này**; mọi tính năng phụ thuộc cổng giữ tắt.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08 mục 1, 7, 10, eval/README.md (toàn bộ), eval/rubric.md, docs/12 (dòng Q11 — xác nhận đã cho phép và phạm vi cho phép, ví dụ chỉ dữ liệu đã che), báo cáo eval gần nhất trong eval/reports/.

Bước 0 — Điều kiện: Q11 ghi "cho phép" kèm ngày và người chốt; khóa API đặt trong Omni Settings (không in ra); ngân sách token ngày đủ cho số ca × 3 lần gọi (phân tích, trả lời, giám khảo) — ước tính và ghi. Thiếu điều kiện nào → DỪNG và báo.

Bước 1 — Bộ đề: liệt kê số ca theo nguồn (Real đã che / Synthetic), theo nhóm, số ca critical; kiểm tra đủ cơ cấu ở eval/README.md mục 2. Ca thật chỉ dùng khi đã được người duyệt sửa cột kỳ vọng (cờ đã duyệt); chưa duyệt → loại khỏi lần đo và ghi số ca bị loại.

Bước 2 — Đo:
- Cổng `phan_tich` với câu lệnh đang dùng (và với v2 nếu V2-06 chưa đổi).
- Cổng `nhap` với câu lệnh trả lời đang dùng.
- Nếu V2-06 đã có bản v2 chưa được bật: cổng `doi_cau_lenh` v2 so với v1.
Dùng đúng lệnh `run_cli` của D2-08. Ghi `Omni Eval Run` và báo cáo eval/reports/<ngày>-<prompt_version>.md.

Bước 3 — Chấm lại bởi người: xuất tệp chấm lại 20% (công cụ D2-08) và DỪNG chờ người điền. Khi tệp đã điền được đặt lại vào eval/reports/, nhập kết quả và tính lại chỉ số cuối. Không tự điền kết luận thay người.

Bước 4 — Kết luận theo bảng cổng eval/README.md mục 4, mỗi cổng: Đạt / Không đạt (kèm chỉ số và ca trượt gom theo loại). Không làm tròn lên để đạt.

Bước 5 — Theo kết quả:
- `phan_tich` đạt → được phép đặt bot phân tích ở chế độ chạy thật (ghi CRM) trên staging; production chờ anh duyệt.
- `nhap` đạt → được phép chuyển các nhóm câu hỏi có đủ ca và đạt riêng theo nhóm sang Draft trên staging; nhóm `always_handoff` không bao giờ Draft/Auto.
- Không đạt → giữ nguyên chế độ hiện tại; đề xuất sửa kho tri thức hoặc câu lệnh (tạo phiên bản mới, không sửa bản đang dùng), không tự sửa trong prompt này.
Không bật Auto cho nhóm nào (Auto cần quyết định riêng theo docs/08 và D3-05).

Hoàn thành khi: có báo cáo đo thật cho từng cổng, kết quả chấm lại của người đã nhập, bảng "chế độ đề xuất cho từng nhóm câu hỏi"; docs/12 ghi kết quả cổng kèm ngày.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4, thêm bảng so sánh với lần đo trước (nếu có).
```

## Người duyệt kiểm tra

1. Đọc báo cáo: mọi ca critical đạt; con số khớp sau khi nhập kết quả chấm lại.
2. Quyết định nhóm nào chuyển Draft trên production.
