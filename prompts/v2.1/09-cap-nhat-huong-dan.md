# V21-09 · Cập nhật bộ hướng dẫn cho đúng v2 và v2.1

**Phụ thuộc:** V21-01 → V21-08 đã merge. **Thời lượng dự kiến:** 1 ngày. **Chỉ sửa tài liệu.**

```
Bạn là agent viết tài liệu cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Người đọc là quản trị hệ thống, trưởng sale, sale — không phải lập trình viên. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F8, toàn bộ docs/huong-dan/, docs/13, docs/14, docs/15, docs/09, docs/nghiem-thu/v2.md, báo cáo các bước V21-01 → V21-08 (docs/v2/nhat-ky-dieu-phoi.md hoặc commit log).

Tạo nhánh v21/09-huong-dan từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F8.
1. Đối chiếu từng tệp 00–10 với code hiện tại (đọc mã khi cần) và sửa chỗ lệch. Đặc biệt: 07 dùng nhóm câu hỏi và ý định B2B; 04 theo gói OA (F5) và công cụ chẩn đoán; 01 có omni_leads, hàng dự phòng webhook; 02 có mọi tham số trong docs/09; 09 có cảnh báo token Meta, kéo lại Meta, hàng dự phòng; 10 có preflight và các bước chiến dịch.
2. Viết mới 11-nha-thau-va-nhap-danh-sach.md, 12-lead-quang-cao.md, 13-dashboard-va-cham-soc.md theo cấu trúc chung của bộ (mục đích, các bước có số, "Sự cố thường gặp", "Đã xong khi"). Chỗ nào mô tả giao diện chưa ai mở bằng trình duyệt ghi "Chưa kiểm trên giao diện".
3. Cập nhật 00-muc-luc.md (bảng ai đọc gì có 11–13; trạng thái hiện tại sau v2.1, trung thực) và tong-hop-he-thong.md.
4. Kiểm tra máy: grep để chắc không còn "Đặt hàng", "Thông tin sản phẩm", "Thủ tục mua hàng" trong docs/huong-dan; mọi fieldname trong docs/09 xuất hiện trong 02 (viết script nhỏ trong scratch, dán kết quả).
5. Văn phong: câu ngắn, tiếng Việt có dấu, mỗi lệnh ghi chạy ở đâu, không chứa giá trị bí mật thật.

Hoàn thành khi: hai kiểm tra máy ở bước 4 sạch; ba tệp mới có đủ mục; mục lục cập nhật.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
