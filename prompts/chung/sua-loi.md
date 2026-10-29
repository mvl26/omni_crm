# Prompt sửa lỗi

Dùng khi review hoặc người dùng báo lỗi. Một lỗi một phiên.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc `CLAUDE.md` trước.

Lỗi cần sửa:
- Mô tả: {{MO_TA_LOI}}
- Cách tái hiện (nếu có): {{CACH_TAI_HIEN}}
- Nguồn: {{NGUON}}  (ví dụ: Review D1-04 dòng #3, hoặc sale báo trong tuần chạy thử)
- Tài liệu liên quan: {{TAI_LIEU}}  (ví dụ: docs/03-cong-tiep-nhan-va-hang-doi.md mục 3)

Quy trình bắt buộc:
1. Tạo nhánh `fix/{{TEN_NGAN}}`.
2. Viết test tái hiện lỗi TRƯỚC. Chạy để chứng minh test đỏ. Nếu không tái hiện được, dừng và báo những gì đã thử.
3. Tìm nguyên nhân gốc. Ghi một đoạn ngắn: nguyên nhân là gì, vì sao test cũ không bắt được.
4. Sửa ở mức nhỏ nhất đủ đúng. Không refactor kèm theo.
5. Chạy test mới (phải xanh) và toàn bộ test của app (không được đỏ thêm).
6. Nếu sửa đúng đòi hỏi đổi thiết kế trong `docs/`, dừng lại, ghi ADR "Đề xuất" và báo.
7. Tối đa 3 lần thử sửa; quá 3 lần thì dừng và báo cáo trung thực.

Kết thúc bằng báo cáo theo mẫu ở `prompts/chung/00-cach-dung-prompt.md` mục 4, thêm mục "Nguyên nhân gốc".
```
