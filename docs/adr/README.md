# Ghi quyết định kiến trúc (ADR)

Mỗi quyết định làm thay đổi hoặc bổ sung đặc tả là một tệp `NNNN-ten-ngan.md` trong thư mục này. Agent **tạo ADR và dừng hỏi** nếu quyết định thay đổi DocType, luồng dữ liệu, hoặc hành vi với khách. Các quyết định trong phạm vi một module (ví dụ chọn cách hiển thị hội thoại A/B/C ở `docs/06` mục 8) thì agent ghi ADR và tiếp tục.

## Mẫu

```markdown
# NNNN · Tiêu đề quyết định

- Ngày: YYYY-MM-DD
- Trạng thái: Đề xuất | Đã chấp nhận | Thay thế bởi NNNN
- Prompt: D1-06

## Bối cảnh
Vấn đề cần quyết, ràng buộc, điều đã kiểm tra (kèm tệp/dòng).

## Các lựa chọn
1. … — ưu, nhược
2. … — ưu, nhược

## Quyết định
Chọn … vì …

## Hệ quả
Điều phải làm thêm, điều trở nên khó hơn, tài liệu cần sửa.
```
