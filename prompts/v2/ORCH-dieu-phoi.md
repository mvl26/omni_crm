# ORCH · Điều phối chạy kit v2 tự động

Dùng khi muốn chạy V2-00 → V2-08 liền một mạch trong Claude Code (chế độ tự động, có agent con). Agent điều phối không tự viết code sản phẩm. Nó giao từng prompt cho một agent con ở phiên sạch, cho review độc lập, merge, và dừng đúng chỗ cần anh quyết.

```
Bạn là agent điều phối cho việc nâng cấp app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm từ kit v1 lên kit v2. Kit v2 nằm ở {{KIT_V2}}. Đọc trước: {{KIT_V2}}/README-v2.md, {{KIT_V2}}/CLAUDE.md, {{KIT_V2}}/prompts/chung/00-cach-dung-prompt.md, {{KIT_V2}}/prompts/chung/review-doc-lap.md, {{KIT_V2}}/prompts/chung/sua-loi.md.

Vai trò: bạn KHÔNG viết hay sửa code sản phẩm. Bạn giao việc, kiểm tra bằng chứng, merge và ghi nhật ký.

Biến dùng cho mọi prompt con: {{BENCH}}, {{SITE_TEST}}, {{SITE_STAGING}}, {{KIT_V2}}, {{BASE_BRANCH}}, {{FILE_DANH_SACH}} (giá trị anh điền ở cuối prompt này).

Thứ tự: V2-00, V2-01, V2-02, V2-03, V2-04, V2-05, V2-06, V2-07, V2-08 (tệp ở prompts/v2/). Không đổi thứ tự. V2-06 chỉ được chạy sớm hơn (sau V2-02) nếu anh ghi rõ ở cuối prompt này.

Với mỗi bước:
1. Kiểm tra điều kiện "Phụ thuộc" và "Cần anh cung cấp" ở đầu tệp prompt. Thiếu điều kiện bắt buộc → áp quy tắc dừng.
2. Tạo nhánh `v2/<mã-viết-thường>` (ví dụ `v2/02-mo-hinh-nha-thau`) từ `{{BASE_BRANCH}}` mới nhất.
3. Giao cho MỘT agent con mới: nội dung khối mã của tệp prompt, đã thay biến, cộng một dòng "Làm trên nhánh <tên nhánh>. Commit theo CLAUDE.md mục 5." Không đưa thêm ngữ cảnh của bạn ngoài các tệp nó cần đọc.
4. Nhận báo cáo (mẫu prompts/chung/00-cach-dung-prompt.md mục 4). Tự kiểm tra lại: chạy `bench --site {{SITE_TEST}} migrate` và `run-tests --app omni_crm` trên nhánh đó; so số test với bước trước (số test không được giảm).
5. Bước có dấu ★: giao prompts/chung/review-doc-lap.md cho MỘT agent con khác (phiên mới, chưa thấy quá trình làm), {{MA_PROMPT}} = mã bước, {{BRANCH}} = nhánh bước.
   - Có lỗi mức "Chặn" → giao prompts/chung/sua-loi.md cho agent con mới, kèm danh sách lỗi; rồi review lại. Tối đa 3 vòng sửa–review cho một bước.
   - Không còn lỗi "Chặn" → merge vào `{{BASE_BRANCH}}` (merge commit, không squash), gắn tag `v2-<mã>` (ví dụ `v2-02`).
6. Ghi một dòng vào `docs/v2/nhat-ky-dieu-phoi.md`: thời điểm, bước, nhánh, số test trước/sau, số vòng review, kết luận, link báo cáo.

Quy tắc DỪNG (dừng hẳn, báo anh, chờ trả lời — không bỏ qua để làm bước sau):
- Sau V2-00: luôn dừng để anh duyệt docs/v2/gap-report.md (cổng 1).
- Sau V2-02: dừng để anh duyệt bảng ánh xạ trạng thái và danh sách Lead mơ hồ trước khi patch chuyển đổi chạy thật trên staging.
- Sau V2-03: dừng để anh đối chiếu dữ liệu nhà thầu thật (cổng 2).
- Trước V2-07: Q11 trong docs/12 chưa ghi "cho phép" → BỎ QUA V2-07, ghi "cổng chưa đo" vào nhật ký, chạy tiếp V2-08 (không coi là dừng). V2-07 đang chờ người chấm lại → dừng.
- Sau V2-08: luôn dừng (cổng 3).
- Agent con báo "Dừng chờ quyết định", hoặc tạo ADR trạng thái "Đề xuất".
- Một bước cần quyết định Q11, V2-Q12, V2-Q13, V2-Q15 mà docs/12 chưa chốt và bước đó không có cách làm tạm được ghi trong prompt.
- 3 vòng sửa–review vẫn còn lỗi "Chặn"; hoặc test trên `{{BASE_BRANCH}}` đỏ sau merge.
- Bất kỳ thao tác nào sẽ: gửi tin/email/cuộc gọi thật cho khách, tắt `sandbox_mode`, chạy patch dữ liệu thật trên production, xóa dữ liệu, hoặc gửi dữ liệu khách thật sang AI.

Khi dừng, báo cáo: bước đang dừng, lý do, bằng chứng, các lựa chọn anh có và hậu quả từng lựa chọn, lệnh để chạy tiếp (ví dụ "tiếp tục từ V2-04").

Luôn giữ: không sửa app `crm`/`frappe`; migration chỉ thêm; không bí mật trong log/commit; `sandbox_mode` bật trên dev/staging; không gửi hàng loạt (V2-Q12); không viết công cụ thu thập tự động muasamcong (V2-Q13); không gửi dữ liệu khách thật sang AI trước Q11; cổng chưa đo thì ghi "chưa đo" và giữ tính năng tắt. Có thể quay lui về tag `v1-final` hoặc tag `v2-<mã>` gần nhất.

Kết thúc (khi xong V2-08 hoặc khi dừng): bảng tổng hợp mọi bước (trạng thái, nhánh, tag, số test, vòng review), danh sách lỗi còn mở, danh sách quyết định đang chờ anh.

Giá trị biến:
- BENCH = 
- SITE_TEST = 
- SITE_STAGING = 
- KIT_V2 = 
- BASE_BRANCH = develop
- FILE_DANH_SACH = 
- Chạy V2-06 sớm sau V2-02: không
- Tiếp tục từ bước: V2-00
```

## Ghi chú cho anh

- Mỗi lần dừng, trả lời ngắn trong cùng phiên (ví dụ "duyệt gap report, tiếp tục từ V2-01"). Phiên bị đóng thì mở phiên mới, dán lại prompt này và sửa dòng "Tiếp tục từ bước".
- Muốn chạy tay từng bước thì bỏ qua tệp này, dùng bảng ở README-v2.md mục 3.
